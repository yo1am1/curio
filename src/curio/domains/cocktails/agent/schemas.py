"""The cocktail agent's session state and approved effects, as the client holds them."""

from typing import ClassVar, Literal

from pydantic import Field, PositiveInt

from curio.domains.cocktails.models import CocktailPreferences
from curio.engine.schemas import AppliedEffect, ChatTurn, TurnContext


class CocktailChatTurn(ChatTurn):
    # Cocktail cards shown with an assistant turn, so follow-ups can refer to them.
    drink_ids: list[PositiveInt] = Field(default_factory=list, max_length=5)


class CocktailContext(TurnContext):
    cocktail_preferences: CocktailPreferences = Field(default_factory=CocktailPreferences)
    cocktail_shortlist: list[PositiveInt] = Field(default_factory=list, max_length=20)


class CocktailEffect(AppliedEffect):
    """Approved drinks for the cocktail shortlist, or remembered tastes."""

    kind: Literal["cocktail_shortlist", "cocktail_preferences"]
    drink_ids: list[int] = Field(default_factory=list)
    cocktail_preferences: CocktailPreferences | None = None

    remembers: ClassVar = {"cocktail_preferences": "cocktail_preferences"}
    adds: ClassVar = {"drink_ids": "cocktail_shortlist"}
