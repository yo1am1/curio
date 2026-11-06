"""Plan my evening: one outfit, one book and a drink paired with it, under one theme.

1. Theme. The occasion (or a mood that names one) maps to an evening theme (`themes.py`):
   "rainy Sunday in" is cosy, "summer garden party" summery, "book club" thoughtful.
2. Outfit. The theme's outfit occasion, slots and per-slot queries go to fashion's
   `OutfitPlanner`, under the budget and the audience; a remembered per-piece limit applies.
3. Book. The theme's book searches run through the books recommender in order (children's
   books are left out; remembered avoided genres and authors too; liked genres rank higher).
4. Drink. The pairer pairs the chosen book (alcohol-free when asked, remembered or for a
   children's or young-adult book); among its drinks the first whose recipe uses one of the
   theme's drink words suits the evening best. The drink always comes from the pairer: when
   it finds none, the next book is tried, and if none pairs the plan says so.
5. Explain. "Rainy Sunday in → cosy: Paloma sweater (€24.99) · The Name of the Wind
   (fantasy, ★4.55) · Hot Toddy (Whiskey, Honey; pairs with fantasy and magic)": every part
   cites its own evidence.

Every part is optional: without fashion data the plan has a book and a drink and says the
outfit needs the fashion catalogue; without books (or cocktails) it says what is missing.
The planner is deterministic and offline: equal inputs give the same evening.
"""

import logging
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from curio.domains.books.catalog import BookCatalog
from curio.domains.books.models import Book, BookQuery
from curio.domains.books.recommend import BookRecommender
from curio.domains.books.taxonomy import MOODS, shelf_label
from curio.domains.cocktails.catalog import CocktailCatalog
from curio.domains.cocktails.models import Cocktail
from curio.domains.evening.models import (
    MAX_SKIPPED_BOOKS,
    MAX_SKIPPED_DRINKS,
    BookPart,
    DrinkPart,
    EveningAudience,
    EveningPart,
    EveningPlan,
    EveningRequest,
    EveningState,
    EveningSwapMode,
    EveningTastes,
    MissingPart,
    OutfitPart,
    ThemeInfo,
)
from curio.domains.evening.themes import (
    FALLBACK_BOOKS,
    KIDS_BOOKS,
    THEMES_BY_ID,
    BookSearch,
    Theme,
    ThemeMatch,
    detect_theme,
    guard_in,
    mentions,
)
from curio.domains.fashion.models import Product
from curio.domains.fashion.outfits import (
    SLOT_LABELS,
    Outfit,
    OutfitError,
    OutfitPlanner,
    OutfitRequest,
    SlotName,
    audience_in,
    occasion_season,
)
from curio.domains.pairings.models import MAX_PAIRINGS, BookPairing, Pairing, PairingRequest
from curio.domains.pairings.pairer import BookCocktailPairer, PairingError

__all__ = [
    "EveningError",
    "EveningPlanner",
    "alcohol_free_in",
    "budget_in",
    "resolve_alcohol_free",
    "wants_alcohol_free",
]

logger = logging.getLogger(__name__)

