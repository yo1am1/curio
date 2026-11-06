"""Validated actions available to the cocktail assistant, each with an execution policy.

The schemas follow `curio.engine.tools`: strict, bounded, and executed only
under their policy. AskShopper is the engine's shared ASK tool.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints, model_validator

from curio.domains.cocktails.models import AlcoholFilter
from curio.engine.tools import AskShopper, Policy

IngredientName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)
]


class SearchCocktails(BaseModel):
    """Search the recipe catalog by description (name, style, flavour words, glass), required
    ingredients, alcohol ('alcoholic', 'non_alcoholic' or 'any') and category. 'non_alcoholic'
    keeps only alcohol-free recipes; alcohol-optional ones count as alcoholic. The shopper's
    remembered tastes (alcohol-free, avoided ingredients) are applied automatically."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=200)
    ingredients: list[IngredientName] = Field(default_factory=list, max_length=10)
    alcohol: AlcoholFilter = "any"
    category: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def _not_empty(self) -> "SearchCocktails":
        if not (self.query.strip() or self.ingredients or self.category or self.alcohol != "any"):
            raise ValueError("Give a query, ingredients, a category or an alcohol filter")
        return self


class InspectCocktails(BaseModel):
    """Read full recipes (ingredients with amounts, glass, method) for up to five drink IDs."""

    model_config = ConfigDict(extra="forbid")
    drink_ids: list[PositiveInt] = Field(min_length=1, max_length=5)


class SimilarCocktails(BaseModel):
    """Find drinks that share the most distinctive ingredients with one catalog drink."""

    model_config = ConfigDict(extra="forbid")
    drink_id: PositiveInt
    alcohol: AlcoholFilter = "any"


class WhatCanIMake(BaseModel):
    """Rank drinks the shopper can make from the ingredients they have, allowing up to
    `max_missing` missing ingredients; each result lists what they have and what is missing."""

    model_config = ConfigDict(extra="forbid")
    ingredients: list[IngredientName] = Field(min_length=1, max_length=15)
    alcohol: AlcoholFilter = "any"
    max_missing: int = Field(default=2, ge=0, le=3)


class SaveCocktails(BaseModel):
    """Propose saving catalog drinks to the shopper's cocktail shortlist. The shopper must
    approve it before anything is saved; this ends your turn. Only propose when they ask to
    save, keep or shortlist drinks."""

    model_config = ConfigDict(extra="forbid")
    drink_ids: list[PositiveInt] = Field(min_length=1, max_length=5)
    note: str = Field(default="", max_length=200)


class RememberTastes(BaseModel):
    """Propose remembering drink tastes for later searches in this session: alcohol-free only
    (which also leaves out alcohol-optional recipes), liked ingredients, avoided ingredients.
    The shopper must approve it first; this ends your turn. Only propose tastes the shopper
    stated as lasting ('I don't drink alcohol', 'I hate gin'). Avoided ingredients filter
    listed recipe ingredients; they are no allergy guarantee."""

    model_config = ConfigDict(extra="forbid")
    alcohol_free: bool = False
    liked_ingredients: list[IngredientName] = Field(default_factory=list, max_length=10)
    avoided_ingredients: list[IngredientName] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _not_empty(self) -> "RememberTastes":
        if not (self.alcohol_free or self.liked_ingredients or self.avoided_ingredients):
            raise ValueError("Propose at least one taste")
        return self


COCKTAIL_TOOLS: dict[str, type[BaseModel]] = {
    schema.__name__: schema
    for schema in (
        SearchCocktails,
        InspectCocktails,
        SimilarCocktails,
        WhatCanIMake,
        AskShopper,
        SaveCocktails,
        RememberTastes,
    )
}

COCKTAIL_POLICIES: dict[str, Policy] = {
    "SearchCocktails": Policy.READ,
    "InspectCocktails": Policy.READ,
    "SimilarCocktails": Policy.READ,
    "WhatCanIMake": Policy.READ,
    "AskShopper": Policy.ASK,
    "SaveCocktails": Policy.APPROVE,
    "RememberTastes": Policy.APPROVE,
}
