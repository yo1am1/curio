"""The book agent's session state and approved effects, as the client holds them."""

from typing import ClassVar, Literal

from pydantic import Field, PositiveInt

from curio.domains.books.models import BookPreferences
from curio.engine.schemas import AppliedEffect, ChatTurn, TurnContext


class BookChatTurn(ChatTurn):
    # Book cards shown with an assistant turn, so follow-ups can refer to them.
    book_ids: list[PositiveInt] = Field(default_factory=list, max_length=5)


class BookContext(TurnContext):
    book_preferences: BookPreferences = Field(default_factory=BookPreferences)
    # The reading list.
    book_shortlist: list[PositiveInt] = Field(default_factory=list, max_length=20)


class BookEffect(AppliedEffect):
    """Approved books for the reading list, or remembered reading tastes."""

    kind: Literal["book_shortlist", "book_preferences"]
    # The reading list (book shortlist) and remembered reading tastes.
    book_ids: list[int] = Field(default_factory=list)
    book_preferences: BookPreferences | None = None

    remembers: ClassVar = {"book_preferences": "book_preferences"}
    adds: ClassVar = {"book_ids": "book_shortlist"}
