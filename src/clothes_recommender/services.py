"""Composition root: wires settings, data, models and stores into ready-to-use services."""

import logging
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import pandas as pd

from clothes_recommender.catalog import CustomerData, ProductCatalog, load_shopper_purchases
from clothes_recommender.chat.assistant import ShoppingAssistant
from clothes_recommender.chat.cocktail_agent import CocktailAssistant
from clothes_recommender.chat.guardrails import ActionSigner
from clothes_recommender.chat.llm import build_chat_model
from clothes_recommender.chat.navigator import RecommenderNavigator
from clothes_recommender.cocktails.catalog import CocktailCatalog
from clothes_recommender.cocktails.recommend import CocktailRecommender
from clothes_recommender.config import Settings
from clothes_recommender.demo import DemoCustomer, select_demo_customers
from clothes_recommender.embeddings import ClipEncoder
from clothes_recommender.recommenders.collaborative import CollaborativeRecommender
from clothes_recommender.recommenders.content_based import ContentBasedRecommender
from clothes_recommender.recommenders.graph import GraphRecommender
from clothes_recommender.recommenders.learned import LearnedRecommender, load_model
from clothes_recommender.recommenders.popularity import PopularityRecommender
from clothes_recommender.recommenders.repeat import RepeatPurchaseRecommender
from clothes_recommender.search import ProductSearch
from clothes_recommender.vector_store import ProductVectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Services:
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
    # The cocktail domain is optional: None until data/cocktails.parquet is prepared.
    cocktail_catalog: CocktailCatalog | None = None
    cocktail_recommender: CocktailRecommender | None = None
    cocktail_assistant: CocktailAssistant | None = None

    @cached_property
    def demo_customers(self) -> list[DemoCustomer]:
        return select_demo_customers(self.customers, self.catalog, self.graph, self.collaborative)


def build_services(settings: Settings) -> Services:
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
        cocktails = _load_cocktails(settings.cocktails_path)
        assistant = cocktail_assistant = None
        if settings.llm_configured:
            key = settings.agent_signing_key
            chat_model = build_chat_model(settings)
            # One signer for both domains; proposals are bound to their tool's domain.
            signer = ActionSigner(key.get_secret_value().encode() if key else None)
            assistant = ShoppingAssistant(
                chat_model,
                search,
                catalog,
                # The validated ranker when present; collaborative filtering otherwise.
                navigator=RecommenderNavigator(graph, search, learned or collaborative),
                signer=signer,
            )
            if cocktails is not None:
                cocktail_assistant = CocktailAssistant(chat_model, *cocktails, signer=signer)
        else:
            logger.warning("LLM_MODEL is not set: the shopping assistants are disabled.")
        return Services(
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
            cocktail_catalog=cocktails[0] if cocktails else None,
            cocktail_recommender=cocktails[1] if cocktails else None,
            cocktail_assistant=cocktail_assistant,
        )
    except BaseException:
        store.close()
        raise


def _load_cocktails(path: Path) -> tuple[CocktailCatalog, CocktailRecommender] | None:
    """The optional cocktail domain; a missing or broken file never stops fashion serving."""
    if not path.exists():
        logger.warning(
            "No %s: the cocktail domain is disabled. Run `uv run python -m "
            "scripts.prepare_cocktails` to enable it.",
            path,
        )
        return None
    try:
        catalog = CocktailCatalog.from_parquet(path)
        return catalog, CocktailRecommender(catalog)
    except Exception:
        logger.exception("Could not load %s: the cocktail domain is disabled.", path)
        return None
