"""Shopping agent: plan, act through policy-gated tools, verify, and hand control back.

One turn runs as a generator of observable events:

1. Plan: extract a structured search intent, merged with approved session preferences.
2. Act: a bounded tool loop. Read tools run immediately; AskShopper pauses for a quick
   reply; SaveToShortlist / RememberPreferences pause for approval (human in the loop)
   unless the shopper opted into auto-approval for that tool.
3. Verify: prices and product IDs in the final answer are checked against tool evidence.
   One self-repair attempt is allowed before a deterministic, grounded fallback.

The server is stateless: shortlist, preferences and pending proposals live on the client.
Proposals are HMAC-signed, so a resumed decision can approve or narrow exactly what the
agent proposed, never forge or widen it.
"""

import logging
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, ValidationError

from clothes_recommender.catalog import ProductCatalog
from clothes_recommender.chat.guardrails import (
    ActionSigner,
    amounts_in,
    check_grounding,
    narrow_args,
)
from clothes_recommender.chat.intent import IntentExtractor, SearchIntent
from clothes_recommender.chat.navigator import CatalogNavigator, NoPersonalHistoryError
from clothes_recommender.chat.prompts import ANSWER_PROMPT, REPAIR_INSTRUCTION, render_products
from clothes_recommender.chat.schemas import (
    ActionDecision,
    AgentEvent,
    AgentStep,
    AppliedEffect,
    AssistantReply,
    ChatTurn,
    Clarification,
    GroundingReport,
    ReplyEvent,
    ShopperPreferences,
    StepEvent,
    TurnContext,
)
from clothes_recommender.chat.tools import (
    POLICIES,
    TOOLS,
    AskShopper,
    FindRelated,
    InspectProducts,
    PersonalPicks,
    Policy,
    RememberPreferences,
    SaveToShortlist,
    SearchCatalog,
)
from clothes_recommender.domain import Product
from clothes_recommender.search import ProductSearch

__all__ = ["MAX_HISTORY_TURNS", "MAX_TOOL_CALLS", "AssistantReply", "ChatTurn", "ShoppingAssistant"]

logger = logging.getLogger(__name__)

# Each ChatTurn is one message; older turns rarely matter and only cost tokens.
MAX_HISTORY_TURNS = 10
MAX_TOOL_CALLS = 5
# Wall-clock budget for one turn; checked before every model call.
TURN_DEADLINE_SECONDS = 90.0
INVALID_ACTION = (
    "Invalid action. Use SearchCatalog with a visual query, InspectProducts or SaveToShortlist "
    "with one to five positive catalog product IDs, FindRelated with one product ID and a "
    "relation, or AskShopper with a question and two to four options."
)


class _ToolError(Exception):
    """A valid call that cannot run here; the message goes back to the model."""


@dataclass
class _Turn:
    message: str
    context: TurnContext
    intent: SearchIntent
    started: float
    products: list[Product] = field(default_factory=list)
    reasons: dict[int, list[str]] = field(default_factory=dict)
    evidence: dict[int, Product] = field(default_factory=dict)
    steps: list[AgentStep] = field(default_factory=list)
    applied: list[AppliedEffect] = field(default_factory=list)
    calls: int = 0
    # After a decision the shopper already answered, the agent may not pause again.
    resuming: bool = False

    def show(self, products: Iterable[Product], reasons: dict[int, list[str]] | None = None):
        self.products = list(products)
        self.reasons = reasons or {}
        self.evidence.update({p.product_id: p for p in self.products})


