"""Offline cocktail search, similar recipes and "what can I make?" pantry matching.

Everything is precomputed from the catalog at construction, deterministic, and ties are broken
by the lower drink_id.

Words are normalised the same way for recipes and requests (`term`): a light plural rule
("limes" -> "lime", "cherries" -> "cherry") and the spelling variants in `VARIANTS`
("whisky" -> "whiskey").

Ingredient matching:
- Search filters (`ingredients`, `exclude_ingredients`) match a recipe ingredient when the
  names are equal ignoring case and plurals, an alias in `PANTRY_ALIASES` covers it, or every
  word of the filter is a word of the ingredient ("rum" matches "Light rum", "eggs" matches
  "Egg white"). The broad match keeps exclusions on the safe side; it is still no allergen
  guarantee.
- The pantry is stricter: an item covers a recipe ingredient only when the names are equal
  ignoring case and plurals or `PANTRY_ALIASES` lists it, and `PANTRY_STAPLES` count as always
  available.
- Similar recipes compare `FAMILIES` of ingredients ("Lime" ~ "Lime juice", "White rum" ~
  "Light rum").

Free-text search also maps flavour words to ingredient hints (`FLAVOURS`: "fresh", "creamy",
"fruity", ...) and reads "alcohol-free", "non-alcoholic" or "mocktail" as the alcohol filter.
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from curio.domains.cocktails.catalog import CocktailCatalog, ingredient_key
from curio.domains.cocktails.models import (
    OPTIONAL_ALCOHOL,
    AlcoholFilter,
    Cocktail,
    CocktailHit,
    CocktailQuery,
    PantryMatch,
)

_LIME = ("lime", "lime juice", "fresh lime juice", "lime peel")
_LEMON = ("lemon", "lemon juice", "fresh lemon juice", "lemon peel")
_ORANGE = ("orange", "orange juice", "orange peel")
_RUM = ("rum", "light rum", "white rum", "dark rum", "gold rum")
_SODA = ("soda water", "club soda", "carbonated water")
_SUGAR = ("sugar", "sugar syrup", "simple syrup")
# What a shopper's item also covers (keys and values are `ingredient_key` forms). A fresh lime
# gives juice, a wedge and peel, but bottled lime juice is not a wedge, so the mapping is one-way.
# Generic "rum" covers the common styles; spirits with their own character (151 proof, spiced)
# and liqueurs are left out on purpose.
PANTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "lime": _LIME,
    "limes": _LIME,
    "lemon": _LEMON,
    "lemons": _LEMON,
    "orange": _ORANGE,
    "oranges": _ORANGE,
    "rum": _RUM,
    "soda": _SODA,
    "soda water": _SODA,
    "club soda": _SODA,
    "carbonated water": _SODA,
    "sugar": _SUGAR,
    "sugar syrup": ("sugar syrup", "simple syrup"),
    "simple syrup": ("sugar syrup", "simple syrup"),
}
# Spelling variants mapped to the catalog's more common form (after accent folding).
VARIANTS: dict[str, str] = {
    "whisky": "whiskey",
    "whiskies": "whiskey",
    "yogurt": "yoghurt",
    "sirup": "syrup",
    "liquor": "liqueur",
    "chilli": "chili",
}
# Ingredient families for similar recipes (keys and values are normalised `term` forms).
FAMILIES: dict[str, str] = {
    "lime juice": "lime",
    "fresh lime juice": "lime",
    "lemon juice": "lemon",
    "fresh lemon juice": "lemon",
    "orange juice": "orange",
    "fresh orange juice": "orange",
    "white rum": "light rum",
    "silver rum": "light rum",
    "club soda": "soda water",
    "carbonated water": "soda water",
    "sugar syrup": "simple syrup",
}
# Flavour words in a search and the catalog words they hint at; their weight is shared.
FLAVOURS: dict[str, tuple[str, ...]] = {
    "fresh": ("mint", "lime", "lemon", "cucumber"),
    "refreshing": ("mint", "lime", "lemon", "cucumber"),
    "minty": ("mint", "menthe"),
    "fruity": ("fruit", "pineapple", "strawberry", "orange", "mango", "peach", "passion"),
    "tropical": ("pineapple", "coconut", "mango", "passion", "banana", "papaya"),
    "citrusy": ("citrus", "lime", "lemon", "orange", "grapefruit"),
    "sweet": ("sugar", "syrup", "grenadine", "honey"),
    "sour": ("sour", "lemon", "lime"),
    "creamy": ("creamy", "cream", "milk"),
    "chocolatey": ("chocolate", "cocoa", "cacao"),
    "chocolaty": ("chocolate", "cocoa", "cacao"),
    "spicy": ("spice", "spiced", "pepper", "ginger", "cinnamon", "chili"),
    "fizzy": ("soda", "tonic", "fizz", "champagne", "prosecco"),
    "bubbly": ("soda", "tonic", "fizz", "champagne", "prosecco"),
    "smoky": ("scotch", "islay", "mezcal"),
}
# Query words (or word pairs) that ask for alcohol-free drinks instead of matching text.
ALCOHOL_FREE_WORDS = frozenset({"mocktail", "nonalcoholic", "alcoholfree"})
ALCOHOL_FREE_PAIRS = frozenset(
    {("alcohol", "free"), ("non", "alcoholic"), ("no", "alcohol"), ("without", "alcohol")}
)
# Assumed to be in every kitchen, so they never count as missing.
PANTRY_STAPLES = frozenset({"ice", "water"})
# Drink names that are crude, sexual or a slur: Curio is a public demo, so search, "similar"
# and "What can I make?" never offer them unless the query names the drink; pairings never.
CRUDE_NAMES = frozenset(
    name.casefold()
    for name in (
        "After sex",
        "Brain Fart",
        "Dirty Nipple",
        "Fuzzy Asshole",
        "Hot Creamy Bush",
        "Orgasm",
        "Pink Panty Pulldowns",
        "Popped cherry",
        "Pornstar Martini",
        "Quick F**K",
        "Royal Bitch",
        "Smut",
        "Zipperhead",
    )
)
# The words of a base spirit: a pantry drink missing one is further from made than a drink
# missing only a mixer or a garnish.
SPIRIT_WORDS = frozenset(
    {
        "vodka", "gin", "rum", "tequila", "mezcal", "whiskey", "bourbon", "scotch", "rye",
        "brandy", "cognac", "pisco", "cachaca", "applejack", "absinthe", "everclear",
        "calvado", "grappa", "ouzo", "sake", "soju", "aquavit", "kirschwasser",
    }
)  # fmt: skip
NAME_WEIGHT = 2
MAX_PANTRY_ITEMS = 50
MAX_REASON_ITEMS = 3
# Unknown query words at least this long also match vocabulary words they start ("marg").
MIN_PREFIX = 3
_STOPWORDS = frozenset({"a", "an", "and", "the", "of", "with", "in", "on", "for", "to", "or"})
_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    """Lowercase, accent-free words without stopwords ("Crème de Cassis" -> creme, cassis)."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    ascii_text = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return [t for t in _TOKEN.findall(ascii_text) if t not in _STOPWORDS]


