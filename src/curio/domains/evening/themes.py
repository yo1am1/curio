"""Evening themes: the one shared mood that drives every part of a planned evening.

An occasion in free text ("rainy Sunday in", "summer garden party") maps to one theme by the
words it uses; each theme says what to wear (an outfit occasion plus a query per slot for the
`OutfitPlanner`), what to read (book searches for the books recommender, tried in order) and
which drinks suit it (words that pick, among the drinks the pairer pairs with the chosen
book, the one that fits the evening best). The table is hand-written and English-only.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from curio.domains.books.models import BookQuery
from curio.domains.fashion.outfits import SlotName

ThemeId = Literal[
    "cosy", "romantic", "glamorous", "summery", "mysterious", "thoughtful", "adventurous", "playful"
]
# Genres an evening plan never reads unless the reader asks for them: picture books and
# middle grade are not an evening read for grown-ups. Young-adult books stay, and the pairer
# keeps their drinks alcohol-free.
EVENING_EXCLUDED_GENRES = ("Children's",)


@dataclass(frozen=True)
class BookSearch:
    """One book search a theme tries: a query, genres (all must apply), a mood and limits."""

    query: str = ""
    genres: tuple[str, ...] = ()
    mood: str | None = None
    min_rating: float | None = None
    exclude_genres: tuple[str, ...] = ()

    def to_query(self, top_k: int) -> BookQuery:
        return BookQuery(
            query=self.query,
            genres=list(self.genres),
            mood=self.mood,
            min_rating=self.min_rating,
            exclude_genres=[*EVENING_EXCLUDED_GENRES, *self.exclude_genres],
            top_k=top_k,
        )

    def describe(self) -> str:
        parts = list(self.genres)
        if self.mood:
            parts.append(f"{self.mood} mood")
        if self.query:
            parts.append(f"“{self.query}”")
        if self.min_rating is not None:
            parts.append(f"★{self.min_rating:g}+")
        return ", ".join(parts)


@dataclass(frozen=True)
class Theme:
    id: ThemeId
    label: str  # "Cosy"
    icon: str  # a Material icon name for the UI
    # Words in an occasion or mood that point to this theme, matched as whole words.
    words: tuple[str, ...]
    # What the evening feels like, for explanations: "a warm night in".
    feeling: str
    # The outfit: an occasion for `OutfitPlanner` (its words pick the template's style and
    # season), the slots to fill, and a query per slot.
    outfit_occasion: str
    slots: tuple[SlotName, ...] = ()
    queries: tuple[tuple[SlotName, str], ...] = ()
    # Menswear queries that replace `queries` for the same slots.
    men_queries: tuple[tuple[SlotName, str], ...] = ()
    # Book searches, tried in order until one finds a book.
    books: tuple[BookSearch, ...] = ()
    # Drink words (ingredients, names, glasses): among the pairer's drinks for the book, the
    # first one using any of them suits the evening best.
    drink_words: tuple[str, ...] = ()
    # Shown as "pairs with …" when a drink uses one of `drink_words`.
    drink_feeling: str = ""

    def queries_for(self, audience: str | None) -> tuple[tuple[SlotName, str], ...]:
        """The slot queries for an audience: menswear replaces the slots it names."""
        if audience != "men":
            return self.queries
        return tuple({**dict(self.queries), **dict(self.men_queries)}.items())


THEMES: tuple[Theme, ...] = (
    Theme(
        id="cosy",
        label="Cosy",
        icon="fireplace",
        words=(
            "rainy",
            "rain",
            "sunday",
            "night in",
            "evening in",
            "stay in",
            "staying in",
            "cosy",
            "cozy",
            "sofa",
            "couch",
            "home",
            "winter",
            "snow",
            "snowy",
            "christmas",
            "fireside",
            "fireplace",
            "blanket",
            "hygge",
            "lazy",
            "chill",
            "autumn",
            "movie night",
            "film night",
            "movies",
        ),
        feeling="a warm night in",
        outfit_occasion="casual autumn weekend at home",
        slots=("top", "bottom", "outerwear"),
        queries=(
            ("top", "chunky knit sweater"),
            ("bottom", "soft relaxed trousers"),
            ("outerwear", "soft knit cardigan"),
        ),
        books=(
            BookSearch(genres=("Fantasy",), min_rating=4.5),
            BookSearch(mood="Cosy", min_rating=3.8),
        ),
        drink_words=(
            "hot",
            "warm",
            "toddy",
            "honey",
            "cinnamon",
            "chocolate",
            "cocoa",
            "coffee",
            "tea",
            "milk",
            "cream",
            "cider",
            "nutmeg",
            "irish coffee cup",
            "coffee mug",
        ),
        drink_feeling="warming",
    ),
    Theme(
        id="romantic",
        label="Romantic",
        icon="favorite",
        words=(
            "date",
            "date night",
            "first date",
            "anniversary",
            "valentine",
            "valentines",
            "romantic",
            "romance",
            "proposal",
            "honeymoon",
            "for two",
            "candlelit",
        ),
        feeling="an evening for two",
        outfit_occasion="date dinner",
        queries=(
            ("dress", "elegant romantic dress"),
            ("shoes", "elegant heeled sandals"),
            ("bag", "small elegant handbag"),
            ("accessory", "delicate earrings"),
        ),
        men_queries=(
            ("top", "smart shirt"),
            ("bottom", "slim smart trousers"),
            ("shoes", "smart leather shoes"),
            ("accessory", "leather belt"),
        ),
        books=(
            BookSearch(mood="Romantic", min_rating=4.2, exclude_genres=("Young adult",)),
            BookSearch(genres=("Romance",)),
        ),
        drink_words=(
            "champagne",
            "prosecco",
            "sparkling",
            "strawberry",
            "strawberries",
            "raspberry",
            "cherry",
            "rose",
            "chocolate",
            "champagne flute",
            "kir",
        ),
        drink_feeling="sparkling and sweet",
    ),
    Theme(
        id="glamorous",
        label="Glamorous",
        icon="diamond",
        words=(
            "party",
            "gala",
            "cocktail party",
            "new year",
            "new year's eve",
            "nye",
            "celebration",
            "celebrate",
            "birthday",
            "glamorous",
            "glam",
            "black tie",
            "night out",
            "premiere",
            "opening",
            "dance",
            "dancing",
            "disco",
        ),
        feeling="a night to dress up for",
        outfit_occasion="party",
        queries=(
            ("dress", "sparkly party dress"),
            ("shoes", "party heels"),
            ("bag", "small evening bag"),
            ("accessory", "statement earrings"),
        ),
        men_queries=(
            ("top", "satin party shirt"),
            ("bottom", "slim black trousers"),
            ("shoes", "smart leather shoes"),
            ("accessory", "watch"),
        ),
        books=(
            BookSearch(query="jazz age glamour party", genres=("Classics",)),
            BookSearch(genres=("Classics",), min_rating=4.0),
        ),
        drink_words=(
            "champagne",
            "prosecco",
            "sparkling",
            "martini",
            "vermouth",
            "gin",
            "cocktail glass",
            "champagne flute",
            "cognac",
        ),
        drink_feeling="a classic cocktail",
    ),
    Theme(
        id="summery",
        label="Summery",
        icon="sunny",
        words=(
            "summer",
            "garden",
            "picnic",
            "beach",
            "barbecue",
            "bbq",
            "rooftop",
            "sunny",
            "terrace",
            "outdoor",
            "outdoors",
            "pool",
            "sun",
            "patio",
            "festival",
            "july",
            "august",
            "heatwave",
        ),
        feeling="a long light evening outdoors",
        outfit_occasion="summer party",
        queries=(
            ("dress", "light floral summer dress"),
            ("shoes", "summer sandals"),
            ("bag", "straw summer bag"),
            ("accessory", "sunglasses"),
        ),
        men_queries=(
            ("top", "linen summer shirt"),
            ("bottom", "light linen trousers"),
            ("shoes", "summer sneakers"),
            ("accessory", "sunglasses"),
        ),
        books=(
            BookSearch(query="summer", min_rating=4.0, exclude_genres=("Young adult",)),
            BookSearch(mood="Funny", min_rating=4.0),
        ),
        drink_words=(
            "lime",
            "mint",
            "lemon",
            "soda",
            "tonic",
            "pineapple",
            "ice",
            "cucumber",
            "watermelon",
            "orange",
            "grapefruit",
            "lemonade",
            "highball glass",
            "collins glass",
        ),
        drink_feeling="fresh and long",
    ),
    Theme(
        id="mysterious",
        label="Mysterious",
        icon="dark_mode",
        words=(
            "mystery",
            "murder",
            "murder mystery",
            "halloween",
            "spooky",
            "noir",
            "dark",
            "stormy",
            "storm",
            "gothic",
            "crime",
            "detective",
            "mysterious",
            "thriller",
            "midnight",
            "candlelight",
        ),
        feeling="a dark and stormy night",
        outfit_occasion="casual autumn evening walk",
        slots=("top", "bottom", "outerwear", "shoes"),
        queries=(
            ("top", "black turtleneck sweater"),
            ("bottom", "black trousers"),
            ("outerwear", "dark long coat"),
            ("shoes", "black ankle boots"),
        ),
        books=(
            BookSearch(genres=("Mystery",), mood="Dark", min_rating=3.9),
            BookSearch(genres=("Mystery",), min_rating=4.0),
        ),
        drink_words=(
            "bitters",
            "absinthe",
            "coffee",
            "dark",
            "black",
            "whiskey",
            "bourbon",
            "rye",
            "scotch",
            "blackberry",
            "old-fashioned glass",
        ),
        drink_feeling="dark and bitter",
    ),
    Theme(
        id="thoughtful",
        label="Thoughtful",
        icon="psychology",
        words=(
            "book club",
            "reading group",
            "book group",
            "discussion",
            "literary",
            "thoughtful",
            "salon",
            "debate",
            "philosophy",
            "quiet",
            "reading",
            "read",
        ),
        feeling="an evening of good conversation",
        outfit_occasion="casual smart weekend",
        slots=("top", "bottom", "shoes"),
        queries=(
            ("top", "smart casual blouse"),
            ("bottom", "tailored trousers"),
            ("shoes", "classic loafers"),
        ),
        men_queries=(("top", "smart casual shirt"), ("bottom", "chinos")),
        books=(
            BookSearch(mood="Thought-provoking", min_rating=4.2),
            BookSearch(genres=("Literary fiction",), min_rating=4.0),
        ),
        drink_words=(
            "whiskey",
            "scotch",
            "tea",
            "vermouth",
            "sherry",
            "port",
            "bitters",
            "wine",
            "coffee",
        ),
        drink_feeling="to sip slowly",
    ),
    Theme(
        id="adventurous",
        label="Adventurous",
        icon="explore",
        words=(
            "adventure",
            "adventurous",
            "trip",
            "travel",
            "camping",
            "hike",
            "hiking",
            "road trip",
            "explore",
            "exploring",
            "campfire",
            "mountain",
            "journey",
        ),
        feeling="an evening with a sense of adventure",
        outfit_occasion="casual autumn weekend trip",
        slots=("top", "bottom", "outerwear", "shoes"),
        queries=(
            ("top", "utility shirt"),
            ("bottom", "cargo trousers"),
            ("outerwear", "rain jacket"),
            ("shoes", "sturdy boots"),
        ),
        books=(
            BookSearch(mood="Adventurous", min_rating=4.2, exclude_genres=("Young adult",)),
            BookSearch(genres=("Adventure",), min_rating=4.0),
        ),
        drink_words=(
            "rum",
            "ginger",
            "pineapple",
            "tequila",
            "coconut",
            "lime",
            "spiced",
            "dark rum",
        ),
        drink_feeling="bold and spiced",
    ),
    Theme(
        id="playful",
        label="Playful",
        icon="celebration",
        words=(
            "game night",
            "games",
            "board games",
            "friends",
            "girls night",
            "girls' night",
            "boys night",
            "boys' night",
            "baby shower",
            "kids party",
            "kids' party",
            "comedy",
            "laugh",
            "laughs",
            "fun",
            "playful",
            "karaoke",
            "quiz",
            "silly",
        ),
        feeling="an evening of laughs",
        outfit_occasion="casual weekend with friends",
        slots=("top", "bottom", "shoes", "accessory"),
        queries=(
            ("top", "colourful printed t-shirt"),
            ("bottom", "relaxed jeans"),
            ("shoes", "colourful sneakers"),
            ("accessory", "fun earrings"),
        ),
        men_queries=(("accessory", "cap"),),
        books=(
            BookSearch(mood="Funny", min_rating=4.0, exclude_genres=("Graphic novels",)),
            BookSearch(genres=("Humour",)),
        ),
        drink_words=(
            "fruit",
            "grenadine",
            "punch",
            "cherry",
            "blue curacao",
            "juice",
            "peach",
            "strawberry",
            "watermelon",
        ),
        drink_feeling="fruity and fun",
    ),
)
THEMES_BY_ID: dict[str, Theme] = {theme.id: theme for theme in THEMES}
# Tried after a theme's own searches, so an evening always has a well-rated book.
FALLBACK_BOOKS = BookSearch(min_rating=4.0)
# With no theme word in the occasion or mood, an evening is planned cosy.
DEFAULT_THEME = THEMES_BY_ID["cosy"]


# Occasions that are never romantic or glamorous, whatever else they say: with kids, with
# family, or for work. Each has a theme to fall back on when nothing else names one.
Guard = Literal["kids", "family", "work"]
GUARDS: dict[Guard, tuple[str, ...]] = {
    "kids": (
        "kids",
        "kid",
        "kids'",
        "kiddies",
        "children",
        "children's",
        "childrens",
        "child",
        "toddler",
        "toddlers",
        "little ones",
        "family-friendly",
        "family friendly",
        "sleepover",
        "playdate",
    ),
    "family": (
        "family",
        "families",
        "parents",
        "parent",
        "mum",
        "mom",
        "mother",
        "dad",
        "father",
        "in-laws",
        "in laws",
        "grandparents",
        "grandma",
        "grandpa",
        "granny",
        "relatives",
        "baby shower",
        "christening",
        "baptism",
    ),
    "work": (
        "work",
        "business",
        "client",
        "clients",
        "colleague",
        "colleagues",
        "coworkers",
        "co-workers",
        "office",
        "team",
        "networking",
        "conference",
        "boss",
        "meeting",
    ),
}
GUARDED_THEMES: frozenset[str] = frozenset({"romantic", "glamorous"})
GUARD_FALLBACK: dict[Guard, ThemeId] = {
    "kids": "playful",
    "family": "thoughtful",
    "work": "thoughtful",
}
GUARD_LABELS: dict[Guard, str] = {
    "kids": "an evening with kids",
    "family": "a family occasion",
    "work": "a work occasion",
}


@dataclass(frozen=True)
class ThemeMatch:
    theme: Theme
    # The words that chose it, as written ("rainy", "Sunday"); empty for the default.
    words: tuple[str, ...]
    source: Literal["mood", "occasion", "guard", "default"]
    # Why romance and glamour are off the table ("kids", "family", "work"), if they are.
    guard: Guard | None = None

    def evidence(self, occasion: str) -> str:
        """Why this theme, in plain words ("you mentioned rainy and Sunday"); it reads after
        the theme's label: "Cosy: you mentioned rainy and Sunday"."""
        if self.source == "mood" and self.words:
            return f"you asked for a {self.words[0].lower()} mood"
        if self.source == "guard" and self.guard is not None:
            said = _and_words(self.words[:2])
            return f"{GUARD_LABELS[self.guard]} (you mentioned {said})"
        if not self.words:
            return "the occasion names no theme"
        return f"you mentioned {_and_words(self.words[:3])}"