class ShoppingAssistant:
    def __init__(
        self,
        chat_model: BaseChatModel,
        search: ProductSearch,
        catalog: ProductCatalog,
        top_k: int = 5,
        intent_extractor: IntentExtractor | None = None,
        *,
        navigator: CatalogNavigator | None = None,
        signer: ActionSigner | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._search = search
        self._catalog = catalog
        self._top_k = top_k
        self._navigator = navigator
        self._signer = signer or ActionSigner()
        self._clock = clock
        self._vocab = {
            name: {value.lower(): value for value in catalog.distinct(name)}
            for name in ("section", "colour")
        }
        self._intent_extractor = intent_extractor or IntentExtractor(
            chat_model,
            product_types=catalog.distinct("product_type"),
            sections=catalog.distinct("section"),
            colours=catalog.distinct("colour"),
        )
        self._agent = chat_model.bind_tools(list(TOOLS.values()))

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
        context = self._clean_context(context or TurnContext())
        history_messages, referenced = _to_messages(history[-MAX_HISTORY_TURNS:], self._catalog)
        if decision is not None:
            yield from self._resume(decision, history_messages, referenced, context)
            return
        turn = _Turn(message, context, SearchIntent(query=message), self._clock())
        turn.evidence.update({p.product_id: p for p in referenced})

        started = self._clock()
        intent = self._intent_extractor.extract(message, history_messages)
        intent, remembered = _with_preferences(intent, context.preferences)
        turn.intent = intent
        yield self._record(
            turn,
            AgentStep(
                kind="plan",
                name="Understand request",
                summary=_describe(intent, remembered),
                detail=intent.model_dump(exclude_defaults=True) | {"from_memory": remembered},
            ),
            started,
        )
        if intent.wants_products:
            started = self._clock()
            turn.show(self._find_products(intent))
            yield self._record(turn, _search_step(intent, turn.products), started)
        messages = ANSWER_PROMPT.invoke(
            {
                "message": message,
                "history": history_messages,
                "products": render_products(turn.products),
                "session": self._session(turn),
            }
        ).to_messages()
        yield from self._loop(turn, messages)

    # --- Turn phases ----------------------------------------------------------------------

    def _resume(
        self,
        decision: ActionDecision,
        history: list[BaseMessage],
        referenced: list[Product],
        context: TurnContext,
    ) -> Iterator[AgentEvent]:
        started = self._clock()
        action = self._signer.verify(decision.action)
        turn = _Turn(
            action.request,
            context,
            SearchIntent(query=action.request, wants_products=False),
            started,
            resuming=True,
        )
        turn.evidence.update({p.product_id: p for p in referenced})
        schema = TOOLS[action.tool]
        if decision.approved:
            args = narrow_args(decision)
            effect, products, outcome = self._apply(schema.model_validate(args))
            turn.applied.append(effect)
            turn.show(products)
            step = AgentStep(
                kind="approval", name=action.tool, summary=f"Approved: {outcome}", detail=args
            )
            note = f"The shopper approved your proposal. {outcome} Confirm briefly."
        else:
            proposed = self._catalog.get_many(action.args.get("product_ids", []))
            turn.show(proposed)
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
        messages = ANSWER_PROMPT.invoke(
            {
                "message": f"[Decision on your proposal] {note}",
                "history": history,
                "products": render_products(turn.products),
                "session": self._session(turn),
            }
        ).to_messages()
        yield from self._loop(turn, messages)

    def _loop(self, turn: _Turn, messages: list[BaseMessage]) -> Iterator[AgentEvent]:
        while True:
            if self._clock() - turn.started > TURN_DEADLINE_SECONDS:
                yield self._record(
                    turn,
                    AgentStep(
                        kind="guardrail",
                        name="Time budget",
                        summary="Stopped after the turn's time budget; showing verified items.",
                        status="blocked",
                    ),
                )
                yield self._reply(turn, _fallback(turn.products), _deterministic())
                return
            started = self._clock()
            answer = self._agent.invoke(messages)
            if not answer.tool_calls:
                text = StrOutputParser().invoke(answer).strip()
                if not text:
                    yield self._reply(turn, _fallback(turn.products), _deterministic())
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
                    yield self._reply(turn, _fallback(turn.products), _deterministic())
                    return
                turn.calls += 1
                started = self._clock()
                action = _validate(call["name"], call["args"])
                if action is None:
                    yield self._record(
                        turn,
                        AgentStep(
                            kind="tool",
                            name=str(call["name"])[:40],
                            summary="Rejected an invalid action; nothing ran.",
                            status="blocked",
                        ),
                        started,
                    )
                    messages.append(ToolMessage(content=INVALID_ACTION, tool_call_id=call["id"]))
                    continue
                policy = POLICIES[call["name"]]
                if policy is Policy.READ:
                    try:
                        result, step = self._read(turn, action)
                    except _ToolError as error:
                        result = str(error)
                        step = AgentStep(
                            kind="tool", name=call["name"], summary=result, status="error"
                        )
                    yield self._record(turn, step, started)
                    messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
                    continue
                if turn.resuming and call["name"] not in turn.context.auto_approve:
                    result = "Not available now: the shopper just answered a proposal."
                    yield self._record(
                        turn,
                        AgentStep(kind="tool", name=call["name"], summary=result, status="blocked"),
                        started,
                    )
                    messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
                    continue
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
                    yield self._reply(
                        turn,
                        action.question,
                        None,
                        status="needs_input",
                        clarification=Clarification(
                            question=action.question, options=action.options
                        ),
                    )
                    return
                # Approval-gated state change.
                try:
                    if call["name"] in turn.context.auto_approve:
                        effect, products, outcome = self._apply(action)
                        turn.applied.append(effect)
                        turn.show(products)
                        result = f"Auto-approved by the shopper's settings. {outcome}"
                        yield self._record(
                            turn,
                            AgentStep(
                                kind="approval",
                                name=call["name"],
                                summary=f"Auto-approved: {outcome}",
                                detail=action.model_dump(),
                            ),
                            started,
                        )
                        messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
                        continue
                    _, products, outcome = self._apply(action, dry_run=True)
                except _ToolError as error:
                    yield self._record(
                        turn,
                        AgentStep(
                            kind="tool", name=call["name"], summary=str(error), status="error"
                        ),
                        started,
                    )
                    messages.append(ToolMessage(content=str(error), tool_call_id=call["id"]))
                    continue
                if products:
                    turn.show(products)
                summary = f"Proposed: {outcome}"
                pending = self._signer.issue(
                    call["name"],
                    _canonical_args(action),
                    turn.message,
                    summary=summary,
                    products=products,
                )
                yield self._record(
                    turn,
                    AgentStep(
                        kind="approval",
                        name=call["name"],
                        summary="Waiting for the shopper's approval.",
                        status="pending",
                        detail=pending.args,
                    ),
                    started,
                )
                text = (
                    "I'd like to save these pieces to your shortlist. Approve, edit or decline?"
                    if isinstance(action, SaveToShortlist)
                    else "Should I remember these preferences for your next searches? "
                    "Approve, edit or decline."
                )
                yield self._reply(turn, text, None, status="needs_approval", pending_action=pending)
                return

    def _verify(
        self,
        turn: _Turn,
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
        evidence = list(turn.evidence.values())
        allowed = self._allowed_amounts(turn)
        report = check_grounding(text, evidence, allowed)
        if report.status != "fallback":
            if report.status == "verified":
                yield self._record(
                    turn,
                    AgentStep(
                        kind="guardrail",
                        name="Grounding check",
                        summary=f"Verified {report.checked} prices and IDs against the catalog.",
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
        started = self._clock()
        repaired = self._agent.invoke(
            [
                *messages,
                answer,
                HumanMessage(REPAIR_INSTRUCTION.format(issues="; ".join(report.issues))),
            ]
        )
        repaired_text = "" if repaired.tool_calls else StrOutputParser().invoke(repaired).strip()
        second = check_grounding(repaired_text, evidence, allowed) if repaired_text else None
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
        final = GroundingReport(status="fallback", checked=report.checked, issues=report.issues)
        yield self._reply(turn, _fallback(turn.products), final)

    # --- Tools ----------------------------------------------------------------------------

    def _read(self, turn: _Turn, action: BaseModel) -> tuple[str, AgentStep]:
        name = type(action).__name__
        if isinstance(action, SearchCatalog):
            turn.intent = turn.intent.model_copy(
                update={"query": action.query.strip(), "wants_products": True}
            )
            turn.show(self._find_products(turn.intent))
            step = _search_step(turn.intent, turn.products)
        elif isinstance(action, InspectProducts):
            turn.show(self._catalog.get_many(dict.fromkeys(action.product_ids)))
            step = AgentStep(
                kind="tool",
                name=name,
                summary=f"Read exact details of {len(turn.products)} products.",
                detail={"product_ids": [p.product_id for p in turn.products]},
            )
        elif isinstance(action, FindRelated):
            product = self._catalog.get(action.product_id)
            if product is None:
                raise _ToolError(f"Product {action.product_id} is not in the catalog.")
            if self._navigator is None:
                raise _ToolError("Related products are unavailable right now.")
            found = self._navigator.related(action.product_id, action.relation, self._top_k)
            turn.show(
                self._catalog.get_many(e.product_id for e in found),
                {e.product_id: list(e.reasons) for e in found},
            )
            label = {
                "complement": "pieces bought with",
                "similar": "look-alikes of",
                "alternative": "higher-priced alternatives to",
            }[action.relation]
            step = AgentStep(
                kind="tool",
                name=name,
                summary=f"Found {len(turn.products)} {label} {product.name}.",
                detail={"product_id": action.product_id, "relation": action.relation},
            )
        elif isinstance(action, PersonalPicks):
            if turn.context.shopper_id is None:
                raise _ToolError(
                    "No shopping history is linked. Ask the shopper to choose one under "
                    "'Shopping history', or search by description instead."
                )
            if self._navigator is None:
                raise _ToolError("Personal picks are unavailable right now.")
            try:
                found = self._navigator.personal(turn.context.shopper_id, self._top_k)
            except NoPersonalHistoryError as error:
                raise _ToolError("The linked shopping history was not found.") from error
            turn.show(
                self._catalog.get_many(e.product_id for e in found),
                {e.product_id: list(e.reasons) for e in found},
            )
            step = AgentStep(
                kind="tool",
                name=name,
                summary=f"Ranked {len(turn.products)} picks from the linked shopping history.",
                detail={"shopper_id": turn.context.shopper_id},
            )
        else:  # pragma: no cover - POLICIES and TOOLS are defined together
            raise _ToolError(INVALID_ACTION)
        return render_products(turn.products, turn.reasons), step

    def _apply(
        self, action: BaseModel, *, dry_run: bool = False
    ) -> tuple[AppliedEffect, list[Product], str]:
        """Validate a state change against the catalog; the client applies the effect."""
        if isinstance(action, SaveToShortlist):
            products = self._catalog.get_many(dict.fromkeys(action.product_ids))
            if not products:
                raise _ToolError("None of those product IDs are in the catalog.")
            names = ", ".join(p.name for p in products)
            verb = "Would save" if dry_run else "Saved"
            return (
                AppliedEffect(kind="shortlist", product_ids=[p.product_id for p in products]),
                products,
                f"{verb} {len(products)} to the shortlist: {names}.",
            )
        if isinstance(action, RememberPreferences):
            preferences = ShopperPreferences(
                sections=_known(action.sections, self._vocab["section"]),
                colours=_known(action.colours, self._vocab["colour"]),
                max_price=action.max_price,
            )
            if preferences.is_empty():
                raise _ToolError("None of those preferences match the catalog's values.")
            verb = "Would remember" if dry_run else "Remembered"
            return (
                AppliedEffect(kind="preferences", preferences=preferences),
                [],
                f"{verb} {_describe_preferences(preferences)}.",
            )
        raise _ToolError(INVALID_ACTION)

    # --- Helpers --------------------------------------------------------------------------

    def _find_products(self, intent: SearchIntent) -> list[Product]:
        hits = self._search.by_text(
            intent.query, top_k=self._top_k, product_filter=intent.to_filter()
        )
        return self._catalog.get_many(hit.product_id for hit in hits)

    def _clean_context(self, context: TurnContext) -> TurnContext:
        preferences = context.preferences.model_copy(
            update={
                "sections": _known(context.preferences.sections, self._vocab["section"]),
                "colours": _known(context.preferences.colours, self._vocab["colour"]),
            }
        )
        return context.model_copy(update={"preferences": preferences})

    def _session(self, turn: _Turn) -> str:
        context = turn.context
        parts = [
            "a shopping history is linked"
            if context.shopper_id
            else "no shopping history is linked",
        ]
        if not context.preferences.is_empty():
            parts.append(
                "remembered preferences (already applied to searches): "
                + _describe_preferences(context.preferences)
            )
        shortlist = self._catalog.get_many(dict.fromkeys(context.shortlist))
        turn.evidence.update({p.product_id: p for p in shortlist})
        if shortlist:
            parts.append(
                "shortlist: "
                + "; ".join(f"[id {p.product_id}] {p.name} €{p.price:.2f}" for p in shortlist)
            )
        else:
            parts.append("the shortlist is empty")
        return ". ".join(parts) + "."

    def _allowed_amounts(self, turn: _Turn) -> list[float]:
        intent, preferences = turn.intent, turn.context.preferences
        return [
            *amounts_in(turn.message),
            *(
                value
                for value in (intent.min_price, intent.max_price, preferences.max_price)
                if value is not None
            ),
        ]

    def _record(self, turn: _Turn, step: AgentStep, started: float | None = None) -> StepEvent:
        if started is not None:
            step.duration_ms = max(0, round((self._clock() - started) * 1000))
        turn.steps.append(step)
        logger.info("agent step %s/%s: %s (%s)", step.kind, step.name, step.summary, step.status)
        return StepEvent(step=step)

    def _reply(
        self,
        turn: _Turn,
        text: str,
        grounding: GroundingReport | None,
        **fields,
    ) -> ReplyEvent:
        return ReplyEvent(
            reply=AssistantReply(
                text=text,
                products=turn.products,
                intent=turn.intent,
                steps=turn.steps,
                applied=turn.applied,
                grounding=grounding,
                suggestions=_suggestions(turn, fields.get("status", "complete")),
                **fields,
            )
        )


def _final(events: Iterable[AgentEvent]) -> AssistantReply:
    for event in events:
        if isinstance(event, ReplyEvent):
            return event.reply
    raise RuntimeError("The assistant finished without a reply")  # pragma: no cover


def _validate(name: str, args: object) -> BaseModel | None:
    schema = TOOLS.get(name)
    if schema is None:
        return None
    try:
        return schema.model_validate(args)
    except ValidationError:
        return None


def _canonical_args(action: BaseModel) -> dict:
    return action.model_dump(mode="json", exclude_defaults=True)


def _known(values: Iterable[str], vocabulary: dict[str, str]) -> list[str]:
    return list(
        dict.fromkeys(
            canonical for value in values if (canonical := vocabulary.get(value.strip().lower()))
        )
    )


def _with_preferences(
    intent: SearchIntent, preferences: ShopperPreferences
) -> tuple[SearchIntent, list[str]]:
    """Fill constraints the shopper did not state this turn from approved preferences."""
    update: dict = {}
    if preferences.sections and not intent.sections:
        update["sections"] = list(preferences.sections)
    if preferences.colours and not intent.colours:
        update["colours"] = list(preferences.colours)
    if preferences.max_price is not None and intent.max_price is None:
        update["max_price"] = preferences.max_price
    return intent.model_copy(update=update), sorted(update)


def _describe(intent: SearchIntent, remembered: Sequence[str] = ()) -> str:
    if not intent.wants_products:
        return "No product search needed."
    parts = [f"Looking for “{intent.query}”"]
    filters = (intent.product_types, intent.sections, intent.colours)
    parts += [", ".join(values) for values in filters if values]
    if intent.min_price is not None and intent.max_price is not None:
        parts.append(f"€{intent.min_price:g} to €{intent.max_price:g}")
    elif intent.max_price is not None:
        parts.append(f"under €{intent.max_price:g}")
    elif intent.min_price is not None:
        parts.append(f"over €{intent.min_price:g}")
    summary = " · ".join(parts)
    if remembered:
        summary += " (memory: " + ", ".join(f.replace("_", " ") for f in remembered) + ")"
    return summary


def _describe_preferences(preferences: ShopperPreferences) -> str:
    parts = [*preferences.sections, *preferences.colours]
    if preferences.max_price is not None:
        parts.append(f"budget up to €{preferences.max_price:g}")
    return ", ".join(parts)


def _search_step(intent: SearchIntent, products: Sequence[Product]) -> AgentStep:
    return AgentStep(
        kind="tool",
        name="SearchCatalog",
        summary=f"Found {len(products)} matches for “{intent.query}”.",
        status="ok" if products else "skipped",
        detail={"query": intent.query, "filters": intent.model_dump(exclude_defaults=True)},
    )


def _deterministic() -> GroundingReport:
    return GroundingReport(status="verified", checked=0)


def _suggestions(turn: _Turn, status: str) -> list[str]:
    """Next-step chips; each one exercises a tool the agent can actually use."""
    if status != "complete":
        return []
    products = turn.products
    if not products:
        options = ["Show me something under €30", "White sneakers"]
        if turn.context.shopper_id:
            options.insert(0, "Picks from my shopping history")
        return options
    first = products[0]
    options = [f"What goes with the {first.name}?", "Something cheaper"]
    if len(products) >= 2:
        options.append("Compare the first two")
    shortlisted = set(turn.context.shortlist) | {
        pid for effect in turn.applied for pid in effect.product_ids
    }
    if first.product_id not in shortlisted:
        options.append(f"Shortlist the {first.name}")
    return options[:4]


def _to_messages(
    turns: Sequence[ChatTurn], catalog: ProductCatalog
) -> tuple[list[BaseMessage], list[Product]]:
    messages: list[BaseMessage] = []
    referenced: list[Product] = []
    for turn in turns:
        if turn.role == "user":
            messages.append(HumanMessage(turn.content))
        else:
            products = catalog.get_many(turn.product_ids)
            referenced.extend(products)
            context = (
                "\nReferenced catalog items:\n" + render_products(products) if products else ""
            )
            messages.append(AIMessage(turn.content + context))
    return messages, referenced


def _fallback(products: Sequence[Product]) -> str:
    if not products:
        return "I couldn't confirm matching pieces. Try another description or adjust your filters."
    return "Here are the pieces I could confirm:\n" + "\n".join(
        f"- {p.name} · {p.colour} · €{p.price:.2f}" for p in products
    )
