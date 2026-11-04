"""Fashion shopping agent: the shared agent loop (`chat.engine`) with fashion tools.

1. Plan: extract a structured search intent, merged with approved session preferences, and
   run a first catalog search.
2. Act: SearchCatalog, InspectProducts, FindRelated and PersonalPicks read the catalog and the
   recommenders; AskShopper pauses for a quick reply; SaveToShortlist and RememberPreferences
   pause for approval unless the shopper opted into auto-approval for that tool.
3. Verify: prices and product IDs in the final answer are checked against tool evidence.
"""

import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from clothes_recommender.catalog import ProductCatalog
from clothes_recommender.chat.engine import (
    MAX_HISTORY_TURNS,
    MAX_TOOL_CALLS,
    TURN_DEADLINE_SECONDS,
    AgentEngine,
    ToolError,
    Turn,
)
from clothes_recommender.chat.guardrails import ActionSigner, amounts_in, check_grounding
from clothes_recommender.chat.intent import IntentExtractor, SearchIntent
from clothes_recommender.chat.navigator import CatalogNavigator, NoPersonalHistoryError
from clothes_recommender.chat.prompts import ANSWER_PROMPT, REPAIR_INSTRUCTION, render_products
from clothes_recommender.chat.schemas import (
    AgentStep,
    AppliedEffect,
    AssistantReply,
    ChatTurn,
    GroundingReport,
    ShopperPreferences,
    TurnContext,
)
from clothes_recommender.chat.tools import (
    POLICIES,
    TOOLS,
    FindRelated,
    InspectProducts,
    PersonalPicks,
    RememberPreferences,
    SaveToShortlist,
    SearchCatalog,
)
from clothes_recommender.domain import Product
from clothes_recommender.search import ProductSearch

__all__ = [
    "MAX_HISTORY_TURNS",
    "MAX_TOOL_CALLS",
    "TURN_DEADLINE_SECONDS",
    "AssistantReply",
    "ChatTurn",
    "FashionToolkit",
    "ShoppingAssistant",
]

INVALID_ACTION = (
    "Invalid action. Use SearchCatalog with a visual query, InspectProducts or SaveToShortlist "
    "with one to five positive catalog product IDs, FindRelated with one product ID and a "
    "relation, or AskShopper with a question and two to four options."
)


