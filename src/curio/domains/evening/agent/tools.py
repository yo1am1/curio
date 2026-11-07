"""Validated actions available to the evening assistant, each with an execution policy.

The schemas follow `curio.engine.tools`: strict, bounded, and executed only under their
policy. AskShopper is the engine's shared ASK tool.
"""

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from curio.domains.evening.models import (
    MAX_BUDGET,
    EveningAudience,
    EveningPart,
    EveningSwapMode,
    Mood,
    Occasion,
)
from curio.domains.fashion.outfits import MAX_SLOTS, SlotName
from curio.engine.tools import AskShopper, Policy


class PlanEvening(BaseModel):
    """Plan one coherent evening for an occasion: an outfit, a book and a drink the pairer
    pairs with that book, under one theme (cosy, romantic, glamorous, summery, mysterious,
    thoughtful, adventurous or playful), with a reason for each part and a 'why it works'
    line. Pass the occasion in the shopper's words, plus the outfit budget in euros (a
    budget in dollars or pounds as the same number: prices are euros and nothing is
    converted, which the plan notes), the audience (women or men), a mood and alcohol_free
    when they gave them (true when they ask
    for alcohol-free; false only when they explicitly want alcohol; leave it out otherwise,
    and the occasion's words decide). Remembered tastes apply automatically."""

    model_config = ConfigDict(extra="forbid")
    occasion: Occasion
    budget: float | None = Field(default=None, gt=0, le=MAX_BUDGET)
    audience: EveningAudience | None = None
    mood: Mood | None = None
    alcohol_free: bool | None = None


class SwapEveningPart(BaseModel):
    """Change one part of the current evening plan: part 'outfit' with the slot to change
    ('cheaper shoes': slot shoes, mode cheaper; 'another top': slot top, mode other), part
    'book' for another book (its drink is re-paired), or part 'drink' for another drink
    (mode other) or an alcohol-free one (mode alcohol_free). `refine` adds words for the new
    outfit piece ('in black')."""

    model_config = ConfigDict(extra="forbid")
    part: EveningPart
    slot: SlotName | None = None
    mode: EveningSwapMode = "other"
    refine: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def _consistent(self) -> "SwapEveningPart":
        if self.part == "outfit" and self.slot is None:
            raise ValueError("Name the slot of the outfit piece to change")
        if self.part != "outfit" and self.slot is not None:
            raise ValueError("A slot only applies to the outfit")
        if self.mode == "cheaper" and self.part != "outfit":
            raise ValueError("Only an outfit piece can be made cheaper")
        if self.mode == "alcohol_free" and self.part != "drink":
            raise ValueError("Only the drink can be made alcohol-free")
        return self


class SaveEvening(BaseModel):
    """Propose saving the evening: its outfit pieces to the fashion shortlist, its book to
    the reading list and its drink to the saved cocktails. The shopper must approve it (and
    may untick parts) before anything is saved; this ends your turn. Only propose it when
    they ask to save or keep the evening."""

    model_config = ConfigDict(extra="forbid")
    product_ids: list[PositiveInt] = Field(default_factory=list, max_length=MAX_SLOTS)
    book_ids: list[PositiveInt] = Field(default_factory=list, max_length=2)
    drink_ids: list[PositiveInt] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def _not_empty(self) -> "SaveEvening":
        if not (self.product_ids or self.book_ids or self.drink_ids):
            raise ValueError("Propose at least one part of the evening")
        return self


EVENING_TOOLS: dict[str, type[BaseModel]] = {
    schema.__name__: schema for schema in (PlanEvening, SwapEveningPart, AskShopper, SaveEvening)
}

EVENING_POLICIES: dict[str, Policy] = {
    "PlanEvening": Policy.READ,
    "SwapEveningPart": Policy.READ,
    "AskShopper": Policy.ASK,
    "SaveEvening": Policy.APPROVE,
}
