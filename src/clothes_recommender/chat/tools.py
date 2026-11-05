"""Validated actions available to the shopping assistant, each with an execution policy.

Every action is a strict pydantic schema: unknown fields, empty queries and out-of-range IDs
are rejected before anything runs. The policy decides what happens to a valid call:

- READ actions only look things up and run immediately.
- ASK actions stop the turn and hand a question with quick replies back to the shopper.
- APPROVE actions change shopper state (shortlist, remembered preferences). They never run
  on the model's say-so: the turn stops with a proposal the shopper approves, edits or rejects.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from clothes_recommender.outfits import MAX_SLOTS, Audience, SlotName, SwapMode


class Policy(StrEnum):
    READ = "read"
    ASK = "ask"
    APPROVE = "approve"


class SearchCatalog(BaseModel):
    """Refine the visual query, keeping the shopper's existing metadata and budget filters."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500, pattern=r"\S")


class InspectProducts(BaseModel):
    """Read exact catalog details for up to five product IDs, for questions or comparisons."""

    model_config = ConfigDict(extra="forbid")
    product_ids: list[PositiveInt] = Field(min_length=1, max_length=5)


class FindRelated(BaseModel):
    """Find pieces related to one catalog product: 'complement' (bought together by real
    shoppers), 'similar' (closest product photos) or 'alternative' (same category, higher
    price)."""

    model_config = ConfigDict(extra="forbid")
    product_id: PositiveInt
    relation: Literal["complement", "similar", "alternative"]


class PersonalPicks(BaseModel):
    """Recommend pieces from the linked shopper's purchase history. Only works when the
    shopper has linked a shopping history; explain the evidence for each pick."""

    model_config = ConfigDict(extra="forbid")


class BuildOutfit(BaseModel):
    """Plan a complete outfit for an occasion and fill every slot from the catalog, under
    one total budget. Plans 3-4 slots for the occasion (e.g. dress, shoes, bag, accessory)
    unless `slots` names them, prefers pieces bought together with or looking like the
    pieces already chosen, and reports any slot the budget cannot cover. Use it whenever
    the shopper asks for an outfit, a look or what to wear somewhere."""

    model_config = ConfigDict(extra="forbid")
    occasion: str = Field(
        min_length=2,
        max_length=200,
        pattern=r"\S",
        description="The occasion, season and style in the shopper's words, e.g. "
        "'summer wedding' or 'office, smart'.",
    )
    budget: float | None = Field(
        default=None, gt=0, le=10_000, description="Total budget in euros for the whole outfit."
    )
    audience: Audience | None = Field(
        default=None, description="Who it is for; leave empty if the shopper did not say."
    )
    slots: list[SlotName] | None = Field(
        default=None,
        min_length=2,
        max_length=MAX_SLOTS,
        description="Only when the shopper names the pieces they want.",
    )

    @model_validator(mode="after")
    def _distinct_slots(self) -> "BuildOutfit":
        if self.slots is not None:
            self.slots = list(dict.fromkeys(self.slots))
            if len(self.slots) < 2:
                raise ValueError("Name at least two different slots")
        return self


class SwapOutfitItem(BaseModel):
    """Replace one piece of the current outfit, keeping the rest and the total budget:
    mode 'other' for a different piece, 'cheaper' for a lower-priced one. `refine` adds
    words for the new piece, e.g. 'in black' or 'flat'."""

    model_config = ConfigDict(extra="forbid")
    slot: SlotName
    mode: SwapMode = "other"
    refine: str = Field(default="", max_length=100)


class AskShopper(BaseModel):
    """Ask the shopper one short clarifying question with 2-4 quick-reply options. Use it
    only when the request is too ambiguous to search well (e.g. no audience or occasion for
    a broad request); this ends your turn."""

    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=200, pattern=r"\S")
    options: list[str] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def _clean_options(self) -> "AskShopper":
        options = list(dict.fromkeys(o.strip()[:60] for o in self.options if o.strip()))
        if len(options) < 2:
            raise ValueError("Give at least two distinct options")
        self.options = options
        return self


class SaveToShortlist(BaseModel):
    """Propose saving catalog products to the shopper's shortlist. The shopper must approve
    it before anything is saved; this ends your turn. Only propose when they ask to save,
    keep or shortlist pieces."""

    model_config = ConfigDict(extra="forbid")
    product_ids: list[PositiveInt] = Field(min_length=1, max_length=5)
    note: str = Field(default="", max_length=200)


class RememberPreferences(BaseModel):
    """Propose remembering shopping preferences for later searches in this session. The
    shopper must approve it first; this ends your turn. Only propose preferences the shopper
    stated as lasting ('I always shop menswear', 'my budget is €50')."""

    model_config = ConfigDict(extra="forbid")
    sections: list[str] = Field(default_factory=list, max_length=3)
    colours: list[str] = Field(default_factory=list, max_length=5)
    max_price: float | None = Field(default=None, gt=0, le=10_000)

    @model_validator(mode="after")
    def _not_empty(self) -> "RememberPreferences":
        if not (self.sections or self.colours or self.max_price is not None):
            raise ValueError("Propose at least one preference")
        return self


TOOLS: dict[str, type[BaseModel]] = {
    schema.__name__: schema
    for schema in (
        SearchCatalog,
        InspectProducts,
        FindRelated,
        PersonalPicks,
        BuildOutfit,
        SwapOutfitItem,
        AskShopper,
        SaveToShortlist,
        RememberPreferences,
    )
}

POLICIES: dict[str, Policy] = {
    "SearchCatalog": Policy.READ,
    "InspectProducts": Policy.READ,
    "FindRelated": Policy.READ,
    "PersonalPicks": Policy.READ,
    "BuildOutfit": Policy.READ,
    "SwapOutfitItem": Policy.READ,
    "AskShopper": Policy.ASK,
    "SaveToShortlist": Policy.APPROVE,
    "RememberPreferences": Policy.APPROVE,
}