def _and_words(words: Sequence[str]) -> str:
    words = list(words)
    return ", ".join(words[:-1]) + " and " + words[-1] if len(words) > 1 else "".join(words)


def _pattern(words: Iterable[str]) -> re.Pattern[str]:
    return re.compile(
        r"(?<![\w'])("
        + "|".join(map(re.escape, sorted(words, key=len, reverse=True)))
        + r")(?![\w'])",
        re.IGNORECASE,
    )


_PATTERNS = {theme.id: _pattern(theme.words) for theme in THEMES}
_GUARD_PATTERNS = {guard: _pattern(words) for guard, words in GUARDS.items()}
# "not a date", "no kids tonight", "without alcohol is boring": a mention that says the
# opposite. Checked on the words just before a match, and on what follows it.
_NEGATED_BEFORE = re.compile(
    r"(?:\b(?:not|no|never|without|nothing|hate|hates|skip|isn't|isnt|aren't|wasn't|"
    r"don't|dont|doesn't|won't)\b|\bnot\s+(?:really|very|too)\b)"
    r"(?:\s+(?:a|an|the|any|really|very|so|too|being|into|for))?\s*[\w'-]*\s*$",
    re.IGNORECASE,
)
_NEGATED_AFTER = re.compile(
    r"^[\s,-]*(?:is|are|'s|feels?|sounds?|seems?)?\s*(?:so\s+|too\s+|really\s+)?"
    r"(?:boring|lame|dull|overrated|no fun|not (?:for me|fun|happening|my thing)|sucks?)\b",
    re.IGNORECASE,
)
NEGATION_WINDOW = 24