OUTFIT_NEEDS_FASHION = (
    "The outfit needs the fashion catalogue (the H&M data and photo index), which this "
    "server does not serve."
)
BOOK_NEEDS_BOOKS = "The book needs the book catalogue (run scripts.prepare_books)."
DRINK_NEEDS_COCKTAILS = "The drink needs the cocktail recipes (run scripts.prepare_cocktails)."
DRINK_NEEDS_BOOK = "The drink is paired with the book, so it needs a book first."
DEFAULT_AUDIENCE: EveningAudience = "women"
AUDIENCE_NOTE = "Planned in womenswear; choose men for a menswear look."
# Books tried for a drink before the plan says none pairs.
MAX_BOOKS_TO_PAIR = 3
# Hits read from each book search before the evening's own ranking: a search with words
# keeps its most relevant few, a genre or mood browse reads widely so the best-known books
# of the genre are among them.
BOOK_CANDIDATES = 12
BROWSE_CANDIDATES = 40
# An evening reads a book many people know: the first of these floors (Goodreads ratings)
# that any candidate reaches applies, so a theme with only niche books still gets one.
POPULARITY_FLOORS = (100_000, 25_000, 0)
SEASONS = ("summer", "winter", "autumn", "spring")
_BUDGET = re.compile(
    r"(?:under|below|less than|up to|within|max(?:imum)?|budget(?: of| is)?)\s*€\s?(\d{1,4})"
    r"|(?:under|below|less than|up to|within|budget(?: of| is)?)\s*(\d{1,4})"
    r"\s?(?:€|eur\b|euros?\b)",
    re.IGNORECASE,
)
# Words that ask for an alcohol-free evening, and those that make one alcohol-free for
# safety (a pregnancy, a driver): explicit `alcohol_free=false` overrides only the first.
# Kids make an evening alcohol-free for safety too, family (`themes.GUARDS`) as asked.
_ALCOHOL_FREE = re.compile(
    r"(?<![\w'])(alcohol[- ]free|no alcohol|non[- ]alcoholic|sober|mocktails?|dry january|"
    r"without alcohol|teetotal|teetotaller|booze[- ]free|zero[- ]proof)(?![\w'])",
    re.IGNORECASE,
)
_SAFETY = re.compile(
    r"(?<![\w'])(pregnant|pregnancy|expecting(?!\s+(?:guests|friends|company|people|"
    r"visitors|rain|snow|a crowd))|baby shower|designated driver|driving|(?:i'm|i am) the "
    r"driver)(?![\w'])",
    re.IGNORECASE,
)
SAFETY_NOTE = "The drink stays alcohol-free: {reason}."
_WORD = re.compile(r"[a-z0-9]+")


class EveningError(ValueError):
    """A swap the planner cannot serve; the message says what to change."""


def budget_in(text: str) -> float | None:
    """An outfit budget written in the occasion ("date night under €100")."""
    match = _BUDGET.search(text)
    return float(match.group(1) or match.group(2)) if match else None


def alcohol_free_in(text: str) -> tuple[str, str] | None:
    """Why `text` makes an evening alcohol-free: ("safety", "you mentioned pregnant") for a
    pregnancy, a driver or kids, ("asked", ...) for alcohol-free words or a family occasion,
    else None. Negated mentions ("alcohol-free is boring", "not sober-curious") do not count.
    """
    if said := mentions(_SAFETY, text):
        return "safety", f"you mentioned {said[0].lower()}"
    found = guard_in(text)
    if found is not None and found[0] == "kids":
        return "safety", f"you mentioned {found[1][0].lower()}"
    if said := mentions(_ALCOHOL_FREE, text):
        return "asked", f"you asked for {said[0].lower()}"
    if found is not None and found[0] == "family":
        return "asked", f"you mentioned {found[1][0].lower()}"
    return None


def wants_alcohol_free(text: str) -> bool:
    return alcohol_free_in(text) is not None


def resolve_alcohol_free(text: str, explicit: bool | None) -> tuple[bool, str | None]:
    """Whether the evening is alcohol-free, and a note when the text overrode an explicit
    false. The text's words decide unless `explicit` is given; an explicit false never
    overrides a safety reason (pregnancy, driving, kids)."""
    found = alcohol_free_in(text)
    if found is not None and found[0] == "safety":
        note = SAFETY_NOTE.format(reason=found[1]) if explicit is False else None
        return True, note
    if explicit is not None:
        return explicit, None
    return found is not None, None


@dataclass(frozen=True)
class _Drink:
    pairing: Pairing
    result: BookPairing
    fit: list[str]


