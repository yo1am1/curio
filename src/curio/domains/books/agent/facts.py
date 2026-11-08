"""Book facts an answer cites by reference: a book's title, author, rating, year and pages.

    {book:3}                 **The Hobbit**
    {author:book:3}          J.R.R. Tolkien (the first listed author, as the cards show)
    {rating:book:3}          4.25 (the Goodreads average)
    {year:book:3}            1937 (the original publication year; "450 BC" before year 1)
    {pages:book:3}           366
    {total:pages:3,5}        1,124
    {diff:rating:3,5}        0.17 (the larger minus the smaller)

The kind may be left out ({rating:3}): ratings, authors, years and pages are books'.
"""

from collections.abc import Iterable

from curio.domains.books.models import Book
from curio.engine.facts import Attribute, FactSheet, ItemKind


def _year(year: int) -> str:
    return f"{-year} BC" if year < 0 else str(year)


def _rating(value: float) -> str:
    return f"{value:.2f}"


def _pages(value: int) -> str:
    return f"{value:,}"


BOOK_ATTRIBUTES = {
    "author": Attribute("author", "book", lambda b, _: b.author, str),
    "rating": Attribute("rating", "book", lambda b, _: b.average_rating, _rating, difference=True),
    "year": Attribute(
        "year", "book", lambda b, _: b.year, _year, difference=True, show_derived=str
    ),
    "pages": Attribute(
        "pages", "book", lambda b, _: b.pages or None, _pages, total=True, difference=True
    ),
}


def book_facts(books: Iterable[Book]) -> FactSheet:
    """The fact sheet for `books` (the turn's evidence)."""
    kind = ItemKind(lambda b: b.title, {b.book_id: b for b in books})
    return FactSheet({"book": kind}, BOOK_ATTRIBUTES)


# How the answer prompts explain references to the model.
BOOK_REFERENCES = (
    "{book:ID} for a book's title (rendered in bold, so do not add ** around it), "
    "{author:book:ID} for its author, {rating:book:ID} for its Goodreads rating (write "
    "★ {rating:book:ID}), {year:book:ID} for its publication year, {pages:book:ID} for its "
    "page count (write {pages:book:ID} pages), {total:pages:ID,ID,...} for several books' "
    "pages together and {diff:rating:ID,ID} or {diff:pages:ID,ID} for the difference of two"
)
# Drinks a pairing returned (the pairings domain renders them; this is only how the book
# prompt explains them, since the book domain does not import the cocktail domain).
PAIRED_DRINK_REFERENCES = (
    "{drink:N} for a paired drink's name, {alcohol:drink:N} for its alcohol label, "
    "{recipe:drink:N} for its ingredients with their amounts and "
    "{amount:drink:N:Ingredient} for one ingredient's amount"
)
