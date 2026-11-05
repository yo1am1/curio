"""Genres, moods and shelf-name cleaning for the goodbooks-10k tags.

Goodreads tags are reader shelves: genres ("fantasy", "sci-fi"), but also bookkeeping
("to-read", "owned", "read-in-2015", "kindle") and opinions ("favorites", "dnf"). The prepare
script keeps the descriptive ones (`is_descriptive`) as each book's tags and maps groups of
shelf names onto a small, fixed genre list (`GENRES`). `MOODS` are browse shortcuts over those
shelves, used by search and the Books page.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

# Display genre -> shelf names that count towards it.
GENRES: Mapping[str, tuple[str, ...]] = {
    "Fantasy": ("fantasy", "high-fantasy", "epic-fantasy", "urban-fantasy", "dark-fantasy"),
    "Science fiction": (
        "science-fiction",
        "sci-fi",
        "scifi",
        "sf",
        "space-opera",
        "cyberpunk",
        "speculative-fiction",
    ),
    "Mystery": ("mystery", "mysteries", "detective", "cozy-mystery", "mystery-thriller"),
    "Thriller": ("thriller", "thrillers", "suspense", "psychological-thriller", "legal-thriller"),
    "Crime": ("crime", "true-crime"),
    "Romance": (
        "romance",
        "contemporary-romance",
        "historical-romance",
        "paranormal-romance",
        "chick-lit",
    ),
    "Historical fiction": ("historical-fiction", "historical"),
    "Horror": ("horror", "scary", "creepy"),
    "Young adult": ("young-adult", "ya", "teen", "ya-fiction", "young-adult-fiction"),
    "Children's": (
        "childrens",
        "children",
        "children-s",
        "children-s-books",
        "picture-books",
        "middle-grade",
        "kids",
    ),
    "Classics": ("classics", "classic", "classic-literature", "1001-books"),
    "Literary fiction": ("literary-fiction", "literature", "literary"),
    "Contemporary": ("contemporary", "contemporary-fiction", "realistic-fiction"),
    "Dystopian": ("dystopian", "dystopia", "post-apocalyptic"),
    "Paranormal": ("paranormal", "supernatural", "vampires", "werewolves"),
    "Non-fiction": ("non-fiction", "nonfiction"),
    "Biography & memoir": ("biography", "memoir", "memoirs", "autobiography", "biography-memoir"),
    "History": ("history",),
    "Science": ("science",),
    "Philosophy": ("philosophy",),
    "Psychology": ("psychology",),
    "Self-help": ("self-help",),
    "Business": ("business", "economics"),
    "Poetry": ("poetry",),
    "Plays": ("plays", "drama", "shakespeare"),
    "Graphic novels": ("graphic-novels", "graphic-novel", "comics", "manga"),
    "Humour": ("humor", "humour", "funny", "comedy"),
    "Adventure": ("adventure", "action", "action-adventure"),
    "Short stories": ("short-stories",),
    "Religion": ("religion", "christian", "spirituality"),
}
# How readers name genres in a request (after lowercasing; plurals included). Search turns them
# into genre filters, so "sci-fi" never matches "science" in a popular-science title.
GENRE_WORDS: Mapping[str, str] = {
    **{genre.casefold(): genre for genre in GENRES},
    "science-fiction": "Science fiction",
    "sci-fi": "Science fiction",
    "sci fi": "Science fiction",
    "scifi": "Science fiction",
    "sf": "Science fiction",
    "mysteries": "Mystery",
    "detective": "Mystery",
    "whodunit": "Mystery",
    "whodunnit": "Mystery",
    "thrillers": "Thriller",
    "romances": "Romance",
    "love stories": "Romance",
    "love story": "Romance",
    "historical novel": "Historical fiction",
    "historical novels": "Historical fiction",
    "ya": "Young adult",
    "teen": "Young adult",
    "childrens": "Children's",
    "children": "Children's",
    "kids": "Children's",
    "classic": "Classics",
    "dystopia": "Dystopian",
    "dystopias": "Dystopian",
    "nonfiction": "Non-fiction",
    "non fiction": "Non-fiction",
    "biography": "Biography & memoir",
    "biographies": "Biography & memoir",
    "memoir": "Biography & memoir",
    "memoirs": "Biography & memoir",
    "autobiography": "Biography & memoir",
    "self help": "Self-help",
    "poems": "Poetry",
    "play": "Plays",
    "graphic novel": "Graphic novels",
    "comics": "Graphic novels",
    "comic": "Graphic novels",
    "manga": "Graphic novels",
    "humor": "Humour",
    "short story": "Short stories",
    "horror": "Horror",
}
# A genre is assigned when its shelves gather at least this share of the book's most used
# descriptive shelf; at most MAX_GENRES per book, strongest first.
GENRE_MIN_SHARE = 0.12
MAX_GENRES = 4
MAX_TAGS = 12


@dataclass(frozen=True)
class Mood:
    """A browse shortcut: shelves that express it, or a page limit, or both."""

    shelves: tuple[str, ...] = ()
    max_pages: int | None = None


MOODS: Mapping[str, Mood] = {
    "Cosy": Mood(
        ("cozy-mystery", "cozy-mysteries", "cozy", "feel-good", "comfort-reads", "heartwarming")
    ),
    "Dark": Mood(("dark-fantasy", "gothic", "horror", "psychological-thriller", "noir")),
    "Funny": Mood(("humor", "humour", "funny", "comedy", "satire")),
    "Romantic": Mood(("romance", "love", "contemporary-romance", "historical-romance")),
    "Thought-provoking": Mood(("philosophy", "thought-provoking", "literary-fiction", "book-club")),
    "Adventurous": Mood(("adventure", "action", "action-adventure", "epic")),
    "Emotional": Mood(("tear-jerker", "tearjerker", "sad", "emotional")),
    "Epic": Mood(("epic", "epic-fantasy", "high-fantasy", "space-opera")),
    "Short reads": Mood(("novella", "novellas", "short-stories", "short"), max_pages=200),
}
# Mood words in a request; search turns the first one into the mood filter.
MOOD_WORDS: Mapping[str, str] = {
    "cosy": "Cosy",
    "cozy": "Cosy",
    "dark": "Dark",
    "funny": "Funny",
    "hilarious": "Funny",
    "humorous": "Funny",
    "romantic": "Romantic",
    "thought-provoking": "Thought-provoking",
    "thought provoking": "Thought-provoking",
    "adventurous": "Adventurous",
    "emotional": "Emotional",
    "sad": "Emotional",
    "tear-jerker": "Emotional",
    "tearjerker": "Emotional",
    "epic": "Epic",
}
# "Short" in a search means at most this many pages.
SHORT_PAGES = 250

# Shelves about the reader, not the book: reading status, ownership, formats, dates, ratings.
_BOOKKEEPING = re.compile(
    r"(^|-)(to-?read|tbr|read|reading|re-?read|reread|currently|finished|dnf|did-not-finish|"
    r"didn-t-finish|abandoned|unfinished|own|owned|i-own|own-it|have|bought|buy|to-buy|"
    r"wish|wishlist|borrowed|library|libros?|kindle|nook|ebooks?|e-books?|audio|audiobooks?|"
    r"audible|paperback|hardcover|default|favou?rites?|faves|fav|all-time|must-read|"
    r"recommended|reviewed|maybe|later|shelf|shelves|books?|my|stars?|series|"
    r"first-in-series|part-of-a-series|stand-?alone|challenge|club|bookclub|school|"
    r"for-school|read-for-school|\d+)($|-)"
)
_KEEP = frozenset({"book-club"})  # an opinion about the book, used by MOODS
# Too broad to tell books apart; they still count towards genres.
_GENERIC = frozenset(
    {"fiction", "adult", "adult-fiction", "general-fiction", "novels", "novel", "english"}
)
_SHELF = re.compile(r"^[a-z][a-z0-9-]{1,39}$")


def is_descriptive(shelf: str) -> bool:
    """Whether a shelf name says something about the book itself ("fantasy", "dragons")."""
    if shelf in _KEEP:
        return True
    return shelf not in _GENERIC and bool(_SHELF.match(shelf)) and not _BOOKKEEPING.search(shelf)


def shelf_label(shelf: str) -> str:
    """Display form of a shelf name ("historical-fiction" -> "historical fiction")."""
    return shelf.replace("-", " ")


def genres_for(shelf_counts: Mapping[str, int]) -> list[str]:
    """`GENRES` a book belongs to, strongest first, from all its shelf counts; shares are
    relative to its most used descriptive shelf."""
    top = max((n for shelf, n in shelf_counts.items() if is_descriptive(shelf)), default=0)
    if top <= 0:
        return []
    strength = {
        genre: sum(shelf_counts.get(shelf, 0) for shelf in shelves) / top
        for genre, shelves in GENRES.items()
    }
    ranked = sorted(
        (genre for genre, share in strength.items() if share >= GENRE_MIN_SHARE),
        key=lambda genre: (-strength[genre], genre),
    )
    return ranked[:MAX_GENRES]
