"""Book recommender: search, readers also liked, because you liked, by author and browse."""

import pytest

from clothes_recommender.books.domain import BookQuery
from clothes_recommender.books.recommend import BookRecommender, quality, term, tokens
from tests.conftest import book_neighbours_frame


def ids(hits) -> list[int]:
    return [hit.book.book_id for hit in hits]


def search(recommender, query: str = "", **filters):
    return recommender.search(BookQuery(query=query, **filters))


def test_like_a_title_ranks_what_its_readers_liked_without_its_series(book_recommender):
    hits = search(book_recommender, "Fantasy like The Name of the Wind")
    assert ids(hits) == [3, 4]  # 2 is the same series; 7 is not fantasy
    assert hits[0].reasons[0] == "960 readers who liked The Name of the Wind also liked this"
    assert "Genre: Fantasy" in hits[0].reasons
    assert book_recommender.seed_of("something like the name of the wind").book_id == 1
    assert book_recommender.seed_of("fantasy") is None


def test_like_without_other_words_keeps_every_genre(book_recommender):
    assert ids(search(book_recommender, "similar to Gone Girl")) == [7, 9]  # never itself


def test_short_classic_novels_filters_genre_and_length(book_recommender):
    hits = search(book_recommender, "short classic novels")
    assert set(ids(hits)) == {5, 6}
    assert all(h.book.pages <= 250 for h in hits)
    assert {"Genre: Classics", "112 pages"} <= set(hits[ids(hits).index(5)].reasons)


def test_short_stories_is_a_genre_not_a_length(book_recommender):
    assert search(book_recommender, "short stories") == []  # no short-story book here


def test_genre_words_and_aliases_become_filters(book_recommender):
    assert ids(search(book_recommender, "sci-fi")) == [6]
    assert ids(search(book_recommender, "ya romance")) == [10]


def test_by_author_in_the_query_filters_on_the_catalog_author(book_recommender):
    hits = search(book_recommender, "books by stephen king")
    assert ids(hits) == [7]
    assert hits[0].reasons == ["By Stephen King"]
    # An unknown author stays plain text.
    assert search(book_recommender, "books by nobody at all") == []


def test_mood_words_and_named_moods_browse_by_shelf(book_recommender):
    cosy = search(book_recommender, "cosy mystery")
    assert ids(cosy) == [9]
    assert cosy[0].reasons == ["Genre: Mystery", "Mood: Cosy (shelved as cozy mystery)"]
    assert set(ids(search(book_recommender, mood="Dark"))) == {7, 8}
    assert ids(search(book_recommender, mood="dark", genres=["Mystery"])) == [8]
    # An unknown mood name is ignored (the agent validates moods before searching).
    assert len(search(book_recommender, mood="Unknown mood")) == 10


def test_text_search_matches_titles_shelves_and_authors(book_recommender):
    magic = search(book_recommender, "magic")
    assert set(ids(magic)) == {1, 2, 4}
    assert all("Shelved as magic" in h.reasons for h in magic)
    assert ids(search(book_recommender, "the way of kings"))[0] == 3
    assert ids(search(book_recommender, "rothfuss")) == [1, 2]
    assert search(book_recommender, "zzzz unknown") == []
    assert ids(search(book_recommender, "clown")) == [7]  # plurals fold ("clowns")


def test_filters_exclusions_and_boosts(book_recommender):
    assert ids(search(book_recommender, "dark", exclude_genres=["horror"])) == [8]
    assert 7 not in ids(search(book_recommender, mood="Dark", exclude_authors=["stephen KING"]))
    assert ids(search(book_recommender, genres=["Fantasy"], min_rating=4.6)) == [3]
    # Books without a known page count are left out of a length filter.
    assert 10 not in ids(search(book_recommender, genres=["Romance"], max_pages=2000))
    plain = search(book_recommender, "dark")
    boosted = search(book_recommender, "dark", boost_genres=["Mystery"])
    assert ids(boosted)[0] == 8 and boosted[0].score > plain[ids(plain).index(8)].score
    assert "A genre you like: Mystery" in boosted[0].reasons


def test_empty_query_with_genre_ranks_by_genre_strength_then_quality(book_recommender):
    hits = search(book_recommender, genres=["Classics"])
    assert ids(hits) == [6, 5] or ids(hits) == [5, 6]
    assert all(h.reasons == ["Genre: Classics"] for h in hits)
    # Nothing asked: the best-loved books by the quality prior (rating and popularity).
    popular = search(book_recommender, top_k=3)
    assert ids(popular) == [3, 1, 2]
    assert popular[0].reasons == ["Loved by readers: 145,000 Goodreads ratings"]