def term(word: str) -> str:
    """A token's normalised form: spelling variant, then a light plural rule."""
    word = VARIANTS.get(word, word)
    if len(word) <= 3 or word.endswith(("ss", "us")):
        return word
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith(("ches", "shes", "sses", "xes", "oes")):
        word = word[:-2]
    elif word.endswith("s"):
        word = word[:-1]
    return VARIANTS.get(word, word)


def terms(text: str) -> list[str]:
    """`tokens` in their normalised form ("Limes" -> lime, "Whisky" -> whiskey)."""
    return [term(t) for t in tokens(text)]


@dataclass(frozen=True)
class _Ingredient:
    key: str
    name: str  # catalog spelling as written in the recipe
    words: frozenset[str]  # normalised words
    match_key: str  # normalised words joined ("Fresh Limes" -> "fresh lime")
    family: str  # FAMILIES group of match_key, used by similar()


@dataclass(frozen=True)
class _Filter:
    """A search filter term, normalised once per query."""

    key: str
    match_key: str
    aliases: frozenset[str]
    words: frozenset[str]

    def hits(self, ingredient: _Ingredient) -> bool:
        return (
            ingredient.key == self.key
            or ingredient.match_key == self.match_key
            or ingredient.key in self.aliases
            or (bool(self.words) and self.words <= ingredient.words)
        )


