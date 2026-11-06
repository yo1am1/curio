"""Book → cocktail pairing: profile mapping, scoring, diversity, alcohol-free rules,
avoided genres, explanations and determinism."""

import pytest
from pydantic import ValidationError

from curio.domains.books.catalog import BookCatalog
from curio.domains.cocktails.catalog import CocktailCatalog
from curio.domains.cocktails.models import CocktailPreferences
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.domains.pairings.models import PairingRequest
from curio.domains.pairings.pairer import (
    DRINK_NOTE,
    BookCocktailPairer,
    PairingError,
    UnknownBookError,
)
from curio.domains.pairings.themes import LORE, THEMES, Lore
from tests.conftest import book_frame, cocktail_frame
from tests.domains.pairings.conftest import PAIRING_BOOKS, PAIRING_DRINKS

BIG_SLEEP, GONE_GIRL, GATSBY, WIND, DUNE, POTTER, HOBBIT, DRACULA = range(1, 9)
PRIDE, CASINO, CAROL, UNSHELVED = range(9, 13)
WHISKEY = {"Old Fashioned", "Manhattan", "Whiskey Sour", "Sazerac", "Mint Julep", "Hot Toddy"}


def pair(pairer: BookCocktailPairer, **request: object):
    return pairer.pair(PairingRequest(**request))


def names(result) -> list[str]:
    return [p.cocktail.name for p in result.pairings]


# --- Profile mapping -------------------------------------------------------------------------


def test_genres_shelves_and_words_map_to_themes(pairer, book_shares):
    assert next(iter(book_shares(BIG_SLEEP))) == "noir"
    assert next(iter(book_shares(GONE_GIRL))) == "thriller"
    assert next(iter(book_shares(WIND))) == "fantasy"
    assert next(iter(book_shares(DUNE))) == "scifi"
    assert next(iter(book_shares(DRACULA))) == "horror"
    assert "spy" in book_shares(CASINO)  # from the "spy" and "espionage" shelves
    # Words in the description count: an era and a season.
    assert "jazz" in book_shares(GATSBY)
    assert "cosy" in book_shares(CAROL)
    shares = book_shares(BIG_SLEEP)
    assert sum(shares.values()) == pytest.approx(1)
    assert list(shares.values()) == sorted(shares.values(), reverse=True)


def test_broad_themes_weigh_less_than_telling_ones(book_shares):
    # The Big Sleep is a Mystery and a Classic; noir pours first.
    shares = book_shares(BIG_SLEEP)
    assert shares["noir"] > 2 * shares["classics"]


def test_profile_summary_and_evidence_cite_the_book(pairer):
    result = pair(pairer, book_id=BIG_SLEEP)
    profile = result.profile
    assert profile.source == "The Big Sleep"
    assert profile.summary.startswith("The Big Sleep leans Mystery and noir")
    noir = profile.themes[0]
    assert (noir.id, noir.style) == ("noir", "a bitter, spirit-forward whiskey drink")
    assert noir.evidence == "Mystery · shelved as noir and crime fiction"
    assert profile.book_ids == [BIG_SLEEP]


# --- Scoring and explanations ----------------------------------------------------------------


def test_noir_pours_a_whiskey_drink_with_book_and_recipe_evidence(pairer):
    result = pair(pairer, book_id=BIG_SLEEP)
    assert names(result)[0] in WHISKEY
    first = result.pairings[0]
    assert first.theme == "noir"
    assert first.reasons[0] == (
        "The Big Sleep (Mystery · shelved as noir and crime fiction) → a bitter, "
        "spirit-forward whiskey drink: Manhattan (Bourbon, Sweet Vermouth, Angostura Bitters)"
    )
    assert result.note == DRINK_NOTE


@pytest.mark.parametrize(
    ("book_id", "expected"),
    [
        (GONE_GIRL, "Espresso Martini"),  # thrillers: coffee
        (WIND, "Honey Bee"),  # fantasy: honey and spice
        (DUNE, "Grand Blue"),  # sci-fi: blue curaçao
        (DRACULA, "Corpse Reviver"),  # horror: absinthe
        (PRIDE, "Kir Royale"),  # romance: sparkling and berry
        (CAROL, "Hot Toddy"),  # Christmas: something warm
        (CASINO, "Vesper"),  # spies, and Bond's own drink
    ],
)
def test_each_theme_pours_its_style(pairer, book_id, expected):
    assert names(pair(pairer, book_id=book_id))[0] == expected


