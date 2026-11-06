"""Evening agent: the toolkit the engine (`curio.engine`) runs for "Plan my evening".

1. Plan: no intent-extraction call; the plan step shows which remembered tastes apply (from
   every other domain: reading tastes, cocktail tastes, a per-piece price limit).
2. Act: PlanEvening runs the deterministic `EveningPlanner` and streams a step per part
   (theme, outfit, book, drink); SwapEveningPart changes one part of the plan the client
   holds; AskShopper asks for an occasion when the request is vague; SaveEvening proposes
   saving the parts to each domain's own list, and waits for approval (narrowable).
3. Verify: euro amounts and product IDs, titles, book IDs and ratings, drink names, drink IDs
   and recipe amounts must all come from this turn's plans or the plan the client holds
   (re-read from the catalogs and re-paired), reusing the fashion, book and pairing checks.
"""

from collections.abc import Generator, Iterator, Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from curio.domains.books.catalog import BookCatalog
from curio.domains.books.models import Book
from curio.domains.books.taxonomy import MOODS
from curio.domains.cocktails.agent.prompts import alcohol_label
from curio.domains.cocktails.catalog import CocktailCatalog
from curio.domains.cocktails.models import Cocktail
from curio.domains.evening.agent.grounding import EveningGroundingCheck, ordinary_names
from curio.domains.evening.agent.prompts import (
    EVENING_ANSWER_PROMPT,
    EVENING_REPAIR_INSTRUCTION,
    product_line,
    render_plan,
)
from curio.domains.evening.agent.schemas import EveningChatTurn, EveningContext, EveningEffect
from curio.domains.evening.agent.tools import (
    EVENING_POLICIES,
    EVENING_TOOLS,
    PlanEvening,
    SaveEvening,
    SwapEveningPart,
)
from curio.domains.evening.models import EveningAudience, EveningPlan, EveningRequest, EveningTastes
from curio.domains.evening.planner import EveningError, EveningPlanner
from curio.domains.fashion.catalog import ProductCatalog
from curio.domains.fashion.models import Product
from curio.domains.fashion.outfits import SECTION_AUDIENCE, SLOT_LABELS
from curio.engine.grounding import amounts_in
from curio.engine.schemas import AgentStep, GroundingReport
from curio.engine.toolkit import ToolError, Turn

__all__ = ["EveningToolkit"]

type EveningItem = Product | Book | Cocktail

INVALID_ACTION = (
    "Invalid action. Use PlanEvening with an occasion (plus an optional outfit budget, "
    "audience women or men, mood and alcohol_free); SwapEveningPart with part outfit and a "
    "slot, part book, or part drink (mode other or alcohol_free); SaveEvening with the plan's "
    "product, book and drink IDs; or AskShopper with a question and two to four options."
)
NO_PLAN = "There is no evening plan to change yet. Use PlanEvening first."
STARTERS = ("Rainy Sunday in", "Date night under €100", "Book club, alcohol-free")
SAVE_EVENING = "Save the evening"
# Turn.extras keys: the plan this turn made or changed, the plan the client sent, and the
# drinks either holds (the only drinks an answer may name).
EVENING = "evening"
CURRENT_EVENING = "current_evening"
PLAN_DRINKS = "plan_drinks"
PLAN_TEXT = "plan_text"
# Item keys: books and drinks get their own ranges so their ids never meet product ids.
BOOK_KEY = 10**12
DRINK_KEY = 2 * 10**12


