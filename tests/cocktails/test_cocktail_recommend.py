import pandas as pd
import pytest
from pydantic import ValidationError

from clothes_recommender.cocktails.catalog import CocktailCatalog
from clothes_recommender.cocktails.domain import CocktailHit, CocktailQuery
from clothes_recommender.cocktails.recommend import (
    PANTRY_ALIASES,
    CocktailRecommender,
    pantry_coverage,
    tokens,
)
from tests.conftest import cocktail_frame


@pytest.fixture
def recommender(cocktail_catalog: CocktailCatalog) -> CocktailRecommender:
    return CocktailRecommender(cocktail_catalog)


def _ids(hits: list[CocktailHit]) -> list[int]:
    return [hit.cocktail.drink_id for hit in hits]


def _search(recommender: CocktailRecommender, **query: object) -> list[CocktailHit]:
    return recommender.search(CocktailQuery(**query))


def test_tokens_fold_case_accents_and_stopwords() -> None:
    assert tokens("Crème de Cassis and the Gin-Tonic") == ["creme", "de", "cassis", "gin", "tonic"]


def test_search_ranks_name_matches_first(recommender: CocktailRecommender) -> None:
    hits = _search(recommender, query="mojito")
    assert _ids(hits) == [1, 3]
    assert hits[0].score > hits[1].score > 0
    assert hits[0].reasons[0] == 'Name matches "mojito"'


def test_search_matches_ingredients_category_and_glass(recommender: CocktailRecommender) -> None:
    espresso = _search(recommender, query="espresso vodka")
    assert _ids(espresso)[0] == 7
    assert "Contains vodka, espresso" in espresso[0].reasons
    soft = _search(recommender, query="soft drink")
    assert set(_ids(soft)[:2]) == {6, 8}  # "drink" alone also matches "Ordinary Drink"
    assert "Category: Soft Drink" in soft[0].reasons
    collins = _search(recommender, query="collins")
    assert _ids(collins) == [6]
    assert collins[0].reasons == ["Served in a collins glass"]


def test_search_expands_unknown_words_as_prefixes(recommender: CocktailRecommender) -> None:
    assert _ids(_search(recommender, query="marg"))[0] == 5
    assert _search(recommender, query="ma") == []


@pytest.mark.parametrize("query", ["xyzzy", "unicorn tears"])
def test_search_without_any_known_word_is_empty(
    recommender: CocktailRecommender, query: str
) -> None:
    assert _search(recommender, query=query) == []


@pytest.mark.parametrize("query", ["", "   ", "the and of"])
def test_empty_query_browses_by_name(
    recommender: CocktailRecommender, cocktail_catalog: CocktailCatalog, query: str
) -> None:
    hits = _search(recommender, query=query, top_k=50)
    assert [h.cocktail.name for h in hits] == sorted(
        (c.name for c in cocktail_catalog), key=str.casefold
    )
    assert {h.score for h in hits} == {0.0}
    assert all(h.reasons == [] for h in hits)


def test_empty_query_with_filters_ranks_by_name(recommender: CocktailRecommender) -> None:
    hits = _search(recommender, alcohol="non_alcoholic")
    assert [h.cocktail.name for h in hits] == ["Lemonade", "Virgin Mojito"]
    assert {h.score for h in hits} == {1.0}
    assert [h.reasons for h in hits] == [["Alcohol-free"], ["Alcohol-free"]]


def test_alcohol_filters_treat_optional_alcohol_as_alcoholic(
    recommender: CocktailRecommender,
) -> None:
    alcohol_free = set(_ids(_search(recommender, alcohol="non_alcoholic")))
    alcoholic = _search(recommender, alcohol="alcoholic", top_k=50)
    assert alcohol_free == {3, 6}
    assert set(_ids(alcoholic)) == {1, 2, 4, 5, 7, 8}
    assert _ids(_search(recommender, query="mojito", alcohol="non_alcoholic")) == [3]
    assert all(
        h.reasons == ["Alcohol optional"] if h.cocktail.drink_id == 8 else ["Contains alcohol"]
        for h in alcoholic
    )


