"""Composition root: loads every prepared domain into ready-to-use services.

Each domain is optional at serving time. `DOMAINS` is the registry: one `DomainSpec` per
domain, in display order, naming how to load it and how to prepare it when it is missing.
`build_services` loads the specs whose data is prepared and skips the rest, so the API
starts with any subset of fashion, cocktails and books, including none.

Adding a domain: write a runtime (a dataclass with `assistant`, `item_count()` and
`close()`), a loader that returns None while the domain's data is missing, and one
`DomainSpec` entry in `DOMAINS`. `/health`, `/domains`, chat routing and the 503 for an
unprepared domain follow from that entry.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Protocol, cast

import pandas as pd
from langchain_core.language_models import BaseChatModel

from clothes_recommender.books.catalog import BookCatalog
from clothes_recommender.books.recommend import BookRecommender
from clothes_recommender.catalog import CustomerData, ProductCatalog, load_shopper_purchases
from clothes_recommender.chat.assistant import ShoppingAssistant
from clothes_recommender.chat.book_agent import BookAssistant
from clothes_recommender.chat.cocktail_agent import CocktailAssistant
from clothes_recommender.chat.engine import AgentEngine
from clothes_recommender.chat.guardrails import ActionSigner
from clothes_recommender.chat.llm import build_chat_model
from clothes_recommender.chat.navigator import RecommenderNavigator
from clothes_recommender.chat.schemas import Domain
from clothes_recommender.cocktails.catalog import CocktailCatalog
from clothes_recommender.cocktails.recommend import CocktailRecommender
from clothes_recommender.config import Settings
from clothes_recommender.demo import DemoCustomer, select_demo_customers
from clothes_recommender.embeddings import ClipEncoder
from clothes_recommender.outfits import OutfitPlanner
from clothes_recommender.recommenders.collaborative import CollaborativeRecommender
from clothes_recommender.recommenders.content_based import ContentBasedRecommender
from clothes_recommender.recommenders.graph import GraphRecommender
from clothes_recommender.recommenders.learned import LearnedRecommender, load_model
from clothes_recommender.recommenders.popularity import PopularityRecommender
from clothes_recommender.recommenders.repeat import RepeatPurchaseRecommender
from clothes_recommender.search import ProductSearch
from clothes_recommender.vector_store import ProductVectorStore

logger = logging.getLogger(__name__)


class DomainRuntime(Protocol):
    """What the API needs from every loaded domain, whatever it recommends."""

    @property
    def assistant(self) -> AgentEngine | None:
        """The domain's agent; None when no LLM is configured."""
        ...

    def item_count(self) -> int:
        """Catalog size, reported by /health and /domains."""
        ...

    def close(self) -> None:
        """Release what the domain owns (stores, files) at shutdown."""
        ...


@dataclass(frozen=True)
class AgentKit:
    """Shared by every domain's assistant: one chat model and one proposal signer, so a
    proposal is bound to its tool's domain but verifies on any of them."""

    chat_model: BaseChatModel
    signer: ActionSigner


@dataclass(frozen=True)
class FashionServices:
    catalog: ProductCatalog
    customers: CustomerData
    store: ProductVectorStore
    search: ProductSearch
    content_based: ContentBasedRecommender
    collaborative: CollaborativeRecommender
    graph: GraphRecommender
    repeat: RepeatPurchaseRecommender
    images_dir: Path
    # None when no LLM is configured: every other feature still works without an API key.
    assistant: ShoppingAssistant | None
    # Optional demo assets; the endpoints that serve them answer 404 when they are missing.
    query_photos_dir: Path | None = None
    evaluation_path: Path | None = None
    learned: LearnedRecommender | None = None

    @cached_property
    def outfits(self) -> OutfitPlanner:
        """Outfit builder over the photo index, preferring co-purchased and look-alike pieces."""
        return OutfitPlanner(self.search, self.catalog, vectors=self.store, links=self.graph)

    @cached_property
    def demo_customers(self) -> list[DemoCustomer]:
        return select_demo_customers(self.customers, self.catalog, self.graph, self.collaborative)

    def item_count(self) -> int:
        return len(self.catalog)

    def close(self) -> None:
        self.store.close()


@dataclass(frozen=True)
class CocktailServices:
    catalog: CocktailCatalog
    recommender: CocktailRecommender
    assistant: CocktailAssistant | None = None

    def item_count(self) -> int:
        return len(self.catalog)

    def close(self) -> None:
        """Recipes live in memory; nothing to release."""


