"""Book agent: the toolkit the engine (`curio.engine`) runs for books.

1. Plan: no intent-extraction call; the plan step shows which remembered tastes apply.
2. Act: SearchBooks, InspectBooks, ReadersAlsoLiked and BooksByAuthor read the offline
   goodbooks-10k catalog through `BookRecommender`. Approved tastes apply to every call:
   avoided genres and authors are left out, liked genres rank higher. A genre or author the
   reader asks for explicitly wins over a remembered avoidance for that call. AskShopper,
   SaveBooks and RememberReadingTastes pause the turn like their fashion counterparts.
   PairCocktail pairs a book, or the reading list and remembered tastes, with drinks through
   a `DrinkPairing` the pairings domain plugs in when the server also serves cocktails; it
   honours the cocktail domain's remembered alcohol-free taste and avoided ingredients.
3. Verify: book IDs, catalog titles and Goodreads ratings in the answer must come from tool
   evidence (results, history cards or the reading list). Drinks the answer names (in bold or
   as "drink #N") and their amounts must come from this turn's PairCocktail result; earlier
   answers' drinks are context for the model, never evidence.
"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from curio.domains.books.agent.grounding import BookGroundingCheck
from curio.domains.books.agent.prompts import (
    BOOK_ANSWER_PROMPT,
    BOOK_REPAIR_INSTRUCTION,
    book_line,
    render_books,
)
from curio.domains.books.agent.schemas import BookChatTurn, BookContext, BookEffect
from curio.domains.books.agent.tools import (
    BOOK_POLICIES,
    BOOK_TOOLS,
    BooksByAuthor,
    InspectBooks,
    PairCocktail,
    ReadersAlsoLiked,
    RememberReadingTastes,
    SaveBooks,
    SearchBooks,
)
from curio.domains.books.catalog import BookCatalog, name_key
from curio.domains.books.models import Book, BookHit, BookPreferences, BookQuery
from curio.domains.books.recommend import BookRecommender
from curio.domains.books.taxonomy import MOODS
from curio.engine.grounding import merge_reports
from curio.engine.schemas import AgentStep, GroundingReport, TurnContext
from curio.engine.toolkit import ToolError, Turn

__all__ = ["BookToolkit", "DrinkPairing"]

INVALID_ACTION = (
    "Invalid action. Use SearchBooks with a query, genres or a mood; InspectBooks or SaveBooks "
    "with one to five positive book IDs; ReadersAlsoLiked with one book ID; BooksByAuthor with "
    "an author name; PairCocktail with exactly one of book_id, title or use_reading_list; "
    "RememberReadingTastes with at least one taste; or AskShopper with a question and two to "
    "four options."
)
PAIRING_UNAVAILABLE = (
    "Cocktail pairing is not available on this server: the cocktail recipes are not "
    "prepared. Say so and offer book recommendations instead."
)
STARTERS = (
    "Fantasy like The Name of the Wind",
    "Short classic novels",
    "A cosy mystery",
)


class DrinkPairing(Protocol):
    """Drinks for a book: what the pairings domain plugs into the book agent (PairCocktail).

    The book agent registers PairCocktail whatever the server serves; without a pairing the
    tool tells the model that pairing is unavailable. A pairing keeps its results in
    `Turn.extras`, and answers for them in the turn's grounding, fallback, reply and
    suggestions.
    """

    def shown_drinks(self, turn: BookChatTurn) -> str:
        """Context for the drinks an earlier answer showed ("" when it showed none)."""
        ...

    def alcohol_free(self, context: TurnContext) -> bool:
        """Whether remembered tastes keep every pairing alcohol-free."""
        ...

    def pair(
        self, turn: Turn[Book], action: PairCocktail, book: Book | None
    ) -> tuple[str, AgentStep]:
        """Pair `book`, or the reading list and remembered tastes when it is None."""
        ...

    def allowed_names(self, turn: Turn[Book]) -> list[str]:
        """Names this turn's pairings returned, which a book answer may also use."""
        ...

    def check(self, turn: Turn[Book], text: str, books: Sequence[Book]) -> GroundingReport:
        """Ground the drinks the answer names against this turn's pairings."""
        ...

    def fallback(self, extras: Mapping[str, Any]) -> list[str]:
        """Fallback paragraphs for this turn's paired drinks ([] when it paired none)."""
        ...

    def reply_fields(self, turn: Turn[Book]) -> dict[str, Any]: ...

    def suggestions(self, turn: Turn[Book]) -> list[str] | None:
        """Next steps after a pairing; None when the turn paired nothing."""
        ...


