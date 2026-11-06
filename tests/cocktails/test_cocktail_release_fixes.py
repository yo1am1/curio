"""Regressions for the v1.0 pre-release audit of the cocktail domain (SAFE-1, C1, SEC-1, C2,
VAL-3, C3, C5, C7)."""

import json
import time
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from curio.cocktails import recommend
from curio.cocktails.catalog import CocktailCatalog
from curio.cocktails.domain import CocktailHit, CocktailPreferences, CocktailQuery
from curio.cocktails.recommend import CocktailRecommender
from tests.conftest import cocktail_frame

REAL_CATALOG = Path(__file__).resolve().parents[2] / "data" / "cocktails.parquet"


def _row(drink_id: int, name: str, alcoholic: str, ingredients: list[str]) -> dict:
    return {
        "drink_id": drink_id,
        "name": name,
        "alcoholic": alcoholic,
        "category": "Cocktail",
        "glass": "Highball glass",
        "instructions": "Build it.",
        "image_url": None,
        "ingredients": json.dumps([{"name": n, "measure": None} for n in ingredients]),
    }


EXTRA_ROWS = [
    _row(9, "Wine Cooler", "Optional alcohol", ["Red wine", "Lemon-lime soda", "Ice"]),
    _row(10, "Egg Nog", "Optional alcohol", ["Eggs", "Milk", "Light rum", "Bourbon"]),
    _row(11, "Mango Mojito", "Alcoholic", ["White Rum", "Lime", "Mint", "Soda Water", "Mango"]),
    _row(12, "Whiskey Sour", "Alcoholic", ["Blended whiskey", "Lemon", "Sugar"]),
    _row(13, "Cherry Fizz", "Non alcoholic", ["Maraschino cherries", "Soda water"]),
    _row(14, "Iced Coffee", "Non alcoholic", ["Coffee", "Sugar", "Milk"]),
    _row(15, "Black Coffee", "Non alcoholic", ["Coffee", "Water"]),
    _row(16, "Fruit Punch", "Non alcoholic", ["Pineapple juice", "Orange juice", "Strawberries"]),
]


@pytest.fixture
def extended() -> CocktailRecommender:
    frame = pd.concat([cocktail_frame(), pd.DataFrame(EXTRA_ROWS)], ignore_index=True)
    return CocktailRecommender(CocktailCatalog(frame))


def _ids(hits: list[CocktailHit]) -> list[int]:
    return [hit.cocktail.drink_id for hit in hits]


def _names(hits: list[CocktailHit]) -> list[str]:
    return [hit.cocktail.name for hit in hits]


# SAFE-1 / C1: "Optional alcohol" recipes are not alcohol-free.


def test_optional_alcohol_is_not_alcohol_free(cocktail_catalog: CocktailCatalog) -> None:
    shirley_temple = cocktail_catalog.get(8)
    assert shirley_temple is not None and shirley_temple.alcoholic == "Optional alcohol"
    assert not shirley_temple.alcohol_free
    assert cocktail_catalog.get(3).alcohol_free  # "Non alcoholic"


def test_non_alcoholic_filter_drops_optional_alcohol(extended: CocktailRecommender) -> None:
    wine = extended.search(CocktailQuery(query="wine", alcohol="non_alcoholic"))
    assert wine == []
    assert 9 in _ids(extended.search(CocktailQuery(query="wine", alcohol="alcoholic")))
    pantry = extended.pantry(["red wine", "lemon-lime soda"], alcohol="non_alcoholic")
    assert pantry == []
    assert all(h.cocktail.alcohol_free for h in extended.similar(1, 20, "non_alcoholic"))
    optional = extended.search(CocktailQuery(query="egg nog", alcohol="alcoholic"))
    assert _ids(optional) == [10]
    assert "Alcohol optional" in optional[0].reasons


# SEC-1 / C2 / VAL-3: bounded ingredient strings, terms normalised once per query.


@pytest.mark.parametrize("field", ["ingredients", "exclude_ingredients"])
def test_query_ingredient_terms_are_bounded(field: str) -> None:
    with pytest.raises(ValidationError):
        CocktailQuery(**{field: ["zzz " * 50]})
    with pytest.raises(ValidationError):
        CocktailQuery(**{field: ["  "]})
    assert getattr(CocktailQuery(**{field: ["  Lime  "]}), field) == ["Lime"]