class EveningPlanner:
    def __init__(
        self,
        *,
        outfits: OutfitPlanner | None = None,
        books: BookRecommender | None = None,
        book_catalog: BookCatalog | None = None,
        pairer: BookCocktailPairer | None = None,
        cocktails: CocktailCatalog | None = None,
    ) -> None:
        self._outfits = outfits
        self._books = books if book_catalog is not None else None
        self._catalog = book_catalog
        self._pairer = pairer if books is not None and cocktails is not None else None
        self._cocktails = cocktails

    @property
    def parts(self) -> list[EveningPart]:
        """The parts this server can plan."""
        served: list[EveningPart] = []
        if self._outfits is not None:
            served.append("outfit")
        if self._books is not None:
            served.append("book")
        if self._pairer is not None:
            served.append("drink")
        return served

    # --- Plan ------------------------------------------------------------------------------

    def plan(self, request: EveningRequest) -> EveningPlan:
        match = detect_theme(request.occasion, request.mood)
        budget = request.budget if request.budget is not None else budget_in(request.occasion)
        audience = request.audience or audience_of(request.occasion)
        alcohol_free, alcohol_note = resolve_alcohol_free(
            f"{request.occasion} {request.mood or ''}", request.alcohol_free
        )
        state = EveningState(
            occasion=request.occasion,
            mood=request.mood,
            theme=match.theme.id,
            budget=budget,
            audience=audience,
            alcohol_free=alcohol_free,
        )
        tastes = EveningTastes.model_validate(request.model_dump(include=_TASTE_FIELDS))
        outfit, outfit_issue = self._build_outfit(state, match.theme, tastes)
        state = state.model_copy(update={"outfit": outfit.state if outfit else None})
        plan = self._compose(state, match, tastes, outfit, outfit_issue)
        notes = [note for note in (alcohol_note,) if note]
        return plan.model_copy(update={"notes": [*notes, *plan.notes]}) if notes else plan

    def restore(self, state: EveningState, tastes: EveningTastes | None = None) -> EveningPlan:
        """The plan the client holds, re-read from the catalogs; the drink is re-paired (the
        client's drink stays only when the pairer still pairs it with the book)."""
        tastes = tastes or EveningTastes()
        match = self._match(state)
        outfit = None
        issue = None if self._outfits is not None else OUTFIT_NEEDS_FASHION
        if self._outfits is not None and state.outfit is not None:
            outfit = self._outfits.restore(state.outfit)
        return self._compose(state, match, tastes, outfit, issue, keep_book=True)

    def swap(
        self,
        state: EveningState,
        part: EveningPart,
        *,
        slot: SlotName | None = None,
        mode: EveningSwapMode = "other",
        refine: str = "",
        tastes: EveningTastes | None = None,
    ) -> EveningPlan:
        """Change one part: another (or a cheaper) outfit piece, another book (re-paired with a
        drink), another drink, or an alcohol-free drink."""
        tastes = tastes or EveningTastes()
        if mode == "cheaper" and part != "outfit":
            raise EveningError("Only an outfit piece can be made cheaper.")
        if mode == "alcohol_free" and part != "drink":
            raise EveningError("Only the drink can be made alcohol-free.")
        if part == "outfit":
            return self._swap_piece(state, slot, mode, refine, tastes)
        if slot is not None:
            raise EveningError("A slot only applies to the outfit.")
        before = self.restore(state, tastes)
        if part == "book":
            if self._books is None:
                raise EveningError(BOOK_NEEDS_BOOKS)
            if before.book is None:
                raise EveningError("There is no book to change yet. Plan the evening first.")
            old = before.book.book
            skipped = [*state.skipped_books, old.book_id][-MAX_SKIPPED_BOOKS:]
            changed = state.model_copy(
                update={"book_id": None, "drink_id": None, "skipped_books": skipped,
                        "skipped_drinks": []}
            )  # fmt: skip
            plan = self._compose(
                changed, self._match(state), tastes, _outfit_of(before), _issue_of(before)
            )
            if plan.book is None:
                return before.model_copy(
                    update={"change": f"No other book suits this evening; kept {old.title}."}
                )
            change = f"Another book: {old.title} → {plan.book.book.title}"
            if plan.drink is not None:
                change += f"; the drink is re-paired with it: {plan.drink.cocktail.name}"
            return plan.model_copy(update={"change": change + "."})
        # The drink.
        if self._pairer is None:
            raise EveningError(DRINK_NEEDS_COCKTAILS)
        if before.drink is None or before.book is None:
            raise EveningError("There is no drink to change yet. Plan the evening first.")
        old_drink = before.drink.cocktail
        if mode == "alcohol_free":
            if before.drink.alcohol_free_reason is not None:
                return before.model_copy(
                    update={"change": f"{old_drink.name} is already alcohol-free."}
                )
            changed = state.model_copy(update={"alcohol_free": True, "drink_id": None})
        else:
            skipped = [*state.skipped_drinks, old_drink.drink_id][-MAX_SKIPPED_DRINKS:]
            changed = state.model_copy(update={"drink_id": None, "skipped_drinks": skipped})
        plan = self._compose(
            changed, self._match(state), tastes, _outfit_of(before), _issue_of(before),
            keep_book=True,
        )  # fmt: skip
        if plan.drink is None:
            kind = "alcohol-free drink" if mode == "alcohol_free" else "other drink"
            return before.model_copy(
                update={
                    "change": f"No {kind} pairs with {before.book.book.title}; kept "
                    f"{old_drink.name}."
                }
            )
        label = "An alcohol-free drink" if mode == "alcohol_free" else "Another drink"
        return plan.model_copy(
            update={"change": f"{label}: {old_drink.name} → {plan.drink.cocktail.name}."}
        )

    def _swap_piece(
        self,
        state: EveningState,
        slot: SlotName | None,
        mode: EveningSwapMode,
        refine: str,
        tastes: EveningTastes,
    ) -> EveningPlan:
        if self._outfits is None:
            raise EveningError(OUTFIT_NEEDS_FASHION)
        if state.outfit is None:
            raise EveningError("There is no outfit to change yet. Plan the evening first.")
        if slot is None:
            raise EveningError("Name the outfit piece to change (its slot).")
        theme = self._match(state).theme
        # The theme's look for the slot keeps a swapped piece in the evening's style.
        look = dict(theme.queries_for(state.outfit.audience)).get(slot, "")
        words = " ".join(part for part in (look, refine.strip()) if part)
        try:
            swap = self._outfits.swap(
                state.outfit, slot, mode, words, max_item_price=tastes.max_item_price
            )
        except OutfitError as error:
            raise EveningError(str(error)) from error
        changed = state.model_copy(update={"outfit": swap.outfit.state})
        plan = self._compose(changed, self._match(state), tastes, swap.outfit, None, keep_book=True)
        label = SLOT_LABELS[slot].lower()
        if swap.new is None:
            kind = "cheaper" if mode == "cheaper" else "other"
            change = f"No {kind} {label} fits the budget; kept {swap.old.name}."
        else:
            change = (
                f"{'A cheaper' if mode == 'cheaper' else 'Another'} {label}: {swap.old.name} → "
                f"{swap.new.name} (€{swap.new.price:.2f}); outfit total "
                f"€{swap.outfit.total:.2f}."
            )
        return plan.model_copy(update={"change": change})

    # --- Parts -----------------------------------------------------------------------------

    def _match(self, state: EveningState) -> ThemeMatch:
        """The state's theme, with the words that chose it for the explanation."""
        found = detect_theme(state.occasion, state.mood)
        if found.theme.id == state.theme:
            return found
        return ThemeMatch(THEMES_BY_ID[state.theme], (), "default")

    def _build_outfit(
        self, state: EveningState, theme: Theme, tastes: EveningTastes
    ) -> tuple[Outfit | None, str | None]:
        if self._outfits is None:
            return None, OUTFIT_NEEDS_FASHION
        # An evening's look is for a grown-up: without an audience it is womenswear (the
        # fashion planner's best-match probe would often pick children's pieces here).
        audience = state.audience or DEFAULT_AUDIENCE
        request = OutfitRequest(
            occasion=_outfit_occasion(theme, state.occasion),
            budget=state.budget,
            audience=audience,
            slots=theme.slots,
            max_item_price=tastes.max_item_price,
            queries=theme.queries_for(audience),
        )
        try:
            outfit = self._outfits.outfit(request)
        except OutfitError as error:
            return None, str(error)
        except Exception:  # the photo index or the encoder failed: plan the rest
            logger.exception("Could not build the evening's outfit")
            return None, "The outfit could not be planned right now (the photo search failed)."
        if state.audience is None:
            notes = [AUDIENCE_NOTE, *outfit.notes]
            outfit = outfit.model_copy(
                update={"notes": notes, "state": outfit.state.model_copy(update={"notes": notes})}
            )
        if outfit.status == "empty":
            notes = " ".join(outfit.notes)
            return None, f"No outfit fits this evening's budget. {notes}".strip()
        return outfit, None

    def _book_candidates(
        self, match: ThemeMatch, tastes: EveningTastes, skipped: Iterable[int]
    ) -> list[tuple[Book, BookSearch]]:
        """The theme's books, best first: well-known ones (`POPULARITY_FLOORS`), then the
        theme's searches in order (mainstream first), books that open their series (or
        stand alone), and the quality prior (rating times log popularity)."""
        assert self._books is not None and self._catalog is not None
        prefs = tastes.book_preferences
        avoided = [g for g in map(self._catalog.canonical_genre, prefs.avoided_genres) if g]
        liked = [g for g in map(self._catalog.canonical_genre, prefs.liked_genres) if g]
        authors = [a for a in map(self._catalog.canonical_author, prefs.avoided_authors) if a]
        skip = set(skipped)
        searches = list(match.theme.books)
        if match.guard == "kids":
            searches.insert(0, KIDS_BOOKS)
        found: dict[int, tuple[Book, BookSearch, int]] = {}
        for index, search in enumerate((*searches, FALLBACK_BOOKS)):
            if len(found) >= MAX_BOOKS_TO_PAIR and search is FALLBACK_BOOKS:
                break
            if set(search.genres) & set(avoided):
                continue  # a remembered avoidance wins over the theme's genre
            wanted = BOOK_CANDIDATES if search.query else BROWSE_CANDIDATES
            query = search.to_query(min(wanted + len(skip), 50))
            query = BookQuery.model_validate(
                query.model_dump()
                | {
                    "exclude_genres": list(dict.fromkeys([*query.exclude_genres, *avoided]))[:10],
                    "exclude_authors": authors[:10],
                    "boost_genres": liked[:10],
                }
            )
            for hit in self._books.search(query):
                if hit.book.book_id not in skip:
                    found.setdefault(hit.book.book_id, (hit.book, search, index))
        if not found:
            return []
        floor = next(
            f for f in POPULARITY_FLOORS if any(b.ratings_count >= f for b, _, _ in found.values())
        )
        liked_keys = {g.casefold() for g in liked}

        def rank(item: tuple[Book, BookSearch, int]) -> tuple:
            book, _, index = item
            loved = any(g.casefold() in liked_keys for g in book.genres)
            return (
                book.ratings_count < floor,
                index,
                not _opens_series(book),
                not loved,
                -_prior(book),
                book.book_id,
            )

        return [(book, search) for book, search, _ in sorted(found.values(), key=rank)]

    def _pair(
        self,
        book: Book,
        state: EveningState,
        tastes: EveningTastes,
        theme: Theme,
        keep: int | None = None,
    ) -> _Drink | None:
        """The drink for `book`: the client's `keep` when the pairer still pairs it, else the
        pairer's drink that suits the theme best."""
        assert self._pairer is not None
        request = PairingRequest(
            book_id=book.book_id,
            avoided_genres=tastes.book_preferences.avoided_genres,
            alcohol_free=state.alcohol_free,
            cocktail_preferences=tastes.cocktail_preferences,
            exclude_drink_ids=state.skipped_drinks,
            top_k=MAX_PAIRINGS,
        )
        try:
            result = self._pairer.pair(request)
        except PairingError:
            return None
        if not result.pairings:
            return None
        if keep is not None:
            for seed in range(result.sets):
                found = (
                    result
                    if seed == 0
                    else self._pairer.pair(request.model_copy(update={"seed": seed}))
                )
                for pairing in found.pairings:
                    if pairing.cocktail.drink_id == keep:
                        return _Drink(pairing, found, _fit(pairing.cocktail, theme))
        fits = [(p, _fit(p.cocktail, theme)) for p in result.pairings]
        pairing, fit = next(((p, f) for p, f in fits if f), fits[0])
        return _Drink(pairing, result, fit)

    # --- The plan --------------------------------------------------------------------------

    def _compose(
        self,
        state: EveningState,
        match: ThemeMatch,
        tastes: EveningTastes,
        outfit: Outfit | None,
        outfit_issue: str | None,
        *,
        keep_book: bool = False,
    ) -> EveningPlan:
        theme = match.theme
        missing: list[MissingPart] = []
        notes: list[str] = []
        if outfit is None:
            missing.append(
                MissingPart(part="outfit", reason=outfit_issue or "No outfit is planned yet.")
            )
        book: Book | None = None
        drink: _Drink | None = None
        if self._books is None or self._catalog is None:
            missing.append(MissingPart(part="book", reason=BOOK_NEEDS_BOOKS))
        else:
            held = self._catalog.get(state.book_id) if keep_book and state.book_id else None
            candidates = (
                [held]
                if held is not None
                else [b for b, _ in self._book_candidates(match, tastes, state.skipped_books)]
            )
            if not candidates:
                missing.append(
                    MissingPart(part="book", reason="No book in the catalogue suits this evening.")
                )
            elif self._pairer is None:
                book = candidates[0]
            else:
                for position, candidate in enumerate(candidates[:MAX_BOOKS_TO_PAIR]):
                    keep = state.drink_id if candidate is held else None
                    drink = self._pair(candidate, state, tastes, theme, keep)
                    if drink is not None:
                        book = candidate
                        if position:
                            notes.append(
                                f"No drink pairs with {candidates[0].title} under these rules, "
                                f"so the book is {candidate.title}."
                            )
                        break
                if book is None:
                    book = candidates[0]
        if self._pairer is None:
            reason = DRINK_NEEDS_COCKTAILS if self._cocktails is None else DRINK_NEEDS_BOOK
            missing.append(MissingPart(part="drink", reason=reason))
        elif book is None:
            missing.append(MissingPart(part="drink", reason=DRINK_NEEDS_BOOK))
        elif drink is None:
            free = " alcohol-free" if state.alcohol_free else ""
            missing.append(
                MissingPart(
                    part="drink", reason=f"No{free} recipe pairs with {book.title} right now."
                )
            )
        outfit_part = None
        if outfit is not None:
            outfit_part = OutfitPart(outfit=outfit, reason=_outfit_reason(theme, outfit))
        book_part = BookPart(book=book, reason=_book_reason(book, theme)) if book else None
        drink_part = _drink_part(drink, theme) if drink is not None else None
        state = state.model_copy(
            update={
                "theme": theme.id,
                "outfit": outfit.state if outfit is not None else state.outfit,
                "book_id": book.book_id if book else None,
                "drink_id": drink.pairing.cocktail.drink_id if drink else None,
            }
        )
        parts = [outfit_part, book_part, drink_part]
        filled = sum(part is not None for part in parts)
        status = "complete" if filled == 3 else ("partial" if filled else "empty")
        return EveningPlan(
            occasion=state.occasion,
            theme=ThemeInfo(
                id=theme.id,
                label=theme.label,
                icon=theme.icon,
                feeling=theme.feeling,
                evidence=match.evidence(state.occasion),
            ),
            outfit=outfit_part,
            book=book_part,
            drink=drink_part,
            why=_why(state.occasion, theme, outfit_part, book_part, drink_part, missing),
            missing=missing,
            notes=notes,
            status=status,
            state=state,
        )