class BookToolkit:
    tools = BOOK_TOOLS
    policies = BOOK_POLICIES
    invalid_action = INVALID_ACTION
    repair_instruction = BOOK_REPAIR_INSTRUCTION
    grounding_subject = "titles, IDs and ratings"

    def __init__(
        self,
        catalog: BookCatalog,
        recommender: BookRecommender,
        top_k: int = 5,
        *,
        pairing: DrinkPairing | None = None,
    ) -> None:
        self._catalog = catalog
        self._recommender = recommender
        self._top_k = top_k
        self._moods = {name_key(mood): mood for mood in MOODS}
        self._grounding = BookGroundingCheck(
            catalog.titles(), other_names=[*catalog.authors(), *catalog.genres(), *MOODS]
        )
        self._pairing = pairing

    def attach_pairing(self, pairing: DrinkPairing) -> None:
        """Enable PairCocktail once the cocktail domain is loaded beside the books."""
        self._pairing = pairing

    def key(self, item: Book) -> int:
        return item.book_id

    # --- Turn phases ----------------------------------------------------------------------

    def clean_context(self, context: BookContext) -> BookContext:
        tastes = context.book_preferences
        cleaned = BookPreferences(
            liked_genres=self._genres(tastes.liked_genres),
            avoided_genres=self._genres(tastes.avoided_genres),
            avoided_authors=self._authors(tastes.avoided_authors),
        )
        return context.model_copy(update={"book_preferences": cleaned})

    def history(self, turns: Sequence[BookChatTurn]) -> tuple[list[BaseMessage], list[Book]]:
        messages: list[BaseMessage] = []
        referenced: list[Book] = []
        for turn in turns:
            if turn.role == "user":
                messages.append(HumanMessage(turn.content))
                continue
            books = self._catalog.get_many(turn.book_ids)
            referenced.extend(books)
            context = "\nReferenced books:\n" + render_books(books) if books else ""
            if self._pairing is not None:
                context += self._pairing.shown_drinks(turn)
            messages.append(AIMessage(turn.content + context))
        return messages, referenced

    def plan(self, turn: Turn[Book], history: Sequence[BaseMessage]) -> Iterator[AgentStep]:
        tastes = turn.context.book_preferences
        described = _describe_tastes(tastes)
        summary = (
            f"Applying remembered tastes to every search: {described}."
            if described
            else "No remembered tastes to apply."
        )
        detail = tastes.model_dump(exclude_defaults=True) | {"from_memory": _memory_filters(tastes)}
        if self._pairing is not None and self._pairing.alcohol_free(turn.context):
            summary += " Drink pairings stay alcohol-free (remembered cocktail taste)."
            detail["pairing_alcohol_free"] = True
        yield AgentStep(kind="plan", name="Understand request", summary=summary, detail=detail)

    def refresh(self, turn: Turn[Book], approved: BookEffect | None = None) -> None:
        """Nothing to do: every book tool reads remembered tastes from `turn.context`."""

    def prompt(
        self, turn: Turn[Book], message: str, history: Sequence[BaseMessage]
    ) -> list[BaseMessage]:
        return BOOK_ANSWER_PROMPT.invoke(
            {
                "message": message,
                "history": list(history),
                "books": render_books(turn.items),
                "session": self._session(turn),
            }
        ).to_messages()

    # --- Tools ----------------------------------------------------------------------------

    def read(self, turn: Turn[Book], action: BaseModel) -> tuple[str, AgentStep]:
        tastes = turn.context.book_preferences
        if isinstance(action, SearchBooks):
            step = self._search(turn, action, tastes)
        elif isinstance(action, InspectBooks):
            turn.show(self._catalog.get_many(dict.fromkeys(action.book_ids)))
            step = AgentStep(
                kind="tool",
                name="InspectBooks",
                summary=f"Read the details of {len(turn.items)} books.",
                detail={"book_ids": [b.book_id for b in turn.items]},
            )
            return render_books(turn.items, details=True), step
        elif isinstance(action, ReadersAlsoLiked):
            step = self._also_liked(turn, action, tastes)
        elif isinstance(action, BooksByAuthor):
            step = self._by_author(turn, action)
        elif isinstance(action, PairCocktail):
            return self._pair(turn, action)
        else:  # pragma: no cover - BOOK_POLICIES and BOOK_TOOLS are defined together
            raise ToolError(INVALID_ACTION)
        return render_books(turn.items, turn.reasons), step

    def _search(self, turn: Turn[Book], action: SearchBooks, tastes: BookPreferences) -> AgentStep:
        genres = []
        for genre in action.genres:
            canonical = self._catalog.canonical_genre(genre)
            if canonical is None:
                raise ToolError(
                    f"Unknown genre {genre!r}. Known genres: "
                    + ", ".join(self._catalog.genres())
                    + "."
                )
            genres.append(canonical)
        mood = None
        if action.mood:
            mood = self._moods.get(name_key(action.mood))
            if mood is None:
                raise ToolError(f"Unknown mood {action.mood!r}. Moods: {', '.join(MOODS)}.")
        asked = {name_key(g) for g in genres}
        avoided = [g for g in tastes.avoided_genres if name_key(g) not in asked]
        query = BookQuery(
            query=action.query.strip(),
            genres=genres,
            mood=mood,
            min_rating=action.min_rating,
            max_pages=action.max_pages,
            exclude_genres=avoided,
            exclude_authors=tastes.avoided_authors,
            boost_genres=tastes.liked_genres,
            top_k=self._top_k,
        )
        hits = self._recommender.search(query)
        if seed := self._recommender.seed_of(query.query):
            turn.remember([seed])  # the answer may name the book the search started from
        _show_hits(turn, hits)
        subject = f"for “{query.query}”" if query.query else "matching the filters"
        return AgentStep(
            kind="tool",
            name="SearchBooks",
            summary=f"Found {len(hits)} books {subject}.",
            status="ok" if hits else "skipped",
            detail=query.model_dump(exclude_defaults=True)
            | {"from_memory": _memory_filters(tastes)},
        )

    def _also_liked(
        self, turn: Turn[Book], action: ReadersAlsoLiked, tastes: BookPreferences
    ) -> AgentStep:
        book = self._catalog.get(action.book_id)
        if book is None:
            raise ToolError(f"Book {action.book_id} is not in the catalog.")
        hits = self._recommender.similar(
            action.book_id,
            self._top_k,
            other_authors_only=action.other_authors_only,
            exclude_genres=tastes.avoided_genres,
            exclude_authors=tastes.avoided_authors,
        )
        turn.remember([book])
        _show_hits(turn, hits)
        return AgentStep(
            kind="tool",
            name="ReadersAlsoLiked",
            summary=f"Found {len(hits)} books readers of {book.title} also liked.",
            status="ok" if hits else "skipped",
            detail={
                "book_id": action.book_id,
                "other_authors_only": action.other_authors_only,
                "from_memory": _memory_filters(tastes),
            },
        )

    def _by_author(self, turn: Turn[Book], action: BooksByAuthor) -> AgentStep:
        author = self._catalog.canonical_author(action.author)
        if author is None:
            raise ToolError(
                f"No author named {action.author!r} in the catalog. Try SearchBooks with the name."
            )
        hits = self._recommender.by_author(author, self._top_k)
        _show_hits(turn, hits)
        return AgentStep(
            kind="tool",
            name="BooksByAuthor",
            summary=f"Found {len(hits)} books by {author}.",
            detail={"author": author},
        )

    def _pair(self, turn: Turn[Book], action: PairCocktail) -> tuple[str, AgentStep]:
        if self._pairing is None:
            raise ToolError(PAIRING_UNAVAILABLE)
        if action.use_reading_list:
            tastes = turn.context.book_preferences
            if not turn.context.book_shortlist and not tastes.liked_genres:
                raise ToolError(
                    "The reading list is empty and no liked genres are remembered. Ask which "
                    "book to pair, or pair one book by book_id or title."
                )
            return self._pairing.pair(turn, action, None)
        book = (
            self._catalog.get(action.book_id)
            if action.book_id is not None
            else self._recommender.resolve_title(action.title or "")
        )
        if book is None:
            missing = action.book_id if action.book_id is not None else repr(action.title)
            raise ToolError(
                f"Book {missing} is not in the catalog. Find it with SearchBooks first."
            )
        return self._pairing.pair(turn, action, book)

    def apply(
        self, action: BaseModel, *, dry_run: bool = False
    ) -> tuple[BookEffect, list[Book], str]:
        """Validate a state change against the catalog; the client applies the effect."""
        if isinstance(action, SaveBooks):
            books = self._catalog.get_many(dict.fromkeys(action.book_ids))
            if not books:
                raise ToolError("None of those book IDs are in the catalog.")
            titles = ", ".join(b.title for b in books)
            verb = "Would save" if dry_run else "Saved"
            return (
                BookEffect(kind="book_shortlist", book_ids=[b.book_id for b in books]),
                books,
                f"{verb} {len(books)} to the reading list: {titles}.",
            )
        if isinstance(action, RememberReadingTastes):
            tastes = BookPreferences(
                liked_genres=self._genres(action.liked_genres),
                avoided_genres=self._genres(action.avoided_genres),
                avoided_authors=self._authors(action.avoided_authors),
            )
            if tastes.is_empty():
                raise ToolError(
                    "None of those genres or authors are in the catalog. Genres: "
                    + ", ".join(self._catalog.genres())
                    + "."
                )
            verb = "Would remember" if dry_run else "Remembered"
            return (
                BookEffect(kind="book_preferences", book_preferences=tastes),
                [],
                f"{verb} {_describe_tastes(tastes)}.",
            )
        raise ToolError(INVALID_ACTION)

    def proposed(self, args: Mapping[str, Any]) -> list[Book]:
        return self._catalog.get_many(args.get("book_ids", []))

    def approval_text(self, action: BaseModel) -> str:
        if isinstance(action, SaveBooks):
            return "I'd like to add these books to your reading list. Approve, edit or decline?"
        return (
            "Should I remember these reading tastes for your next searches? "
            "Approve, edit or decline."
        )

    def pending_display(self, items: list[Book]) -> dict[str, Any]:
        return {"books": items}

    # --- Verification and reply -----------------------------------------------------------

    def check(self, turn: Turn[Book], text: str) -> GroundingReport:
        books = list(turn.evidence.values())
        paired = self._pairing.allowed_names(turn) if self._pairing is not None else []
        report = self._grounding.check(text, books, typed=turn.message, also_allowed=paired)
        if self._pairing is None:
            return report
        return merge_reports(report, self._pairing.check(turn, text, books))

    def fallback(self, items: Sequence[Book], extras: Mapping[str, Any] | None = None) -> str:
        drinks = self._pairing.fallback(extras or {}) if self._pairing is not None else []
        if not items and not drinks:
            return (
                "I couldn't confirm matching books. Try another title, author, genre or mood, "
                "or fewer filters."
            )
        parts = []
        if items:
            lines = [f"- {book_line(b)}" for b in items]
            parts.append("Here are the books I could confirm:\n" + "\n".join(lines))
        return "\n\n".join(parts + drinks)

    def reply_fields(self, turn: Turn[Book]) -> dict[str, Any]:
        fields: dict[str, Any] = {"domain": "books", "books": turn.items}
        if self._pairing is not None:
            fields |= self._pairing.reply_fields(turn)
        return fields

    def suggestions(self, turn: Turn[Book]) -> list[str]:
        """Next-step chips; each one exercises a tool the agent can actually use."""
        if self._pairing is not None and (paired := self._pairing.suggestions(turn)) is not None:
            return paired
        books = turn.items
        if not books:
            return list(STARTERS)
        first = books[0]
        options = [f"Readers of {first.title} also liked…", f"More by {first.author}"]
        saved = set(turn.context.book_shortlist) | {
            book_id for effect in turn.applied for book_id in effect.book_ids
        }
        if first.book_id not in saved:
            options.append(f"Save {first.title}")
        options.append("Something shorter")
        return options[:4]

    # --- Helpers --------------------------------------------------------------------------

    def _session(self, turn: Turn[Book]) -> str:
        context = turn.context
        tastes = context.book_preferences
        parts = []
        if not tastes.is_empty():
            parts.append(
                "remembered tastes (already applied to every search): " + _describe_tastes(tastes)
            )
        reading_list = self._catalog.get_many(dict.fromkeys(context.book_shortlist))
        turn.remember(reading_list)
        if reading_list:
            parts.append(
                "reading list: "
                + "; ".join(f"[id {b.book_id}] {book_line(b)}" for b in reading_list)
            )
        else:
            parts.append("the reading list is empty")
        if self._pairing is None:
            parts.append("cocktail pairing is unavailable on this server")
        elif self._pairing.alcohol_free(context):
            parts.append("remembered cocktail taste: alcohol-free (PairCocktail applies it)")
        return ". ".join(parts) + "."

    def _genres(self, values: Iterable[str]) -> list[str]:
        """Catalog genre names; unknown values are dropped."""
        known = (self._catalog.canonical_genre(v) for v in values)
        return list(dict.fromkeys(g for g in known if g))

    def _authors(self, values: Iterable[str]) -> list[str]:
        """Catalog author spellings; unknown authors are dropped."""
        known = (self._catalog.canonical_author(v) for v in values)
        return list(dict.fromkeys(a for a in known if a))


def _memory_filters(tastes: BookPreferences) -> list[str]:
    filters = []
    if tastes.avoided_genres:
        filters.append("exclude_genres")
    if tastes.avoided_authors:
        filters.append("exclude_authors")
    if tastes.liked_genres:
        filters.append("boost_genres")
    return filters


def _describe_tastes(tastes: BookPreferences) -> str:
    parts = []
    if tastes.liked_genres:
        parts.append("likes " + ", ".join(tastes.liked_genres))
    if tastes.avoided_genres:
        parts.append("avoiding " + ", ".join(tastes.avoided_genres))
    if tastes.avoided_authors:
        parts.append("no books by " + ", ".join(tastes.avoided_authors))
    return "; ".join(parts)


def _show_hits(turn: Turn[Book], hits: Sequence[BookHit]) -> None:
    turn.show((h.book for h in hits), {h.book.book_id: list(h.reasons) for h in hits})