def test_literary_lore_leads_for_its_author(pairer):
    result = pair(pairer, book_id=GATSBY)
    assert names(result)[:2] == ["Gin Rickey", "Mint Julep"]
    assert result.pairings[0].reasons[0] == (
        "The Great Gatsby is by F. Scott Fitzgerald. Tom Buchanan mixes gin rickeys in The "
        "Great Gatsby → Gin Rickey (Gin, Lime, Soda Water)"
    )
    # A second theme the recipe also pours is cited.
    assert "Also suits Classics: Gin" in result.pairings[0].reasons


def test_reasons_cite_only_ingredients_of_the_style(pairer):
    """A theme's evidence names the recipe's ingredients that pour it, never others."""
    for book_id in range(1, 13):
        for item in pair(pairer, book_id=book_id, top_k=5).pairings:
            assert 1 <= len(item.reasons) <= 3
            recipe = {i.name for i in item.cocktail.ingredients}
            cited = item.reasons[0].rsplit("(", 1)[1].rstrip(")").split(", ")
            assert set(cited) <= recipe or cited[0].startswith("served in")


def test_a_book_without_signals_gets_well_loved_classics(pairer):
    result = pair(pairer, book_id=UNSHELVED)
    assert result.profile.themes == []
    assert result.profile.summary == (
        "An Unshelved Book gives no strong flavour cue: here are well-loved classics"
    )
    assert names(result)[0] == "Old Fashioned"
    assert "no strong flavour cue → a well-loved classic" in result.pairings[0].reasons[0]


# --- Responsible drinking --------------------------------------------------------------------


@pytest.mark.parametrize("book_id", [POTTER, HOBBIT])
def test_young_adult_and_childrens_books_are_always_alcohol_free(pairer, book_id):
    """Harry Potter by its genre, The Hobbit by its "childrens" shelf alone."""
    for seed in range(4):
        result = pair(pairer, book_id=book_id, seed=seed, top_k=5)
        assert result.profile.alcohol_free
        assert result.pairings
        assert all(p.cocktail.alcohol_free for p in result.pairings)
        assert all(p.cocktail.alcoholic == "Non alcoholic" for p in result.pairings)
        assert result.note.startswith("Alcohol-free recipes only:")
        assert all("children's or young-adult book" in p.reasons[-1] for p in result.pairings)


def test_a_young_book_on_the_reading_list_or_a_liked_young_genre_is_alcohol_free(pairer):
    listed = pair(pairer, book_ids=[BIG_SLEEP, POTTER])
    assert listed.profile.alcohol_free
    assert listed.profile.alcohol_free_reason == (
        "your reading list includes Harry Potter and the Sorcerer's Stone, a children's or "
        "young-adult book"
    )
    assert all(p.cocktail.alcohol_free for p in listed.pairings)
    liked = pair(pairer, liked_genres=["young adult"])
    assert liked.profile.alcohol_free_reason == "you like Young adult books"


def test_asking_or_a_remembered_taste_keeps_pairings_alcohol_free(pairer):
    asked = pair(pairer, book_id=BIG_SLEEP, alcohol_free=True)
    assert asked.profile.alcohol_free_reason == "you asked for alcohol-free drinks"
    assert asked.pairings and all(p.cocktail.alcohol_free for p in asked.pairings)
    # Alcohol-free styles describe what is poured, not the spirit it replaces.
    assert asked.pairings[0].reasons == [
        "The Big Sleep (Mystery · shelved as noir and crime fiction) → a dark, bitter coffee "
        "or cola: Iced Coffee (Coffee)",
        "Alcohol-free, because you asked for alcohol-free drinks",
    ]
    remembered = pair(
        pairer,
        book_id=GONE_GIRL,
        cocktail_preferences=CocktailPreferences(alcohol_free=True),
    )
    assert remembered.profile.alcohol_free_reason == (
        "your remembered cocktail taste is alcohol-free"
    )
    assert all(p.cocktail.alcohol_free for p in remembered.pairings)