@pytest.mark.parametrize("field", ["liked_ingredients", "avoided_ingredients"])
def test_preference_ingredient_terms_are_bounded(field: str) -> None:
    with pytest.raises(ValidationError):
        CocktailPreferences(**{field: ["Gin.\n\n## " + "gin " * 1000]})
    with pytest.raises(ValidationError):
        CocktailPreferences(**{field: [""]})
    assert getattr(CocktailPreferences(**{field: [" gin "]}), field) == ["gin"]


@pytest.fixture
def large() -> CocktailRecommender:
    """425 recipes, the size of the shipped catalog."""
    base = cocktail_frame()
    frame = pd.concat(
        [base.assign(drink_id=base["drink_id"] + 8 * copy) for copy in range(54)],
        ignore_index=True,
    ).head(425)
    return CocktailRecommender(CocktailCatalog(frame))


def test_filter_terms_are_tokenised_once_per_query(
    large: CocktailRecommender, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    original = recommend.tokens

    def counting(text: str) -> list[str]:
        nonlocal calls
        calls += 1
        return original(text)

    monkeypatch.setattr(recommend, "tokens", counting)
    terms = [f"{i}" + "q" * 59 for i in range(10)]
    large.search(CocktailQuery(ingredients=terms[:5], exclude_ingredients=terms[5:]))
    assert calls <= 50  # was one call per term per recipe ingredient (thousands)


def test_long_filter_terms_stay_fast(large: CocktailRecommender) -> None:
    terms = [f"{i} " + "zz " * 19 for i in range(10)]
    started = time.perf_counter()
    large.search(CocktailQuery(exclude_ingredients=terms, top_k=50))
    large.search(CocktailQuery(query="lime", ingredients=terms, top_k=50))
    assert time.perf_counter() - started < 1.0


# C3: plurals and spelling variants in ingredient filters and the pantry.


def test_excluding_plurals_and_variants(extended: CocktailRecommender) -> None:
    def without(*terms: str) -> set[int]:
        return set(_ids(extended.search(CocktailQuery(exclude_ingredients=list(terms), top_k=50))))

    assert 10 not in without("eggs")
    assert 10 not in without("egg")
    assert 12 not in without("whisky")
    assert 13 not in without("cherry")
    assert 8 not in without("cherries")  # Maraschino cherry
    assert 1 not in without("limes")
    assert 3 in without("eggs")


def test_requiring_plurals_and_variants(extended: CocktailRecommender) -> None:
    def having(term: str) -> list[int]:
        return _ids(extended.search(CocktailQuery(ingredients=[term], top_k=50)))

    assert having("eggs") == having("egg") == [10]
    assert having("Whisky") == [12]
    assert set(having("cherries")) == {8, 13}
    assert set(having("strawberry")) == {16}


def test_pantry_accepts_plurals(extended: CocktailRecommender) -> None:
    matches = extended.pantry(["eggs", "milk", "light rum", "bourbons"], max_missing=0)
    assert [m.cocktail.drink_id for m in matches] == [10]


# C5: plurals and flavour words in free-text search.


@pytest.mark.parametrize(
    ("query", "first"),
    [("margaritas", 5), ("mojitos", 1), ("daiquiris", 2), ("limes", None)],
)
def test_search_understands_plurals(
    extended: CocktailRecommender, query: str, first: int | None
) -> None:
    hits = extended.search(CocktailQuery(query=query))
    assert hits
    if first is not None:
        assert _ids(hits)[0] == first


def test_search_understands_flavour_words(extended: CocktailRecommender) -> None:
    fresh = extended.search(CocktailQuery(query="Something fresh with lime and mint"))
    assert set(_ids(fresh)[:3]) <= {1, 3, 11}
    minty = extended.search(CocktailQuery(query="minty"))
    assert set(_ids(minty)[:3]) == {1, 3, 11}
    creamy = extended.search(CocktailQuery(query="Creamy coffee"))
    assert _ids(creamy)[0] == 14  # coffee with milk before plain coffee
    fruity = extended.search(CocktailQuery(query="Something fruity", alcohol="non_alcoholic"))
    assert _ids(fruity)[0] == 16


def test_alcohol_free_words_become_the_filter(extended: CocktailRecommender) -> None:
    for query in ["Something fruity and alcohol-free", "a non-alcoholic mojito", "mocktail"]:
        hits = extended.search(CocktailQuery(query=query))
        assert hits, query
        assert all(h.cocktail.alcohol_free for h in hits), query
        assert all("Alcohol-free" in h.reasons for h in hits), query
    assert _ids(extended.search(CocktailQuery(query="a non-alcoholic mojito")))[0] == 3


# C7: similar() compares canonical ingredients.


def test_similar_treats_lime_and_white_rum_variants_as_the_same(
    extended: CocktailRecommender,
) -> None:
    hits = extended.similar(1, top_k=3)
    assert _ids(hits)[:2] == [3, 11]  # Virgin Mojito, then Mango Mojito (was below Daiquiri)
    assert hits[1].reasons[0] == "Shares mint, light rum, soda water +1 more"
    gin_tonic = extended.similar(4, top_k=10)
    assert {1, 2, 3, 5} <= set(_ids(gin_tonic))  # Lime ~ Lime juice


# Probes on the shipped catalog (skipped when it has not been prepared).


@pytest.fixture(scope="module")
def real() -> CocktailRecommender:
    if not REAL_CATALOG.exists():
        pytest.skip("data/cocktails.parquet is not prepared")
    return CocktailRecommender(CocktailCatalog.from_parquet(REAL_CATALOG))


def _ingredient_names(hit: CocktailHit) -> set[str]:
    return {i.name.casefold() for i in hit.cocktail.ingredients}


def test_real_catalog_alcohol_free_has_no_optional_alcohol(real: CocktailRecommender) -> None:
    for query in ["wine", "galliano", "bourbon", "rum"]:
        hits = real.search(CocktailQuery(query=query, alcohol="non_alcoholic", top_k=50))
        assert all(h.cocktail.alcoholic == "Non alcoholic" for h in hits), query


def test_real_catalog_example_prompts(real: CocktailRecommender) -> None:
    fresh = real.search(CocktailQuery(query="Something fresh with lime and mint", top_k=5))
    assert "Mojito" in _names(fresh[:3])
    assert all("mint" in " ".join(_ingredient_names(hit)) for hit in fresh[:3])
    assert sum("lime" in " ".join(_ingredient_names(hit)) for hit in fresh) >= 4
    creamy = real.search(CocktailQuery(query="Creamy coffee", top_k=5))
    for hit in creamy:
        words = " ".join(_ingredient_names(hit))
        assert "coffee" in words or "kahlua" in words, hit.cocktail.name
        assert any(w in words for w in ("cream", "milk", "baileys")), hit.cocktail.name
    rum_lime = real.search(CocktailQuery(query="Something with rum and lime", top_k=5))
    for hit in rum_lime:
        words = " ".join(_ingredient_names(hit))
        assert "rum" in words and "lime" in words, hit.cocktail.name
    fruity = real.search(
        CocktailQuery(query="Something fruity and alcohol-free", alcohol="non_alcoholic")
    )
    assert fruity and all(h.cocktail.alcohol_free for h in fruity)
    for query in ["margaritas", "mojitos", "limes", "minty"]:
        assert real.search(CocktailQuery(query=query)), query
    without_eggs = real.search(CocktailQuery(exclude_ingredients=["eggs"], query="egg nog"))
    assert not any("egg" in " ".join(_ingredient_names(h)) for h in without_eggs)
    assert "Egg Nog #4" not in _names(without_eggs)


def test_real_catalog_similar_mojito(real: CocktailRecommender) -> None:
    hits = real.search(CocktailQuery(query="mojito"))
    mojito = next(h.cocktail for h in hits if h.cocktail.name == "Mojito")
    similar = real.similar(mojito.drink_id, top_k=3)
    assert "Mango Mojito" in _names(similar)
