"""The HTTP API's chat models: the engine's wire models with every served domain's payloads.

Each domain declares its own session fields, chat-turn card IDs and approved effects
(`curio.domains.*.agent.schemas`). This module combines them into the one request and reply
format `/chat` speaks, and `WIRE` hands those classes to every assistant's engine.
"""

from typing import Annotated, Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from curio.domains.books.agent.schemas import BookChatTurn, BookContext, BookEffect
from curio.domains.books.models import Book
from curio.domains.cocktails.agent.schemas import (
    CocktailChatTurn,
    CocktailContext,
    CocktailEffect,
)
from curio.domains.cocktails.models import Cocktail
from curio.domains.evening.agent.schemas import EveningContext, EveningEffect
from curio.domains.evening.models import EveningPlan
from curio.domains.fashion.agent.intent import SearchIntent
from curio.domains.fashion.agent.schemas import (
    FashionChatTurn,
    FashionContext,
    FashionEffect,
    ShopperPreferences,
)
from curio.domains.fashion.models import Product
from curio.domains.fashion.outfits import Outfit
from curio.domains.pairings.models import BookPairing
from curio.engine import schemas as engine
from curio.engine.schemas import (
    AgentEvent,
    AgentStep,
    Clarification,
    GroundingReport,
    ReplyEvent,
    Schemas,
    StepEvent,
)

__all__ = [
    "WIRE",
    "ActionDecision",
    "AgentEvent",
    "AgentStep",
    "AppliedEffect",
    "ApprovalTool",
    "AssistantReply",
    "BookEffect",
    "ChatRequest",
    "ChatTurn",
    "Clarification",
    "CocktailEffect",
    "Domain",
    "EveningEffect",
    "FashionEffect",
    "GroundingReport",
    "PendingAction",
    "ReplyEvent",
    "SearchIntent",
    "ShopperPreferences",
    "StepEvent",
    "TurnContext",
]

Domain = Literal["fashion", "cocktails", "books", "evening"]
ApprovalTool = Literal[
    "SaveToShortlist",
    "RememberPreferences",
    "SaveCocktails",
    "RememberTastes",
    "SaveBooks",
    "RememberReadingTastes",
    "SaveEvening",
]
# Each effect kind serializes with its own domain's fields only.
AppliedEffect = Annotated[
    FashionEffect | CocktailEffect | BookEffect | EveningEffect, Field(discriminator="kind")
]


# Bases are listed last-first: pydantic orders fields from the last base to the first.
class ChatTurn(BookChatTurn, CocktailChatTurn, FashionChatTurn):
    """One earlier message, with the IDs of the cards an assistant turn showed."""


class TurnContext(EveningContext, BookContext, CocktailContext, FashionContext):
    """Every domain's client-held session state; each assistant reads its own fields (the
    evening assistant reads every domain's tastes and lists, plus the current plan)."""

    # Human-in-the-loop policy: approval tools listed here run without asking.
    auto_approve: list[ApprovalTool] = Field(default_factory=list, max_length=7)


class PendingAction(engine.PendingAction):
    """A proposal as the API sends it: the signed fields plus display-only cards.

    The server signs everything except `products`, `cocktails`, `books` and `summary`.
    """

    tool: ApprovalTool
    intent: SearchIntent | None = None
    products: list[Product] = Field(default_factory=list)
    cocktails: list[Cocktail] = Field(default_factory=list, max_length=5)
    books: list[Book] = Field(default_factory=list, max_length=5)


class ActionDecision(engine.ActionDecision):
    action: PendingAction


# The JSON key order replies have always had.
_REPLY_ORDER = (
    "text",
    "products",
    "intent",
    "domain",
    "cocktails",
    "books",
    "status",
    "steps",
    "clarification",
    "pending_action",
    "applied",
    "grounding",
    "suggestions",
    "outfit",
    "pairing",
    "evening",
)


class AssistantReply(engine.AssistantReply):
    intent: SearchIntent
    pending_action: PendingAction | None = None
    applied: list[AppliedEffect] = Field(default_factory=list)
    domain: Domain = "fashion"
    products: list[Product] = Field(default_factory=list)
    cocktails: list[Cocktail] = Field(default_factory=list)
    books: list[Book] = Field(default_factory=list)
    # The outfit this turn built or changed; the client keeps `outfit.state` for follow-ups.
    outfit: Outfit | None = None
    # A book turn's cocktail pairing (PairCocktail): the profile, drinks and reasons. Its
    # drinks are also in `cocktails`.
    pairing: BookPairing | None = None
    # An evening turn's plan (PlanEvening, SwapEveningPart): its parts, reasons and the
    # client-held `state`. Its pieces, book and drink are also in products, books, cocktails.
    # Left out of other domains' replies, whose format predates it.
    evening: EveningPlan | None = None

    # No return annotation: pydantic then keeps the field-by-field JSON schema for responses.
    @model_serializer(mode="wrap")
    def _wire_order(self, handler: SerializerFunctionWrapHandler):
        data: dict[str, Any] = handler(self)
        if data.get("evening") is None and self.domain != "evening":
            data.pop("evening", None)
        return {name: data[name] for name in _REPLY_ORDER if name in data} | data


class ChatRequest(TurnContext):
    domain: Domain = "fashion"
    message: str = Field(default="", max_length=2000)
    history: list[ChatTurn] = Field(default=[], max_length=50)
    decision: ActionDecision | None = None

    @model_validator(mode="after")
    def _message_or_decision(self) -> "ChatRequest":
        if not self.message.strip() and self.decision is None:
            raise ValueError("Send a message or a decision on a pending action")
        return self


# What every assistant behind the API builds its turns from.
WIRE = Schemas(
    intent=SearchIntent,
    context=TurnContext,
    pending_action=PendingAction,
    reply=AssistantReply,
)