class CocktailRecommender:
    def __init__(self, catalog: CocktailCatalog) -> None:
        self._cocktails: list[Cocktail] = list(catalog)
        self._row = {c.drink_id: row for row, c in enumerate(self._cocktails)}
        self._recipes = [_recipe_ingredients(c) for c in self._cocktails]
        self._vocabulary, self._idf, self._documents = _tfidf(
            [_document_tokens(c) for c in self._cocktails]
        )
        self._ingredient_idf, self._ingredient_vectors = _ingredient_matrix(self._recipes)

    def search(self, query: CocktailQuery) -> list[CocktailHit]:
        words, alcohol_free = _alcohol_words(tokens(query.query))
        if alcohol_free and query.alcohol == "any":
            query = query.model_copy(update={"alcohol": "non_alcoholic"})
        required = _filters(query.ingredients)
        excluded = _filters(query.exclude_ingredients)
        named = query.query.casefold()
        candidates = [
            row
            for row, cocktail in enumerate(self._cocktails)
            if (not is_crude(cocktail) or cocktail.name.casefold() in named)
            and _alcohol_ok(cocktail, query.alcohol)
            and _category_ok(cocktail, query.category)
            and self._ingredients_ok(row, required, excluded)
        ]
        concepts = self._query_concepts(words)
        weights: Counter[str] = Counter()
        for concept in concepts:
            weights.update(concept)
        vector = self._query_vector(weights)
        if vector is None:
            if words:
                return []  # no recipe uses any query word
            has_filters = bool(
                query.ingredients
                or query.exclude_ingredients
                or query.category
                or query.alcohol != "any"
            )
            ranked = sorted(candidates, key=lambda row: self._name_order(row))
            scores = dict.fromkeys(ranked, 1.0 if has_filters else 0.0)
        else:
            similarity = self._documents[candidates] @ vector if candidates else np.empty(0)
            scores = {
                row: float(score) * self._coverage(row, concepts)
                for row, score in zip(candidates, similarity, strict=True)
                if score > 0
            }
            ranked = sorted(scores, key=lambda row: (-round(scores[row], 9), self._row_id(row)))
        return [
            CocktailHit(
                cocktail=self._cocktails[row],
                score=round(scores[row], 6),
                reasons=self._search_reasons(row, query, required, set(weights)),
            )
            for row in ranked[: query.top_k]
        ]

    def similar(
        self, drink_id: int, top_k: int = 5, alcohol: AlcoholFilter = "any"
    ) -> list[CocktailHit]:
        row = self._row.get(drink_id)
        if row is None or top_k < 1 or self._ingredient_vectors.shape[1] == 0:
            return []
        similarity = self._ingredient_vectors @ self._ingredient_vectors[row]
        ranked = sorted(
            (
                other
                for other in np.flatnonzero(similarity > 1e-12).tolist()
                if other != row
                and _alcohol_ok(self._cocktails[other], alcohol)
                and not is_crude(self._cocktails[other])
            ),
            key=lambda other: (-round(float(similarity[other]), 9), self._row_id(other)),
        )
        return [
            CocktailHit(
                cocktail=self._cocktails[other],
                score=round(float(similarity[other]), 6),
                reasons=self._similar_reasons(row, other, alcohol),
            )
            for other in ranked[:top_k]
        ]

    def pantry(
        self,
        ingredients: Sequence[str],
        *,
        alcohol: AlcoholFilter = "any",
        max_missing: int = 2,
        top_k: int = 10,
    ) -> list[PantryMatch]:
        covered = pantry_coverage(ingredients)
        if not covered or top_k < 1:
            return []
        covered_terms = {_match_key(key) for key in covered}

        def has(i: _Ingredient) -> bool:
            return i.key in covered or i.match_key in covered_terms

        matches = []
        spirits = []
        for row, cocktail in enumerate(self._cocktails):
            if not _alcohol_ok(cocktail, alcohol) or is_crude(cocktail):
                continue
            recipe = self._recipes[row]
            have = [i.name for i in recipe if has(i) or i.key in PANTRY_STAPLES]
            missing = [i.name for i in recipe if not (has(i) or i.key in PANTRY_STAPLES)]
            uses_pantry = any(has(i) for i in recipe)
            if not recipe or not uses_pantry or len(missing) > max(max_missing, 0):
                continue
            matches.append(
                PantryMatch(
                    cocktail=cocktail,
                    have=have,
                    missing=missing,
                    coverage=round(len(have) / len(recipe), 6),
                )
            )
            spirits.append(any(i.words & SPIRIT_WORDS for i in recipe if i.name in missing))
        # A drink missing its base spirit ranks below drinks missing only mixers or garnish.
        order = sorted(
            range(len(matches)),
            key=lambda i: (
                spirits[i],
                len(matches[i].missing),
                -matches[i].coverage,
                matches[i].cocktail.name.casefold(),
                matches[i].cocktail.drink_id,
            ),
        )
        return [matches[i] for i in order[:top_k]]

    # --- Profile scoring (used by book-cocktail pairing) ---------------------------------

    def term_scores(self, weights: Mapping[str, float]) -> dict[int, float]:
        """TF-IDF cosine between every recipe and a weighted bag of words, by drink_id.

        Words are normalised like recipe words ("whisky" -> "whiskey", "bitters" ->
        "bitter"); words no recipe uses are ignored, and recipes using none of the words are
        left out. This is the search index without the query parsing, so callers that build
        a flavour profile (`pairings`) score recipes exactly as search does.
        """
        merged: Counter[str] = Counter()
        for word, weight in weights.items():
            for normalised in terms(word):
                if normalised in self._vocabulary and weight > 0:
                    merged[normalised] += weight
        vector = self._query_vector(merged)
        if vector is None:
            return {}
        similarity = self._documents @ vector
        return {
            self._row_id(row): round(float(similarity[row]), 9)
            for row in np.flatnonzero(similarity > 1e-12).tolist()
        }

    def ingredients_using(self, drink_id: int, words: Iterable[str]) -> list[str]:
        """The recipe's ingredient names (catalog spelling, recipe order) that use any of
        `words` after normalisation ("bitters" finds "Angostura Bitters")."""
        row = self._row.get(drink_id)
        wanted = {normalised for word in words for normalised in terms(word)}
        if row is None or not wanted:
            return []
        return [i.name for i in self._recipes[row] if i.words & wanted]

    def contains_any(self, drink_id: int, ingredients: Sequence[str]) -> bool:
        """Whether the recipe lists any of `ingredients`, with the broad matching of search
        exclusions ("egg" also matches "Egg white"); never an allergen guarantee."""
        row = self._row.get(drink_id)
        filters = _filters(ingredients)
        return row is not None and any(_filter_hits(f, self._recipes[row]) for f in filters)

    def ingredient_similarity(self, drink_id: int, other_id: int) -> float:
        """IDF-weighted overlap of two recipes' ingredient families (as `similar`), 0-1."""
        row, other = self._row.get(drink_id), self._row.get(other_id)
        if row is None or other is None or self._ingredient_vectors.shape[1] == 0:
            return 0.0
        return float(self._ingredient_vectors[row] @ self._ingredient_vectors[other])

    def _ingredients_ok(
        self, row: int, required: Sequence[_Filter], excluded: Sequence[_Filter]
    ) -> bool:
        recipe = self._recipes[row]
        return all(_filter_hits(f, recipe) for f in required) and not any(
            _filter_hits(f, recipe) for f in excluded
        )

    def _query_concepts(self, words: Sequence[str]) -> list[dict[str, float]]:
        """Per known query word, the vocabulary words it stands for with their weights: the
        word itself, a flavour word's hints (sharing its weight) or, for other unknown words,
        the vocabulary words it starts (also sharing its weight)."""
        concepts = []
        for token in words:
            word = term(token)
            hints = [w for w in map(term, FLAVOURS.get(token, ())) if w in self._vocabulary]
            if not hints and word in self._vocabulary:
                hints = [word]
            elif not hints and len(word) >= MIN_PREFIX:
                hints = [w for w in self._vocabulary if w.startswith(word)]
            if hints:
                concepts.append(dict.fromkeys(hints, 1 / len(hints)))
        return concepts

    def _coverage(self, row: int, concepts: Sequence[dict[str, float]]) -> float:
        """Share of the query's words a recipe uses, so recipes using all of them rank first
        ("rum and lime" puts rum-and-lime drinks above rum-only ones)."""
        document = self._documents[row]
        used = sum(any(document[self._vocabulary[w]] > 0 for w in c) for c in concepts)
        return used / len(concepts)

    def _query_vector(self, weights: dict[str, float]) -> np.ndarray | None:
        if not weights:
            return None
        vector = np.zeros(len(self._vocabulary))
        for word, weight in weights.items():
            column = self._vocabulary[word]
            vector[column] = weight * self._idf[column]
        return vector / np.linalg.norm(vector)

    def _search_reasons(
        self,
        row: int,
        query: CocktailQuery,
        required: Sequence[_Filter],
        query_words: set[str],
    ) -> list[str]:
        cocktail = self._cocktails[row]
        recipe = self._recipes[row]
        reasons = []
        in_name = [w for w in dict.fromkeys(tokens(cocktail.name)) if term(w) in query_words]
        if in_name:
            reasons.append(f'Name matches "{" ".join(in_name)}"')
        contained = [
            i.name.lower()
            for i in recipe
            if i.words & query_words or any(f.hits(i) for f in required)
        ]
        if contained:
            reasons.append(f"Contains {_join(contained)}")
        if query.category or query_words & set(terms(cocktail.category)):
            reasons.append(f"Category: {cocktail.category}")
        if query_words & (set(terms(cocktail.glass)) - {"glass"}):
            reasons.append(f"Served in a {cocktail.glass.lower()}")
        if any(t.strip() for t in query.exclude_ingredients):
            excluded = [t.strip().lower() for t in query.exclude_ingredients if t.strip()]
            reasons.append(f"Without {_join(excluded)}")
        if query.alcohol != "any":
            reasons.append(_alcohol_label(cocktail))
        return reasons

    def _similar_reasons(self, row: int, other: int, alcohol: AlcoholFilter) -> list[str]:
        theirs = {i.family for i in self._recipes[other]}
        shared: dict[str, str] = {}
        for i in self._recipes[row]:
            if i.family in theirs:
                shared.setdefault(i.family, i.name)
        by_rarity = sorted(shared, key=lambda key: (-self._ingredient_idf[key], key))
        reasons = [f"Shares {_join([shared[key].lower() for key in by_rarity])}"]
        if self._cocktails[row].category == self._cocktails[other].category:
            reasons.append(f"Also a {self._cocktails[other].category.lower()}")
        if alcohol != "any":
            reasons.append(_alcohol_label(self._cocktails[other]))
        return reasons

    def _name_order(self, row: int) -> tuple[str, int]:
        return self._cocktails[row].name.casefold(), self._row_id(row)

    def _row_id(self, row: int) -> int:
        return self._cocktails[row].drink_id