class FashionToolkit:
    tools = TOOLS
    policies = POLICIES
    invalid_action = INVALID_ACTION
    repair_instruction = REPAIR_INSTRUCTION
    grounding_subject = "prices and IDs"

    def __init__(
        self,
        search: ProductSearch,
        catalog: ProductCatalog,
        top_k: int,
        intent_extractor: IntentExtractor,
        navigator: CatalogNavigator | None,
    ) -> None:
        self._search = search
        self._catalog = catalog
        self._top_k = top_k
        self._intent_extractor = intent_extractor
        self._navigator = navigator
        self._vocab = {
            name: {value.lower(): value for value in catalog.distinct(name)}
            for name in ("section", "colour")
        }

    def key(self, item: Product) -> int:
        return item.product_id

    # --- Turn phases ----------------------------------------------------------------------

    def clean_context(self, context: TurnContext) -> TurnContext:
        preferences = context.preferences.model_copy(
            update={
                "sections": _known(context.preferences.sections, self._vocab["section"]),
                "colours": _known(context.preferences.colours, self._vocab["colour"]),
            }
        )
        return context.model_copy(update={"preferences": preferences})

    def history(self, turns: Sequence[ChatTurn]) -> tuple[list[BaseMessage], list[Product]]:
        messages: list[BaseMessage] = []
        referenced: list[Product] = []
        for turn in turns:
            if turn.role == "user":
                messages.append(HumanMessage(turn.content))
            else:
                products = self._catalog.get_many(turn.product_ids)
                referenced.extend(products)
                context = (
                    "\nReferenced catalog items:\n" + render_products(products) if products else ""
                )
                messages.append(AIMessage(turn.content + context))
        return messages, referenced

    def plan(self, turn: Turn[Product], history: Sequence[BaseMessage]) -> Iterator[AgentStep]:
        intent = self._intent_extractor.extract(turn.message, history)
        intent, remembered = _with_preferences(intent, turn.context.preferences)
        turn.intent = intent
        yield AgentStep(
            kind="plan",
            name="Understand request",
            summary=_describe(intent, remembered),
            detail=intent.model_dump(exclude_defaults=True) | {"from_memory": remembered},
        )
        if intent.wants_products:
            turn.show(self._find_products(intent))
            yield _search_step(intent, turn.items)

    def refresh(self, turn: Turn[Product], approved: AppliedEffect | None = None) -> None:
        turn.intent, _ = _with_preferences(turn.intent, turn.context.preferences)
        if approved is not None and approved.preferences is not None:
            # Just-approved values are the shopper's latest word, so they replace earlier ones.
            stated = approved.preferences.model_dump(exclude_defaults=True)
            turn.intent = turn.intent.model_copy(update=stated)

    def prompt(
        self, turn: Turn[Product], message: str, history: Sequence[BaseMessage]
    ) -> list[BaseMessage]:
        return ANSWER_PROMPT.invoke(
            {
                "message": message,
                "history": list(history),
                "products": render_products(turn.items),
                "session": self._session(turn),
            }
        ).to_messages()

    # --- Tools ----------------------------------------------------------------------------

    def read(self, turn: Turn[Product], action: BaseModel) -> tuple[str, AgentStep]:
        name = type(action).__name__
        if isinstance(action, SearchCatalog):
            turn.intent = turn.intent.model_copy(
                update={"query": action.query.strip(), "wants_products": True}
            )
            turn.show(self._find_products(turn.intent))
            step = _search_step(turn.intent, turn.items)
        elif isinstance(action, InspectProducts):
            turn.show(self._catalog.get_many(dict.fromkeys(action.product_ids)))
            step = AgentStep(
                kind="tool",
                name=name,
                summary=f"Read exact details of {len(turn.items)} products.",
                detail={"product_ids": [p.product_id for p in turn.items]},
            )
        elif isinstance(action, FindRelated):
            step = self._related(turn, action)
            if step.status == "skipped":
                # Keep the cards already shown and tell the model what else it can try.
                return step.summary + " " + _RELATED_HINTS[action.relation], step
        elif isinstance(action, PersonalPicks):
            step = self._personal(turn)
        else:  # pragma: no cover - POLICIES and TOOLS are defined together
            raise ToolError(INVALID_ACTION)
        return render_products(turn.items, turn.reasons), step

    def _related(self, turn: Turn[Product], action: FindRelated) -> AgentStep:
        product = self._catalog.get(action.product_id)
        if product is None:
            raise ToolError(f"Product {action.product_id} is not in the catalog.")
        if self._navigator is None:
            raise ToolError("Related products are unavailable right now.")
        found = self._navigator.related(action.product_id, action.relation, self._top_k)
        label = {
            "complement": "pieces bought with",
            "similar": "look-alikes of",
            "alternative": "higher-priced alternatives to",
        }[action.relation]
        if not found:
            return AgentStep(
                kind="tool",
                name="FindRelated",
                summary=f"Found no {label} {product.name}.",
                status="skipped",
                detail={"product_id": action.product_id, "relation": action.relation},
            )
        turn.show(
            self._catalog.get_many(e.product_id for e in found),
            {e.product_id: list(e.reasons) for e in found},
        )
        return AgentStep(
            kind="tool",
            name="FindRelated",
            summary=f"Found {len(turn.items)} {label} {product.name}.",
            detail={"product_id": action.product_id, "relation": action.relation},
        )

    def _personal(self, turn: Turn[Product]) -> AgentStep:
        if turn.context.shopper_id is None:
            raise ToolError(
                "No shopping history is linked. Ask the shopper to choose one under "
                "'Shopping history', or search by description instead."
            )
        if self._navigator is None:
            raise ToolError("Personal picks are unavailable right now.")
        try:
            found = self._navigator.personal(turn.context.shopper_id, self._top_k)
        except NoPersonalHistoryError as error:
            raise ToolError("The linked shopping history was not found.") from error
        turn.show(
            self._catalog.get_many(e.product_id for e in found),
            {e.product_id: list(e.reasons) for e in found},
        )
        return AgentStep(
            kind="tool",
            name="PersonalPicks",
            summary=f"Ranked {len(turn.items)} picks from the linked shopping history.",
            detail={"shopper_id": turn.context.shopper_id},
        )

    def apply(
        self, action: BaseModel, *, dry_run: bool = False
    ) -> tuple[AppliedEffect, list[Product], str]:
        """Validate a state change against the catalog; the client applies the effect."""
        if isinstance(action, SaveToShortlist):
            products = self._catalog.get_many(dict.fromkeys(action.product_ids))
            if not products:
                raise ToolError("None of those product IDs are in the catalog.")
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
                raise ToolError("None of those preferences match the catalog's values.")
            verb = "Would remember" if dry_run else "Remembered"
            return (
                AppliedEffect(kind="preferences", preferences=preferences),
                [],
                f"{verb} {_describe_preferences(preferences)}.",
            )
        raise ToolError(INVALID_ACTION)

    def proposed(self, args: Mapping[str, Any]) -> list[Product]:
        return self._catalog.get_many(args.get("product_ids", []))

    def approval_text(self, action: BaseModel) -> str:
        if isinstance(action, SaveToShortlist):
            return "I'd like to save these pieces to your shortlist. Approve, edit or decline?"
        return (
            "Should I remember these preferences for your next searches? Approve, edit or decline."
        )

    def pending_display(self, items: list[Product]) -> dict[str, Any]:
        return {"products": items}

    # --- Verification and reply -----------------------------------------------------------

    def check(self, turn: Turn[Product], text: str) -> GroundingReport:
        return check_grounding(text, list(turn.evidence.values()), self._allowed_amounts(turn))

    def fallback(self, items: Sequence[Product]) -> str:
        if not items:
            return (
                "I couldn't confirm matching pieces. Try another description or adjust your "
                "filters."
            )
        return "Here are the pieces I could confirm:\n" + "\n".join(
            f"- {p.name} · {p.colour} · €{p.price:.2f}" for p in items
        )

    def reply_fields(self, turn: Turn[Product]) -> dict[str, Any]:
        return {"domain": "fashion", "products": turn.items}

    def suggestions(self, turn: Turn[Product]) -> list[str]:
        """Next-step chips; each one exercises a tool the agent can actually use."""
        products = turn.items
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

    # --- Helpers --------------------------------------------------------------------------

    def _find_products(self, intent: SearchIntent) -> list[Product]:
        hits = self._search.by_text(
            intent.query, top_k=self._top_k, product_filter=intent.to_filter()
        )
        return self._catalog.get_many(hit.product_id for hit in hits)

    def _session(self, turn: Turn[Product]) -> str:
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
        turn.remember(shortlist)
        if shortlist:
            parts.append(
                "shortlist: "
                + "; ".join(f"[id {p.product_id}] {p.name} €{p.price:.2f}" for p in shortlist)
            )
        else:
            parts.append("the shortlist is empty")
        return ". ".join(parts) + "."

    def _allowed_amounts(self, turn: Turn[Product]) -> list[float]:
        intent, preferences = turn.intent, turn.context.preferences
        return [
            *amounts_in(turn.message),
            *(
                value
                for value in (intent.min_price, intent.max_price, preferences.max_price)
                if value is not None
            ),
        ]


class ShoppingAssistant(AgentEngine[Product]):
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
        extractor = intent_extractor or IntentExtractor(
            chat_model,
            product_types=catalog.distinct("product_type"),
            sections=catalog.distinct("section"),
            colours=catalog.distinct("colour"),
        )
        toolkit = FashionToolkit(search, catalog, top_k, extractor, navigator)
        super().__init__(chat_model, toolkit, signer=signer, clock=clock)
        self._navigator = navigator


def _known(values: Iterable[str], vocabulary: dict[str, str]) -> list[str]:
    return list(
        dict.fromkeys(
            canonical for value in values if (canonical := vocabulary.get(value.strip().lower()))
        )
    )


_RELATED_HINTS = {
    "complement": "No shopper bought it together with pieces from another product group. "
    "Try relation 'similar' for look-alikes or 'alternative' for upgrades, and say that "
    "there is no bought-together evidence.",
    "similar": "It has no photo in the image index. Try relation 'complement' or a "
    "SearchCatalog query describing it.",
    "alternative": "No higher-priced piece shares its specific category, type and section. "
    "Try relation 'similar'.",
}


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
