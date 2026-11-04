"""Domain-agnostic agent turn: plan, act through policy-gated tools, verify, hand back control.

One turn runs as a generator of observable events:

1. Plan: the domain's planning steps (e.g. intent extraction and a first search, or applying
   remembered tastes).
2. Act: a bounded tool loop. Read tools run immediately; AskShopper pauses for a quick reply;
   approval tools pause for the shopper's decision (human in the loop) unless the shopper
   opted into auto-approval for that tool.
3. Verify: the domain's grounding check compares the final answer with tool evidence. One
   self-repair attempt is allowed before a deterministic, grounded fallback.

Everything domain-specific (tools, prompts, evidence, grounding, suggestions) lives behind the
`Toolkit` protocol; budgets, the trace, approvals and resume are shared by every domain.

The server is stateless: shortlists, preferences and pending proposals live on the client.
Proposals are HMAC-signed over their tool name and arguments, so a resumed decision can approve
or narrow exactly what this agent proposed, never forge, widen or replay it in another domain.
"""

import logging
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, ValidationError

from clothes_recommender.chat.guardrails import ActionSigner, InvalidActionError, narrow_args
from clothes_recommender.chat.intent import SearchIntent
from clothes_recommender.chat.schemas import (
    ActionDecision,
    AgentEvent,
    AgentStep,
    AppliedEffect,
    AssistantReply,
    ChatTurn,
    Clarification,
    GroundingReport,
    PendingAction,
    ReplyEvent,
    StepEvent,
    TurnContext,
)
from clothes_recommender.chat.tools import AskShopper, Policy

logger = logging.getLogger(__name__)

# Each ChatTurn is one message; older turns rarely matter and only cost tokens.
MAX_HISTORY_TURNS = 10
MAX_TOOL_CALLS = 5
# Wall-clock budget for one turn; checked before every model call.
TURN_DEADLINE_SECONDS = 90.0
# TurnContext.shortlist and cocktail_shortlist hold at most this many IDs.
MAX_SHORTLIST = 20


class ToolError(Exception):
    """A valid call that cannot run here; the message goes back to the model."""


@dataclass
class Turn[T]:
    """Mutable state of one turn. `items` are the latest tool results shown to the shopper;
    `evidence` is everything the answer may cite (results, history cards, shortlist)."""

    message: str
    context: TurnContext
    intent: SearchIntent
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

    def show(self, items: Iterable[T], reasons: dict[int, list[str]] | None = None) -> None:
        self.items = list(items)
        self.reasons = reasons or {}
        self.remember(self.items)

    def remember(self, items: Iterable[T]) -> None:
        self.evidence.update({self.key(item): item for item in items})


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

    def read(self, turn: Turn[T], action: BaseModel) -> tuple[str, AgentStep]:
        """Run a READ action: the tool result for the model and its trace step."""
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

    def fallback(self, items: Sequence[T]) -> str:
        """A deterministic answer built from verified catalog facts only."""
        ...

    def reply_fields(self, turn: Turn[T]) -> dict[str, Any]:
        """Domain fields of AssistantReply (domain, products, cocktails)."""
        ...

    def suggestions(self, turn: Turn[T]) -> list[str]:
        """Next-step chips for a completed answer."""
        ...


