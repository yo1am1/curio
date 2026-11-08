"""Grounding for the drinks a book answer pairs with a book (PairCocktail)."""

import re
from collections.abc import Sequence

from curio.domains.cocktails.agent.grounding import CocktailGroundingCheck
from curio.domains.cocktails.models import Cocktail
from curio.engine.grounding import DASHES, Citation, Tally, marked_name, name_allowed
from curio.engine.schemas import GroundingReport

# A book answer cites paired drinks as "drink #12": "[id 12]" there means a book.
_PAIRED_DRINK_ID = re.compile(r"\b(?:drink|recipe|cocktail) #(\d+)\b", re.IGNORECASE)
PAIRED_CITATIONS = (Citation(_PAIRED_DRINK_ID, "drink"),)
_DRINK_ID_AFTER = re.compile(rf"\s*[(,:{DASHES}]?\s*(?:drink|recipe|cocktail) #\d+", re.I)
_DRINK_ID_BEFORE = re.compile(rf"(?:drink|recipe|cocktail) #\d+\s*[):,{DASHES}]?\s*$", re.I)


class PairedDrinkCheck(CocktailGroundingCheck):
    """Checks the drinks a book answer pairs with a book against this turn's pairing.

    In a book answer most capitalised words are about books: "Manhattan", "San Francisco"
    and "Brooklyn" are places, "Rose" a character. So a drink name counts only where the
    answer marks it as a name (bold, italic or quoted, or next to its "drink #N"), as the
    prompt asks, and a name inside a longer evidence book title or author ("Casino" in
    "Casino Royale") is not a drink. Drink IDs ("drink #12") must have been returned.
    Ingredient amounts bind to the paired drink they are about and are checked in turns
    that paired drinks; alcohol labels must match the drink's, and allergen, diet, health
    and age-safety promises are reported.
    """

    def check(
        self,
        text: str,
        evidence: Sequence[Cocktail],
        typed: str = "",
        *,
        also_allowed: Sequence[str] = (),
        quoted: Sequence[str] = (),
    ) -> GroundingReport:
        """`quoted` is the pairer's text, which the answer may repeat word for word."""
        tally = Tally()
        self.check_into(text, evidence, typed, tally, also_allowed=also_allowed, quoted=quoted)
        return tally.report()

    def check_into(
        self,
        text: str,
        evidence: Sequence[Cocktail],
        typed: str,
        tally: Tally,
        *,
        also_allowed: Sequence[str] = (),
        citations: Sequence[Citation] = PAIRED_CITATIONS,
        quoted: Sequence[str] = (),
    ) -> None:
        known_ids = {c.drink_id for c in evidence}
        for match in _PAIRED_DRINK_ID.finditer(text):
            ok = int(match.group(1)) in known_ids
            tally.add("id", ok, f"drink {match.group(1)} was not returned by PairCocktail")
        evidence_names = [c.name for c in evidence] + list(also_allowed)
        for name in self._marked_mentions(text):
            ok = name_allowed(name, evidence_names, typed)
            tally.add("name", ok, f"{name} was not returned by PairCocktail", key=name.casefold())
        self.check_facts(
            text, evidence, typed, tally, citations=citations, amounts=bool(evidence), quoted=quoted
        )

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