def test_alcohol_optional_recipes_are_never_alcohol_free(pairer):
    # Gluehwein ("Optional alcohol") fits a Christmas book, but not an alcohol-free pairing.
    assert "Gluehwein" in names(pair(pairer, book_id=CAROL))
    for seed in range(3):
        result = pair(pairer, book_id=CAROL, alcohol_free=True, seed=seed, top_k=5)
        assert "Gluehwein" not in names(result)


def test_shots_and_overproof_drinks_are_never_offered(pairer):
    offered = {
        name
        for book_id in range(1, 13)
        for seed in range(4)
        for name in names(pair(pairer, book_id=book_id, seed=seed, top_k=5))
    }
    assert offered.isdisjoint({"B-52", "Zombie"})


def test_avoided_ingredients_are_left_out(pairer):
    tastes = CocktailPreferences(avoided_ingredients=["bourbon"])
    result = pair(pairer, book_id=BIG_SLEEP, cocktail_preferences=tastes, top_k=5)
    assert result.pairings
    for item in result.pairings:
        assert "Bourbon" not in {i.name for i in item.cocktail.ingredients}


# --- Diversity, seeds and determinism --------------------------------------------------------


def test_a_set_mixes_spirits_and_styles(pairer):
    result = pair(pairer, book_id=BIG_SLEEP)
    assert len(result.pairings) == 3
    assert not set(names(result)) <= WHISKEY  # not three whiskey drinks in a row
    words = [set(name.lower().split()) for name in names(pair(pairer, book_id=CASINO, top_k=5))]
    # Near-duplicates ("Vesper"/"Martini"/"Espresso Martini") are spread across sets.
    assert sum("martini" in w for w in words) <= 2


def test_pairing_is_deterministic_and_seeds_cycle_through_sets(pairer):
    first = pair(pairer, book_id=BIG_SLEEP)
    assert pair(pairer, book_id=BIG_SLEEP) == first
    assert first.sets >= 2
    another = pair(pairer, book_id=BIG_SLEEP, seed=1)
    assert set(names(another)).isdisjoint(names(first))
    assert another.seed == 1
    assert names(pair(pairer, book_id=BIG_SLEEP, seed=first.sets)) == names(first)


def test_exclude_drink_ids_gives_another_pairing(pairer):
    first = pair(pairer, book_id=BIG_SLEEP)
    shown = [p.cocktail.drink_id for p in first.pairings]
    again = pair(pairer, book_id=BIG_SLEEP, exclude_drink_ids=shown)
    assert again.pairings
    assert set(shown).isdisjoint(p.cocktail.drink_id for p in again.pairings)


# --- Reader taste ------------------------------------------------------------------------------


def test_reader_taste_counts_the_books_that_lean_each_way(pairer):
    result = pair(pairer, book_ids=[BIG_SLEEP, GONE_GIRL, DRACULA])
    profile = result.profile
    assert profile.source == "Your reading list (3 books)"
    noir = next(t for t in profile.themes if t.id == "noir")
    assert (noir.books, noir.evidence) == (2, "2 of 3 books")
    reasons = [p.reasons[0] for p in result.pairings if p.theme == "noir"]
    assert reasons and reasons[0].startswith(
        "Your reading list leans Mystery and noir (2 of 3 books) → a bitter"
    )


def test_liked_genres_count_like_a_book(pairer):
    result = pair(pairer, book_ids=[BIG_SLEEP], liked_genres=["fantasy"])
    assert {t.id for t in result.profile.themes} >= {"noir", "fantasy"}
    only = pair(pairer, liked_genres=["Romance", "nonsense genre"])
    assert only.profile.source == "Your liked genres"
    assert only.profile.summary == "Your liked genres lean Romance"
    assert only.pairings[0].reasons[0].startswith("You like Romance → something sparkling")


