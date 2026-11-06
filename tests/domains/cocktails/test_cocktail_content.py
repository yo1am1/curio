"""Crude drink names stay out of what Curio offers, and pantry results put drinks you can
nearly make first: a missing garnish or mixer before a missing base spirit."""

import pytest

from curio.domains.cocktails.catalog import CocktailCatalog
from curio.domains.cocktails.models import CocktailQuery
from curio.domains.cocktails.recommend import CRUDE_NAMES, CocktailRecommender, is_crude
from tests.conftest import cocktail_frame

A = "Alcoholic"
ROWS = [
    (1, "After sex", A, "Cocktail", "Highball glass",
     [("Vodka", "2 cl"), ("Creme de Banane", "1 cl"), ("Orange juice", None)]),
    (2, "Screwdriver", A, "Cocktail", "Highball glass",
     [("Vodka", "2 oz"), ("Orange juice", None)]),
    (3, "Pornstar Martini", A, "Cocktail", "Cocktail glass",
     [("Vodka", "2 oz"), ("Passion fruit", None), ("Prosecco", None)]),
    # With gin, orange juice and tonic: missing only its garnish, half the recipe at hand.
    (4, "Gin Orange", A, "Cocktail", "Highball glass", [("Gin", "2 oz"), ("Orange peel", None)]),
    # Missing only its base spirit, though two thirds of the recipe are at hand.
    (5, "Rum Orange", A, "Cocktail", "Highball glass",
     [("Light rum", "2 oz"), ("Orange juice", None), ("Tonic water", None)]),
]  # fmt: skip


@pytest.fixture(scope="module")
def recommender() -> CocktailRecommender:
    return CocktailRecommender(CocktailCatalog(cocktail_frame(ROWS)))


def names(results) -> list[str]:
    return [r.cocktail.name for r in results]


def test_crude_names_are_never_offered_by_default(recommender):
    assert is_crude(CocktailCatalog(cocktail_frame(ROWS)).get(1))
    assert "after sex" in CRUDE_NAMES and "zipperhead" in CRUDE_NAMES
    assert "After sex" not in names(recommender.search(CocktailQuery(query="vodka orange")))
    assert "Pornstar Martini" not in names(recommender.search(CocktailQuery(query="")))
    assert "After sex" not in names(recommender.similar(2, 5))
    pantry = recommender.pantry(["Vodka", "Orange juice", "Creme de Banane"], max_missing=2)
    assert names(pantry)[0] == "Screwdriver" and "After sex" not in names(pantry)


def test_a_search_that_names_the_drink_still_finds_it(recommender):
    assert names(recommender.search(CocktailQuery(query="After sex"))) == ["After sex"]


def test_a_drink_missing_its_base_spirit_ranks_below_one_missing_a_garnish_or_mixer(
    recommender,
):
    matches = recommender.pantry(["Gin", "Orange juice", "Tonic water"], max_missing=2)
    assert names(matches) == ["Gin Orange", "Rum Orange", "Screwdriver"]
    assert [m.missing for m in matches] == [["Orange peel"], ["Light rum"], ["Vodka"]]
