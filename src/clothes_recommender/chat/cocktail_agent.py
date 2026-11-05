"""Cocktail agent: the shared agent loop (`chat.engine`) with recipe tools.

1. Plan: no intent-extraction call; the plan step shows which remembered tastes apply.
2. Act: SearchCocktails, InspectCocktails, SimilarCocktails and WhatCanIMake read the offline
   recipe catalog through `CocktailRecommender`. Approved tastes fill missing filters on every
   call: alcohol-free turns an "any" alcohol filter into "non_alcoholic", and avoided
   ingredients are always excluded (with the recommender's broad word matching, so "egg"
   also excludes "Egg white"). AskShopper, SaveCocktails and RememberTastes pause the turn
   like their fashion counterparts.
3. Verify: drink IDs and catalog drink names in the answer must come from tool evidence
   (results, history cards or the shortlist) and every amount must appear in an evidence
   recipe.
"""

import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from clothes_recommender.chat.cocktail_tools import (
    COCKTAIL_POLICIES,
    COCKTAIL_TOOLS,
    InspectCocktails,
    RememberTastes,
    SaveCocktails,
    SearchCocktails,
    SimilarCocktails,
    WhatCanIMake,
)
from clothes_recommender.chat.engine import AgentEngine, ToolError, Turn
from clothes_recommender.chat.guardrails import ActionSigner, CocktailGroundingCheck
from clothes_recommender.chat.prompts import (
    COCKTAIL_ANSWER_PROMPT,
    COCKTAIL_REPAIR_INSTRUCTION,
    alcohol_label,
    render_cocktails,
)
from clothes_recommender.chat.schemas import (
    AgentStep,
    AppliedEffect,
    ChatTurn,
    GroundingReport,
    TurnContext,
)
from clothes_recommender.cocktails.catalog import CocktailCatalog, ingredient_key
from clothes_recommender.cocktails.domain import (
    AlcoholFilter,
    Cocktail,
    CocktailHit,
    CocktailPreferences,
    CocktailQuery,
    PantryMatch,
)
from clothes_recommender.cocktails.recommend import (
    PANTRY_ALIASES,
    CocktailRecommender,
    terms,
    tokens,
)

__all__ = ["CocktailAssistant", "CocktailToolkit"]

INVALID_ACTION = (
    "Invalid action. Use SearchCocktails with a query, ingredients, a category or an alcohol "
    "filter; InspectCocktails or SaveCocktails with one to five positive drink IDs; "
    "SimilarCocktails with one drink ID; WhatCanIMake with one to fifteen ingredients and "
    "max_missing from 0 to 3; RememberTastes with at least one taste; or AskShopper with a "
    "question and two to four options."
)
# Similar and pantry results are filtered for avoided ingredients after ranking, so fetch
# enough candidates that the filter rarely leaves fewer than top_k.
MAX_CANDIDATES = 50
EXAMPLE_PANTRY = "What can I make with lime and mint?"


