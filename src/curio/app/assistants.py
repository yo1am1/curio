"""The assistants the API serves: the engine running each domain's toolkit on `WIRE`.

A domain owns its toolkit; this module binds it to the engine with the HTTP API's models,
so every assistant replies in the same format and verifies the others' proposals.
"""

import time
from collections.abc import Callable

from langchain_core.language_models import BaseChatModel

from curio.app.schemas import WIRE
from curio.domains.books.agent.toolkit import BookToolkit
from curio.domains.books.catalog import BookCatalog
from curio.domains.books.models import Book
from curio.domains.books.recommend import BookRecommender
from curio.domains.cocktails.agent.toolkit import CocktailToolkit
from curio.domains.cocktails.catalog import CocktailCatalog
from curio.domains.cocktails.models import Cocktail
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.domains.evening.agent.toolkit import EveningItem, EveningToolkit
from curio.domains.evening.planner import EveningPlanner
from curio.domains.fashion.agent.intent import IntentExtractor
from curio.domains.fashion.agent.toolkit import FashionToolkit
from curio.domains.fashion.catalog import ProductCatalog
from curio.domains.fashion.models import Product
from curio.domains.fashion.navigator import CatalogNavigator
from curio.domains.fashion.outfits import OutfitPlanner
from curio.domains.fashion.search import ProductSearch
from curio.domains.pairings.agent.toolkit import CocktailPairing
from curio.domains.pairings.pairer import BookCocktailPairer
from curio.engine import ActionSigner, AgentEngine

__all__ = ["BookAssistant", "CocktailAssistant", "EveningAssistant", "ShoppingAssistant"]


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
        outfits: OutfitPlanner | None = None,
        signer: ActionSigner | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        extractor = intent_extractor or IntentExtractor(
            chat_model,
            product_types=catalog.distinct("product_type"),
            sections=catalog.distinct("section"),
            colours=catalog.distinct("colour"),
        )
        toolkit = FashionToolkit(search, catalog, top_k, extractor, navigator, outfits)
        super().__init__(chat_model, toolkit, signer=signer, clock=clock, schemas=WIRE)
        self._navigator = navigator


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
        super().__init__(chat_model, toolkit, signer=signer, clock=clock, schemas=WIRE)


class BookAssistant(AgentEngine[Book]):
    def __init__(
        self,
        chat_model: BaseChatModel,
        catalog: BookCatalog,
        recommender: BookRecommender,
        top_k: int = 5,
        *,
        signer: ActionSigner | None = None,
        clock: Callable[[], float] = time.monotonic,
        pairer: BookCocktailPairer | None = None,
        cocktails: CocktailCatalog | None = None,
    ) -> None:
        self._catalog = catalog
        self.toolkit = BookToolkit(catalog, recommender, top_k)
        if pairer is not None and cocktails is not None:
            self.attach_pairing(pairer, cocktails)
        super().__init__(chat_model, self.toolkit, signer=signer, clock=clock, schemas=WIRE)

    def attach_pairing(self, pairer: BookCocktailPairer, cocktails: CocktailCatalog) -> None:
        """Enable PairCocktail (the server loaded cocktails beside the books)."""
        self.toolkit.attach_pairing(CocktailPairing(pairer, cocktails, self._catalog))


class EveningAssistant(AgentEngine[EveningItem]):
    """Plan my evening: an outfit, a book and a drink paired with it, on the same engine."""

    def __init__(
        self,
        chat_model: BaseChatModel,
        planner: EveningPlanner,
        *,
        books: BookCatalog,
        cocktails: CocktailCatalog,
        products: ProductCatalog | None = None,
        signer: ActionSigner | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.toolkit = EveningToolkit(planner, books=books, cocktails=cocktails, products=products)
        super().__init__(chat_model, self.toolkit, signer=signer, clock=clock, schemas=WIRE)
