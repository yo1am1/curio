"""The fashion agent's session state and approved effects, as the client holds them."""

from typing import ClassVar, Literal

from pydantic import BaseModel, Field, PositiveInt

from curio.domains.fashion.outfits import OutfitState
from curio.engine.schemas import AppliedEffect, ChatTurn, TurnContext


class ShopperPreferences(BaseModel):
    """Approved, session-scoped preferences the client sends back with each message."""

    sections: list[str] = Field(default_factory=list, max_length=3)
    colours: list[str] = Field(default_factory=list, max_length=5)
    max_price: float | None = Field(default=None, gt=0, le=10_000)

    def is_empty(self) -> bool:
        return not (self.sections or self.colours or self.max_price is not None)


class FashionChatTurn(ChatTurn):
    # Product cards shown with an assistant turn, so follow-ups can refer to them.
    product_ids: list[PositiveInt] = Field(default_factory=list, max_length=5)


class FashionContext(TurnContext):
    preferences: ShopperPreferences = Field(default_factory=ShopperPreferences)
    shopper_id: PositiveInt | None = None
    shortlist: list[PositiveInt] = Field(default_factory=list, max_length=20)
    # The latest outfit the fashion agent built, so a follow-up can swap or save its pieces.
    outfit: OutfitState | None = None


class FashionEffect(AppliedEffect):
    """An approved shortlist addition or remembered shopping preferences."""

    kind: Literal["shortlist", "preferences"]
    product_ids: list[int] = Field(default_factory=list)
    preferences: ShopperPreferences | None = None

    remembers: ClassVar = {"preferences": "preferences"}
    adds: ClassVar = {"product_ids": "shortlist"}