# --- Helpers --------------------------------------------------------------------------------

_TASTE_FIELDS = set(EveningTastes.model_fields)


def _outfit_of(plan: EveningPlan) -> Outfit | None:
    return plan.outfit.outfit if plan.outfit is not None else None


def _issue_of(plan: EveningPlan) -> str | None:
    return next((m.reason for m in plan.missing if m.part == "outfit"), None)


def _outfit_occasion(theme: Theme, occasion: str) -> str:
    """The theme's outfit occasion, in the season the evening's occasion names, if any."""
    season = occasion_season(occasion)
    base = theme.outfit_occasion
    if season and season not in base:
        base = " ".join(w for w in base.split() if w not in SEASONS)
        return f"{season} {base}"
    return base


def _fit(cocktail: Cocktail, theme: Theme) -> list[str]:
    """Recipe ingredients (or the name or glass) that use one of the theme's drink words."""
    words = {w.casefold() for w in theme.drink_words}
    fit = []
    for ingredient in cocktail.ingredients:
        name = ingredient.name.casefold()
        if name in words or set(_WORD.findall(name)) & words:
            fit.append(ingredient.name)
    if not fit:
        name = cocktail.name.casefold()
        glass = cocktail.glass.casefold()
        if set(_WORD.findall(name)) & words:
            fit.append(cocktail.name)
        elif glass in words:
            fit.append(f"served in a {cocktail.glass.lower()}")
    return list(dict.fromkeys(fit))[:3]


