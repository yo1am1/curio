"""Composition root: loads every prepared domain into ready-to-use services.

Each domain is optional at serving time. `DOMAINS` is the registry: one `DomainSpec` per
domain, in display order, naming how to load it and how to prepare it when it is missing.
`build_services` loads the specs whose data is prepared and skips the rest, so the API
starts with any subset of fashion, cocktails and books, including none.

Adding a domain: write a runtime (a dataclass with `assistant`, `item_count()` and
`close()`), a loader that returns None while the domain's data is missing, and one
`DomainSpec` entry in `DOMAINS`. `/health`, `/domains`, chat routing and the 503 for an
unprepared domain follow from that entry.

Cross-domain features link loaded domains after loading (`link_domains`): with books and
cocktails both served, the book runtime gets a `BookCocktailPairer` and its agent the
PairCocktail tool.

A composed domain has no data of its own: its spec has `compose` instead of `load`, and it is
built from the loaded domains after linking. "Plan my evening" (`evening`) is served whenever
books and cocktails are (with fashion its plans have an outfit too); ENABLED_DOMAINS limits
the data domains it builds on, and naming `evening` in it enables those domains too.
"""

import dataclasses
import logging
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Protocol, cast

import pandas as pd
from langchain_core.language_models import BaseChatModel

from curio.app.assistants import (
    BookAssistant,
    CocktailAssistant,
    EveningAssistant,
    ShoppingAssistant,
)
from curio.app.config import Settings
from curio.app.demo import DemoCustomer, select_demo_customers
from curio.app.schemas import Domain
from curio.domains.books.catalog import BookCatalog
from curio.domains.books.recommend import BookRecommender
from curio.domains.cocktails.catalog import CocktailCatalog
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.domains.evening.planner import OUTFIT_NEEDS_FASHION, EveningPlanner
from curio.domains.fashion.catalog import CustomerData, ProductCatalog, load_shopper_purchases
from curio.domains.fashion.embeddings import ClipEncoder
from curio.domains.fashion.navigator import RecommenderNavigator
from curio.domains.fashion.outfits import OutfitPlanner
from curio.domains.fashion.recommenders.collaborative import CollaborativeRecommender
from curio.domains.fashion.recommenders.content_based import ContentBasedRecommender
from curio.domains.fashion.recommenders.graph import GraphRecommender
from curio.domains.fashion.recommenders.learned import LearnedRecommender, load_model
from curio.domains.fashion.recommenders.popularity import PopularityRecommender
from curio.domains.fashion.recommenders.repeat import RepeatPurchaseRecommender
from curio.domains.fashion.search import ProductSearch
from curio.domains.fashion.vector_store import ProductVectorStore
from curio.domains.pairings.pairer import BookCocktailPairer
from curio.engine import TURN_DEADLINE_SECONDS, ActionSigner, AgentEngine
from curio.engine.llm import build_chat_model

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
    # The wall-clock budget of one agent turn (AGENT_TURN_DEADLINE_SECONDS).
    turn_deadline_seconds: float = TURN_DEADLINE_SECONDS


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
    # Book → cocktail pairing; set by `link_domains` when cocktails are served too.
    pairing: BookCocktailPairer | None = None

    def item_count(self) -> int:
        return len(self.catalog)

    def close(self) -> None:
        """Books live in memory; nothing to release."""


@dataclass(frozen=True)
class EveningServices:
    """Plan my evening, composed from the served books, cocktails and (optionally) fashion."""

    planner: EveningPlanner
    assistant: EveningAssistant | None = None

    def item_count(self) -> int:
        """The parts it can plan: 3, or 2 without fashion."""
        return len(self.planner.parts)

    @property
    def notice(self) -> str | None:
        """What a served evening still lacks: the outfit without fashion data."""
        return None if "outfit" in self.planner.parts else OUTFIT_NEEDS_FASHION

    def close(self) -> None:
        """The parts belong to their own domains; nothing to release."""


@dataclass(frozen=True)
class DomainSpec[R: DomainRuntime]:
    id: Domain
    label: str
    # How to prepare the domain: the 503 detail of its endpoints and the hint in /domains.
    setup_hint: str
    runtime_type: type[R]
    # Returns None while the domain's data is missing; raises when present but unusable,
    # unless the loader itself decides a broken file should only disable the domain.
    load: Callable[[Settings, AgentKit | None], R | None] | None = None
    # A composed domain instead builds on the loaded domains (None while they are missing).
    compose: Callable[[Mapping[str, "DomainRuntime"], AgentKit | None], R | None] | None = None
    # For a composed domain: what is missing, given what the server serves.
    missing: Callable[["Services"], str] | None = None
    # For a composed domain: the data domains it builds on, which ENABLED_DOMAINS naming it
    # enables too (each still loads only when its data is prepared).
    builds_on: tuple[str, ...] = ()

    def of(self, services: "Services") -> R | None:
        """This domain's runtime in `services`, or None when it is not served."""
        runtime = services.domains.get(self.id)
        return runtime if isinstance(runtime, self.runtime_type) else None

    def hint(self, services: "Services") -> str:
        """How to serve this domain: its setup hint, or a composed domain's missing parts."""
        return self.missing(services) if self.missing is not None else self.setup_hint


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

    @property
    def evening(self) -> EveningServices | None:
        return EVENING.of(self)

    @property
    def pairing(self) -> BookCocktailPairer | None:
        """Book → cocktail pairing, served when books and cocktails both are."""
        books = self.books
        return books.pairing if books is not None and self.cocktails is not None else None

    def close(self) -> None:
        for runtime in self.domains.values():
            runtime.close()


