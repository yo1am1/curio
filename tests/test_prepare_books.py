"""scripts.prepare_books: reading goodbooks-10k, cleaning books and precomputing neighbours."""

import zipfile
from pathlib import Path

import httpx
import pandas as pd
import pytest

from clothes_recommender.books.catalog import COLUMNS, NEIGHBOUR_COLUMNS, BookCatalog
from clothes_recommender.books.recommend import BookRecommender
from scripts import prepare_books
from scripts.prepare_books import (
    BOOKS_CSV,
    DEFAULT_SOURCE,
    SourceFile,
    clean_description,
    describe,
    fetch,
    main,
    neighbours,
    parse_args,
    prepare,
    read_details,
    split_authors,
    split_title,
)

BOOKS_HEADER = (
    "book_id,goodreads_book_id,best_book_id,work_id,books_count,isbn,isbn13,authors,"
    "original_publication_year,original_title,title,language_code,average_rating,ratings_count,"
    "work_ratings_count,work_text_reviews_count,ratings_1,ratings_2,ratings_3,ratings_4,"
    "ratings_5,image_url,small_image_url"
)
COVER = "https://images.gr-assets.com/books/1{0}m/{0}.jpg"
NO_COVER = "https://s.gr-assets.com/assets/nophoto/book/111x148-bcc042a9c9.png"


def _book(book_id: int, title: str, authors: str, year: str, cover: str) -> str:
    return (
        f'{book_id},{100 + book_id},0,0,1,,,"{authors}",{year},,"{title}",eng,4.1,1000,0,0,'
        f"0,0,0,0,0,{cover},{cover}"
    )


