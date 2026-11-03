"""Cocktail domain types shared by the catalog, recommenders, agent, API and UI.

Recipes come from a prepared, offline catalog (`data/cocktails.parquet`, built by
`scripts.prepare_cocktails`). Recipe data originates from TheCocktailDB.
"""

from typing import Literal

from pydantic import BaseModel, Field, PositiveInt

AlcoholFilter = Literal["any", "alcoholic", "non_alcoholic"]
# Values as stored in the catalog's `alcoholic` column.
ALCOHOLIC = "Alcoholic"
NON_ALCOHOLIC = "Non alcoholic"
OPTIONAL_ALCOHOL = "Optional alcohol"
SOURCE_NAME = "TheCocktailDB"


class Ingredient(BaseModel):
    name: str
    measure: str | None = None


class Cocktail(BaseModel):
    drink_id: PositiveInt
    name: str
    alcoholic: str  # ALCOHOLIC, NON_ALCOHOLIC or OPTIONAL_ALCOHOL
    category: str
    glass: str
    instructions: str
    ingredients: list[Ingredient]
    # Only https URLs on www.thecocktaildb.com are kept; anything else becomes None.
    image_url: str | None = None
    source_url: str

    @property
    def alcohol_free(self) -> bool:
        """True for recipes that can be made without alcohol (non-alcoholic or optional)."""
        return self.alcoholic in (NON_ALCOHOLIC, OPTIONAL_ALCOHOL)


class CocktailHit(BaseModel):
    cocktail: Cocktail
    score: float
    reasons: list[str] = Field(default_factory=list)


class PantryMatch(BaseModel):
    """A recipe scored by how much of it the shopper's pantry covers."""

    cocktail: Cocktail
    have: list[str]  # recipe ingredient names (catalog spelling) the pantry covers
    missing: list[str]  # recipe ingredient names the pantry lacks
    coverage: float = Field(ge=0, le=1)


class CocktailPreferences(BaseModel):
    """Approved, session-scoped cocktail tastes the client sends back with each message."""

    alcohol_free: bool = False
    liked_ingredients: list[str] = Field(default_factory=list, max_length=10)
    # A hard exclusion filter on listed recipe ingredients; never an allergen guarantee.
    avoided_ingredients: list[str] = Field(default_factory=list, max_length=10)

    def is_empty(self) -> bool:
        return not (self.alcohol_free or self.liked_ingredients or self.avoided_ingredients)


class CocktailQuery(BaseModel):
    query: str = Field(default="", max_length=200)
    ingredients: list[str] = Field(default_factory=list, max_length=10)
    alcohol: AlcoholFilter = "any"
    category: str | None = Field(default=None, max_length=60)
    exclude_ingredients: list[str] = Field(default_factory=list, max_length=10)
    top_k: PositiveInt = Field(default=10, le=50)


DrinkId = PositiveInt
