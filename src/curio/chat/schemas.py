"""Request, trace and reply models shared by the assistant, the API and the UI."""

from typing import Any, Literal

from pydantic import (
    BaseModel,
    Field,
    PositiveInt,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from curio.books.domain import Book, BookPreferences
from curio.chat.intent import SearchIntent
from curio.cocktails.domain import Cocktail, CocktailPreferences
from curio.domain import Product
from curio.outfits import Outfit, OutfitState
from curio.pairings.domain import BookPairing

Domain = Literal["fashion", "cocktails", "books"]
ApprovalTool = Literal[
    "SaveToShortlist",
    "RememberPreferences",
    "SaveCocktails",
    "RememberTastes",
    "SaveBooks",
    "RememberReadingTastes",
]


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)
    product_ids: list[PositiveInt] = Field(default_factory=list, max_length=5)
    # Cocktail cards shown with an assistant turn, so follow-ups can refer to them.
    drink_ids: list[PositiveInt] = Field(default_factory=list, max_length=5)
    book_ids: list[PositiveInt] = Field(default_factory=list, max_length=5)


class ShopperPreferences(BaseModel):
    """Approved, session-scoped preferences the client sends back with each message."""

    sections: list[str] = Field(default_factory=list, max_length=3)
    colours: list[str] = Field(default_factory=list, max_length=5)
    max_price: float | None = Field(default=None, gt=0, le=10_000)

    def is_empty(self) -> bool:
        return not (self.sections or self.colours or self.max_price is not None)


class AgentStep(BaseModel):
    """One observable step of a turn, streamed to the UI as it happens."""

    kind: Literal["plan", "tool", "guardrail", "approval", "answer"]
    name: str
    summary: str
    status: Literal["ok", "error", "blocked", "pending", "skipped"] = "ok"
    detail: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0


class Clarification(BaseModel):
    question: str
    options: list[str]


class PendingAction(BaseModel):
    """A state-changing action proposed by the agent, waiting for the shopper's decision.

    The server signs everything except `products`, `cocktails`, `books` and `summary`
    (display-only),
    so a client can approve exactly what the agent proposed, or narrow it, but never forge or
    widen it. `intent` keeps the proposing turn's search filters for the resumed turn.
    """

    id: str
    tool: ApprovalTool
    args: dict[str, Any]
    request: str = Field(max_length=2000)
    issued_at: int
    signature: str
    intent: SearchIntent | None = None
    summary: str = ""
    products: list[Product] = Field(default_factory=list)
    cocktails: list[Cocktail] = Field(default_factory=list, max_length=5)
    books: list[Book] = Field(default_factory=list, max_length=5)


class ActionDecision(BaseModel):
    action: PendingAction
    approved: bool
    # Optional narrowing edits: a subset of proposed product IDs / preference values.
    edited_args: dict[str, Any] | None = None


# Each effect kind's own fields; every other domain's fields are left out of its JSON.
_EFFECT_FIELDS = {
    "shortlist": ("product_ids", "preferences"),
    "preferences": ("product_ids", "preferences"),
    "cocktail_shortlist": ("drink_ids", "cocktail_preferences"),
    "cocktail_preferences": ("drink_ids", "cocktail_preferences"),
    "book_shortlist": ("book_ids", "book_preferences"),
    "book_preferences": ("book_ids", "book_preferences"),
}
_ALL_EFFECT_FIELDS = {name for names in _EFFECT_FIELDS.values() for name in names}


class AppliedEffect(BaseModel):
    """A state change the client should now apply to its shortlist or memory.

    Each kind is serialized with its own domain's fields only, so fashion effects keep the
    exact wire format they had before the cocktail and book domains existed.
    """

    kind: Literal[
        "shortlist",
        "preferences",
        "cocktail_shortlist",
        "cocktail_preferences",
        "book_shortlist",
        "book_preferences",
    ]
    product_ids: list[int] = Field(default_factory=list)
    preferences: ShopperPreferences | None = None
    drink_ids: list[int] = Field(default_factory=list)
    cocktail_preferences: CocktailPreferences | None = None
    # The reading list (book shortlist) and remembered reading tastes.
    book_ids: list[int] = Field(default_factory=list)
    book_preferences: BookPreferences | None = None

    # No return annotation: pydantic then keeps the field-by-field JSON schema for responses.
    @model_serializer(mode="wrap")
    def _domain_fields(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        for name in _ALL_EFFECT_FIELDS - set(_EFFECT_FIELDS[self.kind]):
            data.pop(name, None)
        return data


class GroundingReport(BaseModel):
    """Result of checking the final answer's prices and IDs against tool evidence."""

    status: Literal["verified", "repaired", "fallback", "not_applicable"]
    checked: int = 0
    issues: list[str] = Field(default_factory=list)


class AssistantReply(BaseModel):
    text: str
    products: list[Product]
    intent: SearchIntent
    domain: Domain = "fashion"
    cocktails: list[Cocktail] = Field(default_factory=list)
    books: list[Book] = Field(default_factory=list)
    status: Literal["complete", "needs_input", "needs_approval"] = "complete"
    steps: list[AgentStep] = Field(default_factory=list)
    clarification: Clarification | None = None
    pending_action: PendingAction | None = None
    applied: list[AppliedEffect] = Field(default_factory=list)
    grounding: GroundingReport | None = None
    suggestions: list[str] = Field(default_factory=list)
    # The outfit this turn built or changed; the client keeps `outfit.state` for follow-ups.
    outfit: Outfit | None = None
    # A book turn's cocktail pairing (PairCocktail): the profile, drinks and reasons. Its
    # drinks are also in `cocktails`.
    pairing: BookPairing | None = None


class TurnContext(BaseModel):
    """Client-held session state the agent may read; the server stays stateless."""

    preferences: ShopperPreferences = Field(default_factory=ShopperPreferences)
    shopper_id: PositiveInt | None = None
    shortlist: list[PositiveInt] = Field(default_factory=list, max_length=20)
    # Human-in-the-loop policy: approval tools listed here run without asking.
    auto_approve: list[ApprovalTool] = Field(default_factory=list, max_length=6)
    cocktail_preferences: CocktailPreferences = Field(default_factory=CocktailPreferences)
    cocktail_shortlist: list[PositiveInt] = Field(default_factory=list, max_length=20)
    book_preferences: BookPreferences = Field(default_factory=BookPreferences)
    # The reading list.
    book_shortlist: list[PositiveInt] = Field(default_factory=list, max_length=20)
    # The latest outfit the fashion agent built, so a follow-up can swap or save its pieces.
    outfit: OutfitState | None = None


class ChatRequest(TurnContext):
    domain: Domain = "fashion"
    message: str = Field(default="", max_length=2000)
    history: list[ChatTurn] = Field(default=[], max_length=50)
    decision: ActionDecision | None = None

    @model_validator(mode="after")
    def _message_or_decision(self) -> "ChatRequest":
        if not self.message.strip() and self.decision is None:
            raise ValueError("Send a message or a decision on a pending action")
        return self


class StepEvent(BaseModel):
    type: Literal["step"] = "step"
    step: AgentStep


class ReplyEvent(BaseModel):
    type: Literal["reply"] = "reply"
    reply: AssistantReply


AgentEvent = StepEvent | ReplyEvent
