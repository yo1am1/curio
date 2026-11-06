"""What a domain plugs into the agent loop: a `Toolkit` over the domain's catalog items.

A toolkit names its tools and their policies, and supplies planning, the READ tools, the
validation of APPROVE effects, the grounding check, the deterministic fallback and the
reply's domain fields. Everything else (budgets, approvals, resume, self-correction, the
trace) is the engine's.

`BaseToolkit` implements the optional hooks with no-op defaults, so a domain writes only its
tools, prompt, READ tools, APPROVE effects, grounding check, fallback and reply fields.

Optional hook: a toolkit may define `vet_proposal(turn, action) -> None`, which the engine
calls before it applies or signs an APPROVE action, to refuse one that does not fit this
turn (raise ToolError; the message goes back to the model). The evening toolkit uses it so a
save names only the current plan's parts.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from curio.engine.schemas import (
    AgentStep,
    AppliedEffect,
    ChatTurn,
    GroundingReport,
    Intent,
    TurnContext,
)
from curio.engine.tools import Policy


class ToolError(Exception):
    """A valid call that cannot run here; the message goes back to the model."""


@dataclass
class Turn[T]:
    """Mutable state of one turn. `items` are the latest tool results shown to the shopper;
    `evidence` is everything the answer may cite (results, history cards, shortlist)."""

    message: str
    context: TurnContext
    intent: Intent
    started: float
    key: Callable[[T], int]
    items: list[T] = field(default_factory=list)
    reasons: dict[int, list[str]] = field(default_factory=dict)
    evidence: dict[int, T] = field(default_factory=dict)
    steps: list[AgentStep] = field(default_factory=list)
    applied: list[AppliedEffect] = field(default_factory=list)
    calls: int = 0
    # After a decision the shopper already answered, the agent may not pause again.
    resuming: bool = False
    # Domain-owned results of this turn that are not catalog items (e.g. a planned outfit).
    extras: dict[str, Any] = field(default_factory=dict)

    def show(self, items: Iterable[T], reasons: dict[int, list[str]] | None = None) -> None:
        self.items = list(items)
        self.reasons = reasons or {}
        self.remember(self.items)

    def remember(self, items: Iterable[T]) -> None:
        self.evidence.update({self.key(item): item for item in items})


type ReadOutcome = tuple[str, AgentStep]
type ReadResult = ReadOutcome | Generator[AgentStep, None, ReadOutcome]


class Toolkit[T](Protocol):
    """What one domain contributes to the shared agent loop."""

    tools: Mapping[str, type[BaseModel]]
    policies: Mapping[str, Policy]
    # Tool message for a call that fails schema validation.
    invalid_action: str
    # Self-correction request; formatted with `issues`.
    repair_instruction: str
    # What a verified grounding check covered, e.g. "prices and IDs".
    grounding_subject: str

    def key(self, item: T) -> int: ...

    def clean_context(self, context: TurnContext) -> TurnContext:
        """Drop client-held values this domain does not recognise."""
        ...

    def history(self, turns: Sequence[ChatTurn]) -> tuple[list[BaseMessage], list[T]]:
        """Chat messages for the model, plus the catalog items earlier turns showed."""
        ...

    def plan(self, turn: Turn[T], history: Sequence[BaseMessage]) -> Iterator[AgentStep]:
        """Planning steps before the tool loop; each yielded step is timed separately."""
        ...

    def refresh(self, turn: Turn[T], approved: AppliedEffect | None = None) -> None:
        """Re-apply `turn.context`'s remembered preferences to the turn's filters, after a
        resume or an approved preferences change; `approved` values replace earlier ones."""
        ...

    def prompt(
        self, turn: Turn[T], message: str, history: Sequence[BaseMessage]
    ) -> list[BaseMessage]: ...

    def read(self, turn: Turn[T], action: BaseModel) -> ReadResult:
        """Run a READ action: the tool result for the model and its trace step.

        A tool that works in stages may instead return a generator that yields a progress
        step per stage (streamed as it happens) and returns the result and final step.
        """
        ...

    def apply(
        self, action: BaseModel, *, dry_run: bool = False
    ) -> tuple[AppliedEffect, list[T], str]:
        """Validate an APPROVE action: its effect, the items it touches and an outcome line."""
        ...

    def proposed(self, args: Mapping[str, Any]) -> list[T]:
        """Items a (declined) proposal referred to, for display."""
        ...

    def approval_text(self, action: BaseModel) -> str: ...

    def pending_display(self, items: list[T]) -> dict[str, Any]:
        """Display-only PendingAction fields for the items a proposal touches."""
        ...

    def check(self, turn: Turn[T], text: str) -> GroundingReport: ...

    def fallback(self, items: Sequence[T], extras: Mapping[str, Any] | None = None) -> str:
        """A deterministic answer built from verified catalog facts only; `extras` are the
        turn's domain results that are not catalog items (a book turn's drink pairing)."""
        ...

    def reply_fields(self, turn: Turn[T]) -> dict[str, Any]:
        """The reply's domain fields, e.g. the items shown; the app's reply model declares them."""
        ...

    def suggestions(self, turn: Turn[T]) -> list[str]:
        """Next-step chips for a completed answer."""
        ...


class BaseToolkit[T](ABC):
    """A `Toolkit` with no-op defaults for every optional hook.

    Subclasses set `tools` and `policies` and implement the abstract methods. Override a hook
    only when the domain needs it: remembered filters (`clean_context`, `refresh`), cards that
    earlier turns showed (`history`), planning steps (`plan`), what a proposal shows
    (`proposed`, `approval_text`, `pending_display`), a per-turn veto on proposals
    (`vet_proposal`) and next-step chips (`suggestions`).
    """

    tools: Mapping[str, type[BaseModel]]
    policies: Mapping[str, Policy]
    invalid_action = "Invalid action. Call one of the tools with valid arguments."
    repair_instruction = "Verification failed: {issues}. Use only facts the tools returned."
    grounding_subject = "facts"

    @abstractmethod
    def key(self, item: T) -> int: ...

    @abstractmethod
    def prompt(
        self, turn: Turn[T], message: str, history: Sequence[BaseMessage]
    ) -> list[BaseMessage]: ...

    @abstractmethod
    def read(self, turn: Turn[T], action: BaseModel) -> ReadResult: ...

    @abstractmethod
    def apply(
        self, action: BaseModel, *, dry_run: bool = False
    ) -> tuple[AppliedEffect, list[T], str]: ...

    @abstractmethod
    def check(self, turn: Turn[T], text: str) -> GroundingReport: ...

    @abstractmethod
    def fallback(self, items: Sequence[T], extras: Mapping[str, Any] | None = None) -> str: ...

    @abstractmethod
    def reply_fields(self, turn: Turn[T]) -> dict[str, Any]: ...

    def clean_context(self, context: TurnContext) -> TurnContext:
        """Keep the client-held context as it is."""
        return context

    def history(self, turns: Sequence[ChatTurn]) -> tuple[list[BaseMessage], list[T]]:
        """Earlier messages as plain chat messages; no earlier cards are cited."""
        return [(HumanMessage if t.role == "user" else AIMessage)(t.content) for t in turns], []

    def plan(self, turn: Turn[T], history: Sequence[BaseMessage]) -> Iterator[AgentStep]:
        """One planning step that only records the request."""
        yield AgentStep(kind="plan", name="Understand request", summary="Reading the request.")

    def refresh(self, turn: Turn[T], approved: AppliedEffect | None = None) -> None:
        """No remembered filters to re-apply."""
        return

    def proposed(self, args: Mapping[str, Any]) -> list[T]:
        """No items to show for a declined proposal."""
        return []

    def approval_text(self, action: BaseModel) -> str:
        return "Shall I go ahead? Approve, edit or decline."

    def pending_display(self, items: list[T]) -> dict[str, Any]:
        return {}

    def vet_proposal(self, turn: Turn[T], action: BaseModel) -> None:
        """Accept every proposal; raise ToolError to refuse one in this turn's context."""
        return

    def suggestions(self, turn: Turn[T]) -> list[str]:
        return []
