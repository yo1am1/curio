"""Grounding for the drinks a book answer pairs with a book (PairCocktail)."""

import re
from collections.abc import Sequence

from curio.domains.cocktails.agent.grounding import (
    CocktailGroundingCheck,
    measures_in,
    same_measure,
)
from curio.domains.cocktails.models import Cocktail
from curio.engine.grounding import DASHES, grounding_report, marked_name, name_allowed
from curio.engine.schemas import GroundingReport

# A book answer cites paired drinks as "drink #12": "[id 12]" there means a book.
_PAIRED_DRINK_ID = re.compile(r"\b(?:drink|recipe|cocktail) #(\d+)\b", re.IGNORECASE)
_DRINK_ID_AFTER = re.compile(rf"\s*[(,:{DASHES}]?\s*(?:drink|recipe|cocktail) #\d+", re.I)
_DRINK_ID_BEFORE = re.compile(rf"(?:drink|recipe|cocktail) #\d+\s*[):,{DASHES}]?\s*$", re.I)


class PairedDrinkCheck(CocktailGroundingCheck):
    """Checks the drinks a book answer pairs with a book against this turn's pairing.

    In a book answer most capitalised words are about books: "Manhattan", "San Francisco"
    and "Brooklyn" are places, "Rose" a character. So a drink name counts only where the
    answer marks it as a name (bold, italic or quoted, or next to its "drink #N"), as the
    prompt asks, and a name inside a longer evidence book title or author ("Casino" in
    "Casino Royale") is not a drink. Drink IDs ("drink #12") must have been returned, and
    ingredient amounts are checked like cocktail answers when the turn paired drinks.
    """

    def check(
        self,
        text: str,
        evidence: Sequence[Cocktail],
        typed: str = "",
        *,
        also_allowed: Sequence[str] = (),
    ) -> GroundingReport:
        issues: list[str] = []
        checked = 0
        known_ids = {c.drink_id for c in evidence}
        for match in _PAIRED_DRINK_ID.finditer(text):
            checked += 1
            if int(match.group(1)) not in known_ids:
                issues.append(f"drink {match.group(1)} was not returned by PairCocktail")
        evidence_names = [c.name for c in evidence] + list(also_allowed)
        for name in self._marked_mentions(text):
            checked += 1
            if not name_allowed(name, evidence_names, typed):
                issues.append(f"{name} was not returned by PairCocktail")
        if evidence:
            allowed = [
                m for c in evidence for i in c.ingredients for _, m in measures_in(i.measure or "")
            ]
            allowed += [m for _, m in measures_in(typed)]
            for raw, measure in measures_in(text):
                checked += 1
                if not any(same_measure(measure, other) for other in allowed):
                    issues.append(f"{raw} does not match any amount in the paired recipes")
        return grounding_report(checked, issues)

    def _marked_mentions(self, text: str) -> list[str]:
        """Drink names the answer marks as names, outside longer ingredient or glass names."""
        if self._names is None:
            return []
        phrases = [m.span() for m in self._phrases.finditer(text)] if self._phrases else []
        found = []
        for match in self._names.finditer(text):
            start, end = match.span()
            if any(s <= start and end <= e and e - s > end - start for s, e in phrases):
                continue
            if marked_name(text, start, end, _DRINK_ID_AFTER, _DRINK_ID_BEFORE):
                found.append(match.group(0))
        return found
