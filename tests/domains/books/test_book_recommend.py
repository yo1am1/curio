"""Book recommender: search, readers also liked, because you liked, by author and browse."""

import pytest

from curio.domains.books.models import BookQuery
from curio.domains.books.recommend import BookRecommender, quality, term, tokens
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
    # (9, not 10: The Wise Man's Fear gives way to the first book of its series.)
    assert len(search(book_recommender, mood="Unknown mood")) == 9


def test_text_search_matches_titles_shelves_and_authors(book_recommender):
    magic = search(book_recommender, "magic")
    assert set(ids(magic)) == {1, 4}  # 2 is the second book of 1's series
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
    assert ids(popular) == [3, 1, 4]  # 2 continues 1's series
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

    from curio.domains.books import recommend
    from curio.domains.books.catalog import BookCatalog
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


# --- Golden queries: the topic of a request, not its phrasing ------------------------------

# Titles that share a request word ("gift", "love", "long", "world", "2") with the golden
# queries below, beside the books those queries are about. Laid out like BOOK_ROWS.
GOLDEN_ROWS = [
    (1, "The Girl with All the Gifts", ["M.R. Carey"], None, None, 2014, 3.9, 120_000, 460,
     ["Horror", "Science fiction"], ["zombies", "dystopia"], True),
    (2, "The Gift of the Magi", ["O. Henry"], None, None, 1905, 4.1, 90_000, 20,
     ["Classics", "Short stories"], ["christmas", "classic"], True),
    (3, "The Five Love Languages", ["Gary Chapman"], None, None, 1992, 4.2, 150_000, 204,
     ["Non-fiction", "Self-help"], ["relationships", "marriage", "love"], True),
    (4, "The Botany of Desire", ["Michael Pollan"], None, None, 2001, 4.0, 70_000, 271,
     ["Non-fiction", "Science"], ["gardening", "botany", "food"], True),
    (5, "The History of Love", ["Nicole Krauss"], None, None, 2005, 3.9, 80_000, 252,
     ["Literary fiction"], ["love", "literary fiction"], True),
    (6, "P.S. I Love You", ["Cecelia Ahern"], None, None, 2004, 4.0, 300_000, 512,
     ["Romance"], ["love", "romance", "chick lit"], True),
    (7, "The Girl with the Dragon Tattoo", ["Stieg Larsson"], "Millennium", "1", 2005,
     4.1, 2_000_000, 465, ["Mystery", "Thriller"], ["crime", "sweden"], True),
    (8, "How to Train Your Dragon", ["Cressida Cowell"], "How to Train Your Dragon", "1",
     2003, 3.9, 50_000, 214, ["Children's", "Fantasy"], ["dragons", "middle grade"], True),
    (9, "Eragon", ["Christopher Paolini"], "The Inheritance Cycle", "1", 2002, 3.9, 1_000_000,
     509, ["Young adult", "Fantasy"], ["dragons", "fantasy"], True),
    (10, "Night World, No. 2", ["L.J. Smith"], "Night World", "4-6", 1997, 4.1, 30_000, 640,
     ["Young adult", "Paranormal"], ["vampires", "night world"], True),
    (11, "Persepolis 2", ["Marjane Satrapi"], "Persepolis", "2", 2004, 4.2, 40_000, 187,
     ["Graphic novels", "Biography & memoir"], ["iran", "memoir"], True),
    (12, "All the Light We Cannot See", ["Anthony Doerr"], None, None, 2014, 4.3, 600_000, 531,
     ["Historical fiction"], ["wwii", "world war ii", "historical fiction"], True),
    (13, "A Long Way Down", ["Nick Hornby"], None, None, 2005, 3.5, 90_000, 333,
     ["Contemporary", "Humour"], ["humor", "funny", "british"], True),
    (14, "Me Talk Pretty One Day", ["David Sedaris"], None, None, 2000, 4.0, 200_000, 272,
     ["Humour", "Biography & memoir"], ["humor", "essays", "funny"], True),
    (15, "Dune", ["Frank Herbert"], "Dune Chronicles", "1", 1965, 4.2, 600_000, 661,
     ["Science fiction"], ["science fiction", "space"], True),
    (16, "Catch-22", ["Joseph Heller"], None, None, 1961, 3.9, 600_000, 453,
     ["Classics"], ["war", "satire"], True),
]  # fmt: skip


@pytest.fixture
def golden():
    import pandas as pd

    from curio.domains.books.catalog import BookCatalog
    from tests.conftest import book_frame

    links = pd.DataFrame(
        [(9, 8, 0.2, 50)], columns=["book_id", "neighbour_id", "score", "co_readers"]
    )
    return BookRecommender(BookCatalog(book_frame(GOLDEN_ROWS)), links)


def test_an_empty_neighbour_file_still_serves_search(book_catalog):
    empty = book_neighbours_frame().iloc[:0]
    recommender = BookRecommender(book_catalog, empty)
    assert ids(search(recommender, "magic")) and recommender.similar(1, 2)[0].book.book_id == 3


def test_a_gift_request_finds_the_hobby_not_titles_with_gift_or_love(golden):
    hits = search(golden, "a gift for my mom who loves gardening")
    assert ids(hits) == [4]
    assert hits[0].reasons == ["Shelved as gardening, botany"]


