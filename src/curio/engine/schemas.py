"""Wire models every agent turn shares: the trace, proposals, decisions, effects and replies.

These models know no domain. A domain extends them with its own payloads (its catalog
cards, remembered tastes and session fields), and an app combines those extensions into
the models it serves and passes their classes to `AgentEngine` as `Schemas`;
`curio.app.schemas` does that for the HTTP API. The engine's own models are enough for a
small domain on its own (`examples/hello_domain`).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, Field, SerializeAsAny


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


class GroundingReport(BaseModel):
    """Result of checking an answer's claims against tool evidence.

    `verified`: at least one fact was rendered or checked and nothing failed.
    `not_applicable`: nothing was rendered or checkable. `repaired`: the first draft failed
    and the rewrite passed. `fallback`: the answer was replaced by catalog facts.

    `rendered` counts the facts the server rendered from fact references in the text the
    client shows, by kind ("name", "price", "colour", "rating", "author", "year", "pages",
    "alcohol", "recipe", "amount", "total", "diff"). Each is the cited item's catalog value;
    cite mode does not check that the cited item is the one the sentence names. `checked`
    counts the values the model typed itself that the backstop check covered in the first
    draft, and `counts` splits them by kind ("price", "id", "rating", "title", "name",
    "author", "amount", "alcohol", "pages", "year", "claim", "fact"). A budget or limit (the
    shopper's own, or one a question suggests) is allowed but is not a catalog value, so it
    is not counted. Servers before cite mode send no `rendered`.
    """

    status: Literal["verified", "repaired", "fallback", "not_applicable"]
    checked: int = 0
    issues: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    rendered: dict[str, int] = Field(default_factory=dict)


class Intent(BaseModel):
    """What the shopper asked for in this turn.

    A domain may subclass it with its own filters (fashion's `SearchIntent`). Proposals are
    signed over it, so a turn resumed after the shopper's decision keeps the same filters.
    """

    query: str

    def resumed(self) -> Self:
        """This intent for a turn resumed after the shopper answered a proposal."""
        return self


class ChatTurn(BaseModel):
    """One earlier message. Domains add the IDs of the cards an assistant turn showed."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class TurnContext(BaseModel):
    """Client-held session state the agent may read; the server stays stateless.

    Domains add their own fields (shortlists, remembered tastes).
    """

    # Human-in-the-loop policy: approval tools listed here run without asking. This is the
    # shopper's own preference, held and sent by their client like the rest of this state;
    # nothing on the server can (or tries to) stop a client from setting it.
    auto_approve: list[str] = Field(default_factory=list, max_length=20)
    # A random id the client keeps for its conversation. Proposals are signed over it and
    # answer only in the session they were made for. Without one, a proposal is bound to
    # "no session" and stays single-use.
    session_id: str | None = Field(
        default=None, min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"
    )


class PendingAction(BaseModel):
    """A state-changing action proposed by the agent, waiting for the shopper's decision.

    The server signs every field here except `summary` (display-only), so a client can
    approve exactly what the agent proposed, or narrow it, but never forge or widen it.
    `intent` keeps the proposing turn's filters for the resumed turn. Apps add display-only
    fields (the cards the proposal touches) in a subclass.
    """

    id: str
    tool: str
    args: dict[str, Any]
    request: str = Field(max_length=2000)
    issued_at: int
    signature: str
    intent: SerializeAsAny[Intent] | None = None
    summary: str = ""


class ActionDecision(BaseModel):
    action: PendingAction
    approved: bool
    # Optional narrowing edits: a subset of proposed IDs / preference values.
    edited_args: dict[str, Any] | None = None


class AppliedEffect(BaseModel):
    """A state change the client should now apply to its shortlist or memory.

    Each domain subclasses it with its own kinds and fields, and says which `TurnContext`
    field each of them updates, so the engine can apply an approved effect to the rest of
    the turn the way the client will.
    """

    kind: str
    # Effect field -> context field holding remembered values; approved values are merged in.
    remembers: ClassVar[Mapping[str, str]] = {}
    # Effect field -> context field holding a list of IDs; approved IDs are appended.
    adds: ClassVar[Mapping[str, str]] = {}


class AssistantReply(BaseModel):
    """The end of a turn. Domains add their items through `Toolkit.reply_fields`."""

    text: str
    intent: SerializeAsAny[Intent]
    status: Literal["complete", "needs_input", "needs_approval"] = "complete"
    steps: list[AgentStep] = Field(default_factory=list)
    clarification: Clarification | None = None
    pending_action: SerializeAsAny[PendingAction] | None = None
    applied: list[SerializeAsAny[AppliedEffect]] = Field(default_factory=list)
    grounding: GroundingReport | None = None
    suggestions: list[str] = Field(default_factory=list)


class StepEvent(BaseModel):
    type: Literal["step"] = "step"
    step: AgentStep


class ReplyEvent(BaseModel):
    type: Literal["reply"] = "reply"
    reply: SerializeAsAny[AssistantReply]


AgentEvent = StepEvent | ReplyEvent


@dataclass(frozen=True)
class Schemas:
    """The model classes an engine builds: the engine's own, or an app's subclasses."""

    intent: Callable[..., Intent] = Intent
    context: Callable[[], TurnContext] = TurnContext
    pending_action: type[PendingAction] = PendingAction
    reply: Callable[..., AssistantReply] = AssistantReply