class EveningToolkit:
    tools = EVENING_TOOLS
    policies = EVENING_POLICIES
    invalid_action = INVALID_ACTION
    repair_instruction = EVENING_REPAIR_INSTRUCTION
    grounding_subject = "prices, IDs, titles, ratings and drinks"

    def __init__(
        self,
        planner: EveningPlanner,
        *,
        books: BookCatalog,
        cocktails: CocktailCatalog,
        products: ProductCatalog | None = None,
    ) -> None:
        self._planner = planner
        self._books = books
        self._cocktails = cocktails
        self._products = products
        drink_names = [c.name for c in cocktails]
        self._grounding = EveningGroundingCheck(
            titles=books.titles(),
            other_names=[*books.authors(), *books.genres(), *MOODS],
            drink_names=drink_names,
            ingredients=cocktails.ingredients(),
            glass_names={c.glass for c in cocktails},
            ordinary=ordinary_names(drink_names, (f"{b.title} {b.description}" for b in books)),
        )

    def key(self, item: EveningItem) -> int:
        if isinstance(item, Book):
            return BOOK_KEY + item.book_id
        if isinstance(item, Cocktail):
            return DRINK_KEY + item.drink_id
        return item.product_id

    # --- Turn phases ----------------------------------------------------------------------

    def clean_context(self, context: EveningContext) -> EveningContext:
        return context

    def history(
        self, turns: Sequence[EveningChatTurn]
    ) -> tuple[list[BaseMessage], list[EveningItem]]:
        messages: list[BaseMessage] = []
        referenced: list[EveningItem] = []
        for turn in turns:
            if turn.role == "user":
                messages.append(HumanMessage(turn.content))
                continue
            products = self._products.get_many(turn.product_ids) if self._products else []
            books = self._books.get_many(turn.book_ids)
            referenced.extend([*products, *books])
            lines = [product_line(p) for p in products]
            lines += [f"book #{b.book_id} {b.title} by {b.author}" for b in books]
            # Earlier drinks are context, not evidence: the plan must be read again.
            drinks = self._cocktails.get_many(turn.drink_ids)
            lines += [f"drink #{c.drink_id} {c.name} (shown earlier)" for c in drinks]
            context = "\nReferenced: " + "; ".join(lines) if lines else ""
            messages.append(AIMessage(turn.content + context))
        return messages, referenced

    def plan(self, turn: Turn[EveningItem], history: Sequence[BaseMessage]) -> Iterator[AgentStep]:
        context: EveningContext = turn.context
        applied = _describe_tastes(context)
        summary = (
            f"Applying remembered tastes to every part: {applied}."
            if applied
            else "No remembered tastes to apply."
        )
        served = self._planner.parts
        if len(served) < 3:
            missing = [part for part in ("outfit", "book", "drink") if part not in served]
            summary += f" This server cannot plan the {' or '.join(missing)}."
        yield AgentStep(
            kind="plan",
            name="Understand request",
            summary=summary,
            detail={"parts": served, "has_plan": context.evening is not None},
        )

    def refresh(self, turn: Turn[EveningItem], approved: EveningEffect | None = None) -> None:
        """Nothing to do: every evening tool reads remembered tastes from `turn.context`."""

    def prompt(
        self, turn: Turn[EveningItem], message: str, history: Sequence[BaseMessage]
    ) -> list[BaseMessage]:
        return EVENING_ANSWER_PROMPT.invoke(
            {"message": message, "history": list(history), "session": self._session(turn)}
        ).to_messages()

    # --- Tools ----------------------------------------------------------------------------

    def read(
        self, turn: Turn[EveningItem], action: BaseModel
    ) -> Generator[AgentStep, None, tuple[str, AgentStep]] | tuple[str, AgentStep]:
        if isinstance(action, PlanEvening):
            return self._plan_evening(turn, action)
        if isinstance(action, SwapEveningPart):
            return self._swap(turn, action)
        raise ToolError(INVALID_ACTION)  # pragma: no cover - the policies name every READ

    def _plan_evening(
        self, turn: Turn[EveningItem], action: PlanEvening
    ) -> Generator[AgentStep, None, tuple[str, AgentStep]]:
        context: EveningContext = turn.context
        tastes = _tastes(context)
        try:
            request = EveningRequest(
                occasion=action.occasion,
                budget=action.budget,
                audience=action.audience or _audience(context),
                mood=action.mood,
                alcohol_free=action.alcohol_free,
                **tastes.model_dump(),
            )
            plan = self._planner.plan(request)
        except ValidationError as error:
            raise ToolError(_invalid(error)) from error
        yield AgentStep(
            kind="plan",
            name="Evening theme",
            summary=f"{plan.theme.label}: {plan.theme.evidence}, so {plan.theme.feeling}.",
            detail={"theme": plan.theme.id},
        )
        yield from _part_steps(plan)
        self._show(turn, plan)
        filled = [part for part in ("outfit", "book", "drink") if getattr(plan, part)]
        step = AgentStep(
            kind="tool",
            name="PlanEvening",
            summary=f"Planned a {plan.theme.label.lower()} evening: {_and(filled) or 'nothing'}"
            + (f"; missing {_and([m.part for m in plan.missing])}." if plan.missing else "."),
            status="ok" if plan.status == "complete" else "skipped",
            detail={"theme": plan.theme.id, "status": plan.status, "why": plan.why},
        )
        return render_plan(plan), step

    def _swap(self, turn: Turn[EveningItem], action: SwapEveningPart) -> tuple[str, AgentStep]:
        current: EveningPlan | None = turn.extras.get(EVENING) or turn.extras.get(CURRENT_EVENING)
        if current is None:
            raise ToolError(NO_PLAN)
        try:
            plan = self._planner.swap(
                current.state,
                action.part,
                slot=action.slot,
                mode=action.mode,
                refine=action.refine,
                tastes=_tastes(turn.context),
            )
        except EveningError as error:
            raise ToolError(str(error)) from error
        except ValidationError as error:
            raise ToolError(_invalid(error)) from error
        # What was swapped out stays citable ("instead of …").
        turn.remember(_items(current))
        self._show(turn, plan)
        label = action.part if action.slot is None else SLOT_LABELS[action.slot].lower()
        step = AgentStep(
            kind="tool",
            name="SwapEveningPart",
            summary=plan.change or f"Changed the {label}.",
            status="ok" if plan.change and "kept" not in plan.change else "skipped",
            detail=action.model_dump(exclude_defaults=True),
        )
        return render_plan(plan), step

    def _show(self, turn: Turn[EveningItem], plan: EveningPlan) -> None:
        turn.extras[EVENING] = plan
        reasons: dict[int, list[str]] = {}
        if plan.outfit is not None:
            for slot in plan.outfit.outfit.slots:
                if slot.product is not None and slot.reason and not slot.carried:
                    reasons[slot.product.product_id] = [slot.reason]
        if plan.book is not None:
            reasons[self.key(plan.book.book)] = [plan.book.reason]
        if plan.drink is not None:
            reasons[self.key(plan.drink.cocktail)] = list(plan.drink.reasons)
        turn.show(_items(plan), reasons)
        self._remember_drinks(turn, plan)

    def _remember_drinks(self, turn: Turn[EveningItem], plan: EveningPlan) -> None:
        drinks: dict[int, Cocktail] = turn.extras.setdefault(PLAN_DRINKS, {})
        text: list[str] = turn.extras.setdefault(PLAN_TEXT, [])
        if plan.drink is not None:
            drinks[plan.drink.cocktail.drink_id] = plan.drink.cocktail
            text.extend(plan.drink.reasons)
        if plan.book is not None:
            text.append(plan.book.reason)
        # The plan's own words may be repeated: "a dark and stormy night" is not the drink.
        text += [plan.occasion, plan.theme.feeling, plan.why, *plan.notes]

    def apply(
        self, action: BaseModel, *, dry_run: bool = False
    ) -> tuple[EveningEffect, list[EveningItem], str]:
        """Validate a proposed save against the catalogs; the client applies the effect."""
        if not isinstance(action, SaveEvening):
            raise ToolError(INVALID_ACTION)
        products = (
            self._products.get_many(dict.fromkeys(action.product_ids)) if self._products else []
        )
        books = self._books.get_many(dict.fromkeys(action.book_ids))
        drinks = self._cocktails.get_many(dict.fromkeys(action.drink_ids))
        if not (products or books or drinks):
            raise ToolError("None of those pieces, books or drinks are in the catalogs.")
        parts = []
        if products:
            noun = "piece" if len(products) == 1 else "pieces"
            parts.append(f"{len(products)} {noun} to the shortlist")
        if books:
            parts.append(f"{_and([b.title for b in books])} to the reading list")
        if drinks:
            parts.append(f"{_and([c.name for c in drinks])} to the saved cocktails")
        verb = "Would save" if dry_run else "Saved"
        effect = EveningEffect(
            product_ids=[p.product_id for p in products],
            book_ids=[b.book_id for b in books],
            drink_ids=[c.drink_id for c in drinks],
        )
        return effect, [*products, *books, *drinks], f"{verb} {_and(parts)}."

    def proposed(self, args: Mapping[str, Any]) -> list[EveningItem]:
        products = self._products.get_many(args.get("product_ids", [])) if self._products else []
        books = self._books.get_many(args.get("book_ids", []))
        return [*products, *books, *self._cocktails.get_many(args.get("drink_ids", []))]

    def approval_text(self, action: BaseModel) -> str:
        return (
            "Shall I save this evening: the pieces to your shortlist, the book to your reading "
            "list and the drink to your saved cocktails? Approve, untick parts or decline."
        )

    def pending_display(self, items: list[EveningItem]) -> dict[str, Any]:
        return _split(items)

    # --- Verification and reply -----------------------------------------------------------

    def check(self, turn: Turn[EveningItem], text: str) -> GroundingReport:
        evidence = _split(turn.evidence.values())
        plans = [turn.extras[k] for k in (EVENING, CURRENT_EVENING) if k in turn.extras]
        context: EveningContext = turn.context
        amounts = [amount for plan in plans for amount in plan.amounts()]
        amounts += [plan.state.budget for plan in plans if plan.state.budget is not None]
        if context.preferences.max_price is not None:
            amounts.append(context.preferences.max_price)
        return self._grounding.check(
            text,
            products=evidence["products"],
            books=evidence["books"],
            drinks=list(turn.extras.get(PLAN_DRINKS, {}).values()),
            amounts=[*amounts, *amounts_in(turn.message)],
            typed=turn.message,
            cited=turn.extras.get(PLAN_TEXT, []),
        )

    def fallback(
        self, items: Sequence[EveningItem], extras: Mapping[str, Any] | None = None
    ) -> str:
        extras = extras or {}
        plan: EveningPlan | None = extras.get(EVENING) or extras.get(CURRENT_EVENING)
        if plan is None:
            if not items:
                return (
                    "I couldn't plan an evening from that. Tell me the occasion, for example "
                    "“rainy Sunday in” or “date night under €100”."
                )
            return "Here is what I could confirm:\n" + "\n".join(
                f"- {_item_line(item)}" for item in items
            )
        lines = [f"A {plan.theme.label.lower()} evening, from the catalogs:"]
        if plan.outfit is not None:
            outfit = plan.outfit.outfit
            pieces = [s for s in outfit.slots if s.product is not None]
            lines += [f"- {s.label}: {_item_line(s.product)}" for s in pieces if s.product]
            budget = f" of €{outfit.budget:.2f}" if outfit.budget is not None else ""
            lines.append(f"- Outfit total: €{outfit.total:.2f}{budget}")
        if plan.book is not None:
            lines.append(f"- Book: {_item_line(plan.book.book)}")
        if plan.drink is not None:
            lines.append(f"- Drink: {_item_line(plan.drink.cocktail)}")
        lines += [f"- Missing {m.part}: {m.reason}" for m in plan.missing]
        text = "\n".join(lines) + f"\n\nWhy it works: {plan.why}"
        if plan.drink is not None:
            text += f"\n\n{plan.drink.note}"
        return text

    def reply_fields(self, turn: Turn[EveningItem]) -> dict[str, Any]:
        return {"domain": "evening", **_split(turn.items), "evening": turn.extras.get(EVENING)}

    def suggestions(self, turn: Turn[EveningItem]) -> list[str]:
        plan: EveningPlan | None = turn.extras.get(EVENING)
        if plan is None:
            return list(STARTERS)
        options = []
        if plan.book is not None:
            options.append("Another book")
        if plan.outfit is not None:
            pieces = [s for s in plan.outfit.outfit.slots if s.product is not None]
            if pieces:
                priciest = max(pieces, key=lambda s: (s.product.price if s.product else 0, s.slot))
                options.append(f"Cheaper {priciest.label.lower()}")
        if plan.drink is not None:
            alcohol_free = plan.drink.cocktail.alcohol_free
            options.append("Another drink" if alcohol_free else "Make the drink alcohol-free")
        if plan.status != "empty":
            options.append(SAVE_EVENING)
        return options[:4]

    # --- Helpers --------------------------------------------------------------------------

    def _session(self, turn: Turn[EveningItem]) -> str:
        context: EveningContext = turn.context
        parts = []
        applied = _describe_tastes(context)
        if applied:
            parts.append(f"remembered tastes (applied automatically): {applied}")
        served = self._planner.parts
        if len(served) < 3:
            parts.append(f"this server plans only the {_and(served) or 'nothing'}")
        if context.evening is not None:
            current = self._planner.restore(context.evening, _tastes(context))
            turn.extras[CURRENT_EVENING] = current
            turn.remember(_items(current))
            self._remember_drinks(turn, current)
            parts.append(
                "current evening plan (SwapEveningPart and SaveEvening use it): "
                + _plan_line(current)
            )
        else:
            parts.append("no evening is planned yet")
        return ". ".join(parts) + "."


