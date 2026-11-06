"""In-memory access to the prepared book catalog (`data/books.parquet`)."""

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd

from curio.domains.books.models import Book
from curio.engine.data import DataFileMissingError

COVER_HOST = "images.gr-assets.com"
GOODREADS_BOOK_URL = "https://www.goodreads.com/book/show/{}"
COLUMNS = (
    "book_id",
    "goodreads_book_id",
    "title",
    "authors",
    "series",
    "series_position",
    "year",
    "language",
    "average_rating",
    "ratings_count",
    "pages",
    "description",
    "genres",
    "tags",
    "image_url",
    "readers",
)
# Written by newer prepare runs; a catalog prepared before them simply has no ISBNs.
OPTIONAL_COLUMNS = ("isbn",)
OUTPUT_COLUMNS = (*COLUMNS, *OPTIONAL_COLUMNS)
NEIGHBOUR_COLUMNS = ("book_id", "neighbour_id", "score", "co_readers")
# Open Library's cover by ISBN; "default=false" makes a missing cover a 404, not a blank image.
OPEN_LIBRARY_COVER = "https://covers.openlibrary.org/b/isbn/{}-M.jpg?default=false"
OPEN_LIBRARY_BOOK = "https://openlibrary.org/isbn/{}"
_ISBN13 = re.compile(r"97[89]\d{10}")

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]")


class BookDataMissingError(DataFileMissingError):
    """`data/books.parquet` or `data/book_neighbours.parquet` has not been prepared yet."""

    def __init__(self, path: Path) -> None:
        FileNotFoundError.__init__(
            self,
            f"Book data not found: {path}. Prepare it with "
            "`uv run python -m scripts.prepare_books` (see README).",
        )


def name_key(name: str) -> str:
    """Case, accent, punctuation and spacing-insensitive form of a title or author name
    ("J.K. Rowling" -> "jk rowling", "Gabriel García Márquez" -> "gabriel garcia marquez")."""
    folded = name.casefold()
    if not folded.isascii():
        folded = unicodedata.normalize("NFKD", folded)
        folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub("", folded)).strip()


