"""Content safety: erotica, BDSM and abuse shelves stay out of results nobody asked for."""

import pandas as pd
import pytest

from curio.domains.books.catalog import BookCatalog
from curio.domains.books.models import BookQuery
from curio.domains.books.recommend import BookRecommender
from curio.domains.books.taxonomy import EXCLUDED_SHELVES, asked_shelves
from tests.conftest import book_frame

ROWS = [
    (1, "Pride and Prejudice", ["Jane Austen"], None, None, 1813,
     4.24, 2_200_000, 279, ["Classics", "Romance"], ["classic", "romance", "regency"], True),
    (2, "Bared to You", ["Sylvia Day"], "Crossfire", "1", 2012,
     4.1, 220_000, 334, ["Romance"], ["romance", "erotica", "bdsm", "contemporary romance"], True),
    (3, "Reflected in You", ["Sylvia Day"], "Crossfire", "2", 2012,
     4.3, 250_000, 330, ["Romance"], ["romance", "erotic romance", "contemporary romance"], True),
    (4, "Entwined with You", ["Sylvia Day"], "Crossfire", "3", 2013,
     4.3, 140_000, 340, ["Romance"], ["romance", "contemporary romance"], True),
    (5, "Persuasion", ["Jane Austen"], None, None, 1817,
     4.13, 200_000, 249, ["Classics", "Romance"], ["classic", "romance"], True),
    (6, "A Child Called It", ["Dave Pelzer"], None, None, 1995,
     4.0, 310_000, 184, ["Biography & memoir"], ["memoir", "child abuse", "abuse"], True),
    (7, "Steamy Nights", ["Ann Author"], None, None, 2014,
     4.2, 90_000, 300, ["Romance"], ["romance", "steamy", "smut"], True),
]  # fmt: skip
NEIGHBOURS = [
    (1, 5, 0.6, 900),
    (1, 2, 0.5, 800),
    (1, 7, 0.4, 700),
    (5, 2, 0.5, 600),
    (2, 3, 0.9, 5000),
    (2, 7, 0.5, 600),
    (2, 1, 0.2, 100),
]


@pytest.fixture(scope="module")
def recommender() -> BookRecommender:
    catalog = BookCatalog(book_frame(ROWS))
    frame = pd.DataFrame(NEIGHBOURS, columns=["book_id", "neighbour_id", "score", "co_readers"])
    return BookRecommender(catalog, frame)


def ids(hits) -> list[int]:
    return [hit.book.book_id for hit in hits]


def test_the_excluded_shelves_cover_the_real_tag_vocabulary():
    for shelf in ("erotica", "erotic", "erotic romance", "bdsm", "dark romance", "smut", "abuse"):
        assert shelf in EXCLUDED_SHELVES
    # Queries ask for a group by its words, never by a neighbouring genre.
    assert "erotica" in asked_shelves("some steamy romance")
    assert "bdsm" in asked_shelves("Dark romance, please")
    assert "child abuse" in asked_shelves("memoirs about abuse")
    assert asked_shelves("a romance for date night") == frozenset()
    assert asked_shelves("hot chocolate and a cosy read") == frozenset()


def test_default_search_and_browse_leave_out_sensitive_shelves(recommender):
    assert ids(recommender.search(BookQuery(query="romance"))) == [1, 5]
    assert ids(recommender.search(BookQuery(genres=["Romance"]))) == [1, 5]
    assert 6 not in ids(recommender.search(BookQuery(query="memoir")))
    # The filter applies before top_k: a short list is still full of allowed books.
    assert ids(recommender.search(BookQuery(genres=["Romance"], top_k=2))) == [1, 5]


def test_a_series_mostly_shelved_as_erotica_stays_out_as_a_whole(recommender):
    """Entwined with You carries no excluded shelf itself, but two of its series' three do."""
    assert 4 not in ids(recommender.search(BookQuery(query="sylvia day romance")))


def test_a_query_that_asks_for_the_genre_lifts_it(recommender):
    assert 7 in ids(recommender.search(BookQuery(query="steamy romance")))
    assert 3 in ids(recommender.search(BookQuery(query="erotic romance")))
    assert 6 in ids(recommender.search(BookQuery(query="memoir about abuse")))
    # Naming a whole title, a "like <title>" seed or an author is asking for it too.
    assert ids(recommender.search(BookQuery(query="bared to you")))[0] == 2
    assert ids(recommender.search(BookQuery(query="like Bared to You")))[0] == 7
    assert 2 in ids(recommender.search(BookQuery(query="books by Sylvia Day")))
    # An explicit empty list turns the filter off.
    assert 7 in ids(recommender.search(BookQuery(genres=["Romance"], exclude_tags=[])))


def test_readers_also_liked_and_because_you_liked_are_filtered(recommender):
    assert ids(recommender.similar(1, 5)) == [5]
    assert ids(recommender.because([1, 5], 5)) == []
    # A seed that carries the shelves itself was the reader's choice: its group is lifted.
    assert ids(recommender.similar(2, 5))[:3] == [3, 7, 1]
    assert ids(recommender.similar(1, 5, exclude_tags=()))[:3] == [5, 2, 7]


def test_the_api_models_default_to_the_safe_shelves():
    assert set(BookQuery().exclude_tags) == set(EXCLUDED_SHELVES)