class AgentEngine[T]:
    def __init__(
        self,
        chat_model: BaseChatModel,
        toolkit: Toolkit[T],
        *,
        signer: ActionSigner | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._toolkit = toolkit
        self._signer = signer or ActionSigner()
        self._clock = clock
        self._agent = chat_model.bind_tools(list(toolkit.tools.values()))

    # --- Public API -----------------------------------------------------------------------

    def reply(
        self, message: str, history: Sequence[ChatTurn], context: TurnContext | None = None
    ) -> AssistantReply:
        return _final(self.stream(message, history, context))

    def resume(
        self,
        decision: ActionDecision,
        history: Sequence[ChatTurn],
        context: TurnContext | None = None,
    ) -> AssistantReply:
        return _final(self.stream("", history, context, decision=decision))

    def stream(
        self,
        message: str,
        history: Sequence[ChatTurn],
        context: TurnContext | None = None,
        *,
        decision: ActionDecision | None = None,
    ) -> Iterator[AgentEvent]:
        """Run one turn, yielding each step as it completes and the reply last."""
        context = self._toolkit.clean_context(context or TurnContext())
        history_messages, referenced = self._toolkit.history(history[-MAX_HISTORY_TURNS:])
        if decision is not None:
            yield from self._resume(decision, history_messages, referenced, context)
            return
        turn = self._new_turn(message, context, SearchIntent(query=message), referenced)
        started = self._clock()
        for step in self._toolkit.plan(turn, history_messages):
            yield self._record(turn, step, started)
            started = self._clock()
        yield from self._loop(turn, self._toolkit.prompt(turn, message, history_messages))

    # --- Turn phases ----------------------------------------------------------------------

    def _new_turn(
        self,
        message: str,
        context: TurnContext,
        intent: SearchIntent,
        referenced: Iterable[T],
        *,
        resuming: bool = False,
    ) -> Turn[T]:
        turn = Turn(message, context, intent, self._clock(), self._toolkit.key, resuming=resuming)
        turn.remember(referenced)
        return turn

    def _resume(
        self,
        decision: ActionDecision,
        history: list[BaseMessage],
        referenced: list[T],
        context: TurnContext,
    ) -> Iterator[AgentEvent]:
        started = self._clock()
        action = self._signer.verify(decision.action)
        schema = self._approval_schema(action)
        # The proposing turn's signed filters, so a follow-up search keeps them.
        intent = action.intent or SearchIntent(query=action.request)
        turn = self._new_turn(
            action.request,
            context,
            intent.model_copy(update={"wants_products": False}),
            referenced,
            resuming=True,
        )
        self._toolkit.refresh(turn)
        if decision.approved:
            args = narrow_args(decision)
            effect, items, outcome = self._apply_decision(schema, args)
            self._applied(turn, effect)
            if items:
                turn.show(items)
            step = AgentStep(
                kind="approval", name=action.tool, summary=f"Approved: {outcome}", detail=args
            )
            note = f"The shopper approved your proposal. {outcome} Confirm briefly."
        else:
            turn.show(self._toolkit.proposed(action.args))
            step = AgentStep(
                kind="approval",
                name=action.tool,
                summary="Declined by the shopper; nothing changed.",
                status="skipped",
                detail=action.args,
            )
            note = (
                "The shopper declined your proposal; nothing was changed. Acknowledge briefly "
                "and offer one alternative. Do not propose the same change again."
            )
        yield self._record(turn, step, started)
        messages = self._toolkit.prompt(turn, f"[Decision on your proposal] {note}", history)
        yield from self._loop(turn, messages)

    def _approval_schema(self, action: PendingAction) -> type[BaseModel]:
        """The schema of a signed proposal, if it belongs to this assistant's domain."""
        schema = self._toolkit.tools.get(action.tool)
        if schema is None or self._toolkit.policies.get(action.tool) is not Policy.APPROVE:
            raise InvalidActionError("This proposal belongs to a different assistant.")
        return schema

    def _apply_decision(
        self, schema: type[BaseModel], args: dict[str, Any]
    ) -> tuple[AppliedEffect, list[T], str]:
        try:
            return self._toolkit.apply(schema.model_validate(args))
        except ValidationError as error:
            raise InvalidActionError(
                "Nothing valid is left in the edited proposal; decline it instead."
            ) from error
        except ToolError as error:
            raise InvalidActionError(str(error)) from error

    def _loop(self, turn: Turn[T], messages: list[BaseMessage]) -> Iterator[AgentEvent]:
        while True:
            if self._out_of_time(turn):
                yield self._record(turn, _time_budget_step())
                yield self._fallback_reply(turn)
                return
            started = self._clock()
            answer = self._agent.invoke(messages)
            if not answer.tool_calls:
                text = StrOutputParser().invoke(answer).strip()
                if not text:
                    yield self._fallback_reply(turn)
                    return
                yield from self._verify(turn, messages, answer, text, started)
                return
            messages.append(answer)
            for call in answer.tool_calls:
                if turn.calls >= MAX_TOOL_CALLS or not call.get("id"):
                    yield self._record(
                        turn,
                        AgentStep(
                            kind="guardrail",
                            name="Tool budget",
                            summary=(
                                f"Stopped at {MAX_TOOL_CALLS} tool calls; showing verified items."
                                if call.get("id")
                                else "Ignored a malformed tool call; showing verified items."
                            ),
                            status="blocked",
                        ),
                    )
                    yield self._fallback_reply(turn)
                    return
                turn.calls += 1
                outcome = yield from self._call(turn, call, messages)
                if outcome is not None:
                    yield outcome
                    return

    def _call(
        self, turn: Turn[T], call: Mapping[str, Any], messages: list[BaseMessage]
    ) -> Iterator[AgentEvent]:
        """Run one tool call. Returns a reply when the call ends the turn, else None."""
        name, call_id = call["name"], call["id"]
        started = self._clock()
        action = self._validate(name, call["args"])
        if action is None:
            yield self._record(
                turn,
                AgentStep(
                    kind="tool",
                    name=str(name)[:40],
                    summary="Rejected an invalid action; nothing ran.",
                    status="blocked",
                ),
                started,
            )
            messages.append(ToolMessage(content=self._toolkit.invalid_action, tool_call_id=call_id))
            return None
        policy = self._toolkit.policies[name]
        if policy is Policy.READ:
            try:
                result, step = self._toolkit.read(turn, action)
            except ToolError as error:
                result = str(error)
                step = AgentStep(kind="tool", name=name, summary=result, status="error")
            yield self._record(turn, step, started)
            messages.append(ToolMessage(content=result, tool_call_id=call_id))
            return None
        if turn.resuming and name not in turn.context.auto_approve:
            result = "Not available now: the shopper just answered a proposal."
            yield self._record(
                turn, AgentStep(kind="tool", name=name, summary=result, status="blocked"), started
            )
            messages.append(ToolMessage(content=result, tool_call_id=call_id))
            return None
        if isinstance(action, AskShopper):
            yield self._record(
                turn,
                AgentStep(
                    kind="approval",
                    name="AskShopper",
                    summary="Waiting for the shopper's answer.",
                    status="pending",
                    detail=action.model_dump(),
                ),
                started,
            )
            return self._reply(
                turn,
                action.question,
                None,
                status="needs_input",
                clarification=Clarification(question=action.question, options=action.options),
            )
        return (yield from self._propose(turn, name, call_id, action, messages, started))

    def _propose(
        self,
        turn: Turn[T],
        name: str,
        call_id: str,
        action: BaseModel,
        messages: list[BaseMessage],
        started: float,
    ) -> Iterator[AgentEvent]:
        """An approval-gated state change: apply it if auto-approved, else ask the shopper."""
        try:
            if name in turn.context.auto_approve:
                effect, items, outcome = self._toolkit.apply(action)
                self._applied(turn, effect)
                if items:
                    turn.show(items)
                yield self._record(
                    turn,
                    AgentStep(
                        kind="approval",
                        name=name,
                        summary=f"Auto-approved: {outcome}",
                        detail=action.model_dump(),
                    ),
                    started,
                )
                result = f"Auto-approved by the shopper's settings. {outcome}"
                messages.append(ToolMessage(content=result, tool_call_id=call_id))
                return None
            _, items, outcome = self._toolkit.apply(action, dry_run=True)
        except ToolError as error:
            yield self._record(
                turn, AgentStep(kind="tool", name=name, summary=str(error), status="error"), started
            )
            messages.append(ToolMessage(content=str(error), tool_call_id=call_id))
            return None
        if items:
            turn.show(items)
        pending = self._signer.issue(
            name,
            _canonical_args(action),
            turn.message,
            intent=turn.intent,
            summary=f"Proposed: {outcome}",
            **self._toolkit.pending_display(items),
        )
        yield self._record(
            turn,
            AgentStep(
                kind="approval",
                name=name,
                summary="Waiting for the shopper's approval.",
                status="pending",
                detail=pending.args,
            ),
            started,
        )
        return self._reply(
            turn,
            self._toolkit.approval_text(action),
            None,
            status="needs_approval",
            pending_action=pending,
        )

    def _verify(
        self,
        turn: Turn[T],
        messages: list[BaseMessage],
        answer: AIMessage,
        text: str,
        started: float,
    ) -> Iterator[AgentEvent]:
        yield self._record(
            turn,
            AgentStep(kind="answer", name="Draft answer", summary="Drafted a reply."),
            started,
        )
        started = self._clock()
        report = self._toolkit.check(turn, text)
        if report.status != "fallback":
            if report.status == "verified":
                subject = self._toolkit.grounding_subject
                yield self._record(
                    turn,
                    AgentStep(
                        kind="guardrail",
                        name="Grounding check",
                        summary=f"Verified {report.checked} {subject} against the catalog.",
                    ),
                    started,
                )
            yield self._reply(turn, text, report)
            return
        yield self._record(
            turn,
            AgentStep(
                kind="guardrail",
                name="Grounding check",
                summary=f"Found {len(report.issues)} unverified claims; asking for a rewrite.",
                status="error",
                detail={"issues": report.issues},
            ),
            started,
        )
        final = GroundingReport(status="fallback", checked=report.checked, issues=report.issues)
        if self._out_of_time(turn):
            yield self._record(turn, _time_budget_step())
            yield self._reply(turn, self._toolkit.fallback(turn.items), final)
            return
        started = self._clock()
        instruction = self._toolkit.repair_instruction.format(issues="; ".join(report.issues))
        repaired = self._agent.invoke([*messages, answer, HumanMessage(instruction)])
        repaired_text = "" if repaired.tool_calls else StrOutputParser().invoke(repaired).strip()
        second = self._toolkit.check(turn, repaired_text) if repaired_text else None
        if second is not None and second.status in ("verified", "not_applicable"):
            final = GroundingReport(status="repaired", checked=report.checked, issues=report.issues)
            yield self._record(
                turn,
                AgentStep(
                    kind="guardrail",
                    name="Self-correction",
                    summary="Rewrote the answer; every claim now matches the catalog.",
                ),
                started,
            )
            yield self._reply(turn, repaired_text, final)
            return
        yield self._record(
            turn,
            AgentStep(
                kind="guardrail",
                name="Self-correction",
                summary="Rewrite still unverified; showing catalog facts only.",
                status="blocked",
            ),
            started,
        )
        yield self._reply(turn, self._toolkit.fallback(turn.items), final)

    # --- Helpers --------------------------------------------------------------------------

    def _out_of_time(self, turn: Turn[T]) -> bool:
        return self._clock() - turn.started > TURN_DEADLINE_SECONDS

    def _applied(self, turn: Turn[T], effect: AppliedEffect) -> None:
        """Record an applied effect and let later tool calls in this turn use it."""
        turn.applied.append(effect)
        turn.context = _with_effect(turn.context, effect)
        self._toolkit.refresh(turn, effect)

    def _validate(self, name: str, args: object) -> BaseModel | None:
        schema = self._toolkit.tools.get(name)
        if schema is None:
            return None
        try:
            return schema.model_validate(args)
        except ValidationError:
            return None

    def _record(self, turn: Turn[T], step: AgentStep, started: float | None = None) -> StepEvent:
        if started is not None:
            step.duration_ms = max(0, round((self._clock() - started) * 1000))
        turn.steps.append(step)
        logger.info("agent step %s/%s: %s (%s)", step.kind, step.name, step.summary, step.status)
        return StepEvent(step=step)

    def _fallback_reply(self, turn: Turn[T]) -> ReplyEvent:
        # Built from catalog rows only, so it is grounded by construction.
        return self._reply(
            turn,
            self._toolkit.fallback(turn.items),
            GroundingReport(status="verified", checked=0),
        )

    def _reply(
        self, turn: Turn[T], text: str, grounding: GroundingReport | None, **fields: Any
    ) -> ReplyEvent:
        complete = fields.get("status", "complete") == "complete"
        return ReplyEvent(
            reply=AssistantReply(
                text=text,
                intent=turn.intent,
                steps=turn.steps,
                applied=turn.applied,
                grounding=grounding,
                suggestions=self._toolkit.suggestions(turn) if complete else [],
                **self._toolkit.reply_fields(turn),
                **fields,
            )
        )


def _final(events: Iterable[AgentEvent]) -> AssistantReply:
    for event in events:
        if isinstance(event, ReplyEvent):
            return event.reply
    raise RuntimeError("The assistant finished without a reply")  # pragma: no cover


def _time_budget_step() -> AgentStep:
    return AgentStep(
        kind="guardrail",
        name="Time budget",
        summary="Stopped after the turn's time budget; showing verified items.",
        status="blocked",
    )


def _with_effect(context: TurnContext, effect: AppliedEffect) -> TurnContext:
    """The client-held context as the client will store it after applying `effect`."""
    update: dict[str, Any] = {}
    if effect.preferences is not None:
        update["preferences"] = _merged(context.preferences, effect.preferences)
    if effect.cocktail_preferences is not None:
        update["cocktail_preferences"] = _merged(
            context.cocktail_preferences, effect.cocktail_preferences
        )
    if effect.product_ids:
        update["shortlist"] = _union(context.shortlist, effect.product_ids, MAX_SHORTLIST)
    if effect.drink_ids:
        update["cocktail_shortlist"] = _union(
            context.cocktail_shortlist, effect.drink_ids, MAX_SHORTLIST
        )
    return context.model_copy(update=update)


def _merged[M: BaseModel](current: M, approved: M) -> M:
    """Approved values added to remembered ones: lists are joined, set flags and values win."""
    data = current.model_dump()
    for name, value in approved.model_dump(exclude_defaults=True).items():
        if isinstance(value, list):
            limit = next(
                (m.max_length for m in type(current).model_fields[name].metadata if _bounded(m)),
                None,
            )
            value = _newest(data[name], value, limit)
        data[name] = value
    return type(current).model_validate(data)


def _bounded(constraint: object) -> bool:
    return getattr(constraint, "max_length", None) is not None


def _newest[V](current: Sequence[V], added: Iterable[V], limit: int | None) -> list[V]:
    """Approved values win when memory is full, matching the UI: re-approved values move last."""
    added = list(dict.fromkeys(added))
    merged = [value for value in current if value not in added] + added
    return merged[-limit:] if limit else merged


def _union[V](current: Sequence[V], added: Iterable[V], limit: int | None) -> list[V]:
    return list(dict.fromkeys([*current, *added]))[:limit]


def _canonical_args(action: BaseModel) -> dict:
    return action.model_dump(mode="json", exclude_defaults=True)