def _opens_series(book: Book) -> bool:
    """A book read on its own: no series, or the first book of one."""
    position = (book.series_position or "1").strip()
    return position in ("1", "1.0") or position.startswith("1-")


def _prior(book: Book) -> float:
    """The evening's quality prior: the Goodreads average times log popularity."""
    return book.average_rating * math.log10(book.ratings_count + 10)


def _euro(amount: float) -> str:
    return f"€{amount:.2f}"


def _rating(book: Book) -> str:
    """ "★4.55", "★4.5", "★4.0": the Goodreads average at the precision it has."""
    text = f"{book.average_rating:.2f}"
    return f"★{text[:-1] if text.endswith('0') else text}"


def _key_piece(outfit: Outfit) -> Product | None:
    """The piece that sets the look: the first filled slot."""
    return next((s.product for s in outfit.slots if s.product is not None), None)


def _piece(product: Product) -> str:
    """ "Osman crew sweater (€24.99)"; the type is left out when the name says it."""
    kind = product.product_type.lower()
    name = product.name if kind in product.name.lower() else f"{product.name} {kind}"
    return f"{name} ({_euro(product.price)})"


def _outfit_reason(theme: Theme, outfit: Outfit) -> str:
    queries = theme.queries_for(outfit.audience)
    looks = [query for slot, query in queries if any(s.slot == slot for s in outfit.slots)]
    look = _and(looks[:4]) or "a look for the occasion"
    filled = len(outfit.products)
    noun = "piece" if filled == 1 else "pieces"
    budget = f" of a {_euro(outfit.budget)} budget" if outfit.budget is not None else ""
    return (
        f"{theme.label}: {theme.feeling} calls for {look}. {filled} {noun} for "
        f"{_euro(outfit.total)}{budget}."
    )