def trusted_cover_url(url: object) -> str | None:
    """`url` when it is an https cover on Goodreads' image host, else None (the "no photo"
    placeholders live on another host and are dropped too)."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.hostname != COVER_HOST or parts.port is not None:
        return None
    if parts.username or parts.password:
        return None
    return url


def isbn13(isbn: object = None, isbn13_text: object = None) -> str | None:
    """A valid ISBN-13 from goodbooks-10k's mangled columns, or None.

    books.csv stores the ISBN-10 as a number, so leading zeros are lost ("439023483"); it is
    padded, its check digit verified and converted to ISBN-13. The ISBN-13 column is a float
    rounded to 12 significant digits ("9.78055380468e+12" for 9780553804676), so it cannot
    be trusted and only an exact, checked 13-digit value is used.
    """
    ten = isbn.strip().upper().zfill(10) if isinstance(isbn, str) else ""
    if re.fullmatch(r"\d{9}[\dX]", ten) and _isbn10_valid(ten):
        return _with_check_digit("978" + ten[:9])
    text = isbn13_text.strip() if isinstance(isbn13_text, str) else ""
    if re.fullmatch(r"97[89]\d{10}", text) and _with_check_digit(text[:12]) == text:
        return text
    return None


def _isbn10_valid(ten: str) -> bool:
    values = [10 if c == "X" else int(c) for c in ten]
    return "X" not in ten[:9] and sum((10 - i) * v for i, v in enumerate(values)) % 11 == 0


def _with_check_digit(twelve: str) -> str:
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(twelve))
    return twelve + str((10 - total % 10) % 10)


def open_library_cover_url(isbn: str | None) -> str | None:
    """The Open Library cover URL for a valid ISBN-13 (built here, never read from data)."""
    if not isinstance(isbn, str) or not _ISBN13.fullmatch(isbn):
        return None
    return OPEN_LIBRARY_COVER.format(isbn)


class BookCatalog:
    def __init__(self, frame: pd.DataFrame) -> None:
        missing = [column for column in COLUMNS if column not in frame.columns]
        if missing:
            raise ValueError(f"Book catalog is missing columns: {', '.join(missing)}")
        frame = frame.assign(
            **{column: None for column in OPTIONAL_COLUMNS if column not in frame.columns}
        )
        books = [_book(record) for record in frame[list(OUTPUT_COLUMNS)].to_dict("records")]
        self._by_id: dict[int, Book] = {}
        for book in sorted(books, key=lambda b: b.book_id):
            if book.book_id in self._by_id:
                raise ValueError(f"Duplicate book_id {book.book_id} in book catalog")
            self._by_id[book.book_id] = book
        self._authors = _canonical_authors(self._by_id.values())
        self._by_author: dict[str, list[Book]] = {}
        for book in self._by_id.values():
            for author in dict.fromkeys(name_key(a) for a in book.authors):
                self._by_author.setdefault(author, []).append(book)
        self._genres = Counter(genre for book in self._by_id.values() for genre in book.genres)
        self._titles: dict[str, list[int]] = {}
        for book in self._by_id.values():
            self._titles.setdefault(name_key(book.title), []).append(book.book_id)

    @classmethod
    def from_parquet(cls, path: Path) -> "BookCatalog":
        if not path.exists():
            raise BookDataMissingError(path)
        return cls(pd.read_parquet(path))

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Book]:
        """Books in book_id order (goodbooks-10k numbers books by popularity)."""
        return iter(self._by_id.values())

    def get(self, book_id: int) -> Book | None:
        return self._by_id.get(book_id)

    def get_many(self, book_ids: Iterable[int]) -> list[Book]:
        """Books in the given order, silently skipping unknown ids."""
        return [b for book_id in book_ids if (b := self.get(book_id)) is not None]

    def genres(self) -> list[str]:
        """Genres in use, most common first (ties by name)."""
        return sorted(self._genres, key=lambda genre: (-self._genres[genre], genre))

    def canonical_genre(self, name: str) -> str | None:
        key = name_key(name)
        return next((g for g in self._genres if name_key(g) == key), None)

    def canonical_author(self, name: str) -> str | None:
        """The catalog spelling of an author, ignoring case, accents and punctuation."""
        return self._authors.get(name_key(name))

    def by_author(self, name: str) -> list[Book]:
        """Every book listing `name` among its authors, in book_id (popularity) order."""
        return list(self._by_author.get(name_key(name), []))

    def by_title(self, title: str) -> list[Book]:
        """Books whose title (without the series suffix) equals `title`, ignoring case and
        punctuation; the most rated first."""
        books = self.get_many(self._titles.get(name_key(title), []))
        return sorted(books, key=lambda b: (-b.ratings_count, b.book_id))

    def authors(self) -> list[str]:
        """Every distinct author, in catalog spelling."""
        return sorted(self._authors.values())

    def titles(self) -> list[str]:
        """Every distinct title, for spotting titles in text."""
        return sorted({b.title for b in self._by_id.values()})


def _book(record: dict) -> Book:
    return Book(
        book_id=int(record["book_id"]),
        title=_text(record["title"]),
        authors=_list(record["authors"]),
        series=_text(record["series"]) or None,
        series_position=_text(record["series_position"]) or None,
        year=_int(record["year"]),
        language=_text(record["language"]) or None,
        average_rating=float(record["average_rating"]),
        ratings_count=int(record["ratings_count"]),
        pages=_int(record["pages"]),
        description=_text(record["description"]),
        genres=_list(record["genres"]),
        tags=_list(record["tags"]),
        image_url=trusted_cover_url(record["image_url"]),
        isbn=isbn13(None, _text(record["isbn"])),
        source_url=GOODREADS_BOOK_URL.format(int(record["goodreads_book_id"])),
        readers=_int(record["readers"]) or 0,
    )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _list(value: object) -> list[str]:
    items = json.loads(value) if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise ValueError(f"Expected a JSON list of strings, got {value!r:.80}")
    return [item.strip() for item in items if item.strip()]


def _canonical_authors(books: Iterable[Book]) -> dict[str, str]:
    """Author key -> the most used spelling (ties: the alphabetically first)."""
    counts: dict[str, Counter[str]] = {}
    for book in books:
        for author in book.authors:
            counts.setdefault(name_key(author), Counter())[author] += 1
    return {
        key: min(spellings, key=lambda name: (-spellings[name], name))
        for key, spellings in counts.items()
        if key
    }