class CocktailToolkit:
    tools = COCKTAIL_TOOLS
    policies = COCKTAIL_POLICIES
    invalid_action = INVALID_ACTION
    repair_instruction = COCKTAIL_REPAIR_INSTRUCTION
    grounding_subject = "drinks, IDs and amounts"

    def __init__(
        self, catalog: CocktailCatalog, recommender: CocktailRecommender, top_k: int = 5
    ) -> None:
        self._catalog = catalog
        self._recommender = recommender
        self._top_k = top_k
        self._categories = {c.casefold(): c for c in catalog.categories()}
        self._ingredients = [_IngredientWords(name) for name in catalog.ingredients()]
        self._grounding = CocktailGroundingCheck(
            (c.name for c in catalog),
            catalog.ingredients(),
            glass_names={c.glass for c in catalog},
        )

    def key(self, item: Cocktail) -> int:
        return item.drink_id

    # --- Turn phases ----------------------------------------------------------------------

    def clean_context(self, context: TurnContext) -> TurnContext:
        tastes = context.cocktail_preferences
        cleaned = tastes.model_copy(
            update={
                "liked_ingredients": self._known(tastes.liked_ingredients),
                "avoided_ingredients": self._known(tastes.avoided_ingredients),
            }
        )
        return context.model_copy(update={"cocktail_preferences": cleaned})

    def history(self, turns: Sequence[ChatTurn]) -> tuple[list[BaseMessage], list[Cocktail]]:
        messages: list[BaseMessage] = []
        referenced: list[Cocktail] = []
        for turn in turns:
            if turn.role == "user":
                messages.append(HumanMessage(turn.content))
                continue
            cocktails = self._catalog.get_many(turn.drink_ids)
            referenced.extend(cocktails)
            context = "\nReferenced drinks:\n" + render_cocktails(cocktails) if cocktails else ""
            messages.append(AIMessage(turn.content + context))
        return messages, referenced

    def plan(self, turn: Turn[Cocktail], history: Sequence[BaseMessage]) -> Iterator[AgentStep]:
        tastes = turn.context.cocktail_preferences
        from_memory = _memory_filters(tastes)
        filters = _describe_tastes(tastes.model_copy(update={"liked_ingredients": []}))
        summary = (
            f"Applying remembered tastes to every search: {filters}."
            if filters
            else "No remembered filters to apply."
        )
        if tastes.liked_ingredients:
            summary += " Likes " + ", ".join(tastes.liked_ingredients) + "."
        yield AgentStep(
            kind="plan",
            name="Understand request",
            summary=summary,
            detail=tastes.model_dump(exclude_defaults=True) | {"from_memory": from_memory},
        )

    def refresh(self, turn: Turn[Cocktail], approved: AppliedEffect | None = None) -> None:
        """Nothing to do: every cocktail tool reads remembered tastes from `turn.context`."""

    def prompt(
        self, turn: Turn[Cocktail], message: str, history: Sequence[BaseMessage]
    ) -> list[BaseMessage]:
        return COCKTAIL_ANSWER_PROMPT.invoke(
            {
                "message": message,
                "history": list(history),
                "cocktails": render_cocktails(turn.items),
                "session": self._session(turn),
            }
        ).to_messages()

    # --- Tools ----------------------------------------------------------------------------

    def read(self, turn: Turn[Cocktail], action: BaseModel) -> tuple[str, AgentStep]:
        tastes = turn.context.cocktail_preferences
        if isinstance(action, SearchCocktails):
            step = self._search(turn, action, tastes)
        elif isinstance(action, InspectCocktails):
            turn.show(self._catalog.get_many(dict.fromkeys(action.drink_ids)))
            step = AgentStep(
                kind="tool",
                name="InspectCocktails",
                summary=f"Read the full recipes of {len(turn.items)} drinks.",
                detail={"drink_ids": [c.drink_id for c in turn.items]},
            )
            return render_cocktails(turn.items, method=True), step
        elif isinstance(action, SimilarCocktails):
            step = self._similar(turn, action, tastes)
        elif isinstance(action, WhatCanIMake):
            step = self._pantry(turn, action, tastes)
        else:  # pragma: no cover - COCKTAIL_POLICIES and COCKTAIL_TOOLS are defined together
            raise ToolError(INVALID_ACTION)
        return render_cocktails(turn.items, turn.reasons), step

    def _search(
        self, turn: Turn[Cocktail], action: SearchCocktails, tastes: CocktailPreferences
    ) -> AgentStep:
        category = None
        if action.category and action.category.strip():
            category = self._categories.get(action.category.strip().casefold())
            if category is None:
                raise ToolError(
                    f"Unknown category {action.category!r}. Known categories: "
                    + ", ".join(self._categories.values())
                    + "."
                )
        alcohol, from_memory = _alcohol(action.alcohol, tastes)
        query = CocktailQuery(
            query=action.query.strip(),
            ingredients=action.ingredients,
            alcohol=alcohol,
            category=category,
            exclude_ingredients=tastes.avoided_ingredients,
            top_k=self._top_k,
        )
        hits = self._recommender.search(query)
        _show_hits(turn, hits)
        subject = f"for “{query.query}”" if query.query else "matching the filters"
        return AgentStep(
            kind="tool",
            name="SearchCocktails",
            summary=f"Found {len(hits)} drinks {subject}.",
            status="ok" if hits else "skipped",
            detail=query.model_dump(exclude_defaults=True) | {"from_memory": from_memory},
        )

    def _similar(
        self, turn: Turn[Cocktail], action: SimilarCocktails, tastes: CocktailPreferences
    ) -> AgentStep:
        cocktail = self._catalog.get(action.drink_id)
        if cocktail is None:
            raise ToolError(f"Drink {action.drink_id} is not in the catalog.")
        alcohol, from_memory = _alcohol(action.alcohol, tastes)
        hits = self._recommender.similar(action.drink_id, top_k=MAX_CANDIDATES, alcohol=alcohol)
        hits = [h for h in hits if not self._avoided(h.cocktail, tastes)][: self._top_k]
        _show_hits(turn, hits)
        return AgentStep(
            kind="tool",
            name="SimilarCocktails",
            summary=f"Found {len(hits)} drinks similar to {cocktail.name}.",
            status="ok" if hits else "skipped",
            detail={"drink_id": action.drink_id, "alcohol": alcohol, "from_memory": from_memory},
        )

    def _pantry(
        self, turn: Turn[Cocktail], action: WhatCanIMake, tastes: CocktailPreferences
    ) -> AgentStep:
        alcohol, from_memory = _alcohol(action.alcohol, tastes)
        ingredients = self._pantry_terms(action.ingredients)
        matches = self._recommender.pantry(
            ingredients,
            alcohol=alcohol,
            max_missing=action.max_missing,
            top_k=MAX_CANDIDATES,
        )
        matches = [m for m in matches if not self._avoided(m.cocktail, tastes)][: self._top_k]
        turn.show((m.cocktail for m in matches), {m.cocktail.drink_id: _have(m) for m in matches})
        return AgentStep(
            kind="tool",
            name="WhatCanIMake",
            summary=(
                f"Found {len(matches)} drinks to make from {len(action.ingredients)} "
                f"ingredients (at most {action.max_missing} missing)."
            ),
            status="ok" if matches else "skipped",
            detail={
                "ingredients": ingredients,
                "alcohol": alcohol,
                "max_missing": action.max_missing,
                "from_memory": from_memory,
            },
        )

    def apply(
        self, action: BaseModel, *, dry_run: bool = False
    ) -> tuple[AppliedEffect, list[Cocktail], str]:
        """Validate a state change against the catalog; the client applies the effect."""
        if isinstance(action, SaveCocktails):
            cocktails = self._catalog.get_many(dict.fromkeys(action.drink_ids))
            if not cocktails:
                raise ToolError("None of those drink IDs are in the catalog.")
            names = ", ".join(c.name for c in cocktails)
            verb = "Would save" if dry_run else "Saved"
            return (
                AppliedEffect(kind="cocktail_shortlist", drink_ids=[c.drink_id for c in cocktails]),
                cocktails,
                f"{verb} {len(cocktails)} to the cocktail shortlist: {names}.",
            )
        if isinstance(action, RememberTastes):
            tastes = CocktailPreferences(
                alcohol_free=action.alcohol_free,
                liked_ingredients=self._known(action.liked_ingredients),
                avoided_ingredients=self._known(action.avoided_ingredients),
            )
            if tastes.is_empty():
                raise ToolError("None of those ingredients appear in any recipe.")
            verb = "Would remember" if dry_run else "Remembered"
            return (
                AppliedEffect(kind="cocktail_preferences", cocktail_preferences=tastes),
                [],
                f"{verb} {_describe_tastes(tastes)}.",
            )
        raise ToolError(INVALID_ACTION)

    def proposed(self, args: Mapping[str, Any]) -> list[Cocktail]:
        return self._catalog.get_many(args.get("drink_ids", []))

    def approval_text(self, action: BaseModel) -> str:
        if isinstance(action, SaveCocktails):
            return "I'd like to save these drinks to your shortlist. Approve, edit or decline?"
        return (
            "Should I remember these tastes for your next drink searches? Approve, edit or decline."
        )

    def pending_display(self, items: list[Cocktail]) -> dict[str, Any]:
        return {"cocktails": items}

    # --- Verification and reply -----------------------------------------------------------

    def check(self, turn: Turn[Cocktail], text: str) -> GroundingReport:
        return self._grounding.check(text, list(turn.evidence.values()), typed=turn.message)

    def fallback(self, items: Sequence[Cocktail], extras: Mapping[str, Any] | None = None) -> str:
        if not items:
            return (
                "I couldn't confirm matching drinks. Try other ingredients, another style "
                "or fewer filters."
            )
        lines = [f"- {c.name} · {alcohol_label(c)} · {c.glass}" for c in items]
        text = "Here are the drinks I could confirm:\n" + "\n".join(lines)
        if any(not c.alcohol_free for c in items):
            text += "\n\nPlease drink responsibly."
        return text

    def reply_fields(self, turn: Turn[Cocktail]) -> dict[str, Any]:
        return {"domain": "cocktails", "products": [], "cocktails": turn.items}

    def suggestions(self, turn: Turn[Cocktail]) -> list[str]:
        """Next-step chips; each one exercises a tool the agent can actually use."""
        cocktails = turn.items
        if not cocktails:
            return [EXAMPLE_PANTRY, "Something fruity and alcohol-free", "A classic gin drink"]
        first = cocktails[0]
        options = [f"Something similar to {first.name}"]
        alcohol_free = turn.context.cocktail_preferences.alcohol_free
        if not alcohol_free and any(not c.alcohol_free for c in cocktails):
            options.append("Make it alcohol-free")
        options.append(EXAMPLE_PANTRY)
        saved = set(turn.context.cocktail_shortlist) | {
            drink_id for effect in turn.applied for drink_id in effect.drink_ids
        }
        if first.drink_id not in saved:
            options.append(f"Save {first.name}")
        return options[:4]

    # --- Helpers --------------------------------------------------------------------------

    def _session(self, turn: Turn[Cocktail]) -> str:
        context = turn.context
        tastes = context.cocktail_preferences
        parts = []
        if not tastes.is_empty():
            parts.append(
                "remembered tastes (already applied to every search): " + _describe_tastes(tastes)
            )
        shortlist = self._catalog.get_many(dict.fromkeys(context.cocktail_shortlist))
        turn.remember(shortlist)
        if shortlist:
            parts.append(
                "cocktail shortlist: "
                + "; ".join(f"[id {c.drink_id}] {c.name} ({alcohol_label(c)})" for c in shortlist)
            )
        else:
            parts.append("the cocktail shortlist is empty")
        return ". ".join(parts) + "."

    def _known(self, values: Iterable[str]) -> list[str]:
        """Catalog spellings of ingredients, or broader terms that match some recipe
        ingredient ("rum", "egg"); values matching nothing are dropped."""
        known = []
        for value in values:
            canonical = self._catalog.canonical_ingredient(value)
            if canonical is None and any(i.matches(value) for i in self._ingredients):
                # Plain words only, so client-held tastes can't carry punctuation into prompts.
                canonical = " ".join(terms(ingredient_key(value)))
            if canonical:
                known.append(canonical)
        return list(dict.fromkeys(known))

    def _pantry_terms(self, values: Iterable[str]) -> list[str]:
        """Pantry items in catalog spelling, since the pantry matcher is strict: the exact
        ingredient, else the only ingredient containing all of the item's words ("tonic" ->
        "Tonic water"). Alias keys and ambiguous or unknown words are passed through."""
        terms = []
        for value in values:
            term = self._catalog.canonical_ingredient(value)
            if term is None and ingredient_key(value) not in PANTRY_ALIASES:
                words = set(tokens(ingredient_key(value)))
                found = [i.name for i in self._ingredients if words and words <= i.words]
                term = found[0] if len(found) == 1 else None
            terms.append(term or value.strip())
        return list(dict.fromkeys(terms))

    def _avoided(self, cocktail: Cocktail, tastes: CocktailPreferences) -> bool:
        if not tastes.avoided_ingredients:
            return False
        recipe = [_IngredientWords(i.name) for i in cocktail.ingredients]
        return any(i.matches(term) for term in tastes.avoided_ingredients for i in recipe)


