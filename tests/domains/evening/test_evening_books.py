"""How an evening chooses its book: mainstream, well-known, the first of a series, safe."""

import pandas as pd

from curio.domains.books.catalog import BookCatalog
from curio.domains.books.recommend import BookRecommender
from curio.domains.books.taxonomy import EXCLUDED_SHELVES
from curio.domains.evening.models import EveningRequest
from curio.domains.evening.planner import EveningPlanner
from tests.conftest import book_frame

R, C = "Romance", "Classics"
ROWS = [
    # A sexual bestseller: the most popular romance here, and never an evening's pick.
    (1, "Shades of Something", ["E. Writer"], None, None, 2011,
     4.3, 3_000_000, 500, [R], ["romance", "erotica", "bdsm"], True),
    (2, "Pride and Prejudice", ["Jane Austen"], None, None, 1813,
     4.24, 2_000_000, 279, [C, R], ["classic", "romance"], True),
    (3, "Persuasion", ["Jane Austen"], None, None, 1817,
     4.13, 365_000, 249, [C, R], ["classic", "romance"], True),
    # Better rated, but hardly anyone knows it.
    (4, "An Obscure Gem", ["Some Body"], None, None, 1890,
     4.6, 9_000, 300, [C, R], ["classic", "romance"], True),
    # Popular, but the third book of a series.
    (5, "Love Again, Part Three", ["Ser Ial"], "Love Again", "3", 1999,
     4.5, 900_000, 400, [C, R], ["romance", "classic"], True),
    (6, "Rebecca", ["Daphne du Maurier"], None, None, 1938,
     4.2, 177_000, 410, [C, R], ["classic", "romance", "gothic"], True),
    (7, "A Picture Book of Hearts", ["Kid Author"], None, None, 2001,
     4.4, 700_000, 32, ["Children's", R], ["picture books", "childrens"], True),
]  # fmt: skip


def planner(rows=ROWS) -> EveningPlanner:
    catalog = BookCatalog(book_frame(rows))
    frame = pd.DataFrame(columns=["book_id", "neighbour_id", "score", "co_readers"])
    return EveningPlanner(books=BookRecommender(catalog, frame), book_catalog=catalog)


def book(evening):
    return evening.book.book.title


def test_a_date_night_reads_the_classic_love_story_everyone_knows():
    date = planner().plan(EveningRequest(occasion="date night"))
    assert book(date) == "Pride and Prejudice"
    titles = []
    state = date.state
    for _ in range(3):
        swapped = planner().swap(state, "book")
        titles.append(book(swapped))
        state = swapped.state
    # Well-known before obscure, a series' opener before its third book.
    assert titles == ["Persuasion", "Rebecca", "Love Again, Part Three"]


def test_no_evening_book_carries_an_excluded_shelf():
    excluded = set(EXCLUDED_SHELVES)
    for occasion in ("date night", "anniversary", "romantic dinner for two", "rainy Sunday in"):
        evening = planner().plan(EveningRequest(occasion=occasion))
        assert not set(evening.book.book.tags) & excluded, occasion


def test_the_popularity_floor_falls_back_when_no_book_reaches_it():
    niche = [row for row in ROWS if row[0] in (4,)]
    assert book(planner(niche).plan(EveningRequest(occasion="date night"))) == "An Obscure Gem"


def test_an_evening_with_kids_reads_a_childrens_book_and_others_never_do():
    assert book(planner().plan(EveningRequest(occasion="kids birthday party"))) == (
        "A Picture Book of Hearts"
    )
    assert book(planner().plan(EveningRequest(occasion="date night"))) != (
        "A Picture Book of Hearts"
    )