def _invalid(error: ValidationError) -> str:
    """A tool message for values the planner cannot use, instead of a crashed turn."""
    problems = "; ".join(
        f"{'.'.join(map(str, e['loc'])) or 'value'}: {e['msg']}" for e in error.errors()[:3]
    )
    return f"The evening could not be planned with these values ({problems}). Change them."


def _tastes(context: EveningContext) -> EveningTastes:
    return EveningTastes(
        book_preferences=context.book_preferences,
        cocktail_preferences=context.cocktail_preferences,
        max_item_price=context.preferences.max_price,
    )


def _audience(context: EveningContext) -> EveningAudience | None:
    """The audience remembered shopping sections point to, when they point to one."""
    found = {SECTION_AUDIENCE.get(s) for s in context.preferences.sections} - {None}
    audience = found.pop() if len(found) == 1 else None
    return audience if audience in ("women", "men") else None  # type: ignore[return-value]


def _describe_tastes(context: EveningContext) -> str:
    parts = []
    books = context.book_preferences
    if books.avoided_genres:
        parts.append("no " + ", ".join(books.avoided_genres))
    if books.avoided_authors:
        parts.append("no books by " + ", ".join(books.avoided_authors))
    if books.liked_genres:
        parts.append("likes " + ", ".join(books.liked_genres))
    drinks = context.cocktail_preferences
    if drinks.alcohol_free:
        parts.append("alcohol-free drinks")
    if drinks.avoided_ingredients:
        parts.append("avoiding " + ", ".join(drinks.avoided_ingredients))
    if context.preferences.max_price is not None:
        parts.append(f"pieces up to €{context.preferences.max_price:g}")
    return "; ".join(parts)