@dataclass(frozen=True)
class BookServices:
    catalog: BookCatalog
    recommender: BookRecommender
    assistant: BookAssistant | None = None

    def item_count(self) -> int:
        return len(self.catalog)

    def close(self) -> None:
        """Books live in memory; nothing to release."""


@dataclass(frozen=True)
class DomainSpec[R: DomainRuntime]:
    id: Domain
    label: str
    # How to prepare the domain: the 503 detail of its endpoints and the hint in /domains.
    setup_hint: str
    runtime_type: type[R]
    # Returns None while the domain's data is missing; raises when present but unusable,
    # unless the loader itself decides a broken file should only disable the domain.
    load: Callable[[Settings, AgentKit | None], R | None]

    def of(self, services: "Services") -> R | None:
        """This domain's runtime in `services`, or None when it is not served."""
        runtime = services.domains.get(self.id)
        return runtime if isinstance(runtime, self.runtime_type) else None


@dataclass(frozen=True)
class Services:
    """The loaded domains, keyed by id in registry order; unprepared domains are absent."""

    domains: Mapping[str, DomainRuntime] = field(default_factory=dict)
    llm_configured: bool = False

    @property
    def fashion(self) -> FashionServices | None:
        return FASHION.of(self)

    @property
    def cocktails(self) -> CocktailServices | None:
        return COCKTAILS.of(self)

    @property
    def books(self) -> BookServices | None:
        return BOOKS.of(self)

    def close(self) -> None:
        for runtime in self.domains.values():
            runtime.close()


FASHION_MISSING = (
    "The fashion catalog is not prepared on this server: prepare the H&M data and image "
    "index (docs/technical-guide.md#fashion-data), then restart the API."
)
COCKTAILS_MISSING = "Cocktail recipes are not prepared: run scripts.prepare_cocktails."
BOOKS_MISSING = "The book catalog is not prepared: run scripts.prepare_books."


def fashion_prepared(settings: Settings) -> bool:
    """The files `scripts.prepare_data` writes and serving cannot do without."""
    required = (settings.products_path, settings.users_path, settings.purchases_path)
    return all(path.is_file() for path in required)


def load_fashion(settings: Settings, kit: AgentKit | None) -> FashionServices | None:
    if not fashion_prepared(settings):
        logger.warning(
            "No prepared H&M data in %s: the fashion domain is disabled. See "
            "docs/technical-guide.md#fashion-data to enable it.",
            settings.data_dir,
        )
        return None
    catalog = ProductCatalog.from_parquet(settings.products_path)
    customers = CustomerData.from_parquet(settings.users_path, settings.purchases_path)
    store = ProductVectorStore.from_settings(settings)
    try:
        encoder = ClipEncoder(settings.clip_model_name)
        encoder.load()
        if store.exists():
            store.ensure_collection(encoder.dimension)
        search = ProductSearch(encoder, store)
        shopper_purchases = load_shopper_purchases(settings.shopper_purchases_path, customers)
        popularity = PopularityRecommender(shopper_purchases)
        if settings.co_purchases_path.exists():
            links = pd.read_parquet(settings.co_purchases_path)
        else:
            logger.warning(
                "No co_purchases.parquet: graph recommendations use taxonomy and popularity."
            )
            links = pd.DataFrame(columns=["product_id", "other_id", "count", "lift"])

        collaborative = CollaborativeRecommender(customers, catalog, shopper_purchases, popularity)
        learned = None
        if settings.ranker_path.is_file():
            model = load_model(settings.ranker_path)
            as_of = pd.to_datetime(shopper_purchases["purchase_date"]).max().date()
            if model.trained_through > as_of:
                raise ValueError("Ranker training exceeds this dataset's observation window")
            learned = LearnedRecommender(
                customers,
                catalog,
                collaborative,
                popularity,
                model.weights,
                as_of,
                feature_version=model.feature_version,
            )
        graph = GraphRecommender(customers, catalog, popularity, links)
        assistant = None
        if kit is not None:
            assistant = ShoppingAssistant(
                kit.chat_model,
                search,
                catalog,
                # The validated ranker when present; collaborative filtering otherwise.
                navigator=RecommenderNavigator(graph, search, learned or collaborative),
                outfits=OutfitPlanner(search, catalog, vectors=store, links=graph),
                signer=kit.signer,
            )
        return FashionServices(
            catalog=catalog,
            customers=customers,
            store=store,
            search=search,
            content_based=ContentBasedRecommender(customers, catalog, store, popularity),
            collaborative=collaborative,
            learned=learned,
            graph=graph,
            repeat=RepeatPurchaseRecommender(customers, popularity),
            images_dir=settings.images_dir,
            assistant=assistant,
            query_photos_dir=settings.query_photos_dir,
            evaluation_path=settings.evaluation_path,
        )
    except BaseException:
        store.close()
        raise


