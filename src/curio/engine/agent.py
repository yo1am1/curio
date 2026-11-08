"""Domain-agnostic agent turn: plan, act through policy-gated tools, verify, hand back control.

One turn runs as a generator of observable events:

1. Plan: the domain's planning steps (e.g. intent extraction and a first search, or applying
   remembered tastes).
2. Act: a bounded tool loop. Read tools run immediately; AskShopper pauses for a quick reply;
   approval tools pause for the shopper's decision (human in the loop) unless the shopper
   opted into auto-approval for that tool.
3. Verify: the answer's fact references are rendered from the turn's evidence (cite mode,
   `curio.engine.facts`), and the domain's grounding check (the backstop) compares the
   values the model typed itself with tool evidence. A reference that cannot be rendered or
   a typed value that does not match fails verification: one self-repair attempt is allowed
   before a deterministic, grounded fallback. No reference reaches the client: every text
   the engine sends (answers, questions, quick replies, fallbacks) is rendered first.

Everything domain-specific (tools, prompts, evidence, grounding, suggestions) lives behind the
`Toolkit` protocol; budgets, the trace, approvals and resume are shared by every domain.

The server keeps no session state: shortlists, preferences and pending proposals live on
the client. Proposals are HMAC-signed over their tool name, arguments and the client's session
id, so a resumed decision can approve or narrow exactly what this agent proposed in that
session, never forge, widen or move it to another domain or session. The signer's nonce store
remembers answered proposals until they expire, so none is answered twice.
"""

import logging
import time
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, ValidationError

from curio.engine.facts import Rendered, checked_report, describe_report, render
from curio.engine.schemas import (
    ActionDecision,
    AgentEvent,
    AgentStep,
    AppliedEffect,
    AssistantReply,
    ChatTurn,
    Clarification,
    GroundingReport,
    Intent,
    PendingAction,
    ReplyEvent,
    Schemas,
    StepEvent,
    TurnContext,
)
from curio.engine.signing import ActionSigner, InvalidActionError, narrow_args
from curio.engine.toolkit import ReadOutcome, ToolError, Toolkit, Turn
from curio.engine.tools import AskShopper, Policy

logger = logging.getLogger(__name__)

# Each ChatTurn is one message; older turns rarely matter and only cost tokens.
MAX_HISTORY_TURNS = 10
MAX_TOOL_CALLS = 5
# A question whose facts fail grounding twice becomes this one, without quick replies.
FALLBACK_QUESTION = "Could you tell me a little more about what you're looking for?"
QUESTION_REPAIR = (
    "Not asked: the question states facts no tool returned ({issues}). Ask again without "
    "them (refer to items, prices and details from the tool results only through fact "
    "references), or answer instead."
)
# Default wall-clock budget for one turn; checked before every model call. An app may set
# another per engine (`turn_deadline_seconds`), e.g. for a slow model behind a relay.
TURN_DEADLINE_SECONDS = 90.0