def test_similar_follows_neighbours_and_explains_the_link(book_recommender):
    hits = book_recommender.similar(1, 5)
    assert ids(hits) == [2, 3, 4, 7]
    assert hits[0].reasons == [
        "2,400 readers who liked The Name of the Wind also liked this",
        "Also in The Kingkiller Chronicle",
    ]
    assert hits[1].reasons[1] == "Both shelved as epic fantasy, high fantasy"
    assert ids(book_recommender.similar(1, 5, other_authors_only=True)) == [3, 4, 7]
    assert ids(book_recommender.similar(1, 5, exclude_genres=["Horror"])) == [2, 3, 4]
    assert book_recommender.similar(404) == [] and book_recommender.similar(1, 0) == []


def test_similar_tops_up_with_shelf_neighbours(book_recommender):
    hits = book_recommender.similar(4, 3)  # one collaborative neighbour (3)
    assert ids(hits)[0] == 3
    assert set(ids(hits)[1:]) <= {1, 2}
    assert "readers" not in " ".join(hits[1].reasons)


def test_because_sums_seeds_and_skips_seeds_and_their_series(book_recommender):
    hits = book_recommender.because([1, 3])
    assert ids(hits) == [4, 7]
    assert hits[0].reasons == [
        "Because you liked The Way of Kings",
        "800 readers who liked The Way of Kings also liked this",
        "Readers of The Name of the Wind liked it too",
    ]
    assert ids(book_recommender.because([1], exclude_authors=["Brandon Sanderson"])) == [7]
    assert book_recommender.because([404]) == []


def test_because_caps_books_per_author(monkeypatch):
    import pandas as pd

    from clothes_recommender.books import recommend
    from clothes_recommender.books.catalog import BookCatalog
    from tests.conftest import book_frame

    links = pd.DataFrame(
        [(5, 1, 0.9, 100), (5, 2, 0.8, 90), (5, 3, 0.7, 80), (5, 4, 0.6, 70)],
        columns=["book_id", "neighbour_id", "score", "co_readers"],
    )
    recommender = BookRecommender(BookCatalog(book_frame()), links)
    # Rothfuss (1, 2) and Sanderson (3, 4) are two each; 1 and 2 share a series with no seed.
    assert ids(recommender.because([5], top_k=4)) == [1, 2, 3, 4]
    monkeypatch.setattr(recommend, "MAX_PER_AUTHOR", 1)
    assert ids(recommender.because([5], top_k=4)) == [1, 3]
    links.loc[len(links)] = (5, 2, 0.95, 95)  # a repeated pair keeps its strongest link only
    assert ids(BookRecommender(BookCatalog(book_frame()), links).because([5], top_k=1)) == [2]


def test_by_author_lists_most_rated_first_with_series(book_recommender):
    hits = book_recommender.by_author("BRANDON SANDERSON")
    assert ids(hits) == [4, 3]
    assert hits[0].reasons == ["By Brandon Sanderson", "Book 1 of Mistborn"]
    assert ids(book_recommender.by_author("Patrick Rothfuss", exclude_book_id=1)) == [2]
    assert book_recommender.by_author("Nobody") == []


def test_resolve_title_needs_the_whole_phrase(book_recommender):
    assert book_recommender.resolve_title("name of the wind").book_id == 1
    assert book_recommender.resolve_title("the name of the wind and more") is None


def test_neighbour_file_is_validated_and_unknown_ids_are_dropped(book_catalog):
    with pytest.raises(ValueError, match="co_readers"):
        BookRecommender(book_catalog, book_neighbours_frame().drop(columns=["co_readers"]))
    links = book_neighbours_frame()
    links.loc[len(links)] = (1, 999, 0.99, 10_000)
    links.loc[len(links)] = (1, 1, 0.99, 10_000)
    assert ids(BookRecommender(book_catalog, links).similar(1, 2)) == [2, 3]


def test_results_are_deterministic(book_catalog):
    first = BookRecommender(book_catalog, book_neighbours_frame())
    second = BookRecommender(book_catalog, book_neighbours_frame().sample(frac=1, random_state=3))
    for query in ("magic", "dark", "fantasy like the name of the wind"):
        assert search(first, query) == search(second, query)


def test_text_helpers_and_quality_prior(book_catalog):
    assert tokens("Márquez's Café") == ["marquez", "s", "cafe"]
    assert [term(w) for w in ("dragons", "stories", "classics", "glass", "is")] == [
        "dragon",
        "story",
        "classic",
        "glass",
        "is",
    ]
    prior = quality(list(book_catalog))
    assert prior.min() >= 0 and prior.max() <= 1
    assert quality([]).size == 0
