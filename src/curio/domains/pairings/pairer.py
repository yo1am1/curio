"""Book → cocktail pairing: deterministic, offline and explained.

1. Profile. A book's signals point to flavour `themes` (`pairings.themes`): its genres
   (strongest first), its reader shelves (most used first) and words in its title or
   description (a setting such as Paris, an era such as the Jazz Age). Each book's signals
   are turned into shares that sum to 1 (broad themes such as Classics weigh less). A
   reader's taste sums the shares of every book on the reading list, plus one book's worth
   per liked genre. Avoided genres never count: their genre labels and shelves are dropped,
   and a theme whose main genre is avoided (Horror for "Horror and gothic") is switched off.
2. Score. Every theme scores the 425 recipes with the cocktail search index
   (`CocktailRecommender.term_scores`), plus a bonus for the style's signature drinks and
   glasses. A recipe's score is the profile-weighted sum, plus a bonus for a documented
   literary drink of the book's author (`LORE`).
3. Choose. The best recipes form a pool; "sets" of `top_k` are drawn from it greedily, each
   pick penalised for sharing a base spirit, a theme or most ingredients with the earlier
   picks of its set (or a name word: two "Espresso" drinks), so a set is never five
   near-identical whiskey drinks. `seed` picks the
   set (seed modulo the number of sets), so "another pairing" is deterministic.
4. Explain. Each pairing cites the book evidence and the recipe evidence of its theme
   ("The Big Sleep (Mystery · shelved as noir) → a bitter, spirit-forward whiskey drink:
   Old Fashioned (Bourbon, Angostura Bitters)"), or the literary lore that chose it, then a
   second theme or why it is alcohol-free.

Responsible drinking: a children's or young-adult book (by genre or reader shelf), a reader
who asks, or the cocktail domain's remembered alcohol-free taste gets only recipes marked
"Non alcoholic" (`Cocktail.alcohol_free`; "Optional alcohol" counts as alcoholic). Shots,
overproof spirits and crude drink names are never offered as pairings.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from curio.domains.books.catalog import BookCatalog, name_key
from curio.domains.books.models import Book
from curio.domains.books.taxonomy import GENRES, shelf_label
from curio.domains.cocktails.models import Cocktail
from curio.domains.cocktails.recommend import CocktailRecommender, is_crude, terms
from curio.domains.pairings.models import (
    BookPairing,
    FlavourProfile,
    Pairing,
    PairingRequest,
    ThemeWeight,
)
from curio.domains.pairings.themes import (
    LORE,
    THEMES,
    YOUNG_GENRES,
    YOUNG_SHELVES,
    Lore,
    Theme,
    ThemeTable,
)

__all__ = ["BookCocktailPairer", "PairingError", "UnknownBookError"]

# Signal weights: the strongest genre counts most, shelves fade with their rank, and a title
# or description word counts once per distinct word, capped per theme.
GENRE_WEIGHTS = (1.0, 0.8, 0.6, 0.5)
SHELF_WEIGHT = 0.5
SHELF_FADE = 16  # the shelf at rank r weighs SHELF_WEIGHT * (1 - r / SHELF_FADE)
TITLE_WORD = 0.6
DESCRIPTION_WORD = 0.4
WORD_CAP = 1.0
# Themes below this share of a profile are dropped; at most MAX_THEMES are kept.
MIN_SHARE = 0.1
MAX_THEMES = 4
# A book "leans" a theme when it gives it at least this share (reader-taste counts).
BOOK_LEAN = 0.2
# Recipe score bonuses on top of the TF-IDF cosine of a theme's words.
SIGNATURE_BONUS = 0.4
# Signatures are listed most typical first; each later one gets this much less of the bonus.
SIGNATURE_STEP = 0.06
GLASS_BONUS = 0.08
LORE_BONUS = 0.6
# The pool keeps recipes scoring at least this share of the best one, at most
# POOL_SETS * top_k of them.
POOL_SHARE = 0.3
POOL_SETS = 4
# Diversity within a set: multiplied once per earlier pick with the same base spirit or the
# same theme, and once if an earlier pick shares most ingredient families.
SAME_SPIRIT = 0.7
SAME_THEME = 0.75
NEAR_DUPLICATE = 0.5
NEAR_DUPLICATE_SIMILARITY = 0.6
# Name words that say nothing about a drink's style; any other shared word ("Espresso",
# "Martini", "Toddy") makes two drinks near-duplicates.
GENERIC_NAME_WORDS = frozenset({"a", "and", "the", "of", "de", "cocktail", "special", "n"})
# A second theme is cited when it brings at least this share of the main theme's points.
SECOND_THEME_SHARE = 0.5
MAX_EVIDENCE_INGREDIENTS = 3

# Never offered as a pairing: drinking-game shots and slammers, drinks set alight, overproof
# spirits and crude names.
EXCLUDED_CATEGORIES = frozenset({"shot"})
EXCLUDED_NAME_WORDS = frozenset({"shot", "shots", "slammer", "flaming"})
OVERPROOF = ("151 proof rum", "everclear", "grain alcohol", "firewater")
# Base spirit (or, without one, main ingredient) families, for diversity. Checked in order;
# the first family with a word in any ingredient name wins.
SPIRIT_WORDS: tuple[tuple[str, frozenset[str]], ...] = tuple(
    (family, frozenset(words.split()))
    for family, words in (
        ("whiskey", "bourbon whiskey whisky scotch rye daniels turkey royal beam comfort"),
        ("gin", "gin"),
        ("rum", "rum cachaca malibu bacardi"),
        ("vodka", "vodka absolut"),
        ("agave", "tequila mezcal"),
        ("brandy", "brandy cognac applejack pisco"),
        ("wine", "champagne prosecco wine port sherry"),
        ("aperitif", "vermouth campari aperol lillet dubonnet"),
        ("coffee", "coffee espresso kahlua"),
        ("chocolate", "chocolate cocoa"),
        ("tea", "tea chai"),
        ("dairy", "milk yoghurt cream"),
    )
)
ALCOHOL_FREE_NOTE = "Alcohol-free recipes only: {reason}."
DRINK_NOTE = (
    "Please drink responsibly. Ingredient filters only cover listed recipe ingredients; "
    "they are not an allergen guarantee."
)
_WORD = re.compile(r"[a-z0-9]+")


class PairingError(ValueError):
    """Nothing to pair with (no known book or genre)."""


class UnknownBookError(PairingError):
    """The book to pair is not in the catalog."""


@dataclass(frozen=True)
class Signal:
    kind: str  # "genre", "shelf", "word" or "liked"
    value: str  # as shown: "Mystery", "noir", "Paris"
    weight: float


@dataclass
class _Profile:
    source: str
    books: list[Book]
    weights: dict[str, float]  # theme id -> share, summing to 1
    evidence: dict[str, str]  # theme id -> book evidence for reasons
    summary_evidence: dict[str, str]  # theme id -> short evidence for the profile card
    leaning: dict[str, int]  # theme id -> books that lean that way
    alcohol_free_reason: str | None = None
    single: Book | None = None
    authors: set[str] = field(default_factory=set)


class BookCocktailPairer:
    def __init__(
        self,
        books: BookCatalog,
        cocktails: Iterable[Cocktail],
        recommender: CocktailRecommender,
        *,
        table: ThemeTable = THEMES,
        lore: Sequence[Lore] = LORE,
    ) -> None:
        self._books = books
        self._recommender = recommender
        self._table = table
        self._cocktails = {c.drink_id: c for c in cocktails}
        self._eligible = {d for d, c in self._cocktails.items() if _responsible(c)}
        self._spirits = {d: _spirit(c) for d, c in self._cocktails.items()}
        self._name_words = {
            d: frozenset(_WORD.findall(c.name.casefold())) - GENERIC_NAME_WORDS
            for d, c in self._cocktails.items()
        }
        every = (*table.themes, table.fallback)
        # (theme id, alcohol-free) -> drink id -> fit; soft words count only alcohol-free.
        self._fits = {(t.id, soft): self._theme_fit(t, soft) for t in every for soft in (0, 1)}
        self._by_genre: dict[str, list[Theme]] = {}
        self._by_shelf: dict[str, list[Theme]] = {}
        for theme in table.themes:
            for genre in theme.genres:
                self._by_genre.setdefault(genre, []).append(theme)
            for shelf in theme.shelves:
                self._by_shelf.setdefault(shelf, []).append(theme)
        self._words = {
            t.id: re.compile(r"\b(?:" + "|".join(map(re.escape, t.words)) + r")\b", re.I)
            for t in table.themes
            if t.words
        }
        names = {c.name: d for d, c in self._cocktails.items()}
        self._lore = {
            (name_key(item.author), names[item.drink]): item for item in lore if item.drink in names
        }

    # --- Public API -------------------------------------------------------------------------

    def pair(self, request: PairingRequest) -> BookPairing:
        profile = self._profile(request)
        alcohol_free = profile.alcohol_free_reason is not None
        tastes = request.cocktail_preferences
        avoided = tastes.avoided_ingredients if tastes is not None else []
        scores = self._scores(profile, alcohol_free, avoided, set(request.exclude_drink_ids))
        sets = self._sets(scores, profile, request.top_k)
        chosen = sets[request.seed % len(sets)] if sets else []
        pairings = [self._pairing(drink_id, scores[drink_id], profile) for drink_id in chosen]
        note = (
            ALCOHOL_FREE_NOTE.format(reason=profile.alcohol_free_reason)
            if alcohol_free
            else DRINK_NOTE
        )
        return BookPairing(
            profile=self._flavour_profile(profile),
            pairings=pairings,
            seed=request.seed,
            sets=max(1, len(sets)),
            note=note,
        )

    def themes_of(self, book: Book, avoided_genres: Iterable[str] = ()) -> dict[str, float]:
        """A book's theme shares (before the profile's cut-offs), for tests and probes."""
        signals = self._signals(book, self._avoided(avoided_genres))
        return _shares(signals)

    # --- Profile ------------------------------------------------------------------------------

    def _profile(self, request: PairingRequest) -> _Profile:
        avoided = self._avoided(request.avoided_genres)
        if request.book_id is not None:
            book = self._books.get(request.book_id)
            if book is None:
                raise UnknownBookError(f"Book {request.book_id} is not in the catalog.")
            return self._single(book, avoided, request)
        books = self._books.get_many(dict.fromkeys(request.book_ids))
        liked = [
            genre
            for genre in dict.fromkeys(
                g for g in map(self._books.canonical_genre, request.liked_genres) if g
            )
            if genre not in avoided[0]
        ]
        if not books and not liked:
            raise PairingError("None of those books or genres are in the catalog.")
        return self._reader(books, liked, avoided, request)

    def _single(
        self, book: Book, avoided: tuple[set[str], set[str], set[str]], request: PairingRequest
    ) -> _Profile:
        signals = self._signals(book, avoided)
        weights = _cut(_shares(signals))
        evidence = {t: f"{book.title} ({_describe(signals[t])})" for t in weights}
        summary = {t: _describe(signals[t]) for t in weights}
        reason = None
        if _young(book):
            reason = f"{book.title} is a children's or young-adult book"
        return _Profile(
            source=book.title,
            books=[book],
            weights=weights,
            evidence=evidence,
            summary_evidence=summary,
            leaning={t: 1 for t in weights},
            alcohol_free_reason=reason or _asked(request),
            single=book,
            authors={name_key(book.author)},
        )

    def _reader(
        self,
        books: list[Book],
        liked: list[str],
        avoided: tuple[set[str], set[str], set[str]],
        request: PairingRequest,
    ) -> _Profile:
        totals: Counter[str] = Counter()
        leaning: Counter[str] = Counter()
        for book in books:
            for theme_id, share in _shares(self._signals(book, avoided)).items():
                totals[theme_id] += share
                if share >= BOOK_LEAN:
                    leaning[theme_id] += 1
        liked_by_theme: dict[str, list[str]] = {}
        for genre in liked:
            themes = [t for t in self._by_genre.get(genre, []) if t.id not in avoided[2]]
            for theme in themes:
                totals[theme.id] += 1 / len(themes)
                liked_by_theme.setdefault(theme.id, []).append(genre)
        total = sum(totals.values())
        weights = _cut({t: w / total for t, w in totals.items()} if total else {})
        count = len(books)
        noun = "book" if count == 1 else "books"
        evidence, summary = {}, {}
        for theme_id in weights:
            label = self._table.by_id[theme_id].label
            parts = []
            if leaning[theme_id]:
                parts.append(f"{leaning[theme_id]} of {count} {noun}")
            if genres := liked_by_theme.get(theme_id):
                parts.append("you like " + " and ".join(genres))
            if leaning[theme_id]:
                evidence[theme_id] = f"Your reading list leans {label} ({'; '.join(parts)})"
            elif liked_by_theme.get(theme_id):
                evidence[theme_id] = f"You like {' and '.join(liked_by_theme[theme_id])}"
            else:
                evidence[theme_id] = f"Your reading list touches on {label}"
            summary[theme_id] = "; ".join(parts) or "a light touch"
        reason = None
        if young := next((b for b in books if _young(b)), None):
            reason = f"your reading list includes {young.title}, a children's or young-adult book"
        elif young_genre := next((g for g in liked if g in YOUNG_GENRES), None):
            reason = f"you like {young_genre} books"
        source = f"Your reading list ({count} {noun})" if books else "Your liked genres"
        return _Profile(
            source=source,
            books=books,
            weights=weights,
            evidence=evidence,
            summary_evidence=summary,
            leaning=dict(leaning),
            alcohol_free_reason=reason or _asked(request),
            authors={name_key(b.author) for b in books},
        )

    def _avoided(self, genres: Iterable[str]) -> tuple[set[str], set[str], set[str]]:
        """Avoided catalog genres, their shelves and the themes they switch off entirely."""
        names = {g for g in map(self._books.canonical_genre, genres) if g}
        shelves = {shelf_label(s) for g in names for s in GENRES.get(g, ())}
        shelves |= {g.casefold() for g in names}
        themes = {t.id for t in self._table.themes if t.genres and t.genres[0] in names}
        return names, shelves, themes

    def _signals(
        self, book: Book, avoided: tuple[set[str], set[str], set[str]]
    ) -> dict[str, list[Signal]]:
        genres, shelves, themes = avoided
        found: dict[str, list[Signal]] = {}

        def add(theme: Theme, signal: Signal) -> None:
            if theme.id not in themes:
                weighted = Signal(signal.kind, signal.value, signal.weight * theme.strength)
                found.setdefault(theme.id, []).append(weighted)

        for rank, genre in enumerate(book.genres[: len(GENRE_WEIGHTS)]):
            if genre in genres:
                continue
            for theme in self._by_genre.get(genre, []):
                add(theme, Signal("genre", genre, GENRE_WEIGHTS[rank]))
        for rank, shelf in enumerate(book.tags):
            if shelf in shelves:
                continue
            for theme in self._by_shelf.get(shelf, []):
                add(theme, Signal("shelf", shelf, SHELF_WEIGHT * (1 - rank / SHELF_FADE)))
        for theme_id, pattern in self._words.items():
            theme = self._table.by_id[theme_id]
            weight = 0.0
            seen: set[str] = set()
            for text, value in ((book.title, TITLE_WORD), (book.description, DESCRIPTION_WORD)):
                for match in pattern.finditer(text):
                    word = match.group(0)
                    if word.casefold() in seen or weight >= WORD_CAP:
                        continue
                    seen.add(word.casefold())
                    step = min(value, WORD_CAP - weight)
                    weight += step
                    add(theme, Signal("word", word, step))
        return found

    # --- Scoring and choice ---------------------------------------------------------------------

    def _theme_fit(self, theme: Theme, soft: int) -> dict[int, float]:
        """How well every recipe pours a theme: search-index cosine plus style bonuses.

        The cosine only counts for a recipe whose ingredients or name use the theme's words,
        so a reason can always cite them (a category word such as "Coffee / Tea" alone
        does not make chai a coffee drink)."""
        words = _words(theme, bool(soft))
        cosine = self._recommender.term_scores(words)
        fit = {d: score for d, score in cosine.items() if self._uses(d, words)}
        signatures = {name: rank for rank, name in enumerate(theme.signatures)}
        glasses = {g.casefold() for g in theme.glasses}
        for drink_id, cocktail in self._cocktails.items():
            bonus = 0.0
            if (rank := signatures.get(cocktail.name)) is not None:
                bonus += SIGNATURE_BONUS * max(0.5, 1 - SIGNATURE_STEP * rank)
            # The glass only adds to a recipe that already pours the style.
            if bonus or drink_id in fit:
                bonus += GLASS_BONUS * (cocktail.glass.casefold() in glasses)
            if bonus:
                fit[drink_id] = fit.get(drink_id, 0.0) + bonus
        return fit

    def _uses(self, drink_id: int, words: Mapping[str, float]) -> bool:
        """Whether the recipe's ingredients or name use any of these words."""
        if self._recommender.ingredients_using(drink_id, words):
            return True
        wanted = {w for word in words for w in terms(word)}
        return bool(wanted & set(terms(self._cocktails[drink_id].name)))

    def _weights(self, profile: _Profile) -> dict[str, float]:
        return profile.weights or {self._table.fallback.id: 1.0}

    def _scores(
        self,
        profile: _Profile,
        alcohol_free: bool,
        avoided: Sequence[str],
        shown: set[int],
    ) -> dict[int, float]:
        weights = self._weights(profile)
        scores: dict[int, float] = {}
        for drink_id in sorted(self._eligible - shown):
            cocktail = self._cocktails[drink_id]
            if alcohol_free and not cocktail.alcohol_free:
                continue
            if avoided and self._recommender.contains_any(drink_id, avoided):
                continue
            score = sum(
                w * self._fits[t, alcohol_free].get(drink_id, 0.0) for t, w in weights.items()
            )
            if not alcohol_free and self._lore_for(profile, drink_id):
                score += LORE_BONUS
            if score > 0:
                scores[drink_id] = round(score, 9)
        return scores

    def _sets(self, scores: Mapping[int, float], profile: _Profile, top_k: int) -> list[list[int]]:
        ranked = sorted(scores, key=lambda d: (-scores[d], d))
        if not ranked:
            return []
        best = scores[ranked[0]]
        pool = [d for d in ranked if scores[d] >= POOL_SHARE * best][: POOL_SETS * top_k]
        if len(pool) < 2 * top_k:
            pool = ranked[: 2 * top_k]  # every scored recipe has evidence for its theme
        sets: list[list[int]] = []
        remaining = list(pool)
        while remaining:
            chosen: list[int] = []
            while remaining and len(chosen) < top_k:
                pick = max(
                    remaining,
                    key=lambda d: (round(self._diverse(d, chosen, scores, profile), 9), -d),
                )
                chosen.append(pick)
                remaining.remove(pick)
            if len(chosen) < top_k and sets:
                break  # a short last set would only repeat weaker drinks
            sets.append(chosen)
        return sets

    def _diverse(
        self, drink_id: int, chosen: Sequence[int], scores: Mapping[int, float], profile: _Profile
    ) -> float:
        score = scores[drink_id]
        spirit = self._spirits[drink_id]
        theme = self._main_theme(drink_id, profile)
        for other in chosen:
            if self._spirits[other] == spirit:
                score *= SAME_SPIRIT
            if len(self._weights(profile)) > 1 and self._main_theme(other, profile) == theme:
                score *= SAME_THEME
        if any(
            self._name_words[drink_id] & self._name_words[other]
            or self._recommender.ingredient_similarity(drink_id, other) >= NEAR_DUPLICATE_SIMILARITY
            for other in chosen
        ):
            score *= NEAR_DUPLICATE
        return score

    def _contributions(self, drink_id: int, profile: _Profile) -> list[tuple[str, float]]:
        """Each theme's points for a recipe, best first (ties by theme order)."""
        weights = self._weights(profile)
        soft = profile.alcohol_free_reason is not None
        order = {t: i for i, t in enumerate(weights)}
        points = [(t, w * self._fits[t, soft].get(drink_id, 0.0)) for t, w in weights.items()]
        return sorted(points, key=lambda item: (-round(item[1], 9), order[item[0]]))

    def _main_theme(self, drink_id: int, profile: _Profile) -> str:
        return self._contributions(drink_id, profile)[0][0]

    def _lore_for(self, profile: _Profile, drink_id: int) -> Lore | None:
        keys = ((author, drink_id) for author in sorted(profile.authors))
        return next((self._lore[key] for key in keys if key in self._lore), None)

    # --- Explanations ----------------------------------------------------------------------------

    def _pairing(self, drink_id: int, score: float, profile: _Profile) -> Pairing:
        cocktail = self._cocktails[drink_id]
        contributions = self._contributions(drink_id, profile)
        main_id, main_points = contributions[0]
        main = self._table.by_id[main_id]
        evidence = profile.evidence.get(main_id) or f"{profile.source}: no strong flavour cue"
        alcohol_free = profile.alcohol_free_reason is not None
        style = (main.soft_style or main.style) if alcohol_free else main.style
        recipe = self._recipe_evidence(cocktail, main, alcohol_free)
        theme_reason = f"{evidence} → {style}: {cocktail.name} ({recipe})"
        reasons = [theme_reason]
        lore = None if alcohol_free else self._lore_for(profile, drink_id)
        if lore is not None:
            # The author's own drink: the lore chose it, so it leads; the theme follows only
            # when the recipe really pours that style.
            book = next(b for b in profile.books if name_key(b.author) == name_key(lore.author))
            ingredients = _join([i.name for i in cocktail.ingredients])
            reasons = [
                f"{book.title} is by {book.author}. {lore.text} → {cocktail.name} ({ingredients})"
            ]
            if main_points >= LORE_BONUS / 2:
                reasons.append(theme_reason)
        for theme_id, points in contributions[1:2]:
            theme = self._table.by_id[theme_id]
            used = self._recommender.ingredients_using(drink_id, _words(theme, alcohol_free))
            signature = cocktail.name in theme.signatures
            if points >= SECOND_THEME_SHARE * main_points and (used or signature):
                detail = _join(used) if used else "a signature of the style"
                reasons.append(f"Also suits {theme.label}: {detail}")
        if alcohol_free:
            reasons.append(f"Alcohol-free, because {profile.alcohol_free_reason}")
        return Pairing(cocktail=cocktail, score=round(score, 6), theme=main_id, reasons=reasons[:3])

    def _recipe_evidence(self, cocktail: Cocktail, theme: Theme, soft: bool) -> str:
        """The recipe's ingredients that pour the theme; for a signature drink, a drink
        named for it ("Hot Toddy") or one served in its glass, its first ingredients."""
        words = _words(theme, soft)
        used = self._recommender.ingredients_using(cocktail.drink_id, words)
        if used:
            return _join(used)
        if cocktail.name not in theme.signatures and not self._uses(cocktail.drink_id, words):
            glass = cocktail.glass.lower()
            return f"served in a {glass}" if glass else _join([cocktail.ingredients[0].name])
        return _join([i.name for i in cocktail.ingredients[:2]])

    def _flavour_profile(self, profile: _Profile) -> FlavourProfile:
        themes = [
            ThemeWeight(
                id=theme_id,
                label=(theme := self._table.by_id[theme_id]).label,
                style=theme.style,
                weight=round(weight, 4),
                evidence=profile.summary_evidence.get(theme_id, ""),
                books=profile.leaning.get(theme_id, 0) if profile.single is None else 0,
            )
            for theme_id, weight in profile.weights.items()
        ]
        plural = profile.source.endswith("genres")
        if themes:
            verb = "lean" if plural else "leans"
            summary = f"{profile.source} {verb} " + _and([t.label for t in themes[:3]])
        else:
            verb = "give" if plural else "gives"
            summary = f"{profile.source} {verb} no strong flavour cue: here are well-loved classics"
        return FlavourProfile(
            source=profile.source,
            themes=themes,
            summary=summary,
            alcohol_free=profile.alcohol_free_reason is not None,
            alcohol_free_reason=profile.alcohol_free_reason,
            book_ids=[b.book_id for b in profile.books],
        )


