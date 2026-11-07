"""Cocktail grounding: drink IDs, catalog drink names and recipe amounts in an answer."""

import re
from collections.abc import Iterable, Sequence

from curio.domains.cocktails.models import NON_ALCOHOLIC, OPTIONAL_ALCOHOL, Cocktail
from curio.engine.grounding import (
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
    check_unsupported,
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
# alcohol optional, "alcohol optional" only an optional one.
ALCOHOL_CLAIMS = (
    FlagClaim(
        re.compile(r"\balcohol[- ]optional\b|\boptional alcohol\b|\balcohol is optional\b", re.I),
        frozenset({"optional"}),
        frozenset({"free", "contains"}),
        "alcohol optional",
    ),
    FlagClaim(
        re.compile(
            r"\b(?:alcohol|booze)[- ]free\b|\bnon[- ]?alcoholic\b|\bzero[- ]proof\b"
            r"|\bno alcohol\b|\bwithout (?:any )?alcohol\b|\bmocktails?\b|\bvirgin version\b",
            re.I,
        ),
        frozenset({"free"}),
        frozenset({"contains", "optional"}),
        "alcohol-free",
    ),
    FlagClaim(
        re.compile(r"\bcontains? alcohol\b|\b(?<!non-)(?<!non )alcoholic\b|\bhas alcohol\b", re.I),
        frozenset({"contains", "optional"}),
        frozenset({"free"}),
        "a drink with alcohol",
    ),
)
# Allergen, dietary, health and age-safety promises: the recipes cannot support any of them.
SAFETY_CLAIMS = re.compile(
    r"\b(?:nut|peanut|tree[- ]nut|gluten|wheat|dairy|lactose|milk|egg|soy|allergen|allergy|"
    r"sugar|caffeine)[- ]free\b"
    r"|\b(?:allergy|allergen|allergic)[- ](?:safe|friendly)\b"
    r"|\b(?:free (?:of|from)|contains no|has no|without any) (?:nuts?|peanuts?|gluten|dairy|"
    r"lactose|allergens?|eggs?)\b"
    r"|\b(?:completely |totally |perfectly |100% |guaranteed |entirely )?safe (?:for|to|with|"
    r"during|while|when) (?:anyone|everyone|everybody|kids|children|minors|teens?|teenagers|"
    r"pregnan\w*|you|your|people|those|someone|a nut|nut|allerg\w*|drink(?:ing)? while)\b"
    r"|\b(?:kid|child|pregnancy|allergy)[- ](?:safe|friendly)\b"
    r"|\bguaranteed(?: to be)? safe\b"
    r"|\b(?:vegan|vegetarian|keto|paleo|halal|kosher|low[- ]calorie|low[- ]sugar|"
    r"diabetic[- ]friendly)\b"
    r"|\b(?:healthy|good for you|good for your health|cures?|detox\w*|hangover[- ]free|"
    r"prevents? hangovers?|boosts? (?:your )?immun\w*)\b",
    re.IGNORECASE,
)
SAFETY_REASON = "the recipes cannot support allergen, diet, health or age-safety claims"


def alcohol_label(cocktail: Cocktail) -> str:
    """The grounding label of a drink's alcohol status: "free", "optional" or "contains"."""
    if cocktail.alcoholic == NON_ALCOHOLIC:
        return "free"
    if cocktail.alcoholic == OPTIONAL_ALCOHOL:
        return "optional"
    return "contains"


def drink_item(cocktail: Cocktail) -> Item:
    measures = tuple(m for i in cocktail.ingredients for _, m in measures_in(i.measure or ""))
    return Item(
        "drink",
        cocktail.drink_id,
        (cocktail.name,),
        {"amount": measures},
        flags={"alcohol": alcohol_label(cocktail)},
    )


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
    - "Alcohol-free", "contains alcohol" and "alcohol optional" must match the drink's label.
    - Allergen, diet, health and age-safety promises are always reported, unless negated
      or hedged ("I can't guarantee it's nut-free").
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
        check_unsupported(text, SAFETY_CLAIMS, tally, reason=SAFETY_REASON)


def same_measure(a: Measure, b: Measure) -> bool:
    return a[1] == b[1] and abs(a[0] - b[0]) <= _MEASURE_TOLERANCE
