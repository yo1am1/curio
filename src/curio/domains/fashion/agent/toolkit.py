"""Fashion shopping agent: the toolkit the engine (`curio.engine`) runs for fashion.

1. Plan: extract a structured search intent, merged with approved session preferences, and
   run a first catalog search.
2. Act: SearchCatalog, InspectProducts, FindRelated and PersonalPicks read the catalog and the
   recommenders; BuildOutfit plans and fills a whole outfit under a budget, streaming a step
   per slot, and SwapOutfitItem changes one of its pieces; AskShopper pauses for a quick
   reply; SaveToShortlist and RememberPreferences pause for approval unless the shopper opted
   into auto-approval for that tool.
3. Verify: prices, outfit totals and product IDs in the final answer are checked against tool
   evidence.
"""

from collections.abc import Generator, Iterable, Iterator, Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from curio.domains.fashion.agent.grounding import check_grounding
from curio.domains.fashion.agent.intent import IntentExtractor, SearchIntent
from curio.domains.fashion.agent.prompts import (
    ANSWER_PROMPT,
    REPAIR_INSTRUCTION,
    render_outfit,
    render_products,
)
from curio.domains.fashion.agent.schemas import (
    FashionChatTurn,
    FashionContext,
    FashionEffect,
    ShopperPreferences,
)
from curio.domains.fashion.agent.tools import (
    POLICIES,
    TOOLS,
    BuildOutfit,
    FindRelated,
    InspectProducts,
    PersonalPicks,
    RememberPreferences,
    SaveToShortlist,
    SearchCatalog,
    SwapOutfitItem,
)
from curio.domains.fashion.catalog import ProductCatalog
from curio.domains.fashion.models import Product
from curio.domains.fashion.navigator import CatalogNavigator, NoPersonalHistoryError
from curio.domains.fashion.outfits import (
    SECTION_AUDIENCE,
    SLOT_LABELS,
    Audience,
    FilledSlot,
    Outfit,
    OutfitError,
    OutfitPlanner,
    OutfitRequest,
    PlannedSlots,
    audience_in,
)
from curio.domains.fashion.search import ProductSearch
from curio.engine.grounding import amounts_in
from curio.engine.schemas import AgentStep, GroundingReport
from curio.engine.toolkit import ToolError, Turn

__all__ = ["FashionToolkit"]