def test_required_ingredients_must_all_be_present(recommender: CocktailRecommender) -> None:
    assert _ids(_search(recommender, ingredients=["Lime juice", "mint"])) == [1, 3]
    # "lime" covers lime juice through the alias table and the "Lime" wedge directly.
    assert set(_ids(_search(recommender, ingredients=["lime"]))) == {1, 2, 3, 4, 5}
    # Every word of the filter must be a word of the ingredient: "rum" matches "Light rum".
    assert _ids(_search(recommender, ingredients=["RUM"])) == [2, 1]
    assert _search(recommender, ingredients=["Unicorn tears"]) == []
    mojito = _search(recommender, query="mojito", ingredients=["mint"])[0]
    assert "Contains mint" in mojito.reasons


def test_excluded_ingredients_must_all_be_absent(recommender: CocktailRecommender) -> None:
    without_rum = _search(recommender, exclude_ingredients=["rum"], top_k=50)
    assert set(_ids(without_rum)) == {3, 4, 5, 6, 7, 8}
    assert all("Without rum" in hit.reasons for hit in without_rum)
    without_lime = _search(recommender, exclude_ingredients=["lime", "sugar"], top_k=50)
    assert set(_ids(without_lime)) == {7, 8}
    assert _search(recommender, query="mojito", exclude_ingredients=["mint"]) == []


def test_blank_filter_terms_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CocktailQuery(ingredients=["  "])
    with pytest.raises(ValidationError):
        CocktailQuery(exclude_ingredients=[""])


def test_category_filter_is_case_insensitive(recommender: CocktailRecommender) -> None:
    assert _ids(_search(recommender, category="soft DRINK ")) == [6, 8]
    assert _search(recommender, category="Beer") == []


def test_search_top_k_bounds(recommender: CocktailRecommender) -> None:
    assert len(_search(recommender, top_k=2)) == 2
    assert len(_search(recommender, top_k=50)) == 8
    with pytest.raises(ValidationError):
        CocktailQuery(top_k=0)
    with pytest.raises(ValidationError):
        CocktailQuery(top_k=51)


@pytest.fixture
def twins() -> CocktailRecommender:
    """Daiquiri (2) plus two identical copies with higher ids, inserted out of order."""
    frame = cocktail_frame()
    daiquiri = frame[frame["drink_id"] == 2]
    copies = pd.concat([daiquiri.assign(drink_id=10), daiquiri.assign(drink_id=9)])
    return CocktailRecommender(CocktailCatalog(pd.concat([copies, frame], ignore_index=True)))


def test_ties_break_by_lower_drink_id(twins: CocktailRecommender) -> None:
    assert _ids(twins.search(CocktailQuery(query="daiquiri"))) == [2, 9, 10]
    assert _ids(twins.similar(9, top_k=2)) == [2, 10]
    assert _ids(twins.search(CocktailQuery(ingredients=["sugar"], top_k=50)))[:3] == [2, 9, 10]
    first = [m.cocktail.drink_id for m in twins.pantry(["light rum", "lime juice", "sugar"])]
    assert first[:3] == [2, 9, 10]