class AgentEngine[T]:
    def __init__(
        self,
        chat_model: BaseChatModel,
        toolkit: Toolkit[T],
        *,
        signer: ActionSigner | None = None,
        clock: Callable[[], float] = time.monotonic,
        schemas: Schemas | None = None,
        turn_deadline_seconds: float = TURN_DEADLINE_SECONDS,
    ) -> None:
        """`schemas` are the models a turn builds: the engine's own by default, or an app's
        subclasses that carry its domains' payloads (`curio.app.schemas.WIRE`).
        `turn_deadline_seconds` is the wall-clock budget of one turn."""
        if turn_deadline_seconds <= 0:
            raise ValueError("turn_deadline_seconds must be positive")
        self.turn_deadline_seconds = float(turn_deadline_seconds)
        self._toolkit = toolkit
        self._signer = signer or ActionSigner()
        self._clock = clock
        self._schemas = schemas or Schemas()
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
        context = self._toolkit.clean_context(context or self._schemas.context())
        history_messages, referenced = self._toolkit.history(history[-MAX_HISTORY_TURNS:])
        if decision is not None:
            yield from self._resume(decision, history_messages, referenced, context)
            return
        turn = self._new_turn(message, context, self._schemas.intent(query=message), referenced)
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
        intent: Intent,
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
        action = self._signer.verify(decision.action, context.session_id)
        schema = self._approval_schema(action)
        # The proposing turn's signed filters, so a follow-up search keeps them.
        intent = action.intent or self._schemas.intent(query=action.request)
        turn = self._new_turn(action.request, context, intent.resumed(), referenced, resuming=True)
        self._toolkit.refresh(turn)
        if decision.approved:
            args = narrow_args(decision)
            effect, items, outcome = self._apply_decision(schema, args)
            # Single use: from here the decision counts, even if it is sent again. An edit
            # refused above leaves the proposal open, so the shopper can answer it again.
            self._signer.claim(action)
            self._applied(turn, effect)
            if items:
                turn.show(items)
            step = AgentStep(
                kind="approval", name=action.tool, summary=f"Approved: {outcome}", detail=args
            )
            note = f"The shopper approved your proposal. {outcome} Confirm briefly."
        else:
            self._signer.claim(action)
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
        replied = False
        try:
            yield self._record(turn, step, started)
            messages = self._toolkit.prompt(turn, f"[Decision on your proposal] {note}", history)
            for event in self._loop(turn, messages):
                replied = replied or isinstance(event, ReplyEvent)
                yield event
        except BaseException:
            # The reply (and with it the effect) never reached the client: the model failed
            # or the client went away. Let the shopper answer the proposal again.
            if not replied:
                self._signer.release(action)
            raise

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
                    yield self._record(
                        turn,
                        AgentStep(
                            kind="guardrail",
                            name="Empty answer",
                            summary="The model's answer was empty; showing verified items.",
                            status="blocked",
                        ),
                    )
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
                outcome = self._toolkit.read(turn, action)
                if isinstance(outcome, Generator):
                    outcome, started = yield from self._progress(turn, outcome, started)
                result, step = outcome
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
            return (yield from self._ask(turn, action, call_id, messages, started))
        return (yield from self._propose(turn, name, call_id, action, messages, started))

    def _ask(
        self,
        turn: Turn[T],
        action: AskShopper,
        call_id: str,
        messages: list[BaseMessage],
        started: float,
    ) -> Iterator[AgentEvent]:
        """Pause for the shopper's answer. The question and its quick replies are grounded
        like an answer: a first failure goes back to the model (it may ask again or answer),
        a second one asks a generic question without the unverified facts."""
        # Quick replies are candidate answers, so each reads as a question of its own.
        options = "\n".join(f"- {option.rstrip(' .!?')}?" for option in action.options)
        report = self._grounded(turn, self._render(turn, f"{action.question}\n{options}".strip()))
        if report.status == "fallback":
            detail = {"issues": report.issues}
            if turn.question_issues is None:
                turn.question_issues = report
                yield self._record(
                    turn,
                    AgentStep(
                        kind="guardrail",
                        name="Question grounding",
                        summary=(
                            f"Found {len(report.issues)} unverified claims in a question; "
                            "asking for a rewrite."
                        ),
                        status="error",
                        detail=detail,
                    ),
                    started,
                )
                messages.append(
                    ToolMessage(
                        content=QUESTION_REPAIR.format(issues="; ".join(report.issues)),
                        tool_call_id=call_id,
                    )
                )
                return None
            yield self._record(
                turn,
                AgentStep(
                    kind="guardrail",
                    name="Question grounding",
                    summary="Question still unverified; asking a general question instead.",
                    status="blocked",
                    detail=detail,
                ),
                started,
            )
            first = turn.question_issues
            question = getattr(self._toolkit, "fallback_question", FALLBACK_QUESTION)
            grounding = _with_status(first, "fallback", {})
            clarification = Clarification(question=question, options=[])
            return self._reply(
                turn, question, grounding, status="needs_input", clarification=clarification
            )
        if turn.question_issues is not None:
            report = _with_status(turn.question_issues, "repaired", report.rendered)
        question = self._render(turn, action.question).text
        quick = [self._render(turn, option).text for option in action.options]
        yield self._record(
            turn,
            AgentStep(
                kind="approval",
                name="AskShopper",
                summary="Waiting for the shopper's answer.",
                status="pending",
                detail={"question": question, "options": quick},
            ),
            started,
        )
        return self._reply(
            turn,
            question,
            report,
            status="needs_input",
            clarification=Clarification(question=question, options=quick),
        )

    def _progress(
        self, turn: Turn[T], stages: Generator[AgentStep, None, ReadOutcome], started: float
    ) -> Generator[StepEvent, None, tuple[ReadOutcome, float]]:
        """Stream a staged READ tool's progress steps; return its outcome and the time the
        last stage ended, so the final step is timed from there."""
        while True:
            try:
                step = next(stages)
            except StopIteration as done:
                return done.value, started
            yield self._record(turn, step, started)
            started = self._clock()

    def _propose(
        self,
        turn: Turn[T],
        name: str,
        call_id: str,
        action: BaseModel,
        messages: list[BaseMessage],
        started: float,
    ) -> Iterator[AgentEvent]:
        """An approval-gated state change: apply it if auto-approved, else ask the shopper.

        A toolkit may define `vet_proposal(turn, action)` to refuse a proposal in this turn's
        context (raising ToolError) before anything is applied or signed."""
        try:
            if (vet := getattr(self._toolkit, "vet_proposal", None)) is not None:
                vet(turn, action)
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
            session=turn.context.session_id,
            action_type=self._schemas.pending_action,
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
        rendered = self._render(turn, text)
        report = self._grounded(turn, rendered)
        if report.status != "fallback":
            if report.status == "verified":
                yield self._record(
                    turn,
                    AgentStep(
                        kind="guardrail",
                        name="Grounding check",
                        summary=describe_report(report),
                        detail={"counts": report.counts, "rendered": report.rendered},
                    ),
                    started,
                )
            yield self._reply(turn, rendered.text, report)
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
        if self._out_of_time(turn):
            yield self._record(turn, _time_budget_step())
            yield self._fallback_after(turn, report)
            return
        started = self._clock()
        instruction = self._toolkit.repair_instruction.format(issues="; ".join(report.issues))
        repaired = self._agent.invoke([*messages, answer, HumanMessage(instruction)])
        repaired_text = "" if repaired.tool_calls else StrOutputParser().invoke(repaired).strip()
        second_text = self._render(turn, repaired_text) if repaired_text else None
        second = self._grounded(turn, second_text) if second_text else None
        if second_text is not None and second is not None and second.status != "fallback":
            final = _with_status(report, "repaired", second.rendered)
            yield self._record(
                turn,
                AgentStep(
                    kind="guardrail",
                    name="Self-correction",
                    summary="Rewrote the answer; every claim now matches the catalog.",
                ),
                started,
            )
            yield self._reply(turn, second_text.text, final)
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
        yield self._fallback_after(turn, report)

    # --- Helpers --------------------------------------------------------------------------

    def _out_of_time(self, turn: Turn[T]) -> bool:
        return self._clock() - turn.started > self.turn_deadline_seconds

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
        # Summaries quote the shopper ("Found 3 drinks for "..."") and model text: they go
        # to DEBUG only. INFO keeps the step's kind, name, status and duration.
        logger.info(
            "agent step %s/%s (%s, %d ms)", step.kind, step.name, step.status, step.duration_ms
        )
        logger.debug("agent step %s/%s: %s", step.kind, step.name, step.summary)
        return StepEvent(step=step)

    def _fallback_reply(self, turn: Turn[T]) -> ReplyEvent:
        """Catalog facts when a budget ends the turn or the model's answer is empty. They are
        rendered and checked like an answer, so the report says what they state ("verified"
        with its counts), or "not_applicable" when they state nothing."""
        rendered = self._render(turn, self._toolkit.fallback(turn.items, turn.extras))
        report = self._grounded(turn, rendered)
        if report.status != "verified":
            report = GroundingReport(status="not_applicable")
        return self._reply(turn, rendered.text, report)

    def _fallback_after(self, turn: Turn[T], report: GroundingReport) -> ReplyEvent:
        """Catalog facts in place of an unverified answer: `report` (the first draft's) under
        status "fallback", with the facts the fallback renders."""
        rendered = self._render(turn, self._toolkit.fallback(turn.items, turn.extras))
        return self._reply(
            turn, rendered.text, _with_status(report, "fallback", dict(rendered.counts))
        )

    def _render(self, turn: Turn[T], text: str) -> Rendered:
        """`text` with its fact references rendered from this turn's evidence."""
        return render(text, self._toolkit.facts(turn))

    def _grounded(self, turn: Turn[T], rendered: Rendered) -> GroundingReport:
        """The report of a rendered text: its rendered facts, the references that failed and
        the backstop's check of the values the model typed itself."""
        return checked_report(rendered, lambda text: self._toolkit.check(turn, text))

    def _reply(
        self, turn: Turn[T], text: str, grounding: GroundingReport | None, **fields: Any
    ) -> ReplyEvent:
        complete = fields.get("status", "complete") == "complete"
        return ReplyEvent(
            reply=self._schemas.reply(
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


def _with_status(
    report: GroundingReport, status: str, rendered: dict[str, int] | None = None
) -> GroundingReport:
    """`report`'s counts and issues under another status ("repaired", "fallback"), with the
    facts rendered in the text the client is shown instead, when given."""
    update: dict[str, Any] = {"status": status}
    if rendered is not None:
        update["rendered"] = dict(rendered)
    return report.model_copy(update=update)


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
    for name, target in effect.remembers.items():
        if (approved := getattr(effect, name)) is not None:
            update[target] = _merged(getattr(context, target), approved)
    for name, target in effect.adds.items():
        if ids := getattr(effect, name):
            update[target] = _union(getattr(context, target), ids, _limit(context, target))
    return context.model_copy(update=update)


def _merged[M: BaseModel](current: M, approved: M) -> M:
    """Approved values added to remembered ones: lists are joined, set flags and values win."""
    data = current.model_dump()
    for name, value in approved.model_dump(exclude_defaults=True).items():
        if isinstance(value, list):
            value = _newest(data[name], value, _limit(current, name))
        data[name] = value
    return type(current).model_validate(data)


def _limit(model: BaseModel, name: str) -> int | None:
    """The `max_length` of a list field, if it has one."""
    constraints = type(model).model_fields[name].metadata
    return next((m.max_length for m in constraints if _bounded(m)), None)


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
