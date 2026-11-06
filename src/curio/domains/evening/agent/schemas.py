"""The evening agent's session state and approved effect, as the client holds them.

An evening reads every other domain's client-held state (remembered tastes, the three
shortlists) and adds the current plan; its one effect saves the parts to each domain's own
list, exactly as that domain's save effect does.
"""

from typing import ClassVar, Literal

from pydantic import Field

from curio.domains.books.agent.schemas import BookChatTurn, BookContext
from curio.domains.cocktails.agent.schemas import CocktailChatTurn, CocktailContext
from curio.domains.evening.models import EveningState
from curio.domains.fashion.agent.schemas import FashionChatTurn, FashionContext
from curio.engine.schemas import AppliedEffect


class EveningChatTurn(BookChatTurn, CocktailChatTurn, FashionChatTurn):
    """An evening answer shows product, book and drink cards."""


class EveningContext(BookContext, CocktailContext, FashionContext):
    # The latest plan the evening agent made, so a follow-up can swap or save its parts.
    evening: EveningState | None = None


class EveningEffect(AppliedEffect):
    """An approved evening: pieces for the shortlist, books for the reading list and drinks
    for the saved cocktails."""

    kind: Literal["evening"] = "evening"
    product_ids: list[int] = Field(default_factory=list)
    book_ids: list[int] = Field(default_factory=list)
    drink_ids: list[int] = Field(default_factory=list)

    adds: ClassVar = {
        "product_ids": "shortlist",
        "book_ids": "book_shortlist",
        "drink_ids": "cocktail_shortlist",
    }