def test_results_are_deterministic(cocktail_catalog: CocktailCatalog) -> None:
    query = CocktailQuery(query="lime rum mint", top_k=50)
    runs = [CocktailRecommender(cocktail_catalog).search(query) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


def test_similar_uses_shared_ingredients(recommender: CocktailRecommender) -> None:
    hits = recommender.similar(1)
    assert _ids(hits)[0] == 3
    assert 1 not in _ids(hits)
    assert hits[0].reasons[0].startswith("Shares mint, soda water")
    assert "Also a cocktail" in hits[0].reasons
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(0 < score <= 1 for score in scores)
    # Espresso Martini shares nothing with the Mojito.
    assert 7 not in _ids(hits)


def test_similar_respects_alcohol_filter_and_top_k(recommender: CocktailRecommender) -> None:
    alcohol_free = recommender.similar(1, top_k=10, alcohol="non_alcoholic")
    assert _ids(alcohol_free) == [3, 6]
    assert alcohol_free[0].reasons[-1] == "Alcohol-free"
    # The gin and tonic's lime wedge counts as the virgin mojito's lime juice.
    assert set(_ids(recommender.similar(3, top_k=10, alcohol="alcoholic"))) == {1, 2, 4, 5}
    assert len(recommender.similar(1, top_k=1)) == 1
    assert recommender.similar(1, top_k=0) == []


def test_similar_unknown_id_is_empty(recommender: CocktailRecommender) -> None:
    assert recommender.similar(999) == []


def test_pantry_ranks_fewest_missing_then_coverage_then_name(
    recommender: CocktailRecommender,
) -> None:
    matches = recommender.pantry(["Light rum", "lime", "sugar", "mint", "soda"], max_missing=3)
    names = [m.cocktail.name for m in matches]
    assert names[:3] == ["Daiquiri", "Mojito", "Virgin Mojito"]
    assert all(m.missing == [] and m.coverage == 1.0 for m in matches[:3])
    rest = matches[3:]
    assert [(len(m.missing), -m.coverage) for m in rest] == sorted(
        (len(m.missing), -m.coverage) for m in rest
    )
    margarita = next(m for m in matches if m.cocktail.drink_id == 5)
    assert margarita.have == ["Lime juice"]
    assert margarita.missing == ["Tequila", "Triple sec", "Salt"]
    assert margarita.coverage == 0.25


def test_pantry_max_missing_alcohol_and_top_k(recommender: CocktailRecommender) -> None:
    pantry = ["light rum", "lime", "sugar", "mint", "soda"]
    assert {m.cocktail.drink_id for m in recommender.pantry(pantry, max_missing=0)} == {1, 2, 3}
    alcohol_free = recommender.pantry(pantry, alcohol="non_alcoholic", max_missing=3)
    assert [m.cocktail.drink_id for m in alcohol_free] == [3, 6]
    assert len(recommender.pantry(pantry, top_k=1)) == 1
    assert recommender.pantry(pantry, top_k=0) == []
    assert {m.cocktail.drink_id for m in recommender.pantry(pantry, max_missing=-1)} == {1, 2, 3}


@pytest.mark.parametrize("pantry", [[], [""], ["   "]])
def test_empty_pantry_is_empty(recommender: CocktailRecommender, pantry: list[str]) -> None:
    assert recommender.pantry(pantry) == []


def test_pantry_needs_at_least_one_recipe_ingredient(recommender: CocktailRecommender) -> None:
    assert recommender.pantry(["Unicorn tears"], max_missing=5) == []
    # Staples (ice, water) the shopper did not list never make a recipe a match on their own:
    # the Lemonade needs lemon juice and sugar, and the gin only covers the gin and tonic.
    assert [m.cocktail.drink_id for m in recommender.pantry(["Gin"], max_missing=5)] == [4]


def test_pantry_matching_is_case_and_space_insensitive(recommender: CocktailRecommender) -> None:
    matches = recommender.pantry(["  GIN ", "tonic   WATER", "Lime"], max_missing=0)
    assert [(m.cocktail.drink_id, m.have) for m in matches] == [(4, ["Gin", "Tonic water", "Lime"])]


def test_aliases_only_go_from_the_fruit_to_the_juice(recommender: CocktailRecommender) -> None:
    # Lime juice does not give a lime wedge, so the gin and tonic still misses the wedge.
    matches = recommender.pantry(["gin", "tonic water", "lime juice"], max_missing=1)
    gin_tonic = next(m for m in matches if m.cocktail.drink_id == 4)
    assert gin_tonic.missing == ["Lime"]


def test_staples_are_never_missing(recommender: CocktailRecommender) -> None:
    lemonade = recommender.pantry(["lemon", "sugar"], max_missing=0)
    assert [(m.cocktail.name, m.have, m.missing) for m in lemonade] == [
        ("Lemonade", ["Lemon juice", "Sugar", "Water"], [])
    ]


@pytest.mark.parametrize(
    ("item", "covers"),
    [
        ("lime", {"lime", "lime juice", "fresh lime juice", "lime peel"}),
        ("Lemon", {"lemon", "lemon juice", "fresh lemon juice", "lemon peel"}),
        ("soda", {"soda", "soda water", "club soda", "carbonated water"}),
        ("Club Soda", {"club soda", "soda water", "carbonated water"}),
        ("sugar", {"sugar", "sugar syrup", "simple syrup"}),
        ("rum", {"rum", "light rum", "white rum", "dark rum", "gold rum"}),
        ("lime juice", {"lime juice"}),
        ("Gin", {"gin"}),
    ],
)
def test_alias_table(item: str, covers: set[str]) -> None:
    assert pantry_coverage([item]) == covers


def test_alias_table_is_normalised() -> None:
    for key, values in PANTRY_ALIASES.items():
        assert key == key.strip().casefold()
        assert all(value == value.strip().casefold() for value in values)