class CocktailAssistant(AgentEngine[Cocktail]):
    def __init__(
        self,
        chat_model: BaseChatModel,
        catalog: CocktailCatalog,
        recommender: CocktailRecommender,
        top_k: int = 5,
        *,
        signer: ActionSigner | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        toolkit = CocktailToolkit(catalog, recommender, top_k)
        super().__init__(chat_model, toolkit, signer=signer, clock=clock)


class _IngredientWords:
    """A recipe ingredient for filter matching, with the same rules as the recommender's
    search filters: equal names, an alias that covers it, or all of the term's words."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.key = ingredient_key(name)
        # Normalised words, as in search filters: "Eggs" ~ "egg", "Whisky" ~ "whiskey".
        self.words = frozenset(terms(self.key))

    def matches(self, term: str) -> bool:
        key = ingredient_key(term)
        if not key:
            return False
        if key == self.key or self.key in PANTRY_ALIASES.get(key, ()):
            return True
        words = set(terms(key))
        return bool(words) and words <= self.words


def _alcohol(
    requested: AlcoholFilter, tastes: CocktailPreferences
) -> tuple[AlcoholFilter, list[str]]:
    """The alcohol filter to use, and which filters remembered tastes supplied."""
    from_memory = ["exclude_ingredients"] if tastes.avoided_ingredients else []
    if requested == "any" and tastes.alcohol_free:
        return "non_alcoholic", ["alcohol", *from_memory]
    return requested, from_memory


def _memory_filters(tastes: CocktailPreferences) -> list[str]:
    filters = []
    if tastes.alcohol_free:
        filters.append("alcohol")
    if tastes.avoided_ingredients:
        filters.append("exclude_ingredients")
    return filters


def _describe_tastes(tastes: CocktailPreferences) -> str:
    parts = []
    if tastes.alcohol_free:
        parts.append("alcohol-free drinks only")
    if tastes.avoided_ingredients:
        parts.append("avoiding " + ", ".join(tastes.avoided_ingredients))
    if tastes.liked_ingredients:
        parts.append("likes " + ", ".join(tastes.liked_ingredients))
    return "; ".join(parts)


def _show_hits(turn: Turn[Cocktail], hits: Sequence[CocktailHit]) -> None:
    turn.show((h.cocktail for h in hits), {h.cocktail.drink_id: list(h.reasons) for h in hits})


def _have(match: PantryMatch) -> list[str]:
    have = f"You have {', '.join(match.have)}" if match.have else "You have none of it yet"
    missing = f"Missing {', '.join(match.missing)}" if match.missing else "Nothing missing"
    return [have, missing]
