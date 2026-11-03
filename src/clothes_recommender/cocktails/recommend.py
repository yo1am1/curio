"""Offline cocktail search, similar recipes and "what can I make?" pantry matching.

Everything is precomputed from the catalog at construction, deterministic, and ties are broken
by the lower drink_id.

Ingredient matching:
- Search filters (`ingredients`, `exclude_ingredients`) match a recipe ingredient when the
  names are equal ignoring case, an alias in `PANTRY_ALIASES` covers it, or every word of the
  filter is a word of the ingredient ("rum" matches "Light rum", "egg" matches "Egg white").
  The broad match keeps exclusions on the safe side; it is still no allergen guarantee.
- The pantry is stricter: an item covers a recipe ingredient only when the names are equal
  ignoring case or `PANTRY_ALIASES` lists it, and `PANTRY_STAPLES` count as always available.
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from clothes_recommender.cocktails.catalog import CocktailCatalog, ingredient_key
from clothes_recommender.cocktails.domain import (
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
# Assumed to be in every kitchen, so they never count as missing.
PANTRY_STAPLES = frozenset({"ice", "water"})
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


@dataclass(frozen=True)
class _Ingredient:
    key: str
    name: str  # catalog spelling as written in the recipe
    words: frozenset[str]


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
        candidates = [
            row
            for row, cocktail in enumerate(self._cocktails)
            if _alcohol_ok(cocktail, query.alcohol)
            and _category_ok(cocktail, query.category)
            and self._ingredients_ok(row, query.ingredients, query.exclude_ingredients)
        ]
        terms = self._query_terms(query.query)
        vector = self._query_vector(terms)
        if vector is None:
            if tokens(query.query):
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
                row: float(score)
                for row, score in zip(candidates, similarity, strict=True)
                if score > 0
            }
            ranked = sorted(scores, key=lambda row: (-round(scores[row], 9), self._row_id(row)))
        return [
            CocktailHit(
                cocktail=self._cocktails[row],
                score=round(scores[row], 6),
                reasons=self._search_reasons(row, query, set(terms)),
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
                if other != row and _alcohol_ok(self._cocktails[other], alcohol)
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
        matches = []
        for row, cocktail in enumerate(self._cocktails):
            if not _alcohol_ok(cocktail, alcohol):
                continue
            recipe = self._recipes[row]
            have = [i.name for i in recipe if i.key in covered or i.key in PANTRY_STAPLES]
            missing = [i.name for i in recipe if not (i.key in covered or i.key in PANTRY_STAPLES)]
            uses_pantry = any(i.key in covered for i in recipe)
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
        matches.sort(
            key=lambda m: (
                len(m.missing),
                -m.coverage,
                m.cocktail.name.casefold(),
                m.cocktail.drink_id,
            )
        )
        return matches[:top_k]

    def _ingredients_ok(self, row: int, required: Sequence[str], excluded: Sequence[str]) -> bool:
        recipe = self._recipes[row]
        return all(_filter_hits(term, recipe) for term in required if term.strip()) and not any(
            _filter_hits(term, recipe) for term in excluded if term.strip()
        )

    def _query_terms(self, text: str) -> dict[str, float]:
        """Vocabulary words a query uses with their counts; unknown words expand as prefixes."""
        terms: Counter[str] = Counter()
        for word in tokens(text):
            if word in self._vocabulary:
                terms[word] += 1
            elif len(word) >= MIN_PREFIX:
                expansions = [w for w in self._vocabulary if w.startswith(word)]
                for expansion in expansions:
                    terms[expansion] += 1 / len(expansions)
        return dict(terms)

    def _query_vector(self, terms: dict[str, float]) -> np.ndarray | None:
        if not terms:
            return None
        vector = np.zeros(len(self._vocabulary))
        for word, weight in terms.items():
            column = self._vocabulary[word]
            vector[column] = weight * self._idf[column]
        return vector / np.linalg.norm(vector)

    def _search_reasons(self, row: int, query: CocktailQuery, query_words: set[str]) -> list[str]:
        cocktail = self._cocktails[row]
        recipe = self._recipes[row]
        reasons = []
        in_name = [w for w in dict.fromkeys(tokens(cocktail.name)) if w in query_words]
        if in_name:
            reasons.append(f'Name matches "{" ".join(in_name)}"')
        contained = [
            i.name.lower()
            for i in recipe
            if i.words & query_words or any(_ingredient_hit(t, i) for t in query.ingredients)
        ]
        if contained:
            reasons.append(f"Contains {_join(contained)}")
        if query.category or query_words & set(tokens(cocktail.category)):
            reasons.append(f"Category: {cocktail.category}")
        if query_words & (set(tokens(cocktail.glass)) - {"glass"}):
            reasons.append(f"Served in a {cocktail.glass.lower()}")
        if any(t.strip() for t in query.exclude_ingredients):
            excluded = [t.strip().lower() for t in query.exclude_ingredients if t.strip()]
            reasons.append(f"Without {_join(excluded)}")
        if query.alcohol != "any":
            reasons.append(_alcohol_label(cocktail))
        return reasons

    def _similar_reasons(self, row: int, other: int, alcohol: AlcoholFilter) -> list[str]:
        theirs = {i.key for i in self._recipes[other]}
        shared = {i.key: i.name for i in self._recipes[row] if i.key in theirs}
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


def pantry_coverage(items: Iterable[str]) -> set[str]:
    """Ingredient keys a shopper's pantry covers, including `PANTRY_ALIASES` expansions."""
    covered: set[str] = set()
    for item in list(items)[:MAX_PANTRY_ITEMS]:
        key = ingredient_key(item)
        if key:
            covered.add(key)
            covered.update(PANTRY_ALIASES.get(key, ()))
    return covered


def _recipe_ingredients(cocktail: Cocktail) -> list[_Ingredient]:
    unique: dict[str, _Ingredient] = {}
    for ingredient in cocktail.ingredients:
        key = ingredient_key(ingredient.name)
        if key and key not in unique:
            unique[key] = _Ingredient(key, ingredient.name.strip(), frozenset(tokens(key)))
    return list(unique.values())


def _document_tokens(cocktail: Cocktail) -> list[str]:
    words = tokens(cocktail.name) * NAME_WEIGHT
    words += tokens(cocktail.category) + tokens(cocktail.glass)
    for ingredient in cocktail.ingredients:
        words += tokens(ingredient.name)
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
    """IDF per ingredient key and the L2-normalised IDF-weighted recipe x ingredient matrix."""
    frequency = Counter(i.key for recipe in recipes for i in recipe)
    keys = sorted(frequency)
    idf = {key: _smooth_idf(frequency[key], len(recipes)) for key in keys}
    column = {key: i for i, key in enumerate(keys)}
    matrix = np.zeros((len(recipes), len(keys)))
    for row, recipe in enumerate(recipes):
        for ingredient in recipe:
            matrix[row, column[ingredient.key]] = idf[ingredient.key]
    return idf, _normalise_rows(matrix)


def _normalise_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)


def _ingredient_hit(term: str, ingredient: _Ingredient) -> bool:
    key = ingredient_key(term)
    if not key:
        return False
    if key == ingredient.key or ingredient.key in PANTRY_ALIASES.get(key, ()):
        return True
    words = set(tokens(key))
    return bool(words) and words <= ingredient.words


def _filter_hits(term: str, recipe: Sequence[_Ingredient]) -> bool:
    return any(_ingredient_hit(term, ingredient) for ingredient in recipe)


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
