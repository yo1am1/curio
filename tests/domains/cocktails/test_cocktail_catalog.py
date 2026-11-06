import json
from pathlib import Path

import pandas as pd
import pytest

from curio.domains.cocktails.catalog import (
    SOURCE_URL,
    CocktailCatalog,
    CocktailDataMissingError,
    ingredient_key,
    trusted_image_url,
)
from curio.engine.data import DataFileMissingError
from tests.conftest import cocktail_frame


def _frame_with(**overrides: object) -> pd.DataFrame:
    frame = cocktail_frame()
    for column, value in overrides.items():
        frame.loc[0, column] = value
    return frame


def test_catalog_reads_every_recipe_in_id_order(cocktail_catalog: CocktailCatalog) -> None:
    assert len(cocktail_catalog) == 8
    assert [c.drink_id for c in cocktail_catalog] == list(range(1, 9))
    mojito = cocktail_catalog.get(1)
    assert mojito is not None
    assert mojito.name == "Mojito"
    assert mojito.source_url == SOURCE_URL
    assert mojito.image_url == "https://www.thecocktaildb.com/images/media/drink/1.jpg"
    assert [(i.name, i.measure) for i in mojito.ingredients][-1] == ("Soda water", None)


def test_get_unknown_and_get_many_keep_order(cocktail_catalog: CocktailCatalog) -> None:
    assert cocktail_catalog.get(999) is None
    assert [c.drink_id for c in cocktail_catalog.get_many([5, 999, 1, 5])] == [5, 1, 5]


def test_ingredients_and_categories_are_sorted_and_distinct(
    cocktail_catalog: CocktailCatalog,
) -> None:
    ingredients = cocktail_catalog.ingredients()
    assert ingredients == sorted(ingredients, key=str.casefold)
    assert ingredients.count("Lime juice") == 1
    assert "Lime" in ingredients
    assert cocktail_catalog.categories() == ["Cocktail", "Ordinary Drink", "Soft Drink"]


def test_case_variants_collapse_to_the_most_used_spelling() -> None:
    frame = cocktail_frame()
    frame.loc[1, "ingredients"] = json.dumps([{"name": "LIME  JUICE", "measure": "1 oz"}])
    catalog = CocktailCatalog(frame)
    assert [n for n in catalog.ingredients() if ingredient_key(n) == "lime juice"] == ["Lime juice"]
    assert catalog.canonical_ingredient("  lime JUICE ") == "Lime juice"
    assert catalog.canonical_ingredient("Unicorn tears") is None
    assert catalog.canonical_ingredient("") is None


@pytest.mark.parametrize(
    "url",
    [
        "http://www.thecocktaildb.com/images/a.jpg",
        "https://thecocktaildb.com/images/a.jpg",
        "https://www.thecocktaildb.com.evil.example/a.jpg",
        "https://user@www.thecocktaildb.com/a.jpg",
        "https://www.thecocktaildb.com:8443/a.jpg",
        "javascript:alert(1)",
        "",
        None,
        float("nan"),
    ],
)
def test_untrusted_image_urls_are_dropped(url: object) -> None:
    assert trusted_image_url(url) is None
    assert CocktailCatalog(_frame_with(image_url=url)).get(1).image_url is None


def test_trusted_image_url_is_kept() -> None:
    url = "https://www.thecocktaildb.com/images/media/drink/x.jpg"
    assert trusted_image_url(f"  {url} ") == url


def test_invalid_frames_are_rejected() -> None:
    with pytest.raises(ValueError, match="missing columns: ingredients"):
        CocktailCatalog(cocktail_frame().drop(columns="ingredients"))
    duplicated = cocktail_frame()
    duplicated.loc[1, "drink_id"] = 1
    with pytest.raises(ValueError, match="Duplicate drink_id 1"):
        CocktailCatalog(duplicated)
    with pytest.raises(ValueError, match="unknown alcoholic value"):
        CocktailCatalog(_frame_with(alcoholic="Sometimes"))
    with pytest.raises(ValueError, match="JSON list"):
        CocktailCatalog(_frame_with(ingredients='{"name": "Gin"}'))


def test_from_parquet_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cocktails.parquet"
    cocktail_frame().to_parquet(path, index=False)
    catalog = CocktailCatalog.from_parquet(path)
    assert [c.name for c in catalog.get_many([8])] == ["Shirley Temple"]


def test_from_parquet_missing_file_explains_how_to_prepare(tmp_path: Path) -> None:
    path = tmp_path / "cocktails.parquet"
    with pytest.raises(CocktailDataMissingError, match=r"scripts\.prepare_cocktails") as error:
        CocktailCatalog.from_parquet(path)
    assert isinstance(error.value, DataFileMissingError)
    assert isinstance(error.value, FileNotFoundError)
    assert str(path) in str(error.value)