def negated(text: str, start: int, end: int) -> bool:
    """Whether the mention at text[start:end] is negated ("not a date", "no kids",
    "alcohol-free is boring")."""
    before = text[max(0, start - NEGATION_WINDOW) : start]
    return bool(_NEGATED_BEFORE.search(before) or _NEGATED_AFTER.match(text[end:]))


def mentions(pattern: re.Pattern[str], text: str) -> tuple[str, ...]:
    """The distinct words `pattern` finds in `text` that are not negated, as written (a
    typographic apostrophe counts as a plain one)."""
    text = text.replace("\u2019", "'")
    return tuple(
        dict.fromkeys(
            m.group(1) for m in pattern.finditer(text) if not negated(text, m.start(), m.end())
        )
    )


def guard_in(text: str) -> tuple[Guard, tuple[str, ...]] | None:
    """The first guard `text` mentions (kids before family before work), with its words."""
    for guard, pattern in _GUARD_PATTERNS.items():
        if words := mentions(pattern, text):
            return guard, words
    return None


def _matches(
    text: str, blocked: frozenset[str] = frozenset()
) -> list[tuple[Theme, tuple[str, ...]]]:
    """Themes whose words `text` uses (negated mentions aside), most words first (ties in
    table order); `blocked` themes never match."""
    found = []
    for position, theme in enumerate(THEMES):
        if theme.id in blocked:
            continue
        if words := mentions(_PATTERNS[theme.id], text):
            found.append((len(words), position, theme, words))
    found.sort(key=lambda item: (-item[0], item[1]))
    return [(theme, words) for _, _, theme, words in found]