# --- Helpers -------------------------------------------------------------------------------------


def _words(theme: Theme, soft: bool) -> dict[str, float]:
    """A theme's cocktail words; alcohol-free pairings add its soft words."""
    return {**theme.terms, **theme.soft_terms} if soft else dict(theme.terms)


def _responsible(cocktail: Cocktail) -> bool:
    """Whether a recipe may be offered as a pairing at all."""
    if cocktail.category.casefold() in EXCLUDED_CATEGORIES:
        return False
    if is_crude(cocktail):
        return False
    if EXCLUDED_NAME_WORDS & set(_WORD.findall(cocktail.name.casefold())):
        return False
    names = [i.name.casefold() for i in cocktail.ingredients]
    return not any(word in name for name in names for word in OVERPROOF)


def _spirit(cocktail: Cocktail) -> str:
    words = [set(_WORD.findall(i.name.casefold())) for i in cocktail.ingredients]
    for family, family_words in SPIRIT_WORDS:
        if any(w & family_words for w in words):
            return family
    return "fruit"


def _young(book: Book) -> bool:
    """A children's or young-adult book, by genre or by any of its reader shelves."""
    return bool(YOUNG_GENRES & set(book.genres) or YOUNG_SHELVES & set(book.tags))


def _asked(request: PairingRequest) -> str | None:
    if request.alcohol_free:
        return "you asked for alcohol-free drinks"
    if request.cocktail_preferences is not None and request.cocktail_preferences.alcohol_free:
        return "your remembered cocktail taste is alcohol-free"
    return None