def load_cocktails(settings: Settings, kit: AgentKit | None) -> CocktailServices | None:
    """A missing or broken recipe file disables the domain; it never stops the API."""
    path = settings.cocktails_path
    if not path.exists():
        logger.warning(
            "No %s: the cocktail domain is disabled. Run `uv run python -m "
            "scripts.prepare_cocktails` to enable it.",
            path,
        )
        return None
    try:
        catalog = CocktailCatalog.from_parquet(path)
        recommender = CocktailRecommender(catalog)
    except Exception:
        logger.exception("Could not load %s: the cocktail domain is disabled.", path)
        return None
    assistant = None
    if kit is not None:
        assistant = CocktailAssistant(kit.chat_model, catalog, recommender, signer=kit.signer)
    return CocktailServices(catalog=catalog, recommender=recommender, assistant=assistant)


def load_books(settings: Settings, kit: AgentKit | None) -> BookServices | None:
    """A missing or broken book file disables the domain; it never stops the API."""
    paths = (settings.books_path, settings.book_neighbours_path)
    if not all(path.exists() for path in paths):
        logger.warning(
            "No %s and %s: the book domain is disabled. Run `uv run python -m "
            "scripts.prepare_books` to enable it.",
            *paths,
        )
        return None
    try:
        catalog = BookCatalog.from_parquet(settings.books_path)
        recommender = BookRecommender(catalog, pd.read_parquet(settings.book_neighbours_path))
    except Exception:
        logger.exception("Could not load the book catalog: the book domain is disabled.")
        return None
    assistant = None
    if kit is not None:
        assistant = BookAssistant(kit.chat_model, catalog, recommender, signer=kit.signer)
    return BookServices(catalog=catalog, recommender=recommender, assistant=assistant)


FASHION: DomainSpec[FashionServices] = DomainSpec(
    id="fashion",
    label="Fashion",
    setup_hint=FASHION_MISSING,
    runtime_type=FashionServices,
    load=load_fashion,
)
COCKTAILS: DomainSpec[CocktailServices] = DomainSpec(
    id="cocktails",
    label="Cocktails",
    setup_hint=COCKTAILS_MISSING,
    runtime_type=CocktailServices,
    load=load_cocktails,
)
BOOKS: DomainSpec[BookServices] = DomainSpec(
    id="books",
    label="Books",
    setup_hint=BOOKS_MISSING,
    runtime_type=BookServices,
    load=load_books,
)
# The registry, in display order: the UI defaults to the first domain that is served.
DOMAINS: tuple[DomainSpec, ...] = (FASHION, COCKTAILS, BOOKS)


def domain_spec(domain_id: str) -> DomainSpec | None:
    return next((spec for spec in DOMAINS if spec.id == domain_id), None)


def build_services(settings: Settings) -> Services:
    """Load every prepared domain (limited to ENABLED_DOMAINS when that is set)."""
    selected = _selected_domains(settings)
    kit = None
    if settings.llm_configured:
        key = settings.agent_signing_key
        kit = AgentKit(
            chat_model=build_chat_model(settings),
            signer=ActionSigner(key.get_secret_value().encode() if key else None),
        )
    else:
        logger.warning("LLM_MODEL is not set: the assistants are disabled.")
    loaded: dict[str, DomainRuntime] = {}
    try:
        for spec in selected:
            runtime = spec.load(settings, kit)
            if runtime is not None:
                loaded[spec.id] = cast(DomainRuntime, runtime)
    except BaseException:
        Services(loaded).close()  # a later domain failed: release the earlier ones
        raise
    if not loaded:
        logger.warning(
            "No domain is prepared: the API serves /health and /domains only. Run "
            "`uv run python -m scripts.prepare_cocktails` for the quickest start."
        )
    return Services(domains=loaded, llm_configured=kit is not None)


def _selected_domains(settings: Settings) -> list[DomainSpec]:
    if settings.enabled_domains is None:
        return list(DOMAINS)
    known = {spec.id for spec in DOMAINS}
    if unknown := sorted(set(settings.enabled_domains) - known):
        raise ValueError(
            f"ENABLED_DOMAINS names unknown domains {unknown}; known: {', '.join(sorted(known))}."
        )
    skipped = [spec.id for spec in DOMAINS if spec.id not in settings.enabled_domains]
    if skipped:
        logger.info("ENABLED_DOMAINS skips %s.", ", ".join(skipped))
    return [spec for spec in DOMAINS if spec.id in settings.enabled_domains]