def is_crude(cocktail: Cocktail) -> bool:
    """Whether a drink's name is one Curio never offers unprompted (`CRUDE_NAMES`)."""
    return cocktail.name.casefold() in CRUDE_NAMES


def pantry_coverage(items: Iterable[str]) -> set[str]:
    """Ingredient keys a shopper's pantry covers, including `PANTRY_ALIASES` expansions."""
    covered: set[str] = set()
    for item in list(items)[:MAX_PANTRY_ITEMS]:
        key = ingredient_key(item)
        if key:
            covered.add(key)
            covered.update(PANTRY_ALIASES.get(key, ()))
    return covered


def _match_key(key: str) -> str:
    return " ".join(terms(key))


def _recipe_ingredients(cocktail: Cocktail) -> list[_Ingredient]:
    unique: dict[str, _Ingredient] = {}
    for ingredient in cocktail.ingredients:
        key = ingredient_key(ingredient.name)
        if key and key not in unique:
            words = terms(key)
            match_key = " ".join(words)
            unique[key] = _Ingredient(
                key,
                ingredient.name.strip(),
                frozenset(words),
                match_key,
                FAMILIES.get(match_key, match_key or key),
            )
    return list(unique.values())


def _filters(values: Iterable[str]) -> list[_Filter]:
    """Filter terms normalised once per query; blank terms are ignored."""
    filters = []
    for value in values:
        key = ingredient_key(value)
        if not key:
            continue
        words = terms(key)
        match_key = " ".join(words)
        aliases = PANTRY_ALIASES.get(key) or PANTRY_ALIASES.get(match_key, ())
        filters.append(_Filter(key, match_key, frozenset(aliases), frozenset(words)))
    return filters