def test_a_childs_age_picks_childrens_books_about_the_topic(golden):
    hits = search(golden, "something for a 10 year old who loves dragons")
    assert ids(hits) == [8]  # not Eragon (young adult), the Dragon Tattoo or love stories
    assert hits[0].reasons[0] == "Genre: Children's"
    assert ids(search(golden, "dragons for a 15-year-old")) == [9]
    assert ids(search(golden, "a dragon book for a kid")) == [8]


def test_world_war_2_means_the_war_shelves_not_a_2_in_a_title(golden):
    for query in ("best books about world war 2", "WWII novels", "second world war"):
        hits = search(golden, query)
        assert ids(hits) == [12], query
        assert hits[0].reasons == ["Shelved as wwii, world war ii"]
    # A bare one- or two-digit number never matches on its own.
    assert ids(search(golden, "persepolis 2")) == [11]


def test_flight_and_long_are_about_the_trip_not_the_book(golden):
    hits = search(golden, "funny books for a long flight")
    assert set(ids(hits)) == {13, 14, 16}  # the Funny shelves (Catch-22: satire)
    assert not any(r.startswith("Title matches") for h in hits for r in h.reasons)


def test_a_title_counts_for_two_query_words_or_the_whole_title(golden):
    dune = search(golden, "dune")
    assert ids(dune)[0] == 15 and 'Title matches "dune"' in dune[0].reasons
    assert ids(search(golden, "catch 22"))[0] == 16
    assert ids(search(golden, "the girl with the dragon tattoo"))[0] == 7
    # One shared word is a coincidence: the Dragon Tattoo is not about dragons. (Its
    # description here repeats its title, so it still matches, last and without the title.)
    dragons = search(golden, "dragons")
    assert ids(dragons) == [8, 9, 7]
    assert not any(r.startswith("Title matches") for r in dragons[2].reasons)
    # Request words still search when nothing else is asked.
    assert ids(search(golden, "gift"))[0] == 2
    assert set(ids(search(golden, "love"))) >= {3, 5, 6}


# --- Diversity: one prolific author or one long series cannot fill the list ---------------

DIVERSE_ROWS = [
    (1, "Ember Throne", ["Mara Quill"], "The Saga of Stars", "3", 2012, 4.5, 5_000_000, 500,
     ["Fantasy"], ["epic fantasy"], True),
    (2, "Ember Crown", ["Mara Quill"], "The Saga of Stars", "2", 2011, 4.4, 4_000_000, 500,
     ["Fantasy"], ["epic fantasy"], True),
    (3, "Ember Born", ["Mara Quill"], "The Saga of Stars", "1", 2010, 4.2, 1_000_000, 500,
     ["Fantasy"], ["epic fantasy"], True),
    (4, "Glass Harbour", ["Tom Prolific"], None, None, 2001, 4.3, 3_000_000, 400,
     ["Fantasy"], ["epic fantasy"], True),
    (5, "Glass River", ["Tom Prolific"], None, None, 2002, 4.3, 2_900_000, 400,
     ["Fantasy"], ["epic fantasy"], True),
    (6, "Glass Moon", ["Tom Prolific"], None, None, 2003, 4.3, 2_800_000, 400,
     ["Fantasy"], ["epic fantasy"], True),
    (7, "Lone Tower", ["Ida Single"], None, None, 2005, 4.0, 100_000, 300,
     ["Fantasy"], ["epic fantasy"], True),
    (8, "The Saga of Stars Box Set", ["Mara Quill"], "The Saga of Stars", "1-3", 2013, 4.6,
     6_000_000, 1500, ["Fantasy"], ["epic fantasy"], True),
    (9, "Seed Book", ["Ida Single"], None, None, 2000, 4.0, 50_000, 300,
     ["Romance"], ["love"], True),
]  # fmt: skip


@pytest.fixture
def diverse():
    import pandas as pd

    from curio.domains.books.catalog import BookCatalog
    from tests.conftest import book_frame

    # Readers of Seed Book liked the series' later books and Tom Prolific most.
    links = pd.DataFrame(
        [(9, 1, 0.9, 900), (9, 2, 0.8, 800), (9, 4, 0.7, 700), (9, 5, 0.6, 600),
         (9, 6, 0.5, 500), (9, 3, 0.2, 200), (9, 7, 0.1, 100)],
        columns=["book_id", "neighbour_id", "score", "co_readers"],
    )  # fmt: skip
    return BookRecommender(BookCatalog(book_frame(DIVERSE_ROWS)), links)


def test_search_keeps_two_books_per_author_and_the_first_of_a_series(diverse):
    # By quality alone: box set 8, then 1, 2, 4, 5, 6, 3, 7. The series keeps book 1 (not the
    # box set, not book 3), and Tom Prolific two books.
    assert ids(search(diverse, "fantasy")) == [4, 5, 3, 7]
    assert ids(search(diverse, "epic fantasy", top_k=2)) == [4, 5]


def test_seeded_search_is_diverse_too(diverse):
    hits = search(diverse, "fantasy like Seed Book")
    assert ids(hits) == [4, 5, 3, 7]
    assert hits[2].reasons[0] == "200 readers who liked Seed Book also liked this"


def test_a_named_series_or_author_keeps_all_its_books(diverse):
    assert set(ids(search(diverse, "the saga of stars"))) == {1, 2, 3, 8}
    assert set(ids(search(diverse, "fantasy by Tom Prolific"))) == {4, 5, 6}
    assert set(ids(search(diverse, "prolific fantasy"))) >= {4, 5, 6}
    # Other authors stay capped beside a named one.
    assert ids(search(diverse, "fantasy", author="Mara Quill")) == [8, 1, 2, 3]
