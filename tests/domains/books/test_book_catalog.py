"""Book catalog: loading the prepared parquet, lookups and the taxonomy behind it."""

import pytest

from curio.domains.books.catalog import (
    BookCatalog,
    BookDataMissingError,
    name_key,
    trusted_cover_url,
)
from curio.domains.books.taxonomy import genres_for, is_descriptive, shelf_label
from tests.conftest import book_frame


def test_catalog_loads_books_in_id_order_with_clean_fields(book_catalog):
    assert len(book_catalog) == 10
    assert [b.book_id for b in book_catalog][:3] == [1, 2, 3]
    wind = book_catalog.get(1)
    assert wind.title == "The Name of the Wind"
    assert wind.author == "Patrick Rothfuss"
    assert (wind.series, wind.series_position, wind.pages, wind.year) == (
        "The Kingkiller Chronicle",
        "1",
        662,
        2007,
    )
    assert wind.source_url == "https://www.goodreads.com/book/show/1001"
    assert wind.image_url == "https://images.gr-assets.com/books/11m/1.jpg"
    assert book_catalog.get(9).image_url is None
    assert book_catalog.get(10).pages is None
    assert book_catalog.get(404) is None
    assert [b.book_id for b in book_catalog.get_many([5, 404, 1])] == [5, 1]


def test_authors_genres_and_titles_are_matched_loosely(book_catalog):
    assert book_catalog.canonical_author("patrick ROTHFUSS") == "Patrick Rothfuss"
    assert book_catalog.canonical_author("Nobody") is None
    assert [b.book_id for b in book_catalog.by_author("brandon sanderson")] == [3, 4]
    assert book_catalog.by_author("Russell Baker")[0].title == "Animal Farm"  # any listed author
    assert book_catalog.genres()[0] == "Fantasy"  # most common first
    assert book_catalog.canonical_genre("young ADULT") == "Young adult"
    assert book_catalog.by_title("the name of the wind")[0].book_id == 1
    assert "It" in book_catalog.titles()
    assert "George Orwell" in book_catalog.authors()


def test_missing_columns_duplicates_and_files_are_errors(tmp_path):
    with pytest.raises(ValueError, match="missing columns: tags"):
        BookCatalog(book_frame().drop(columns=["tags"]))
    frame = book_frame()
    frame.loc[1, "book_id"] = 1
    with pytest.raises(ValueError, match="Duplicate book_id 1"):
        BookCatalog(frame)
    with pytest.raises(BookDataMissingError, match="prepare_books"):
        BookCatalog.from_parquet(tmp_path / "books.parquet")
    broken = book_frame()
    broken.loc[0, "authors"] = '{"not": "a list"}'
    with pytest.raises(ValueError, match="JSON list of strings"):
        BookCatalog(broken)


def test_catalog_round_trips_through_parquet(tmp_path):
    path = tmp_path / "books.parquet"
    book_frame().to_parquet(path, index=False)
    assert BookCatalog.from_parquet(path).get(6).authors == ["George Orwell", "Russell Baker"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://images.gr-assets.com/books/1447303603m/2767052.jpg", True),
        ("http://images.gr-assets.com/books/1m/2.jpg", False),
        ("https://s.gr-assets.com/assets/nophoto/book/111x148.png", False),
        ("https://images.gr-assets.com:8443/books/1m/2.jpg", False),
        ("https://user@images.gr-assets.com/books/1m/2.jpg", False),
        ("javascript:alert(1)", False),
        (None, False),
    ],
)
def test_only_https_goodreads_covers_are_trusted(url, expected):
    assert (trusted_cover_url(url) is not None) is expected


def test_name_key_ignores_case_accents_and_punctuation():
    assert name_key("J.K. Rowling") == "jk rowling"
    assert name_key("  Gabriel García  Márquez ") == "gabriel garcia marquez"
    assert name_key("Ender's Game") == name_key("enders game")


@pytest.mark.parametrize(
    ("shelf", "descriptive"),
    [
        ("fantasy", True),
        ("epic-fantasy", True),
        ("book-club", True),
        ("to-read", False),
        ("currently-reading", False),
        ("read-in-2015", False),
        ("owned-books", False),
        ("kindle", False),
        ("favourites", False),
        ("fiction", False),  # too broad to tell books apart
        ("mythology", True),  # "my" only counts as a whole word
        ("Fantasy!", False),
    ],
)
def test_bookkeeping_shelves_are_not_tags(shelf, descriptive):
    assert is_descriptive(shelf) is descriptive


def test_genres_come_from_shelf_shares_strongest_first():
    shelves = {"to-read": 9000, "fantasy": 1000, "young-adult": 400, "ya": 300, "romance": 50}
    assert genres_for(shelves) == ["Fantasy", "Young adult"]  # romance is under 12 %
    assert genres_for({"to-read": 10}) == []
    assert shelf_label("historical-fiction") == "historical fiction"


def test_isbns_are_recovered_from_goodbooks_mangled_columns():
    from curio.domains.books.catalog import isbn13, open_library_cover_url

    # The ISBN-10 lost its leading zero. The ISBN-13 float is rounded to 12 digits
    # (9780553804676 -> 9.78055380468e+12), so it is never used.
    assert isbn13("439023483", "9.78043902348e+12") == "9780439023481"
    assert isbn13("553804677", "9.78055380468e+12") == "9780553804676"
    assert isbn13(None, "9.78055380468e+12") is None
    assert isbn13("080442957X") == "9780804429573"  # an X check digit
    assert isbn13(None, "9780439023481") == "9780439023481"
    for bad in (("0804429570", None), (None, "9780439023480"), ("12345", "nan"), (None, "1e12")):
        assert isbn13(*bad) is None, bad
    url = open_library_cover_url("9780439023481")
    assert url == "https://covers.openlibrary.org/b/isbn/9780439023481-M.jpg?default=false"
    for bad in (None, "", "978043902348", "9780439023481/../x", "1234567890123"):
        assert open_library_cover_url(bad) is None


def test_a_catalog_prepared_without_isbns_still_loads():
    from curio.domains.books.catalog import BookCatalog
    from tests.conftest import book_frame

    old = book_frame()
    assert "isbn" not in old.columns
    assert all(book.isbn is None for book in BookCatalog(old))
    current = old.assign(isbn=["9780439023481", None, "not an isbn", *[None] * (len(old) - 3)])
    books = list(BookCatalog(current))
    assert [b.isbn for b in books[:3]] == ["9780439023481", None, None]
