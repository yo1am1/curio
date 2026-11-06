"""In-memory access to the prepared cocktail recipes (`data/cocktails.parquet`)."""

import json
import re
import unicodedata
from collections.abc import Iterable, Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd

from curio.domains.cocktails.models import (
    ALCOHOLIC,
    NON_ALCOHOLIC,
    OPTIONAL_ALCOHOL,
    Cocktail,
    Ingredient,
)
from curio.engine.data import DataFileMissingError

# The CSV ids are not TheCocktailDB drink ids, so recipes can only credit the site itself.
SOURCE_URL = "https://www.thecocktaildb.com/"
IMAGE_HOST = "www.thecocktaildb.com"
ALCOHOL_VALUES = (ALCOHOLIC, NON_ALCOHOLIC, OPTIONAL_ALCOHOL)
COLUMNS = (
    "drink_id",
    "name",
    "alcoholic",
    "category",
    "glass",
    "instructions",
    "image_url",
    "ingredients",
)

_WHITESPACE = re.compile(r"\s+")


class CocktailDataMissingError(DataFileMissingError):
    """`data/cocktails.parquet` has not been prepared yet."""

    def __init__(self, path: Path) -> None:
        FileNotFoundError.__init__(
            self,
            f"Cocktail recipes not found: {path}. Prepare them with "
            "`uv run python -m scripts.prepare_cocktails` (see README).",
        )


def ingredient_key(name: str) -> str:
    """Case, width and spacing-insensitive form of a name ("Lime  Juice" -> "lime juice")."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", name)).strip().casefold()


def trusted_image_url(url: object) -> str | None:
    """`url` when it is an https link on TheCocktailDB, else None (nothing else is loaded)."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.hostname != IMAGE_HOST or parts.port is not None:
        return None
    if parts.username or parts.password:
        return None
    return url


class CocktailCatalog:
    def __init__(self, frame: pd.DataFrame) -> None:
        missing = [column for column in COLUMNS if column not in frame.columns]
        if missing:
            raise ValueError(f"Cocktail catalog is missing columns: {', '.join(missing)}")
        cocktails = [_cocktail(record) for record in frame[list(COLUMNS)].to_dict("records")]
        self._by_id: dict[int, Cocktail] = {}
        for cocktail in sorted(cocktails, key=lambda c: c.drink_id):
            if cocktail.drink_id in self._by_id:
                raise ValueError(f"Duplicate drink_id {cocktail.drink_id} in cocktail catalog")
            self._by_id[cocktail.drink_id] = cocktail
        self._canonical = _canonical_spellings(self._by_id.values())

    @classmethod
    def from_parquet(cls, path: Path) -> "CocktailCatalog":
        if not path.exists():
            raise CocktailDataMissingError(path)
        return cls(pd.read_parquet(path))

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Cocktail]:
        """Recipes in drink_id order."""
        return iter(self._by_id.values())

    def get(self, drink_id: int) -> Cocktail | None:
        return self._by_id.get(drink_id)

    def get_many(self, drink_ids: Iterable[int]) -> list[Cocktail]:
        """Recipes in the given order, silently skipping unknown ids."""
        return [c for drink_id in drink_ids if (c := self.get(drink_id)) is not None]

    def ingredients(self) -> list[str]:
        """Sorted distinct ingredient names, one spelling per case-insensitive name."""
        return sorted(self._canonical.values(), key=lambda name: (name.casefold(), name))

    def categories(self) -> list[str]:
        return sorted({c.category for c in self._by_id.values() if c.category})

    def canonical_ingredient(self, name: str) -> str | None:
        """The catalog spelling of an ingredient, matched ignoring case and spacing."""
        return self._canonical.get(ingredient_key(name))


def _cocktail(record: dict) -> Cocktail:
    alcoholic = str(record["alcoholic"]).strip()
    if alcoholic not in ALCOHOL_VALUES:
        raise ValueError(f"Drink {record['drink_id']} has unknown alcoholic value {alcoholic!r}")
    return Cocktail(
        drink_id=int(record["drink_id"]),
        name=str(record["name"]).strip(),
        alcoholic=alcoholic,
        category=_text(record["category"]),
        glass=_text(record["glass"]),
        instructions=_text(record["instructions"]),
        ingredients=_ingredients(record["ingredients"]),
        image_url=trusted_image_url(record["image_url"]),
        source_url=SOURCE_URL,
    )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _ingredients(value: object) -> list[Ingredient]:
    items = json.loads(value) if isinstance(value, str) else value
    if not isinstance(items, list):
        raise ValueError(f"Ingredients must be a JSON list, got {type(items).__name__}")
    return [Ingredient.model_validate(item) for item in items]


def _canonical_spellings(cocktails: Iterable[Cocktail]) -> dict[str, str]:
    """Ingredient key -> the most used spelling (ties: the alphabetically first)."""
    counts: dict[str, dict[str, int]] = {}
    for cocktail in cocktails:
        for ingredient in cocktail.ingredients:
            spellings = counts.setdefault(ingredient_key(ingredient.name), {})
            name = ingredient.name.strip()
            spellings[name] = spellings.get(name, 0) + 1
    return {
        key: min(spellings, key=lambda name: (-spellings[name], name))
        for key, spellings in counts.items()
        if key
    }