def test_avoided_genres_never_drive_the_profile(pairer, book_shares):
    assert "horror" not in book_shares(DRACULA, avoided=["Horror"])
    result = pair(pairer, book_ids=[DRACULA, BIG_SLEEP], avoided_genres=["horror"])
    assert "horror" not in {t.id for t in result.profile.themes}
    assert all(p.theme != "horror" for p in result.pairings)
    assert "Corpse Reviver" not in names(result)
    # An avoided genre is dropped even when the reader also says they like it.
    liked = pair(pairer, liked_genres=["Horror", "Fantasy"], avoided_genres=["Horror"])
    assert [t.id for t in liked.profile.themes] == ["fantasy"]
    # A single book's avoided genre does not drive its pairing either.
    single = pair(pairer, book_id=DRACULA, avoided_genres=["Horror"])
    assert all(t.id != "horror" for t in single.profile.themes)


# --- Errors and bounds ---------------------------------------------------------------------------


def test_unknown_books_and_empty_tastes_are_errors(pairer):
    with pytest.raises(UnknownBookError):
        pair(pairer, book_id=999)
    with pytest.raises(PairingError):
        pair(pairer, book_ids=[998, 999])
    with pytest.raises(PairingError):
        pair(pairer, liked_genres=["Not a genre"])


@pytest.mark.parametrize(
    "request_body",
    [
        {},
        {"book_id": 0},
        {"book_id": 1, "seed": -1},
        {"book_id": 1, "seed": 10_001},
        {"book_id": 1, "top_k": 6},
        {"book_id": 1, "top_k": 0},
        {"book_ids": list(range(1, 22))},
        {"liked_genres": ["x" * 61]},
        {"liked_genres": [" "]},
        {"book_id": 1, "exclude_drink_ids": list(range(1, 17))},
        {"book_id": 1, "unexpected": True},
    ],
)
def test_requests_are_tightly_bounded(request_body):
    with pytest.raises(ValidationError):
        PairingRequest.model_validate(request_body)


def test_lore_for_missing_drinks_is_ignored(pairer):
    from tests.domains.pairings.conftest import pairing_books, pairing_cocktails

    cocktails = pairing_cocktails()
    custom = BookCocktailPairer(
        pairing_books(),
        cocktails,
        CocktailRecommender(cocktails),
        lore=[Lore("Raymond Chandler", "Gimlet", "not in this catalog")],
    )
    assert custom.pair(PairingRequest(book_id=BIG_SLEEP)).pairings


def test_every_theme_is_well_formed():
    ids = [t.id for t in THEMES.themes]
    assert len(ids) == len(set(ids))
    for theme in (*THEMES.themes, THEMES.fallback):
        assert theme.terms and theme.label and theme.style
        assert 0 < theme.strength <= 1
        assert all(w == w.lower() for w in theme.words)
        assert set(theme.terms).isdisjoint(theme.soft_terms)


@pytest.fixture
def book_shares(pairer):
    def shares(book_id: int, avoided: tuple[str, ...] | list[str] = ()):
        from tests.domains.pairings.conftest import pairing_books

        return pairer.themes_of(pairing_books().get(book_id), avoided)

    return shares


def test_lore_for_one_named_book_leads_for_that_book_only():
    """1984's Victory Gin belongs to 1984, not to every Orwell book."""
    orwell = [
        (13, "1984", ["George Orwell"], None, None, 1949, 4.14, 2_000_000, 328,
         ["Classics", "Science fiction"], ["dystopia", "classics"], True),
        (14, "Animal Farm", ["George Orwell"], None, None, 1945, 3.87, 1_800_000, 122,
         ["Classics", "Science fiction"], ["dystopia", "classics"], True),
    ]  # fmt: skip
    pink_gin = (99, "Pink Gin", "Alcoholic", "Ordinary Drink", "Cocktail glass",
                [("Bitters", "3 dashes"), ("Gin", "2 oz")])  # fmt: skip
    drinks = CocktailCatalog(cocktail_frame([*PAIRING_DRINKS, pink_gin]))

    books = BookCatalog(book_frame([*PAIRING_BOOKS, *orwell]))
    pairer = BookCocktailPairer(books, drinks, CocktailRecommender(drinks), lore=LORE)
    nineteen = pair(pairer, book_id=13)
    assert names(nineteen)[0] == "Pink Gin"
    assert (
        nineteen.pairings[0]
        .reasons[0]
        .startswith("1984 is by George Orwell. Winston Smith drinks Victory Gin")
    )
    farm = pair(pairer, book_id=14, top_k=5)
    assert not any("Victory Gin" in r for p in farm.pairings for r in p.reasons)
