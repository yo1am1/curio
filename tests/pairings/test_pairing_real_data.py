"""Golden checks of book → cocktail pairing on the real prepared data.

Skipped when `data/books.parquet` or `data/cocktails.parquet` is not prepared (CI without the
downloads); the E2E suite repeats the main checks through the running API.
"""

from pathlib import Path

import pytest

from curio.books.catalog import BookCatalog
from curio.cocktails.catalog import CocktailCatalog
from curio.cocktails.recommend import CocktailRecommender
from curio.pairings.domain import PairingRequest
from curio.pairings.pairer import BookCocktailPairer
from curio.pairings.themes import LORE, THEMES

DATA = Path(__file__).resolve().parents[2] / "data"
BOOKS, COCKTAILS = DATA / "books.parquet", DATA / "cocktails.parquet"
pytestmark = pytest.mark.skipif(
    not (BOOKS.exists() and COCKTAILS.exists()), reason="Real book and recipe data not prepared."
)
WHISKEY_WORDS = ("bourbon", "whiskey", "whisky", "scotch", "rye")
CHILDREN = ["Where the Wild Things Are", "Charlotte's Web", "Harry Potter and the Sorcerer's Stone"]


@pytest.fixture(scope="module")
def real():
    books = BookCatalog.from_parquet(BOOKS)
    cocktails = CocktailCatalog.from_parquet(COCKTAILS)
    return books, cocktails, BookCocktailPairer(books, cocktails, CocktailRecommender(cocktails))


def pairings(real, title: str, **request):
    books, _, pairer = real
    book = books.by_title(title)[0]
    return pairer.pair(PairingRequest(book_id=book.book_id, **request))


def test_every_signature_and_lore_drink_is_a_real_recipe(real):
    books, cocktails, _ = real
    names = {c.name for c in cocktails}
    for theme in (*THEMES.themes, THEMES.fallback):
        missing = set(theme.signatures) - names
        assert not missing, f"{theme.id}: {missing}"
        glasses = {c.glass for c in cocktails}
        assert set(theme.glasses) <= glasses, theme.id
    for lore in LORE:
        assert lore.drink in names
        assert books.canonical_author(lore.author) == lore.author


@pytest.mark.parametrize(
    "title", ["The Big Sleep", "The Maltese Falcon", "Murder on the Orient Express"]
)
def test_noir_and_mystery_get_a_whiskey_forward_drink(real, title):
    result = pairings(real, title)
    assert result.profile.themes[0].id == "noir"
    whiskey = [
        p
        for p in result.pairings
        if any(w in i.name.lower() for i in p.cocktail.ingredients for w in WHISKEY_WORDS)
    ]
    assert whiskey, [p.cocktail.name for p in result.pairings]
    assert "whiskey drink" in whiskey[0].reasons[0]


@pytest.mark.parametrize("title", CHILDREN)
def test_childrens_books_get_only_alcohol_free_drinks(real, title):
    for seed in range(5):
        result = pairings(real, title, seed=seed, top_k=5)
        assert result.profile.alcohol_free
        assert result.pairings
        assert all(p.cocktail.alcoholic == "Non alcoholic" for p in result.pairings)


def test_the_great_gatsby_gets_a_jazz_age_drink(real):
    result = pairings(real, "The Great Gatsby")
    jazz_age = {"Gin Rickey", "French 75", "Mint Julep", "Sidecar", "Mimosa"}
    first = result.pairings[0].cocktail
    assert first.name in jazz_age
    ingredients = {i.name.lower() for p in result.pairings for i in p.cocktail.ingredients}
    assert {"gin", "champagne"} & ingredients
    assert "The Great Gatsby" in result.pairings[0].reasons[0]


@pytest.mark.parametrize(
    ("title", "theme", "expected"),
    [
        ("The Name of the Wind", "fantasy", {"Honey Bee", "Figgy Thyme", "Hot Toddy"}),
        ("Neuromancer", "scifi", {"Grand Blue", "Rosemary Blue"}),
        ("Gone Girl", "thriller", {"Espresso Martini"}),
        ("Dracula", "horror", {"Corpse Reviver", "Death in the Afternoon"}),
        ("The Notebook", "romance", {"Kir Royale", "French 75"}),
        ("Casino Royale", "spy", {"Vesper"}),
    ],
)
def test_famous_books_pour_their_style(real, title, theme, expected):
    result = pairings(real, title)
    assert result.profile.themes[0].id == theme
    assert expected & {p.cocktail.name for p in result.pairings}


def test_every_set_is_explained_and_alcohol_labelled(real):
    titles = ["The Great Gatsby", "Pride and Prejudice", "Dune", "The Shining", "Outlander"]
    for title in titles:
        first = pairings(real, title)
        for seed in range(first.sets):
            result = pairings(real, title, seed=seed)
            assert len(result.pairings) == 3
            spirits = [p.cocktail.name for p in result.pairings]
            assert len(set(spirits)) == 3
            for item in result.pairings:
                assert 1 <= len(item.reasons) <= 3
                assert title in item.reasons[0]
                assert item.cocktail.category != "Shot"