def _items(plan: EveningPlan) -> list[EveningItem]:
    items: list[EveningItem] = []
    if plan.outfit is not None:
        items.extend(plan.outfit.outfit.products)
    if plan.book is not None:
        items.append(plan.book.book)
    if plan.drink is not None:
        items.append(plan.drink.cocktail)
    return items


def _split(items) -> dict[str, list]:
    items = list(items)
    return {
        "products": [i for i in items if isinstance(i, Product)],
        "books": [i for i in items if isinstance(i, Book)][:5],
        "cocktails": [i for i in items if isinstance(i, Cocktail)][:5],
    }


def _item_line(item: EveningItem) -> str:
    if isinstance(item, Book):
        return f"{item.title} by {item.author} · ★ {item.average_rating:.2f}"
    if isinstance(item, Cocktail):
        return f"{item.name} · {alcohol_label(item)}"
    return f"{item.name} · {item.colour} · €{item.price:.2f}"


def _plan_line(plan: EveningPlan) -> str:
    parts = [f"theme {plan.theme.label}"]
    if plan.outfit is not None:
        outfit = plan.outfit.outfit
        pieces = "; ".join(
            f"{s.slot} product #{s.product.product_id} {s.product.name} €{s.product.price:.2f}"
            for s in outfit.slots
            if s.product is not None
        )
        parts.append(f"outfit total €{outfit.total:.2f}: {pieces}")
    if plan.book is not None:
        b = plan.book.book
        parts.append(f"book #{b.book_id} {b.title} ★ {b.average_rating:.2f}")
    if plan.drink is not None:
        c = plan.drink.cocktail
        parts.append(f"drink #{c.drink_id} {c.name} ({alcohol_label(c)})")
    return " | ".join(parts)


