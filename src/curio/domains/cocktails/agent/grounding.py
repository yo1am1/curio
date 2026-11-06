"""Cocktail grounding: drink IDs, catalog drink names and recipe amounts in an answer."""

import re
from collections.abc import Iterable, Sequence

from curio.domains.cocktails.models import Cocktail
from curio.engine.grounding import any_of, check_ids, grounding_report, name_allowed
from curio.engine.schemas import GroundingReport

# Cocktail answers cite drinks as "[id 12]" (the prompt's format) or "drink #12".
_DRINK_ID = re.compile(r"\[id (\d+)\]|\b(?:drink|recipe|cocktail) #(\d+)\b", re.IGNORECASE)
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


class CocktailGroundingCheck:
    """Checks that drinks, IDs and amounts in a cocktail answer come from tool evidence.

    Catalog drink names are matched case-sensitively in their catalog spelling, so ordinary
    words ("a perfect afternoon drink") do not count as the drink "Afternoon". Names that are
    also ingredient names ("Limeade") are never treated as drink mentions, and neither is a
    drink name inside a longer glass or ingredient name ("Martini" in "Martini Glass",
    "Daiquiri" in "Daiquiri mix").
    """

    def __init__(
        self,
        drink_names: Iterable[str],
        ingredient_names: Iterable[str],
        *,
        glass_names: Iterable[str] = (),
    ) -> None:
        ingredient_names = list(ingredient_names)
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
        """`typed` is what the shopper wrote: drinks and amounts they named may be repeated."""
        checked, issues = check_ids(text, _DRINK_ID, (c.drink_id for c in evidence), "drink")
        evidence_names = [c.name for c in evidence]
        for name in self._drink_mentions(text):
            checked += 1
            if not name_allowed(name, evidence_names, typed):
                issues.append(f"{name} was not returned by any tool")
        allowed = [
            m for c in evidence for i in c.ingredients for _, m in measures_in(i.measure or "")
        ]
        allowed += [m for _, m in measures_in(typed)]
        for raw, measure in measures_in(text):
            checked += 1
            if not any(same_measure(measure, other) for other in allowed):
                issues.append(f"{raw} does not match any amount in the evidence recipes")
        return grounding_report(checked, issues)


def same_measure(a: Measure, b: Measure) -> bool:
    return a[1] == b[1] and abs(a[0] - b[0]) <= _MEASURE_TOLERANCE
