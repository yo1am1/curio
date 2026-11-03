"""Request, trace and reply models shared by the assistant, the API and the UI."""

from typing import Any, Literal

from pydantic import BaseModel, Field, PositiveInt, model_validator

from clothes_recommender.chat.intent import SearchIntent
from clothes_recommender.domain import Product

ApprovalTool = Literal["SaveToShortlist", "RememberPreferences"]


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)
    product_ids: list[PositiveInt] = Field(default_factory=list, max_length=5)


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

    The server signs everything except `products` and `summary` (display-only), so a client
    can approve exactly what the agent proposed, or narrow it, but never forge or widen it.
    """

    id: str
    tool: ApprovalTool
    args: dict[str, Any]
    request: str = Field(max_length=2000)
    issued_at: int
    signature: str
    summary: str = ""
    products: list[Product] = Field(default_factory=list)


class ActionDecision(BaseModel):
    action: PendingAction
    approved: bool
    # Optional narrowing edits: a subset of proposed product IDs / preference values.
    edited_args: dict[str, Any] | None = None


class AppliedEffect(BaseModel):
    """A state change the client should now apply to its shortlist or memory."""

    kind: Literal["shortlist", "preferences"]
    product_ids: list[int] = Field(default_factory=list)
    preferences: ShopperPreferences | None = None


class GroundingReport(BaseModel):
    """Result of checking the final answer's prices and IDs against tool evidence."""

    status: Literal["verified", "repaired", "fallback", "not_applicable"]
    checked: int = 0
    issues: list[str] = Field(default_factory=list)


class AssistantReply(BaseModel):
    text: str
    products: list[Product]
    intent: SearchIntent
    status: Literal["complete", "needs_input", "needs_approval"] = "complete"
    steps: list[AgentStep] = Field(default_factory=list)
    clarification: Clarification | None = None
    pending_action: PendingAction | None = None
    applied: list[AppliedEffect] = Field(default_factory=list)
    grounding: GroundingReport | None = None
    suggestions: list[str] = Field(default_factory=list)


class TurnContext(BaseModel):
    """Client-held session state the agent may read; the server stays stateless."""

    preferences: ShopperPreferences = Field(default_factory=ShopperPreferences)
    shopper_id: PositiveInt | None = None
    shortlist: list[PositiveInt] = Field(default_factory=list, max_length=20)
    # Human-in-the-loop policy: approval tools listed here run without asking.
    auto_approve: list[ApprovalTool] = Field(default_factory=list, max_length=2)


class ChatRequest(TurnContext):
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
