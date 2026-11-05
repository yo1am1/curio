"""Book → cocktail pairing types shared by the pairer, the book agent, the API and the UI."""

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from clothes_recommender.books.domain import GenreTerm
from clothes_recommender.cocktails.domain import Cocktail, CocktailPreferences

MAX_PAIRINGS = 5
MAX_SEED = 10_000


class PairingRequest(BaseModel):
    """Pair one book (`book_id`) or a reader's taste (`book_ids` and liked genres).

    `alcohol_free` and an alcohol-free `cocktail_preferences` (the cocktail domain's
    remembered taste) keep only alcohol-free recipes; a children's or young-adult book always
    does. Avoided genres never drive the profile; avoided ingredients are left out.
    """

    book_id: PositiveInt | None = None
    book_ids: list[PositiveInt] = Field(default_factory=list, max_length=20)
    liked_genres: list[GenreTerm] = Field(default_factory=list, max_length=10)
    avoided_genres: list[GenreTerm] = Field(default_factory=list, max_length=10)
    alcohol_free: bool = False
    cocktail_preferences: CocktailPreferences | None = None
    # Drinks already shown, left out (the agent's "another pairing").
    exclude_drink_ids: list[PositiveInt] = Field(default_factory=list, max_length=15)
    # 0 is the best pairing; each further seed is another, equally deterministic set.
    seed: int = Field(default=0, ge=0, le=MAX_SEED)
    top_k: int = Field(default=3, ge=1, le=MAX_PAIRINGS)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _something_to_pair(self) -> "PairingRequest":
        if self.book_id is None and not self.book_ids and not self.liked_genres:
            raise ValueError("Give a book_id, book_ids or liked_genres to pair with")
        return self


class ThemeWeight(BaseModel):
    """One flavour theme of a profile: what the books say and what it pours."""

    id: str
    label: str  # "Mystery and noir"
    style: str  # "a bitter, spirit-forward whiskey drink"
    weight: float = Field(ge=0, le=1)  # share of the profile
    evidence: str  # "Mystery · shelved as noir" or "4 of 6 books"
    books: int = 0  # books that lean this way (reader-taste mode)


class FlavourProfile(BaseModel):
    source: str  # "The Big Sleep", "your reading list (6 books)", "the genres you like"
    themes: list[ThemeWeight]
    summary: str
    alcohol_free: bool
    alcohol_free_reason: str | None = None
    book_ids: list[int] = Field(default_factory=list)


class Pairing(BaseModel):
    cocktail: Cocktail
    score: float
    theme: str  # the ThemeWeight id that drove the choice
    reasons: list[str] = Field(min_length=1, max_length=3)


class BookPairing(BaseModel):
    profile: FlavourProfile
    pairings: list[Pairing]
    seed: int
    # How many different sets "Another pairing" cycles through (seed modulo this).
    sets: int = 1
    note: str