def _xml(goodreads_id: int, pages: str, description: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><GoodreadsResponse><book>'
        f"<id>{goodreads_id}</id><description><![CDATA[{description}]]></description>"
        f"<num_pages><![CDATA[{pages}]]></num_pages></book></GoodreadsResponse>"
    )


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A miniature goodbooks-10k checkout with every file prepare_books reads."""
    root = tmp_path / "goodbooks-10k"
    (root / "books_xml").mkdir(parents=True)
    books = [
        _book(1, "The Name of the Wind (The Kingkiller Chronicle, #1)", "Patrick Rothfuss",
              "2007.0", COVER.format(1)),
        _book(2, "The Wise Man's Fear (The Kingkiller Chronicle, #2)", "Patrick Rothfuss",
              "2011.0", COVER.format(2)),
        _book(3, "The Way of Kings (The Stormlight Archive, #1)", "Brandon Sanderson",
              "2010.0", NO_COVER),
        _book(4, "The Iliad", "Homer, Robert Fagles", "-750.0", COVER.format(4)),
        _book(5, "Unloved", "Nobody Known", "", COVER.format(5)),
    ]  # fmt: skip
    (root / "books.csv").write_text("\n".join([BOOKS_HEADER, *books]) + "\n")
    ratings = ["user_id,book_id,rating"]
    for user in range(1, 9):
        ratings += [f"{user},1,5", f"{user},2,4"]
    for user in range(1, 7):
        ratings.append(f"{user},3,5")
    for user in range(1, 4):
        ratings.append(f"{user},4,4")  # only three shared readers: below MIN_CO_READERS
    ratings += ["9,5,2", "9,5,1"]  # disliked, and rated twice
    (root / "ratings.csv").write_text("\n".join(ratings) + "\n")
    (root / "tags.csv").write_text(
        "tag_id,tag_name\n0,to-read\n1,fantasy\n2,epic-fantasy\n3,classics\n4,read-in-2015\n"
        "5,magic\n"
    )
    (root / "book_tags.csv").write_text(
        "goodreads_book_id,tag_id,count\n"
        "101,0,9000\n101,1,800\n101,2,300\n101,5,200\n101,4,100\n"
        "102,1,500\n102,2,100\n103,1,400\n104,3,600\n104,0,50\n"
    )
    with zipfile.ZipFile(root / "books_xml" / "books_xml.zip", "w") as archive:
        archive.writestr("books_xml/101.xml", _xml(101, "662", "<b>Kvothe</b> &amp; magic."))
        archive.writestr("books_xml/104.xml", _xml(104, "", "The wrath of Achilles."))
        archive.writestr("books_xml/README.txt", "not a book")
        archive.writestr(
            "books_xml/102.xml",
            '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><GoodreadsResponse/>',
        )
        archive.writestr("books_xml/103.xml", "<GoodreadsResponse><book>")  # truncated
    return root


def test_prepare_writes_both_files_that_the_catalog_and_recommender_load(source, tmp_path):
    out = tmp_path / "data"
    report = prepare(str(source), out, verify=False)
    assert (report.books, report.with_cover, report.with_pages, report.with_genres) == (5, 4, 1, 4)
    frame = pd.read_parquet(out / "books.parquet")
    assert list(frame.columns) == list(COLUMNS)
    links = pd.read_parquet(out / "book_neighbours.parquet")
    assert list(links.columns) == list(NEIGHBOUR_COLUMNS)

    catalog = BookCatalog.from_parquet(out / "books.parquet")
    wind = catalog.get(1)
    assert (wind.title, wind.series, wind.series_position) == (
        "The Name of the Wind",
        "The Kingkiller Chronicle",
        "1",
    )
    assert (wind.pages, wind.description, wind.genres) == (662, "Kvothe & magic.", ["Fantasy"])
    assert wind.tags == ["fantasy", "epic fantasy", "magic"]  # no "to-read", no "read-in-2015"
    assert wind.readers == 8
    iliad = catalog.get(4)
    assert (iliad.year, iliad.authors, iliad.pages) == (-750, ["Homer", "Robert Fagles"], None)
    assert catalog.get(3).image_url is None  # the "no photo" placeholder is dropped
    assert catalog.get(5).year is None and catalog.get(5).genres == []
    assert catalog.get(5).readers == 1  # the repeated rating counts once

    recommender = BookRecommender(catalog, links)
    assert [h.book.book_id for h in recommender.similar(1, 3)] == [2, 3]
    assert recommender.similar(1, 3)[0].reasons[0].startswith("8 readers who liked")


def test_prepare_without_details_skips_the_archive(source, tmp_path):
    (source / "books_xml" / "books_xml.zip").unlink()
    report = prepare(str(source), tmp_path / "data", details=False, verify=False)
    assert report.with_pages == 0


def test_local_copies_are_checked_against_the_pinned_hashes(source, tmp_path):
    with pytest.raises(ValueError, match=r"SHA-256 mismatch for books\.csv"):
        prepare(str(source), tmp_path / "data")
    assert not (tmp_path / "data" / "books.parquet").exists()


def test_neighbours_damp_popularity_and_drop_thin_links():
    ratings = pd.DataFrame(
        [(u, b, 5) for u in range(1, 21) for b in (1, 2)]  # 20 readers like 1 and 2
        + [(u, 3, 5) for u in range(1, 6)]  # 5 of them like 3 too
        + [(u, 4, 5) for u in range(1, 5)]  # 4 like 4: too few to link
        + [(u, 5, 3) for u in range(1, 21)],  # 3 stars is not "liked"
        columns=["user_id", "book_id", "rating"],
    )
    links = neighbours(ratings, [1, 2, 3, 4, 5])
    from_three = links[links["book_id"] == 3]
    assert from_three["neighbour_id"].tolist() == [1, 2]  # tie on score: lower id first
    assert from_three["co_readers"].tolist() == [5, 5]
    assert not links["neighbour_id"].isin([4, 5]).any()
    assert not (links["book_id"] == links["neighbour_id"]).any()
    one = links[links["book_id"] == 1].set_index("neighbour_id")["score"]
    # Asymmetric cosine with shrinkage: 20 / (20^0.35 * 20^0.65) * 20 / 30 for 1 -> 2.
    assert one[2] == pytest.approx(20 / 30, rel=1e-5)
    assert one[3] == pytest.approx(5 / (20**0.35 * 5**0.65) * 5 / 15, rel=1e-5)
    assert neighbours(ratings[ratings["rating"] < 4], [1, 2]).empty


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Catching Fire (The Hunger Games, #2)", ("Catching Fire", "The Hunger Games", "2")),
        ("Angels & Demons  (Robert Langdon, #1)", ("Angels & Demons", "Robert Langdon", "1")),
        ("The Complete Anne (Anne of Green Gables, #1-8)", ("The Complete Anne",
                                                             "Anne of Green Gables", "1-8")),
        ("Animal Farm", ("Animal Farm", None, None)),
        ("(Untitled, #1)", ("(Untitled, #1)", None, None)),
    ],
)  # fmt: skip
def test_split_title(title, expected):
    assert split_title(title) == expected


def test_split_authors_and_describe():
    assert split_authors("J.K. Rowling, Mary GrandPré,  J.K. Rowling") == [
        "J.K. Rowling",
        "Mary GrandPré",
    ]
    genres, tags = describe({"to-read": 900, "sci-fi": 100, "dystopia": 60, "kindle": 50})
    assert genres == ["Science fiction", "Dystopian"]
    assert tags == ["sci fi", "dystopia"]


def test_clean_description_strips_html_and_cuts_at_a_word(monkeypatch):
    assert clean_description("<p>Fire &amp; <i>blood</i></p>") == "Fire & blood"
    monkeypatch.setattr(prepare_books, "MAX_DESCRIPTION", 12)
    assert clean_description("one two three, four five") == "one two…"


def test_details_skip_oversized_doctype_and_broken_members(source, monkeypatch):
    archive = source / "books_xml" / "books_xml.zip"
    assert read_details(archive) == {
        101: (662, "Kvothe & magic."),
        104: (None, "The wrath of Achilles."),
    }
    monkeypatch.setattr(prepare_books, "MAX_XML_MEMBER", 10)
    assert read_details(archive) == {}


def _mock_stream(monkeypatch, handler) -> list[str]:
    requested: list[str] = []

    def stream(method: str, url: str, **kwargs):
        requested.append(url)
        assert kwargs["timeout"] is not None
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.stream(method, url)

    monkeypatch.setattr(prepare_books.httpx, "stream", stream)
    return requested


def test_fetch_downloads_checks_and_limits(tmp_path, monkeypatch):
    body = b"book_id\n1\n"
    import hashlib

    file = SourceFile("books.csv", hashlib.sha256(body).hexdigest(), 100)
    requested = _mock_stream(monkeypatch, lambda request: httpx.Response(200, content=body))
    assert fetch(DEFAULT_SOURCE, file, tmp_path / "a.csv").read_bytes() == body
    assert requested == [f"{DEFAULT_SOURCE}/books.csv"]
    with pytest.raises(ValueError, match="larger than 4 bytes"):
        fetch(DEFAULT_SOURCE, SourceFile("books.csv", file.sha256, 4), tmp_path / "b.csv")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        fetch(DEFAULT_SOURCE, BOOKS_CSV, tmp_path / "c.csv")
    assert fetch(DEFAULT_SOURCE, BOOKS_CSV, tmp_path / "d.csv", verify=False).exists()
    _mock_stream(monkeypatch, lambda request: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        fetch(DEFAULT_SOURCE, file, tmp_path / "e.csv")
    with pytest.raises(ValueError, match="Only local folders and https"):
        fetch("http://example.com", file, tmp_path / "f.csv")


def test_main_reports_failures_with_exit_code_one(tmp_path, source, capsys):
    assert parse_args([]).source == DEFAULT_SOURCE
    with pytest.raises(SystemExit) as stopped:
        main(["--source", str(tmp_path / "missing"), "--data-dir", str(tmp_path / "out")])
    assert stopped.value.code == 1
    main(["--source", str(source), "--no-verify", "--data-dir", str(tmp_path / "out")])
    assert "Wrote 5 books" in capsys.readouterr().out
