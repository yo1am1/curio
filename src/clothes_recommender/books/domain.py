"""Book domain types shared by the catalog, recommender, agent, API and UI.

Books come from a prepared, offline catalog (`data/books.parquet` and
`data/book_neighbours.parquet`, built by `scripts.prepare_books`) derived from goodbooks-10k.
"""

from typing import Annotated

from pydantic import BaseModel, Field, PositiveInt, StringConstraints

SOURCE_NAME = "goodbooks-10k"
# One genre, mood or author name in a filter or a remembered taste: stripped and bounded, so
# a client cannot send huge strings into search filters or the assistant's prompt.
GenreTerm = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
AuthorTerm = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Book(BaseModel):
    book_id: PositiveInt
    title: str  # without the "(Series, #1)" suffix Goodreads adds
    authors: list[str] = Field(min_length=1)
    series: str | None = None
    series_position: str | None = None  # "1", "2.5" or "1-3" as Goodreads lists it
    year: int | None = None  # original publication year; negative for BC
    language: str | None = None
    average_rating: float = Field(ge=0, le=5)  # Goodreads average, 1-5 stars
    ratings_count: int = Field(ge=0)  # Goodreads ratings behind that average
    pages: int | None = None
    description: str = ""
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)  # descriptive reader shelves, most used first
    # Only https covers on images.gr-assets.com are kept; placeholders become None.
    image_url: str | None = None
    source_url: str  # the book's Goodreads page
    readers: int = Field(default=0, ge=0)  # goodbooks-10k readers who rated it

    @property
    def author(self) -> str:
        """The first listed author (Goodreads lists translators and illustrators after)."""
        return self.authors[0]


class BookRef(BaseModel):
    """A book's id, title and first author, for pickers that list the whole catalog."""

    book_id: PositiveInt
    title: str
    author: str


class BookHit(BaseModel):
    book: Book
    score: float
    reasons: list[str] = Field(default_factory=list)


class BookPreferences(BaseModel):
    """Approved, session-scoped reading tastes the client sends back with each message."""

    liked_genres: list[GenreTerm] = Field(default_factory=list, max_length=10)
    avoided_genres: list[GenreTerm] = Field(default_factory=list, max_length=10)
    avoided_authors: list[AuthorTerm] = Field(default_factory=list, max_length=10)

    def is_empty(self) -> bool:
        return not (self.liked_genres or self.avoided_genres or self.avoided_authors)


class BookQuery(BaseModel):
    query: str = Field(default="", max_length=200)
    genres: list[GenreTerm] = Field(default_factory=list, max_length=5)  # all must apply
    mood: GenreTerm | None = None
    author: AuthorTerm | None = None
    min_rating: float | None = Field(default=None, ge=0, le=5)
    max_pages: int | None = Field(default=None, ge=1, le=5000)
    exclude_genres: list[GenreTerm] = Field(default_factory=list, max_length=10)
    exclude_authors: list[AuthorTerm] = Field(default_factory=list, max_length=10)
    # Soft preference: matching books rank higher and say so; nothing is filtered out.
    boost_genres: list[GenreTerm] = Field(default_factory=list, max_length=10)
    top_k: PositiveInt = Field(default=10, le=50)


class BecauseYouLiked(BaseModel):
    """Recommendations seeded by books the reader liked ("Because you liked …")."""

    liked: list[PositiveInt] = Field(min_length=1, max_length=20)
    exclude_genres: list[GenreTerm] = Field(default_factory=list, max_length=10)
    exclude_authors: list[AuthorTerm] = Field(default_factory=list, max_length=10)
    top_k: PositiveInt = Field(default=10, le=50)


BookId = PositiveInt