def _shares(signals: Mapping[str, list[Signal]]) -> dict[str, float]:
    totals = {t: sum(s.weight for s in found) for t, found in signals.items()}
    total = sum(totals.values())
    if not total:
        return {}
    ranked = sorted(totals, key=lambda t: -totals[t])
    return {t: totals[t] / total for t in ranked}


def _cut(shares: Mapping[str, float]) -> dict[str, float]:
    """The strongest themes above MIN_SHARE, renormalised, strongest first."""
    ranked = sorted(shares, key=lambda t: -shares[t])
    kept = [t for t in ranked if shares[t] >= MIN_SHARE][:MAX_THEMES]
    total = sum(shares[t] for t in kept)
    return {t: shares[t] / total for t in kept} if total else {}


def _describe(signals: Sequence[Signal]) -> str:
    """The strongest distinct signals by kind: "Mystery · shelved as noir", "mentions
    “Gatsby” and “Jazz Age”"."""
    kinds: dict[str, list[str]] = {}
    seen: list[str] = []
    for signal in sorted(signals, key=lambda s: -s.weight):
        key = signal.value.casefold()
        if any(key.startswith(other) or other.startswith(key) for other in seen):
            continue
        seen.append(key)
        kinds.setdefault(signal.kind, []).append(signal.value)
        if len(seen) == 3:
            break
    parts = []
    for kind in ("genre", "shelf", "word"):
        if values := kinds.get(kind):
            if kind == "genre":
                parts.append(" and ".join(values))
            elif kind == "shelf":
                parts.append("shelved as " + " and ".join(values))
            else:
                parts.append("mentions " + " and ".join(f"“{v}”" for v in values))
    return " · ".join(parts)


def _join(names: Sequence[str]) -> str:
    return ", ".join(list(dict.fromkeys(names))[:MAX_EVIDENCE_INGREDIENTS])


def _and(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