def _alcohol_words(words: list[str]) -> tuple[list[str], bool]:
    """Query words without the ones asking for alcohol-free drinks, and whether any did."""
    kept: list[str] = []
    alcohol_free = False
    for word in words:
        if word in ALCOHOL_FREE_WORDS or term(word) in ALCOHOL_FREE_WORDS:
            alcohol_free = True
        elif kept and (kept[-1], word) in ALCOHOL_FREE_PAIRS:
            kept.pop()
            alcohol_free = True
        else:
            kept.append(word)
    return kept, alcohol_free


def _document_tokens(cocktail: Cocktail) -> list[str]:
    words = terms(cocktail.name) * NAME_WEIGHT
    words += terms(cocktail.category) + terms(cocktail.glass)
    for ingredient in cocktail.ingredients:
        words += terms(ingredient.name)
    return words


def _smooth_idf(document_frequency: int, documents: int) -> float:
    return math.log((1 + documents) / (1 + document_frequency)) + 1


def _tfidf(documents: list[list[str]]) -> tuple[dict[str, int], np.ndarray, np.ndarray]:
    """Vocabulary, idf per word and the L2-normalised TF-IDF matrix (one row per document)."""
    vocabulary = {w: i for i, w in enumerate(sorted({w for doc in documents for w in doc}))}
    counts = np.zeros((len(documents), len(vocabulary)))
    for row, doc in enumerate(documents):
        for word, count in Counter(doc).items():
            counts[row, vocabulary[word]] = count
    idf = np.array([_smooth_idf(df, len(documents)) for df in (counts > 0).sum(axis=0).tolist()])
    return vocabulary, idf, _normalise_rows(counts * idf)