INVALID_ACTION = (
    "Invalid action. Use SearchCatalog with a visual query, InspectProducts or SaveToShortlist "
    "with one to five positive catalog product IDs, FindRelated with one product ID and a "
    "relation, BuildOutfit with an occasion (plus an optional total budget, audience and two "
    "to five distinct slots), SwapOutfitItem with a slot of the current outfit, or AskShopper "
    "with a question and two to four options."
)
# Turn.extras keys: the outfit this turn built or changed, and the one the client sent.
OUTFIT = "outfit"
CURRENT_OUTFIT = "current_outfit"


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
        outfits: OutfitPlanner | None = None,
    ) -> None:
        self._search = search
        self._catalog = catalog
        self._top_k = top_k
        self._intent_extractor = intent_extractor
        self._navigator = navigator
        self._outfits = outfits or OutfitPlanner(search, catalog)
        self._vocab = {
            name: {value.lower(): value for value in catalog.distinct(name)}
            for name in ("section", "colour")
        }

    def key(self, item: Product) -> int:
        return item.product_id

    # --- Turn phases ----------------------------------------------------------------------

    def clean_context(self, context: FashionContext) -> FashionContext:
        preferences = context.preferences.model_copy(
            update={
                "sections": _known(context.preferences.sections, self._vocab["section"]),
                "colours": _known(context.preferences.colours, self._vocab["colour"]),
            }
        )
        outfit = context.outfit
        if outfit is not None:
            pieces = [p for p in outfit.pieces if self._catalog.get(p.product_id) is not None]
            outfit = outfit.model_copy(update={"pieces": pieces}) if pieces else None
        return context.model_copy(update={"preferences": preferences, "outfit": outfit})

    def history(self, turns: Sequence[FashionChatTurn]) -> tuple[list[BaseMessage], list[Product]]:
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

    def refresh(self, turn: Turn[Product], approved: FashionEffect | None = None) -> None:
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
        elif isinstance(action, BuildOutfit):
            return self._build_outfit(turn, action)
        elif isinstance(action, SwapOutfitItem):
            return self._swap(turn, action)
        else:  # pragma: no cover - POLICIES and TOOLS are defined together
            raise ToolError(INVALID_ACTION)
        return render_products(turn.items, turn.reasons), step

    def _build_outfit(
        self, turn: Turn[Product], action: BuildOutfit
    ) -> Generator[AgentStep, None, tuple[str, AgentStep]]:
        """Plan the slots, then fill them one by one; each stage is a streamed step."""
        request = OutfitRequest(
            occasion=action.occasion.strip(),
            budget=action.budget,
            audience=(
                action.audience or audience_in(action.occasion) or _audience(turn.intent.sections)
            ),
            slots=tuple(action.slots or ()),
            max_item_price=turn.context.preferences.max_price,
        )
        stages = self._outfits.build(request)
        while True:
            try:
                stage = next(stages)
            except StopIteration as done:
                outfit: Outfit = done.value
                break
            if isinstance(stage, PlannedSlots):
                yield _planned_step(request, stage)
            elif isinstance(stage, FilledSlot):
                yield _filled_step(stage)
        self._show_outfit(turn, outfit)
        filled = len(outfit.products)
        summary = (
            f"Outfit ready: {filled} pieces for {_euro(outfit.total)}"
            if outfit.status == "complete"
            else f"Filled {filled} of {len(outfit.slots)} slots for {_euro(outfit.total)}"
        )
        if outfit.budget is not None:
            summary += f" of a {_euro(outfit.budget)} budget"
        step = AgentStep(
            kind="tool",
            name="BuildOutfit",
            summary=summary + ".",
            status="ok" if outfit.status == "complete" else "skipped",
            detail={"occasion": outfit.occasion, "total": outfit.total, "status": outfit.status},
        )
        return render_outfit(outfit), step

    def _swap(self, turn: Turn[Product], action: SwapOutfitItem) -> tuple[str, AgentStep]:
        current: Outfit | None = turn.extras.get(OUTFIT) or turn.extras.get(CURRENT_OUTFIT)
        if current is None:
            raise ToolError("There is no outfit to change yet. Use BuildOutfit first.")
        try:
            swap = self._outfits.swap(
                current.state,
                action.slot,
                action.mode,
                action.refine,
                max_item_price=turn.context.preferences.max_price,
            )
        except OutfitError as error:
            raise ToolError(str(error)) from error
        turn.remember([swap.old])
        self._show_outfit(turn, swap.outfit)
        label = SLOT_LABELS[action.slot].lower()
        detail = {"slot": action.slot, "mode": action.mode, "old_id": swap.old.product_id}
        if swap.new is None:
            kind = "cheaper " if action.mode == "cheaper" else "other "
            summary = f"Found no {kind}{label} within the budget; kept the {swap.old.name}."
            step = AgentStep(
                kind="tool", name="SwapOutfitItem", summary=summary, status="skipped", detail=detail
            )
            return f"{summary} The outfit is unchanged.\n{render_outfit(swap.outfit)}", step
        summary = (
            f"Swapped {label}: {swap.old.name} → {swap.new.name} · {_euro(swap.new.price)}; "
            f"total {_euro(swap.outfit.total)}."
        )
        step = AgentStep(
            kind="tool",
            name="SwapOutfitItem",
            summary=summary,
            detail=detail | {"new_id": swap.new.product_id},
        )
        replaced = (
            f"Replaced the {swap.old.name} [id {swap.old.product_id}] "
            f"({_euro(swap.old.price)}) with the {swap.new.name}.\n"
        )
        return replaced + render_outfit(swap.outfit), step

    def _show_outfit(self, turn: Turn[Product], outfit: Outfit) -> None:
        turn.extras[OUTFIT] = outfit
        # Carried reasons came from the client: display-only, never evidence for the model.
        turn.show(
            outfit.products,
            {
                s.product.product_id: [s.reason]
                for s in outfit.slots
                if s.product and s.reason and not s.carried
            },
        )

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
    ) -> tuple[FashionEffect, list[Product], str]:
        """Validate a state change against the catalog; the client applies the effect."""
        if isinstance(action, SaveToShortlist):
            products = self._catalog.get_many(dict.fromkeys(action.product_ids))
            if not products:
                raise ToolError("None of those product IDs are in the catalog.")
            names = ", ".join(p.name for p in products)
            verb = "Would save" if dry_run else "Saved"
            return (
                FashionEffect(kind="shortlist", product_ids=[p.product_id for p in products]),
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
                FashionEffect(kind="preferences", preferences=preferences),
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

    def fallback(self, items: Sequence[Product], extras: Mapping[str, Any] | None = None) -> str:
        if not items:
            return (
                "I couldn't confirm matching pieces. Try another description or adjust your "
                "filters."
            )
        return "Here are the pieces I could confirm:\n" + "\n".join(
            f"- {p.name} · {p.colour} · €{p.price:.2f}" for p in items
        )

    def reply_fields(self, turn: Turn[Product]) -> dict[str, Any]:
        return {"domain": "fashion", "products": turn.items, "outfit": turn.extras.get(OUTFIT)}

    def suggestions(self, turn: Turn[Product]) -> list[str]:
        """Next-step chips; each one exercises a tool the agent can actually use."""
        if (outfit := turn.extras.get(OUTFIT)) is not None:
            return _outfit_suggestions(outfit, turn)
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
        if context.outfit is not None and (current := self._outfits.restore(context.outfit)):
            turn.extras[CURRENT_OUTFIT] = current
            turn.remember(current.products)
            parts.append("current outfit (SwapOutfitItem changes it): " + _outfit_line(current))
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
        outfits = [turn.extras[key] for key in (OUTFIT, CURRENT_OUTFIT) if key in turn.extras]
        return [
            *amounts_in(turn.message),
            *(
                value
                for value in (intent.min_price, intent.max_price, preferences.max_price)
                if value is not None
            ),
            # An outfit's total, budget, what is left and what a missing slot would cost.
            *(amount for outfit in outfits for amount in outfit.amounts()),
        ]


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


def _euro(amount: float) -> str:
    return f"€{amount:.2f}"


def _audience(sections: Sequence[str]) -> Audience | None:
    """The audience the shopper's section filters (or remembered sections) point to."""
    audiences = {SECTION_AUDIENCE[s] for s in sections if s in SECTION_AUDIENCE}
    return audiences.pop() if len(audiences) == 1 else None


def _planned_step(request: OutfitRequest, planned: PlannedSlots) -> AgentStep:
    names = [spec.label.lower() for spec in planned.slots]
    summary = f"Planned {len(names)} slots for “{request.occasion}”: {', '.join(names)}"
    if planned.audience:
        source = " (from the best match)" if planned.audience_source == "best match" else ""
        summary += f" · for {planned.audience}{source}"
    if request.budget is not None:
        summary += f" · budget {_euro(request.budget)}"
    return AgentStep(
        kind="plan",
        name="Plan outfit",
        summary=summary + ".",
        detail={
            "slots": [{"slot": spec.slot, "query": spec.query} for spec in planned.slots],
            "audience": planned.audience,
            "style": planned.style,
            "season": planned.season,
        },
    )


def _filled_step(filled: FilledSlot) -> AgentStep:
    slot = filled.slot
    label = slot.label.lower()
    if slot.product is None:
        return AgentStep(
            kind="tool",
            name="Fill slot",
            summary=f"Could not fill {label}: {slot.issue}",
            status="skipped",
            detail={"slot": slot.slot},
        )
    product = slot.product
    return AgentStep(
        kind="tool",
        name="Fill slot",
        summary=f"Filled {label}: {product.name} · {_euro(product.price)}. {slot.reason}",
        detail={"slot": slot.slot, "product_id": product.product_id},
    )


def _outfit_line(outfit: Outfit) -> str:
    pieces = "; ".join(
        f"{s.slot} [id {s.product.product_id}] {s.product.name} {_euro(s.product.price)}"
        for s in outfit.slots
        if s.product is not None
    )
    budget = f" of {_euro(outfit.budget)}" if outfit.budget is not None else ""
    return f"for “{outfit.occasion}”, total {_euro(outfit.total)}{budget}: {pieces}"


def _outfit_suggestions(outfit: Outfit, turn: Turn[Product]) -> list[str]:
    filled = [s for s in outfit.slots if s.product is not None]
    if not filled:
        return ["Raise the budget", "Show me something under €30"]
    priciest = max(filled, key=lambda s: (s.product.price if s.product else 0, s.slot))
    options = [f"Cheaper {priciest.label.lower()}", f"Another {filled[0].label.lower()}"]
    saved = set(turn.context.shortlist) | {
        pid for effect in turn.applied for pid in effect.product_ids
    }
    if any(s.product.product_id not in saved for s in filled if s.product):
        options.append("Save the whole outfit")
    return options


def _search_step(intent: SearchIntent, products: Sequence[Product]) -> AgentStep:
    return AgentStep(
        kind="tool",
        name="SearchCatalog",
        summary=f"Found {len(products)} matches for “{intent.query}”.",
        status="ok" if products else "skipped",
        detail={"query": intent.query, "filters": intent.model_dump(exclude_defaults=True)},
    )
