"""PairCocktail for the book agent: drinks for a book, a reading list or reading tastes.

`CocktailPairing` is the `DrinkPairing` the book toolkit takes once the server serves both
books and cocktails. It runs the deterministic `BookCocktailPairer` (the same one as
`POST /pairings/book-cocktail`), so the agent cites the pairer's reasons, and grounds the
drinks a book answer names against this turn's pairings only: earlier answers' drinks are
context for the model, never evidence.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from curio.domains.books.agent.schemas import BookChatTurn, BookContext
from curio.domains.books.agent.tools import PairCocktail
from curio.domains.books.catalog import BookCatalog
from curio.domains.books.models import Book
from curio.domains.cocktails.agent.facts import drink_facts
from curio.domains.cocktails.agent.schemas import CocktailChatTurn, CocktailContext
from curio.domains.cocktails.catalog import CocktailCatalog
from curio.domains.cocktails.models import Cocktail
from curio.domains.pairings.agent.grounding import PairedDrinkCheck
from curio.domains.pairings.agent.prompts import render_pairing
from curio.domains.pairings.models import BookPairing, PairingRequest
from curio.domains.pairings.pairer import BookCocktailPairer, PairingError
from curio.engine.facts import FactSheet
from curio.engine.schemas import AgentStep, GroundingReport
from curio.engine.toolkit import ToolError, Turn

__all__ = ["PAIR_READING_LIST", "CocktailPairing"]

PAIR_READING_LIST = "Pair a cocktail with my reading list"
# Turn.extras keys: this turn's latest pairing, and every drink a pairing returned this turn
# (the only drinks an answer may name).
PAIRING = "pairing"
PAIRED_DRINKS = "paired_drinks"
# The pairer's profile and reasons this turn: titles they cite (a lore line names The Long
# Goodbye) are tool output, so the answer may repeat them.
PAIRED_TEXT = "paired_text"


class PairingContext(BookContext, CocktailContext):
    """A book turn's context: the cocktail domain's remembered tastes apply to pairings."""


class PairingChatTurn(BookChatTurn, CocktailChatTurn):
    """A book answer that paired drinks shows them as cocktail cards."""


class CocktailPairing:
    def __init__(
        self, pairer: BookCocktailPairer, cocktails: CocktailCatalog, books: BookCatalog
    ) -> None:
        self._pairer = pairer
        self._cocktails = cocktails
        self._books = books
        self._drink_names = {c.name.casefold() for c in cocktails}
        self._drinks = PairedDrinkCheck(
            (c.name for c in cocktails),
            cocktails.ingredients(),
            glass_names={c.glass for c in cocktails},
        )

    def shown_drinks(self, turn: PairingChatTurn) -> str:
        drinks = self._cocktails.get_many(turn.drink_ids)
        if not drinks:
            return ""
        # Context for "another pairing", not evidence: PairCocktail must run again.
        return "\nDrinks shown (an earlier pairing): " + "; ".join(
            f"drink #{c.drink_id} {c.name}" for c in drinks
        )

    def alcohol_free(self, context: PairingContext) -> bool:
        return context.cocktail_preferences.alcohol_free

    def pair(
        self, turn: Turn[Book], action: PairCocktail, book: Book | None
    ) -> tuple[str, AgentStep]:
        context: PairingContext = turn.context
        tastes = context.book_preferences
        common = {
            "avoided_genres": tastes.avoided_genres,
            "alcohol_free": action.alcohol_free,
            "cocktail_preferences": context.cocktail_preferences,
            "exclude_drink_ids": action.exclude_drink_ids,
        }
        if book is None:
            request = PairingRequest(
                book_ids=context.book_shortlist, liked_genres=tastes.liked_genres, **common
            )
            subject = "your reading list"
        else:
            request = PairingRequest(book_id=book.book_id, **common)
            subject = book.title
        try:
            result = self._pairer.pair(request)
        except PairingError as error:
            raise ToolError(str(error)) from error
        turn.remember(self._books.get_many(result.profile.book_ids))
        turn.extras[PAIRING] = result
        paired: dict[int, Cocktail] = turn.extras.setdefault(PAIRED_DRINKS, {})
        paired.update({p.cocktail.drink_id: p.cocktail for p in result.pairings})
        turn.extras.setdefault(PAIRED_TEXT, []).extend(
            [result.profile.summary, *(r for p in result.pairings for r in p.reasons)]
        )
        names = ", ".join(p.cocktail.name for p in result.pairings)
        free = " alcohol-free" if result.profile.alcohol_free else ""
        step = AgentStep(
            kind="tool",
            name="PairCocktail",
            summary=(
                f"Paired {subject} with {len(result.pairings)}{free} drinks: {names}."
                if result.pairings
                else f"Found no{free} drink to pair with {subject}."
            ),
            status="ok" if result.pairings else "skipped",
            detail={
                "subject": subject,
                "themes": [t.label for t in result.profile.themes],
                "alcohol_free": result.profile.alcohol_free,
                "drink_ids": [p.cocktail.drink_id for p in result.pairings],
            },
        )
        return render_pairing(result), step

    def allowed_names(self, turn: Turn[Book]) -> list[str]:
        drinks = turn.extras.get(PAIRED_DRINKS, {}).values()
        return [*(c.name for c in drinks), *turn.extras.get(PAIRED_TEXT, [])]

    def check(self, turn: Turn[Book], text: str, books: Sequence[Book]) -> GroundingReport:
        drinks = list(turn.extras.get(PAIRED_DRINKS, {}).values())
        book_names = [b.title for b in books] + [b.series for b in books if b.series]
        book_names += [author for b in books for author in b.authors]
        return self._drinks.check(
            text,
            drinks,
            typed=turn.message,
            also_allowed=book_names,
            quoted=turn.extras.get(PAIRED_TEXT, []),
        )

    def facts(self, turn: Turn[Book]) -> FactSheet:
        return drink_facts(turn.extras.get(PAIRED_DRINKS, {}).values())

    def is_drink_name(self, name: str) -> bool:
        return name.casefold() in self._drink_names

    def fallback(self, extras: Mapping[str, Any]) -> list[str]:
        pairing: BookPairing | None = extras.get(PAIRING)
        drinks = [p.cocktail for p in pairing.pairings] if pairing else []
        if not pairing or not drinks:
            return []
        lines = [f"- {{drink:{c.drink_id}}} · {{alcohol:drink:{c.drink_id}}}" for c in drinks]
        return [f"{pairing.profile.summary}. To pair:\n" + "\n".join(lines), pairing.note]

    def reply_fields(self, turn: Turn[Book]) -> dict[str, Any]:
        pairing: BookPairing | None = turn.extras.get(PAIRING)
        return {
            "cocktails": [p.cocktail for p in pairing.pairings] if pairing else [],
            "pairing": pairing,
        }

    def suggestions(self, turn: Turn[Book]) -> list[str] | None:
        pairing: BookPairing | None = turn.extras.get(PAIRING)
        if pairing is None:
            return None
        options = ["Another pairing"]
        if not pairing.profile.alcohol_free:
            options.append("Make it alcohol-free")
        if len(pairing.profile.book_ids) == 1 and (
            book := self._books.get(pairing.profile.book_ids[0])
        ):
            options.append(f"Readers of {book.title} also liked…")
        if turn.context.book_shortlist:
            options.append(PAIR_READING_LIST)
        return list(dict.fromkeys(options))[:4]