def _ingredient_matrix(recipes: list[list[_Ingredient]]) -> tuple[dict[str, float], np.ndarray]:
    """IDF per ingredient family and the L2-normalised IDF-weighted recipe x family matrix."""
    frequency = Counter(family for recipe in recipes for family in {i.family for i in recipe})
    keys = sorted(frequency)
    idf = {key: _smooth_idf(frequency[key], len(recipes)) for key in keys}
    column = {key: i for i, key in enumerate(keys)}
    matrix = np.zeros((len(recipes), len(keys)))
    for row, recipe in enumerate(recipes):
        for ingredient in recipe:
            matrix[row, column[ingredient.family]] = idf[ingredient.family]
    return idf, _normalise_rows(matrix)


def _normalise_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)


def _filter_hits(f: _Filter, recipe: Sequence[_Ingredient]) -> bool:
    return any(f.hits(ingredient) for ingredient in recipe)


def _alcohol_ok(cocktail: Cocktail, alcohol: AlcoholFilter) -> bool:
    if alcohol == "non_alcoholic":
        return cocktail.alcohol_free
    if alcohol == "alcoholic":
        return not cocktail.alcohol_free
    return True


def _category_ok(cocktail: Cocktail, category: str | None) -> bool:
    return not (category and category.strip()) or (
        cocktail.category.casefold() == category.strip().casefold()
    )


def _alcohol_label(cocktail: Cocktail) -> str:
    if cocktail.alcoholic == OPTIONAL_ALCOHOL:
        return "Alcohol optional"
    return "Alcohol-free" if cocktail.alcohol_free else "Contains alcohol"


def _join(names: Sequence[str]) -> str:
    """'a, b, c' with at most MAX_REASON_ITEMS names and a '+N more' tail."""
    unique = list(dict.fromkeys(names))
    shown = ", ".join(unique[:MAX_REASON_ITEMS])
    extra = len(unique) - MAX_REASON_ITEMS
    return f"{shown} +{extra} more" if extra > 0 else shown
