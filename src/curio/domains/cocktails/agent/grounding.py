"""Cocktail grounding: drink IDs, catalog drink names and recipe amounts in an answer."""

import re
import unicodedata
from collections.abc import Iterable, Sequence

from curio.domains.cocktails.models import NON_ALCOHOLIC, OPTIONAL_ALCOHOL, Cocktail
from curio.engine.grounding import (
    SAFETY_CLAIMS as ENGINE_SAFETY_CLAIMS,
)
from curio.engine.grounding import (
    SAFETY_REASON,
    Binder,
    Citation,
    Claim,
    FactCheck,
    FlagClaim,
    Item,
    Tally,
    any_of,
    check_flags,
    check_ids,
    check_marked_names,
    check_safety,
    hedged,
    hedged_after,
    name_allowed,
)
from curio.engine.schemas import GroundingReport

# Cocktail answers cite drinks as "[id 12]" (the prompt's format) or "drink #12".
_DRINK_ID = re.compile(r"\[id (\d+)\]|\b(?:drink|recipe|cocktail) #(\d+)\b", re.IGNORECASE)
DRINK_CITATIONS = (Citation(_DRINK_ID, "drink"),)
_VULGAR_FRACTIONS = {"½": " 1/2", "¼": " 1/4", "¾": " 3/4", "⅓": " 1/3", "⅔": " 2/3", "⅛": " 1/8"}
_UNITS = {
    "oz": "oz",
    "ounce": "oz",
    "ounces": "oz",
    "ml": "ml",
    "cl": "cl",
    "tsp": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "tbsp": "tbsp",
    "tblsp": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "cup": "cup",
    "cups": "cup",
    "shot": "shot",
    "shots": "shot",
    "part": "part",
    "parts": "part",
    "dash": "dash",
    "dashes": "dash",
    "jigger": "jigger",
    "jiggers": "jigger",
    "slice": "slice",
    "slices": "slice",
    "wedge": "wedge",
    "wedges": "wedge",
    "leaf": "leaf",
    "leaves": "leaf",
    "sprig": "sprig",
    "sprigs": "sprig",
    "drop": "drop",
    "drops": "drop",
}
# "2 oz", "1 1/2 oz", "1/2 tsp", "1.5 cl", "8 leaves" (after vulgar fractions are spelled out).
_MEASURE = re.compile(
    r"(?<![\w/.,])(\d+(?:[.,]\d+)?(?:\s+\d+/\d+)?|\d+/\d+)\s*("
    + "|".join(sorted(_UNITS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)
_MEASURE_TOLERANCE = 0.001

type Measure = tuple[float, str]


def _quantity(text: str) -> float:
    total = 0.0
    for part in text.replace(",", ".").split():
        numerator, _, denominator = part.partition("/")
        total += float(numerator) / float(denominator) if denominator else float(numerator)
    return total


def measures_in(text: str) -> list[tuple[str, Measure]]:
    """Ingredient amounts in `text` as (as written, (quantity, normalised unit))."""
    for symbol, spelled in _VULGAR_FRACTIONS.items():
        text = text.replace(symbol, spelled)
    found = []
    for match in _MEASURE.finditer(text):
        denominators = [part.partition("/")[2] for part in match.group(1).split()]
        if any(denominator and int(denominator) == 0 for denominator in denominators):
            continue
        found.append((match.group(0), (_quantity(match.group(1)), _UNITS[match.group(2).lower()])))
    return found


# How an answer labels a drink's alcohol status, and the catalog labels each phrase allows:
# "alcohol-free" only a drink marked non-alcoholic, "contains alcohol" one with alcohol or
# alcohol optional, "alcoholic" only one with alcohol, "alcohol optional" only an optional
# one.
ALCOHOL_CLAIMS = (
    FlagClaim(
        re.compile(
            r"\balcohol[- ]optional\b|\boptional alcohol\b|\balcohol is optional\b"
            r"|\bwith or without (?:the |any )?(?:alcohol|booze)\b",
            re.I,
        ),
        frozenset({"optional"}),
        frozenset({"free", "contains"}),
        "alcohol optional",
    ),
    FlagClaim(
        re.compile(
            r"\b(?:alcohol|booze)[- ]free\b|\bnon[- ]?alcoholic\b|\bzero[- ]proof\b"
            r"|\bno alcohol\b|(?<!with or )\bwithout (?:any )?alcohol\b|\bmocktails?\b"
            r"|\bvirgin version\b",
            re.I,
        ),
        frozenset({"free"}),
        frozenset({"contains", "optional"}),
        "alcohol-free",
    ),
    FlagClaim(
        re.compile(r"\bcontains? alcohol\b|\bhas alcohol\b|\bmade with alcohol\b", re.I),
        frozenset({"contains", "optional"}),
        frozenset({"free"}),
        "a drink with alcohol",
    ),
    # "Alcoholic" says the drink always has alcohol: not one whose alcohol is optional.
    FlagClaim(
        re.compile(r"\b(?<!non-)(?<!non )alcoholic\b", re.I),
        frozenset({"contains"}),
        frozenset({"free", "optional"}),
        "always alcoholic",
    ),
)
# Allergen, dietary, health and age-safety promises: the recipes cannot support any of them.
# Every domain's allergen and age-safety phrases, and the diet and health words of drinks.
SAFETY_CLAIMS = re.compile(
    ENGINE_SAFETY_CLAIMS.pattern + r"|\b(?:sugar|caffeine|alcohol[- ]and[- ]caffeine)[- ]free\b"
    r"|\b(?:vegan|vegetarian|keto|paleo|halal|kosher|low[- ]calorie|low[- ]sugar|"
    r"diabetic[- ]friendly)\b"
    r"|\b(?:healthy|good for you|good for your health|cures?|detox\w*|hangover[- ]free|"
    r"prevents? hangovers?|boosts? (?:your )?immun\w*)\b",
    re.IGNORECASE,
)

# Ingredients too generic to check against a recipe: "over ice", "a splash of water".
_GENERIC_INGREDIENTS = frozenset(
    {"ice", "water", "fruit", "fruit juice", "juice", "rose", "port", "garnish", "soda", "mix"}
)
# Names for the same ingredient: an answer may say either.
_INGREDIENT_SYNONYMS = {
    "white rum": "light rum",
    "whipped cream": "whipping cream",
    "club soda": "soda water",
    "carbonated water": "soda water",
    "simple syrup": "sugar syrup",
    "cointreau": "triple sec",
    "yogurt": "yoghurt",
    "cachaça": "cachaca",
}
# Words between a drink and an ingredient that make the ingredient part of its recipe:
# "the Moscow Mule uses ginger beer", "**Afterglow** - grenadine, ...", "made with gin".
_RECIPE_CUE = re.compile(
    r"\b(?:uses?|using|contains?|containing|made (?:with|from|of)|mix(?:es|ed)?|combines?|"
    r"with|has|have|needs?|takes?|calls? for|blends?|built on|topped (?:up )?with|is|are|"
    r"includes?|including|of)\b|[:|\u2013\u2014]|\s-\s"
)
# A clause about a substitute or a choice: "swap in ginger beer", "or use lemon instead".
_SUBSTITUTE = re.compile(
    r"\b(?:swap|substitut\w*|instead|replace\w*|alternative|variation|twist|version|could|"
    r"can also|optional(?:ly)?|prefer|rather)\b"
)
# A bullet that is only an ingredient ("- Ginger beer", "* 2 dashes of bitters").
_INGREDIENT_LINE = re.compile(
    r"[ \t]*(?:[-*+\u2022]|\d+[.)])[ \t]+(?:[^\s.!?]+[ \t]+){0,5}[^\s.!?]*\s*$"
)
# An ingredient said to be missing: "has no tequila", "doesn't contain any cream".
_ABSENT = re.compile(
    r"\b(?:has|have|contains?|uses?|includes?|calls? for|there['\u2019]s|there is)\s+no\s+"
    r"(?:[\w-]+\s+)?$"
    r"|\b(?:doesn['\u2019]t|does not|don['\u2019]t|do not)\s+"
    r"(?:have|contain|use|include|need|call for)\s+(?:any\s+)?(?:[\w-]+\s+)?$"
)
# The shopper's own bottle, not the recipe's: "your ginger beer", "the rum you have".
_OWNED = re.compile(r"\b(?:your|my|our)\s+(?:own\s+)?(?:\w+\s+)?$")
_INGREDIENT_CLAUSE = re.compile(r"[.!?;\n]|,\s|\bbut\b|\bwhile\b")


def alcohol_label(cocktail: Cocktail) -> str:
    """The grounding label of a drink's alcohol status: "free", "optional" or "contains"."""
    if cocktail.alcoholic == NON_ALCOHOLIC:
        return "free"
    if cocktail.alcoholic == OPTIONAL_ALCOHOL:
        return "optional"
    return "contains"


def drink_item(cocktail: Cocktail) -> Item:
    measures = tuple(m for i in cocktail.ingredients for _, m in measures_in(i.measure or ""))
    ingredients = tuple(ingredient_key(i.name) for i in cocktail.ingredients if i.name.strip())
    # The recipe as written, measures included: a measure may describe its ingredient
    # ("Carbonated soft drink: , orange").
    recipe = " ; ".join(ingredient_key(f"{i.measure or ''} {i.name}") for i in cocktail.ingredients)
    return Item(
        "drink",
        cocktail.drink_id,
        (cocktail.name,),
        {"amount": measures, "ingredient": ingredients},
        flags={"alcohol": alcohol_label(cocktail)},
        text=recipe,
    )


def ingredient_key(name: str) -> str:
    """An ingredient name for comparison: lower case, no accents, single spaces, synonyms
    folded ("White Rum" and "light rum" are one)."""
    plain = unicodedata.normalize("NFKD", name)
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    folded = " ".join(plain.casefold().split())
    return _INGREDIENT_SYNONYMS.get(folded, folded)


def recipe_has(ingredients: Sequence[str], named: str) -> bool:
    """Whether a recipe of `ingredients` (keys) has the ingredient `named`: the same one,
    or one naming it more or less precisely ("rum" for light rum, "lime juice" for lime),
    in the singular or the plural ("cherries" for maraschino cherry), or as the end of a
    compound word ("berries" for blackberries)."""
    key = _singular(ingredient_key(named))
    for have in ingredients:
        have = _singular(have)
        if key == have or _whole_word(have, key) or _whole_word(key, have):
            return True
        if len(key) >= 4 and re.search(r"\w" + re.escape(key) + r"(?!\w)", have):
            return True
    return False


def _singular(words: str) -> str:
    """Each word of an ingredient name in a rough singular: "cherries" → "cherry",
    "limes" → "lime", "leaves" stays (too irregular to matter)."""

    def one(word: str) -> str:
        if len(word) > 4 and word.endswith("ies"):
            return word[:-3] + "y"
        if len(word) > 4 and word.endswith(("ches", "shes", "oes", "sses")):
            return word[:-2]
        if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            return word[:-1]
        return word

    return " ".join(one(word) for word in words.split())


def _folded_text(text: str) -> str:
    return " ".join(re.sub(r"[*_`\"\u201c\u201d]", "", text).casefold().split())


def _whole_word(haystack: str, needle: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack) is not None


def measure_claims(text: str) -> list[Claim]:
    """Ingredient amounts in `text` as claims ("1 1/2 oz", "½ oz"), at their positions."""
    spelled, origin = [], []
    for index, char in enumerate(text):
        piece = _VULGAR_FRACTIONS.get(char, char)
        spelled.append(piece)
        origin += [index] * len(piece)
    spelled_text = "".join(spelled)
    claims = []
    for match in _MEASURE.finditer(spelled_text):
        denominators = [part.partition("/")[2] for part in match.group(1).split()]
        if any(denominator and int(denominator) == 0 for denominator in denominators):
            continue
        start, end = origin[match.start()], origin[match.end() - 1] + 1
        measure = (_quantity(match.group(1)), _UNITS[match.group(2).lower()])
        claims.append(Claim("amount", start, end, text[start:end].strip(), measure))
    return claims


def _measure_compare(claimed: object, actual: object, mode: str) -> bool:
    return same_measure(claimed, actual)  # type: ignore[arg-type]


DRINK_COMPARE = {"amount": _measure_compare}


def _describe_measure(measure: Measure) -> str:
    quantity, unit = measure
    return f"{quantity:g} {unit}"


class CocktailGroundingCheck:
    """Checks the drinks an answer names and the facts it states about them.

    - Drink IDs must have been returned.
    - Catalog drink names, matched case-sensitively in catalog spelling, must have been
      returned or typed. Ordinary words ("a perfect afternoon drink") do not count as the
      drink "Afternoon"; names that are also ingredient names ("Limeade") are never drink
      mentions, and neither is a name inside a longer glass or ingredient name ("Martini" in
      "Martini Glass").
    - A bold, italic or quoted span that reads as a drink name must name a returned drink,
      so a drink outside the catalog ("**Paper Plane**") is caught too.
    - Ingredient amounts bind to the drink they are about (the nearest drink named in the
      sentence, else the last one named before) and must be in its recipe; an amount with
      no drink before it is unattributed.
    - "Alcohol-free", "contains alcohol", "alcoholic" and "alcohol optional" must match the
      drink's label ("alcoholic" is wrong for a drink whose alcohol is optional).
    - Allergen, diet, health and age-safety promises are always reported, unless negated
      or hedged ("I can't guarantee it's nut-free"), or attributed to the recipe and true
      by it ("dairy-free going by the ingredient list" for a recipe with no dairy; only
      dairy, egg and nuts can be read off a recipe).
    """

    def __init__(
        self,
        drink_names: Iterable[str],
        ingredient_names: Iterable[str],
        *,
        glass_names: Iterable[str] = (),
        categories: Iterable[str] = (),
    ) -> None:
        ingredient_names = list(ingredient_names)
        glass_names = list(glass_names)
        ingredients = {name.casefold() for name in ingredient_names}
        names = sorted(
            {name for name in drink_names if name.strip() and name.casefold() not in ingredients},
            key=lambda name: (-len(name), name),
        )
        self._names = any_of(names) if names else None
        phrases = sorted(
            {name for name in [*ingredient_names, *glass_names] if name.strip()},
            key=lambda name: (-len(name), name),
        )
        self._phrases = any_of(phrases, re.IGNORECASE) if phrases else None
        self._vocabulary = {
            n.casefold() for n in [*ingredient_names, *glass_names, *categories] if n.strip()
        }
        checked = sorted(
            {
                n
                for n in ingredient_names
                if n.strip() and ingredient_key(n) not in _GENERIC_INGREDIENTS
            },
            key=lambda name: (-len(name), name),
        )
        self._ingredients = (
            re.compile(
                r"(?<![\w-])(" + "|".join(map(re.escape, checked)) + r")(?:e?s)?(?!\w)",
                re.IGNORECASE,
            )
            if checked
            else None
        )
        glasses = sorted({g for g in glass_names if g.strip()}, key=len, reverse=True)
        self._glasses = any_of(glasses, re.IGNORECASE) if glasses else None

    def vocabulary(self, words: str) -> bool:
        """Ingredients, glasses and categories: never a drink name on their own."""
        return words.casefold() in self._vocabulary

    def _drink_mentions(self, text: str) -> list[str]:
        """Drink names in `text`, except those inside a longer glass or ingredient name."""
        if self._names is None:
            return []
        phrases = [m.span() for m in self._phrases.finditer(text)] if self._phrases else []
        mentions = []
        for match in self._names.finditer(text):
            start, end = match.span()
            if not any(s <= start and end <= e and e - s > end - start for s, e in phrases):
                mentions.append(match.group(0))
        return list(dict.fromkeys(mentions))

    def check(self, text: str, evidence: Sequence[Cocktail], typed: str = "") -> GroundingReport:
        """`typed` is what the shopper wrote: drinks they named may be repeated."""
        tally = Tally()
        checked, issues = check_ids(text, _DRINK_ID, (c.drink_id for c in evidence), "drink")
        tally.counts["id"] += checked
        tally.issues += issues
        evidence_names = [c.name for c in evidence]
        for name in self._drink_mentions(text):
            ok = name_allowed(name, evidence_names, typed)
            tally.add("name", ok, f"{name} was not returned by any tool", key=name.casefold())
        self.check_facts(text, evidence, typed, tally, citations=DRINK_CITATIONS)
        check_marked_names(
            text, [*evidence_names, typed], tally, kind="name", vocabulary=self.vocabulary
        )
        return tally.report()

    def check_facts(
        self,
        text: str,
        drinks: Sequence[Cocktail],
        typed: str,
        tally: Tally,
        *,
        citations: Sequence[Citation],
        amounts: bool = True,
        quoted: Sequence[str] = (),
    ) -> None:
        """Amounts, alcohol labels and safety promises, bound to `drinks`."""
        binder = Binder(text, [drink_item(c) for c in drinks], citations)
        if amounts:
            FactCheck(
                binder,
                DRINK_COMPARE,
                typed=typed,
                noun={"amount": "recipe amount"},
                describe={"amount": _describe_measure},
            ).check(measure_claims(text), tally)
        check_flags(
            text,
            binder,
            tally,
            flag="alcohol",
            claims=ALCOHOL_CLAIMS,
            kind="alcohol",
            quoted=quoted,
        )
        if drinks:
            self.check_ingredients(text, binder, tally, quoted=quoted)
        check_safety(
            text,
            tally,
            pattern=SAFETY_CLAIMS,
            reason=SAFETY_REASON,
            sourced=lambda match: borne_out_by_recipes(text, binder, match),
        )

    def check_ingredients(
        self, text: str, binder: Binder, tally: Tally, *, quoted: Sequence[str] = ()
    ) -> None:
        """An ingredient said to be in a drink must be in its recipe. An ingredient counts as
        said to be in a drink when the sentence names the drink and the words between them
        make it part of the recipe ("uses", "made with", a colon or dash before a list), when
        it is "<ingredient>-based", or when its sentence lists amounts ("- 2 oz vodka" under
        the drink). Ingredients in drink or glass names, the shopper's own ("your ginger
        beer"), in a hedged or negated clause ("if you want ginger beer", "not ginger beer"),
        about a substitute ("swap in") or in tool text the answer repeats (`quoted`) are not
        read. An ingredient matches the recipe by name (singular or plural, broader or
        narrower: "cherries" for maraschino cherry), by a word of its measure ("orange" for
        a carbonated soft drink measured as "orange"), or as a word describing the next one
        ("an orange carbonated soft drink"). One said to be missing ("has no tequila") must
        not be in the recipe.
        """
        if self._ingredients is None:
            return
        skip = [(r.start, r.end) for r in binder.refs]
        if self._names is not None:
            skip += [m.span() for m in self._names.finditer(text)]
        if self._glasses is not None:
            skip += [m.span() for m in self._glasses.finditer(text)]
        measured = {binder.sentence(c.start) for c in measure_claims(text)}
        said = [_folded_text(q) for q in quoted if q.strip()]
        for match in self._ingredients.finditer(text):
            start, end = match.span(1)
            if any(s <= start < e for s, e in skip):
                continue
            if _ABSENT.search(self._clause(text, start)):
                self._check_absent(text, binder, tally, match)
                continue
            if hedged(text, start):
                continue
            clause = self._clause(text, start)
            if _SUBSTITUTE.search(clause) or _OWNED.search(clause):
                continue
            sentence = binder.sentence(start)
            window = text[max(sentence[0], start - 30) : min(sentence[1], end + 30)]
            if said and any(_folded_text(window) in q for q in said):
                continue
            binding = binder.bind(start, end)
            if binding is None:
                continue
            based = re.match(r"[ -]based\b", text[match.end() :], re.IGNORECASE)
            cue = self._recipe_cue(text, binder, start, end)
            listed = bool(_INGREDIENT_LINE.match(text[sentence[0] : sentence[1]]))
            if not (sentence in measured or based or cue or listed):
                continue
            named = match.group(1)
            following = text[match.end() : match.end() + 40]
            ok = any(
                recipe_has(item.values.get("ingredient", ()), named)
                or _whole_word(item.text, ingredient_key(named))
                or _modifies(following, item)
                for item in binding.items
            )
            label = binding.items[0].label
            tally.add(
                "ingredient",
                ok,
                f"{label}'s recipe has no {named.lower()}",
                key=f"{label}|{ingredient_key(named)}",
            )

    def _check_absent(self, text: str, binder: Binder, tally: Tally, match: re.Match[str]) -> None:
        """ "The Long Island has no tequila": the drink it binds to in its sentence must not
        list the ingredient. A substitute or the shopper's own bottle is not read."""
        start, end = match.span(1)
        clause = self._clause(text, start)
        if _SUBSTITUTE.search(clause) or _OWNED.search(clause) or hedged_after(text, end):
            return
        binding = binder.bind(start, end)
        if binding is None or not binding.explicit:
            return
        named = match.group(1)
        found = [
            item for item in binding.items if recipe_has(item.values.get("ingredient", ()), named)
        ]
        label = binding.items[0].label
        tally.add(
            "ingredient",
            not found,
            f"{label}'s recipe has {named.lower()}",
            key=f"{label}|no {ingredient_key(named)}",
        )

    @staticmethod
    def _clause(text: str, start: int) -> str:
        before = text[max(0, start - 80) : start]
        cuts = list(_INGREDIENT_CLAUSE.finditer(before))
        return (before[cuts[-1].end() :] if cuts else before).lower()

    @staticmethod
    def _recipe_cue(text: str, binder: Binder, start: int, end: int) -> bool:
        """Whether the drink named last before the ingredient in its sentence is tied to it
        by a recipe word ("the **Moscow Mule** is 2 oz vodka ... and ginger beer")."""
        first, _ = binder.sentence(start)
        before = [r for r in binder.strong if first <= r.start and r.end <= start]
        if not before:
            return False
        gap = text[before[-1].end : start]
        return bool(_RECIPE_CUE.search(gap.lower()))


# The words right after an ingredient, up to punctuation or a conjunction.
_FOLLOWING = re.compile(
    r"[ -]+(?!(?:and|or|with|plus|in|of|on|over|for)\b)([^\W\d_][\w' -]*?)"
    r"\s*(?:[,.;:!?()\n]|\band\b|\bor\b|$)"
)


def _modifies(following: str, item: Item) -> bool:
    """Whether an ingredient word describes the ingredient after it, one the recipe has:
    "orange" in "an orange carbonated soft drink", "lemon" in "lemon soda"."""
    match = _FOLLOWING.match(following)
    if match is None:
        return False
    words = match.group(1).split()
    ingredients = item.values.get("ingredient", ())
    return any(recipe_has(ingredients, " ".join(words[:n])) for n in range(len(words), 0, -1))


# What a recipe must not list for "<allergen>-free" to hold by it. Gluten, sugar, vegan and
# the rest cannot be read off an ingredient list, so they are never borne out.
_ALLERGEN_WORDS = {
    "dairy": r"milk|cream|yog(?:h)?urt|butter|cheese|whey|kefir|half-and-half|eggnog|sherbet|"
    r"custard|ice[- ]cream|baileys|irish cream|condensed|evaporated|lassi|buttermilk",
    "egg": r"eggs?|egg whites?|egg yolks?|eggnog|advocaat|meringue",
    "nut": r"\w*nuts?|almonds?|amaretto|orgeat|frangelico|nocino|praline|marzipan|pecans?|"
    r"cashews?|pistachios?|macadamias?|nutella",
}
_ALLERGEN_OF = re.compile(r"\b(dairy|lactose|milk|eggs?|(?:pea|tree[- ])?nuts?|nut)\b", re.I)
# Plant milks are not dairy: "coconut milk", "almond milk" (almond is caught as a nut).
_PLANT_MILK = re.compile(r"\b(?:coconut|almond|oat|soy|soya|rice|cashew)\s+(?:milk|cream)\b")


def borne_out_by_recipes(text: str, binder: Binder, match: re.Match[str]) -> bool:
    """Whether "<allergen>-free" (or "contains no <allergen>") at `match` holds by the
    recipes of the drinks it is about: those its sentence names, else the drinks a plural
    subject stands for, else the drink it binds to. Only dairy, egg and nuts can be read
    off an ingredient list."""
    found = _ALLERGEN_OF.search(match.group(0))
    if found is None:
        return False
    word = found.group(1).lower()
    allergen = "dairy" if word in ("dairy", "lactose", "milk") else word.rstrip("s")
    allergen = "nut" if "nut" in allergen else allergen
    sentence = binder.sentence(match.start())
    items = binder.items_between(*sentence)
    if not items:
        binding = binder.bind(match.start(), match.end(), nearest=True)
        items = list(binding.items) if binding else []
    if not items:
        return False
    listed = re.compile(r"\b(?:" + _ALLERGEN_WORDS[allergen] + r")\b")
    return not any(listed.search(_PLANT_MILK.sub(" ", item.text)) for item in items)


def same_measure(a: Measure, b: Measure) -> bool:
    return a[1] == b[1] and abs(a[0] - b[0]) <= _MEASURE_TOLERANCE