FASHION_MISSING = (
    "The fashion catalog is not prepared on this server: prepare the H&M data and image "
    "index (docs/technical-guide.md#fashion-data), then restart the API."
)
COCKTAILS_MISSING = "Cocktail recipes are not prepared: run scripts.prepare_cocktails."
BOOKS_MISSING = "The book catalog is not prepared: run scripts.prepare_books."
EVENING_MISSING = "Plan my evening needs the book catalog and the cocktail recipes: {missing}"


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
                turn_deadline_seconds=kit.turn_deadline_seconds,
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
        assistant = CocktailAssistant(
            kit.chat_model,
            catalog,
            recommender,
            signer=kit.signer,
            turn_deadline_seconds=kit.turn_deadline_seconds,
        )
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
        assistant = BookAssistant(
            kit.chat_model,
            catalog,
            recommender,
            signer=kit.signer,
            turn_deadline_seconds=kit.turn_deadline_seconds,
        )
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


def compose_evening(
    domains: Mapping[str, DomainRuntime], kit: AgentKit | None
) -> EveningServices | None:
    """Plan my evening over the loaded domains: books and cocktails (with their pairer) are
    required; fashion adds the outfit."""
    books, cocktails = domains.get(BOOKS.id), domains.get(COCKTAILS.id)
    if not isinstance(books, BookServices) or not isinstance(cocktails, CocktailServices):
        return None
    if books.pairing is None:
        return None  # the pairer failed to build; the drink could not come from it
    fashion = domains.get(FASHION.id)
    fashion = fashion if isinstance(fashion, FashionServices) else None
    planner = EveningPlanner(
        outfits=fashion.outfits if fashion is not None else None,
        books=books.recommender,
        book_catalog=books.catalog,
        pairer=books.pairing,
        cocktails=cocktails.catalog,
    )
    assistant = None
    if kit is not None:
        assistant = EveningAssistant(
            kit.chat_model,
            planner,
            books=books.catalog,
            cocktails=cocktails.catalog,
            products=fashion.catalog if fashion is not None else None,
            signer=kit.signer,
            turn_deadline_seconds=kit.turn_deadline_seconds,
        )
    return EveningServices(planner=planner, assistant=assistant)


def evening_missing(services: "Services") -> str:
    """Which part an unserved evening lacks, and how to prepare it."""
    hints = []
    if services.books is None:
        hints.append(BOOKS_MISSING)
    if services.cocktails is None:
        hints.append(COCKTAILS_MISSING)
    if not hints:
        hints.append("book-cocktail pairing could not be built; see the API log.")
    return EVENING_MISSING.format(missing=" ".join(hints))


EVENING: DomainSpec[EveningServices] = DomainSpec(
    id="evening",
    label="Evening",
    setup_hint=EVENING_MISSING.format(missing=f"{BOOKS_MISSING} {COCKTAILS_MISSING}"),
    runtime_type=EveningServices,
    compose=compose_evening,
    missing=evening_missing,
    builds_on=("fashion", "cocktails", "books"),
)
# The registry, in display order: the UI defaults to the first domain that is served.
DOMAINS: tuple[DomainSpec, ...] = (FASHION, COCKTAILS, BOOKS, EVENING)


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
            turn_deadline_seconds=settings.agent_turn_deadline_seconds,
        )
    else:
        logger.warning("LLM_MODEL is not set: the assistants are disabled.")
    loaded: dict[str, DomainRuntime] = {}
    try:
        for spec in selected:
            if spec.load is not None and (runtime := spec.load(settings, kit)) is not None:
                loaded[spec.id] = cast(DomainRuntime, runtime)
        link_domains(loaded)
        for spec in DOMAINS:
            if spec.compose is not None and (runtime := spec.compose(loaded, kit)) is not None:
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


def link_domains(domains: MutableMapping[str, DomainRuntime]) -> None:
    """Cross-domain features over the loaded domains: book → cocktail pairing needs both.

    The pairer reuses the cocktail recommender's search index; the book agent gets the
    PairCocktail tool. A pairer that cannot be built leaves both domains serving as before.
    """
    books, cocktails = domains.get(BOOKS.id), domains.get(COCKTAILS.id)
    if not isinstance(books, BookServices) or not isinstance(cocktails, CocktailServices):
        return
    try:
        pairer = BookCocktailPairer(books.catalog, cocktails.catalog, cocktails.recommender)
    except Exception:
        logger.exception("Could not build book-cocktail pairing; it is disabled.")
        return
    if books.assistant is not None:
        books.assistant.attach_pairing(pairer, cocktails.catalog)
    domains[BOOKS.id] = dataclasses.replace(books, pairing=pairer)


def _selected_domains(settings: Settings) -> list[DomainSpec]:
    if settings.enabled_domains is None:
        return list(DOMAINS)
    known = {spec.id for spec in DOMAINS}
    if unknown := sorted(set(settings.enabled_domains) - known):
        raise ValueError(
            f"ENABLED_DOMAINS names unknown domains {unknown}; known: {', '.join(sorted(known))}."
        )
    # Composed domains follow the domains they build on, so they are never "skipped"; naming
    # one enables what it builds on ("evening" loads books, cocktails and, when prepared,
    # fashion).
    enabled = set(settings.enabled_domains)
    for spec in DOMAINS:
        if spec.id in settings.enabled_domains and (added := set(spec.builds_on) - enabled):
            logger.info(
                "ENABLED_DOMAINS names %s, so it also enables %s (each when prepared).",
                spec.id,
                ", ".join(sorted(added)),
            )
            enabled |= added
    data = [spec for spec in DOMAINS if spec.load is not None]
    skipped = [spec.id for spec in data if spec.id not in enabled]
    if skipped:
        logger.info("ENABLED_DOMAINS skips %s.", ", ".join(skipped))
    return [spec for spec in data if spec.id in enabled]