def _book_fit(book: Book, theme: Theme) -> str:
    """Evidence that the book suits the theme: its genre, mood shelves or words."""
    genres = [g for search in theme.books for g in search.genres if g in book.genres]
    if genres:
        return f"a {genres[0].lower()} read"
    for search in theme.books:
        if search.mood and (mood := MOODS.get(search.mood)):
            shelves = [shelf_label(s) for s in mood.shelves]
            shelved = [tag for tag in book.tags if tag in shelves]
            if shelved:
                return f"{search.mood.lower()}: shelved as {_and(shelved[:2])}"
        words = [w for w in _WORD.findall(search.query.casefold()) if len(w) > 3]
        text = f"{book.title} {book.description}".casefold()
        if said := [w for w in words if re.search(rf"\b{re.escape(w)}\b", text)]:
            return "mentions " + _and([f"“{w}”" for w in said[:2]])
    genre = book.genres[0].lower() if book.genres else "well-loved"
    return f"a well-rated {genre} read"


def _book_reason(book: Book, theme: Theme) -> str:
    genre = book.genres[0] if book.genres else "Fiction"
    return (
        f"{theme.label} pick, {_book_fit(book, theme)}. {genre} · {_rating(book)} from "
        f"{book.ratings_count:,} Goodreads ratings."
    )


