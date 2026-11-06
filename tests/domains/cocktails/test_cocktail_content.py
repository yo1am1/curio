"""Crude drink names and shots stay out of what Curio offers by default, and pantry results
put drinks you can nearly make first: a missing garnish or mixer before a missing base spirit."""

import pytest

from curio.domains.cocktails.catalog import CocktailCatalog
from curio.domains.cocktails.models import CocktailQuery
from curio.domains.cocktails.recommend import (
    CRUDE_NAMES,
    CocktailRecommender,
    asks_for_shots,
    is_crude,
    is_shot,
)
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


SHOT_ROWS = [
    (1, "Screwdriver", A, "Cocktail", "Highball glass",
     [("Vodka", "2 oz"), ("Orange juice", None)]),
    (2, "Jello shots", A, "Shot", "Shot glass",
     [("Vodka", "2 oz"), ("Jello", None), ("Water", None)]),
    (3, "Vodka Slammer", A, "Ordinary Drink", "Shot glass",
     [("Vodka", "2 oz"), ("Orange juice", None)]),
    (4, "Orange Crush", A, "Shot", "Shot glass", [("Vodka", "1 oz"), ("Orange juice", None)]),
]  # fmt: skip


@pytest.fixture(scope="module")
def shots() -> CocktailRecommender:
    return CocktailRecommender(CocktailCatalog(cocktail_frame(SHOT_ROWS)))


def test_shots_stay_out_of_pantry_matches_and_default_search(shots):
    catalog = CocktailCatalog(cocktail_frame(SHOT_ROWS))
    assert [is_shot(catalog.get(i)) for i in (1, 2, 3, 4)] == [False, True, True, True]
    assert names(shots.pantry(["Vodka", "Orange juice", "Jello"])) == ["Screwdriver"]
    assert names(shots.search(CocktailQuery(query="vodka orange"))) == ["Screwdriver"]
    assert names(shots.search(CocktailQuery(query=""))) == ["Screwdriver"]


def test_shots_are_found_when_the_request_asks_for_them(shots):
    pantry = shots.pantry(["Vodka", "Orange juice", "Jello"], shots=True)
    assert set(names(pantry)) == {"Screwdriver", "Jello shots", "Vodka Slammer", "Orange Crush"}
    assert "Orange Crush" in names(shots.search(CocktailQuery(query="vodka shots")))
    assert "Jello shots" in names(shots.search(CocktailQuery(query="", category="Shot")))
    assert names(shots.search(CocktailQuery(query="Orange Crush")))[0] == "Orange Crush"
    assert asks_for_shots("Tequila shooters for a party") and not asks_for_shots("A short drink")
