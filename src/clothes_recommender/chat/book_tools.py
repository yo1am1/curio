"""Validated actions available to the book assistant, each with an execution policy.

The schemas follow the fashion and cocktail tools: strict, bounded, and executed only under
their policy. AskShopper is shared with the other assistants.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints, model_validator

from clothes_recommender.chat.tools import AskShopper, Policy

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
AuthorName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class SearchBooks(BaseModel):
    """Search the book catalog by description (title, author, topic, genre or mood words),
    with optional filters: genres (all must apply), a mood ('Cosy', 'Dark', 'Funny',
    'Romantic', 'Thought-provoking', 'Adventurous', 'Emotional', 'Epic', 'Short reads'),
    minimum Goodreads rating and maximum page count. 'like <title>' in the query finds books
    that readers of that title also liked. The reader's remembered tastes (avoided genres and
    authors, liked genres) are applied automatically."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=200)
    genres: list[Name] = Field(default_factory=list, max_length=3)
    mood: Name | None = None
    min_rating: float | None = Field(default=None, ge=1, le=5)
    max_pages: int | None = Field(default=None, ge=20, le=2000)

    @model_validator(mode="after")
    def _not_empty(self) -> "SearchBooks":
        if not (self.query.strip() or self.genres or self.mood):
            raise ValueError("Give a query, genres or a mood")
        return self


class InspectBooks(BaseModel):
    """Read full details (description, genres, shelves, rating, pages, year, series) for up
    to five book IDs."""

    model_config = ConfigDict(extra="forbid")
    book_ids: list[PositiveInt] = Field(min_length=1, max_length=5)


class ReadersAlsoLiked(BaseModel):
    """Find books that readers who liked one catalog book also liked (collaborative
    filtering over real ratings). `other_authors_only` leaves out the same author's books."""

    model_config = ConfigDict(extra="forbid")
    book_id: PositiveInt
    other_authors_only: bool = True


class BooksByAuthor(BaseModel):
    """List an author's books in the catalog, most rated first."""

    model_config = ConfigDict(extra="forbid")
    author: AuthorName


class SaveBooks(BaseModel):
    """Propose saving catalog books to the reader's reading list. The reader must approve
    it before anything is saved; this ends your turn. Only propose when they ask to save,
    keep or add books to their list."""

    model_config = ConfigDict(extra="forbid")
    book_ids: list[PositiveInt] = Field(min_length=1, max_length=5)
    note: str = Field(default="", max_length=200)


class RememberReadingTastes(BaseModel):
    """Propose remembering reading tastes for later searches in this session: liked genres
    (ranked higher), avoided genres and avoided authors (left out of every result). The
    reader must approve it first; this ends your turn. Only propose tastes the reader stated
    as lasting ('I love fantasy', 'no horror, ever', 'I can't stand Dan Brown')."""

    model_config = ConfigDict(extra="forbid")
    liked_genres: list[Name] = Field(default_factory=list, max_length=10)
    avoided_genres: list[Name] = Field(default_factory=list, max_length=10)
    avoided_authors: list[AuthorName] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _not_empty(self) -> "RememberReadingTastes":
        if not (self.liked_genres or self.avoided_genres or self.avoided_authors):
            raise ValueError("Propose at least one taste")
        return self


BOOK_TOOLS: dict[str, type[BaseModel]] = {
    schema.__name__: schema
    for schema in (
        SearchBooks,
        InspectBooks,
        ReadersAlsoLiked,
        BooksByAuthor,
        AskShopper,
        SaveBooks,
        RememberReadingTastes,
    )
}

BOOK_POLICIES: dict[str, Policy] = {
    "SearchBooks": Policy.READ,
    "InspectBooks": Policy.READ,
    "ReadersAlsoLiked": Policy.READ,
    "BooksByAuthor": Policy.READ,
    "AskShopper": Policy.ASK,
    "SaveBooks": Policy.APPROVE,
    "RememberReadingTastes": Policy.APPROVE,
}