def detect_theme(occasion: str, mood: str | None = None) -> ThemeMatch:
    """The theme of an evening: a mood that names one wins, then the occasion's words (the
    theme with most matching words, ties in table order), else the cosy default.

    An occasion with kids, with family or for work (`GUARDS`) is never romantic or
    glamorous, even when it says "dinner", "party" or "birthday", or the mood asks for it:
    its other words decide, else the guard's own theme (`GUARD_FALLBACK`).
    """
    found_guard = guard_in(f"{occasion} {mood or ''}")
    guard, guard_words = found_guard if found_guard else (None, ())
    blocked = GUARDED_THEMES if guard else frozenset()
    if mood and (found := _matches(mood, blocked)):
        return ThemeMatch(found[0][0], found[0][1], "mood", guard)
    if mood and (theme := THEMES_BY_ID.get(mood.strip().casefold())) and theme.id not in blocked:
        return ThemeMatch(theme, (mood.strip(),), "mood", guard)
    if found := _matches(occasion, blocked):
        return ThemeMatch(found[0][0], found[0][1], "occasion", guard)
    if guard is not None:
        return ThemeMatch(THEMES_BY_ID[GUARD_FALLBACK[guard]], guard_words, "guard", guard)
    return ThemeMatch(DEFAULT_THEME, (), "default")


def theme_ids() -> Sequence[str]:
    return [theme.id for theme in THEMES]
