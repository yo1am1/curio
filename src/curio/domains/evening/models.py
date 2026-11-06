"""Evening plan types shared by the planner, the evening agent, the API and the UI.

An evening is three parts that share one theme: an outfit (fashion), a book (books) and a
drink the pairer pairs with that book (pairings over cocktails). The server keeps no plan:
the client holds `EveningState` and sends it back to swap a part or to save the evening, and
every part is re-read from its catalog (and the drink re-paired) on the way back.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints

from curio.domains.books.models import Book, BookPreferences
from curio.domains.cocktails.models import Cocktail, CocktailPreferences
from curio.domains.evening.themes import ThemeId
from curio.domains.fashion.outfits import Outfit, OutfitState, SlotName

EveningPart = Literal["outfit", "book", "drink"]
EveningAudience = Literal["women", "men"]
# "other" works for every part; "cheaper" only for an outfit piece; "alcohol_free" only for
# the drink.
EveningSwapMode = Literal["other", "cheaper", "alcohol_free"]
MAX_BUDGET = 5_000
MAX_SKIPPED_BOOKS = 20
MAX_SKIPPED_DRINKS = 15
Occasion = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200, pattern=r"\S")
]
Mood = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class EveningTastes(BaseModel):
    """The reader's remembered tastes from the other domains, applied to every part: avoided
    genres and authors, liked genres, an alcohol-free taste and avoided ingredients, and a
    per-piece price limit."""

    model_config = ConfigDict(extra="forbid")
    book_preferences: BookPreferences = Field(default_factory=BookPreferences)
    cocktail_preferences: CocktailPreferences = Field(default_factory=CocktailPreferences)
    max_item_price: float | None = Field(default=None, gt=0, le=10_000)


class EveningRequest(EveningTastes):
    """Plan an evening: an occasion in free text, plus optional limits and tastes.

    A budget ("under €100") or "alcohol-free" written in the occasion counts as if given.
    """

    occasion: Occasion
    budget: float | None = Field(default=None, gt=0, le=MAX_BUDGET)  # for the outfit
    audience: EveningAudience | None = None
    mood: Mood | None = None
    alcohol_free: bool = False


class EveningState(BaseModel):
    """What the client sends back to swap a part or save the evening. The server re-reads
    every product, book and recipe and re-pairs the drink; nothing here is trusted as a fact."""

    model_config = ConfigDict(extra="forbid")
    occasion: Occasion
    mood: Mood | None = None
    theme: ThemeId
    budget: float | None = Field(default=None, gt=0, le=MAX_BUDGET)
    audience: EveningAudience | None = None
    alcohol_free: bool = False
    outfit: OutfitState | None = None
    book_id: PositiveInt | None = None
    drink_id: PositiveInt | None = None
    # Parts swapped out, so "another book" does not bring an earlier one back.
    skipped_books: list[PositiveInt] = Field(default_factory=list, max_length=MAX_SKIPPED_BOOKS)
    skipped_drinks: list[PositiveInt] = Field(default_factory=list, max_length=MAX_SKIPPED_DRINKS)


class ThemeInfo(BaseModel):
    id: ThemeId
    label: str  # "Cosy"
    icon: str  # Material icon name
    feeling: str  # "a warm night in"
    evidence: str  # "“rainy Sunday in” mentions “rainy” and “Sunday”"


class OutfitPart(BaseModel):
    outfit: Outfit
    reason: str


class BookPart(BaseModel):
    book: Book
    reason: str


class DrinkPart(BaseModel):
    cocktail: Cocktail
    # The pairer's reasons for this drink with the book (the first cites both).
    reasons: list[str] = Field(min_length=1)
    pairs_with: str  # the pairing theme's label: "Fantasy and magic"
    # Recipe ingredients that make it suit the evening's theme ("Honey", "Cinnamon").
    evening_fit: list[str] = Field(default_factory=list)
    alcohol_free_reason: str | None = None
    note: str  # the pairer's note: "Please drink responsibly. …"

    @property
    def reason(self) -> str:
        return self.reasons[0]


class MissingPart(BaseModel):
    part: EveningPart
    reason: str


class EveningPlan(BaseModel):
    occasion: str
    theme: ThemeInfo
    outfit: OutfitPart | None = None
    book: BookPart | None = None
    drink: DrinkPart | None = None
    # One line citing evidence from each part: "Rainy Sunday in → cosy: …".
    why: str
    missing: list[MissingPart] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    status: Literal["complete", "partial", "empty"]
    # What a swap changed ("Another book: … → …"), None for a new plan.
    change: str | None = None
    state: EveningState

    def amounts(self) -> list[float]:
        """Euro figures an answer may state: the outfit's prices are checked separately;
        these are its total, budget, what is left and the cheapest option of a gap."""
        return self.outfit.outfit.amounts() if self.outfit is not None else []


class EveningSwap(BaseModel):
    """Change one part of the evening the client holds."""

    model_config = ConfigDict(extra="forbid")
    evening: EveningState
    part: EveningPart
    slot: SlotName | None = None  # the outfit piece to change
    mode: EveningSwapMode = "other"
    refine: str = Field(default="", max_length=100)
    tastes: EveningTastes = Field(default_factory=EveningTastes)