def _drink_part(drink: _Drink, theme: Theme) -> DrinkPart:
    pairing, result = drink.pairing, drink.result
    labels = {t.id: t.label for t in result.profile.themes}
    pairs_with = labels.get(pairing.theme) or result.profile.summary
    reasons = list(pairing.reasons)
    if drink.fit:
        reasons = [
            *reasons[:2],
            f"Suits {theme.feeling}: {theme.drink_feeling} ({_and(drink.fit)})",
        ]
    return DrinkPart(
        cocktail=pairing.cocktail,
        reasons=reasons,
        pairs_with=pairs_with,
        evening_fit=drink.fit,
        alcohol_free_reason=result.profile.alcohol_free_reason,
        note=result.note,
    )


def _why(
    occasion: str,
    theme: Theme,
    outfit: OutfitPart | None,
    book: BookPart | None,
    drink: DrinkPart | None,
    missing: Sequence[MissingPart],
) -> str:
    """One line citing each part's evidence: price, genre and rating, ingredients and the
    pairing theme."""
    parts = []
    if outfit is not None and (piece := _key_piece(outfit.outfit)) is not None:
        more = len(outfit.outfit.products) - 1
        extra = f" + {more} more, {_euro(outfit.outfit.total)} in all" if more else ""
        parts.append(_piece(piece) + extra)
    if book is not None:
        b = book.book
        genre = b.genres[0].lower() if b.genres else "fiction"
        parts.append(f"{b.title} ({genre}, {_rating(b)})")
    if drink is not None:
        c = drink.cocktail
        ingredients = drink.evening_fit or [i.name for i in c.ingredients[:2]]
        ingredients = [i for i in ingredients if not i.startswith("served in")] or [
            i.name for i in c.ingredients[:2]
        ]
        free = "alcohol-free, " if c.alcohol_free else ""
        parts.append(
            f"{c.name} ({free}{', '.join(ingredients[:2])}; pairs with {drink.pairs_with.lower()})"
        )
    line = f"{occasion[:1].upper()}{occasion[1:]} → {theme.label.lower()}: " + " · ".join(parts)
    gaps = [m.part for m in missing]
    if gaps:
        line += (" · " if parts else "") + "no " + _and(gaps) + " yet"
    return line


def _and(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def audience_of(text: str) -> EveningAudience | None:
    found = audience_in(text)
    return found if found in ("women", "men") else None  # type: ignore[return-value]