def _part_steps(plan: EveningPlan) -> Iterator[AgentStep]:
    """One step per part, as the plan reports it."""
    if plan.outfit is not None:
        outfit = plan.outfit.outfit
        names = ", ".join(p.name for p in outfit.products)
        budget = f" of €{outfit.budget:.2f}" if outfit.budget is not None else ""
        yield AgentStep(
            kind="tool",
            name="Evening outfit",
            summary=f"{len(outfit.products)} pieces for €{outfit.total:.2f}{budget}: {names}.",
            status="ok" if outfit.status == "complete" else "skipped",
            detail={"product_ids": [p.product_id for p in outfit.products]},
        )
    if plan.book is not None:
        b = plan.book.book
        yield AgentStep(
            kind="tool",
            name="Evening book",
            summary=f"{b.title} by {b.author} · ★ {b.average_rating:.2f}. {plan.book.reason}",
            detail={"book_id": b.book_id},
        )
    if plan.drink is not None:
        c = plan.drink.cocktail
        yield AgentStep(
            kind="tool",
            name="Evening drink",
            summary=f"{c.name} ({alcohol_label(c)}), paired with the book: {plan.drink.reason}",
            detail={"drink_id": c.drink_id, "alcohol_free": c.alcohol_free},
        )
    for gap in plan.missing:
        yield AgentStep(
            kind="tool",
            name=f"Evening {gap.part}",
            summary=gap.reason,
            status="skipped",
            detail={"missing": gap.part},
        )


def _and(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
