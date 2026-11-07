"""HTTP API exposing search, recommendations and the shopping assistant.

Run with: uv run uvicorn curio.app.api:create_app --factory
"""

import io
import json
import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import date
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from curio.app.config import Settings, get_settings
from curio.app.demo import DemoCustomer
from curio.app.hardening import SlotStreamingResponse, TurnSlots
from curio.app.schemas import AssistantReply, ChatRequest, Domain, TurnContext
from curio.app.services import (
    BOOKS,
    COCKTAILS,
    DOMAINS,
    EVENING,
    FASHION,
    BookServices,
    CocktailServices,
    DomainRuntime,
    DomainSpec,
    EveningServices,
    FashionServices,
    Services,
    build_services,
    domain_spec,
)
from curio.app.web import serve_web
from curio.domains.books.models import (
    BecauseYouLiked,
    Book,
    BookHit,
    BookQuery,
    BookRef,
)
from curio.domains.books.taxonomy import MOODS
from curio.domains.cocktails.models import (
    AlcoholFilter,
    Cocktail,
    CocktailHit,
    CocktailQuery,
    PantryMatch,
)
from curio.domains.evening.models import EveningPlan, EveningRequest, EveningSwap
from curio.domains.evening.planner import EveningError
from curio.domains.fashion.models import Product, ProductFilter, ScoredProduct, User
from curio.domains.fashion.outfits import (
    MAX_SLOTS,
    Audience,
    Outfit,
    OutfitError,
    OutfitRequest,
    OutfitState,
    SlotName,
    SwapMode,
)
from curio.domains.fashion.recommenders.base import Recommender, UnknownUserError
from curio.domains.fashion.vector_store import IndexNotBuiltError
from curio.domains.pairings.models import BookPairing, PairingRequest
from curio.domains.pairings.pairer import BookCocktailPairer, PairingError, UnknownBookError
from curio.engine import AgentEngine, InvalidActionError

logger = logging.getLogger(__name__)

MAX_TOP_K = 50
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Bounds decode memory: a small compressed file can still expand to gigabytes of pixels.
MAX_IMAGE_PIXELS = 25_000_000
CHAT_DISABLED = "Chat is disabled: set LLM_MODEL (and LLM_API_KEY / LLM_BASE_URL) in .env."

TopK = Annotated[int, Query(ge=1, le=MAX_TOP_K)]


class ProductHit(BaseModel):
    product: Product
    score: float
    reasons: list[str] = Field(default_factory=list)


class TextSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    top_k: int = Field(default=10, ge=1, le=MAX_TOP_K)
    section: str | None = Field(default=None, description="Only this section, e.g. Menswear.")


class QueryPhoto(BaseModel):
    """A photo of an H&M garment outside the catalog, to try image search with."""

    photo_id: int
    name: str
    product_type: str
    colour: str


class GraphNode(BaseModel):
    """A node of a product neighborhood: a product, a catalog attribute or a category."""

    id: str
    label: str
    kind: Literal["product", "attribute", "taxonomy"]
    product_id: int | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: Literal["has_attribute", "in_category", "co_purchased"]
    weight: float


class ProductGraph(BaseModel):
    """A bounded one-hop neighborhood of a product (`/products/{id}/graph`)."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]


# `/evaluation` serves the file `scripts.evaluate --output` wrote, as written: these models
# describe its fields for clients, keep any others (extra="allow") and leave out the ones a
# file does not have (the route excludes unset fields), so the JSON is unchanged.
Metric = int | float


class _EvaluationPart(BaseModel):
    model_config = ConfigDict(extra="allow")


class HoldoutMetrics(_EvaluationPart):
    users_evaluated: int | None = None
    map_at_12: Metric | None = None
    hit_rate: Metric | None = None
    recall_at_12: Metric | None = None


class StrategyResult(_EvaluationPart):
    full_holdout: HoldoutMetrics
    novel_item_holdout: HoldoutMetrics | None = None
    catalog_coverage: Metric | None = None
    recommendation_fill: Metric | None = None


class PairedGain(_EvaluationPart):
    """The learned ranker's MAP@12 gain over buy-again, with a paired bootstrap interval."""

    mean_delta: Metric
    lower_95: Metric
    upper_95: Metric
    paired_customers: int | None = None
    bootstrap_resamples: int | None = None
    scope: str | None = None


class ContentIndex(_EvaluationPart):
    indexed_products: int
    collection: str | None = None


class SampleScope(_EvaluationPart):
    prepared_users: int | None = None
    catalog_products: int | None = None
    holdout_purchases: int | None = None
    training_purchases: int | None = None


class Evaluation(_EvaluationPart):
    """Offline evaluation results (`scripts.evaluate --output`)."""

    top_k: int | None = None
    users_evaluated: int | None = None
    cutoff: str | None = None
    holdout_end: str | None = None
    holdout_days: int | None = None
    evaluation_role: str | None = None
    sample_scope: SampleScope | None = None
    repeat_purchase_limitation: dict[str, Any] | None = None
    content_index: ContentIndex | None = None
    ranker: dict[str, Any] | None = None
    results: dict[str, StrategyResult] = Field(default_factory=dict)
    learned_vs_repeat: PairedGain | None = None


class PurchasedProduct(BaseModel):
    purchase_date: date
    product: Product


class UserProfile(BaseModel):
    user: User
    purchases: list[PurchasedProduct]


class ChatFeedback(BaseModel):
    """A shopper's rating of one assistant answer, for offline review of agent quality."""

    rating: Literal["up", "down"]
    message: str = Field(max_length=2000)
    answer: str = Field(max_length=4000)
    product_ids: list[int] = Field(default_factory=list, max_length=5)
    tools: list[str] = Field(default_factory=list, max_length=12)
    comment: str = Field(default="", max_length=500)


class OutfitQuery(BaseModel):
    """Build an outfit without the assistant: the same planner the BuildOutfit tool uses."""

    occasion: str = Field(min_length=2, max_length=200, pattern=r"\S")
    budget: float | None = Field(default=None, gt=0, le=10_000)
    audience: Audience | None = None
    slots: list[SlotName] | None = Field(default=None, min_length=2, max_length=MAX_SLOTS)
    max_item_price: float | None = Field(default=None, gt=0, le=10_000)


class OutfitSwap(BaseModel):
    outfit: OutfitState
    slot: SlotName
    mode: SwapMode = "other"
    refine: str = Field(default="", max_length=100)


class DomainInfo(BaseModel):
    id: Domain
    label: str
    enabled: bool
    chat_enabled: bool
    item_count: int = 0
    # How to prepare a disabled domain; how to enable chat for an enabled one without it.
    hint: str | None = None


class Health(BaseModel):
    """Service status. The flat fashion and cocktail fields predate `domains` and stay for
    existing clients; they read 0/False when that domain is not served."""

    status: Literal["ok"] = "ok"
    indexed_products: int
    catalog_products: int
    chat_enabled: bool
    ranking_enabled: bool
    fashion_enabled: bool
    cocktails_enabled: bool
    cocktail_count: int
    books_enabled: bool = False
    book_count: int = 0
    llm_configured: bool
    domains: list[DomainInfo]


IngredientName = Annotated[str, Field(min_length=1, max_length=60)]


class PantryRequest(BaseModel):
    ingredients: list[IngredientName] = Field(max_length=30)
    alcohol: AlcoholFilter = "any"
    max_missing: int = Field(default=2, ge=0, le=3)
    top_k: int = Field(default=10, ge=1, le=MAX_TOP_K)


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


def _require[R: DomainRuntime](spec: DomainSpec[R], services: Services) -> R:
    """The domain's runtime, or 503 with how to prepare it."""
    runtime = spec.of(services)
    if runtime is None:
        raise HTTPException(status_code=503, detail=spec.hint(services))
    return runtime


def get_fashion(services: ServicesDep) -> FashionServices:
    return _require(FASHION, services)


def get_cocktails(services: ServicesDep) -> CocktailServices:
    return _require(COCKTAILS, services)


def get_books(services: ServicesDep) -> BookServices:
    return _require(BOOKS, services)


def get_pairing(services: ServicesDep) -> BookCocktailPairer:
    """Pairing needs books and cocktails: 503 with the setup hint of whichever is missing."""
    books = _require(BOOKS, services)
    _require(COCKTAILS, services)
    if books.pairing is None:
        raise HTTPException(status_code=503, detail="Book-cocktail pairing is not available.")
    return books.pairing


def get_evening(services: ServicesDep) -> EveningServices:
    """Plan my evening: 503 naming whichever of books and cocktails is missing."""
    return _require(EVENING, services)


FashionDep = Annotated[FashionServices, Depends(get_fashion)]
EveningDep = Annotated[EveningServices, Depends(get_evening)]
CocktailsDep = Annotated[CocktailServices, Depends(get_cocktails)]
BooksDep = Annotated[BookServices, Depends(get_books)]
PairingDep = Annotated[BookCocktailPairer, Depends(get_pairing)]


def domain_infos(services: Services) -> list[DomainInfo]:
    """Every registered domain in display order, served or not."""
    infos = []
    for spec in DOMAINS:
        runtime = services.domains.get(spec.id)
        chat = runtime is not None and runtime.assistant is not None
        hint = None
        if runtime is None:
            hint = spec.hint(services)
        elif not chat:
            hint = CHAT_DISABLED
        # A composed domain served without one of its optional parts says which.
        if runtime is not None and (notice := getattr(runtime, "notice", None)):
            hint = f"{hint} {notice}" if hint else notice
        infos.append(
            DomainInfo(
                id=spec.id,
                label=spec.label,
                enabled=runtime is not None,
                chat_enabled=chat,
                item_count=runtime.item_count() if runtime is not None else 0,
                hint=hint,
            )
        )
    return infos


def _to_hits(fashion: FashionServices, scored: Sequence[ScoredProduct]) -> list[ProductHit]:
    hits = []
    for item in scored:
        product = fashion.catalog.get(item.product_id)
        if product is not None:
            hits.append(ProductHit(product=product, score=item.score))
    return hits


def _read_image(upload: UploadFile) -> Image.Image:
    if upload.size is None or upload.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image must be at most 10 MB.")
    try:
        image = Image.open(io.BytesIO(upload.file.read()))
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise HTTPException(status_code=413, detail="Image resolution is too large.")
        image.load()  # Image.open is lazy; decode now so corrupt files fail here.
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise HTTPException(status_code=400, detail="Upload a valid PNG or JPEG image.") from error
    return image


def create_app(
    services: Services | None = None,
    *,
    web_dist: Path | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Build the app. Tests pass ready-made `services`; production builds them at startup.

    `web_dist` is the built React app to serve beside the API (`curio.app.web`). Production
    (no `services`) takes it from the settings (`WEB_DIST`, on unless `SERVE_WEB=false`).
    `settings` default to the environment's; the HTTP limits always come from them.
    """
    if settings is None:
        settings = get_settings()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if services is not None:
            app.state.services = services
            yield
            return
        built = app.state.services = build_services(settings)
        try:
            if built.fashion is not None:
                # Choosing the examples runs the recommenders for many customers (~10 s on the
                # default sample); do it before serving so the UI's first page loads fast.
                _ = built.fashion.demo_customers
            yield
        finally:
            built.close()

    app = FastAPI(
        title="Curio Recommendation API",
        version=version("curio-recommendations"),
        lifespan=lifespan,
    )

    @app.exception_handler(IndexNotBuiltError)
    async def index_not_built(_: Request, error: IndexNotBuiltError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(error)})

    @app.exception_handler(UnknownUserError)
    async def unknown_user(_: Request, error: UnknownUserError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.get("/health")
    def health(services: ServicesDep) -> Health:
        fashion, cocktails, books = services.fashion, services.cocktails, services.books
        indexed = 0
        if fashion is not None and fashion.store.exists():
            indexed = fashion.store.count()
        return Health(
            indexed_products=indexed,
            catalog_products=fashion.item_count() if fashion else 0,
            chat_enabled=fashion is not None and fashion.assistant is not None,
            ranking_enabled=fashion is not None and fashion.learned is not None,
            fashion_enabled=fashion is not None,
            cocktails_enabled=cocktails is not None,
            cocktail_count=cocktails.item_count() if cocktails else 0,
            books_enabled=books is not None,
            book_count=books.item_count() if books else 0,
            llm_configured=services.llm_configured,
            domains=domain_infos(services),
        )

    @app.get("/domains")
    def domains(services: ServicesDep) -> list[DomainInfo]:
        return domain_infos(services)

    # Static /cocktails/* paths are registered before /cocktails/{drink_id}.
    @app.get("/cocktails/ingredients")
    def cocktail_ingredients(cocktails: CocktailsDep) -> list[str]:
        return cocktails.catalog.ingredients()

    @app.get("/cocktails/categories")
    def cocktail_categories(cocktails: CocktailsDep) -> list[str]:
        return cocktails.catalog.categories()

    @app.post("/cocktails/search")
    def search_cocktails(query: CocktailQuery, cocktails: CocktailsDep) -> list[CocktailHit]:
        return cocktails.recommender.search(query)

    @app.post("/cocktails/pantry")
    def cocktail_pantry(request: PantryRequest, cocktails: CocktailsDep) -> list[PantryMatch]:
        return cocktails.recommender.pantry(
            request.ingredients,
            alcohol=request.alcohol,
            max_missing=request.max_missing,
            top_k=request.top_k,
        )

    @app.get("/cocktails/{drink_id}")
    def get_cocktail(drink_id: int, cocktails: CocktailsDep) -> Cocktail:
        cocktail = cocktails.catalog.get(drink_id)
        if cocktail is None:
            raise HTTPException(status_code=404, detail=f"Drink {drink_id} not found.")
        return cocktail

    @app.get("/cocktails/{drink_id}/similar")
    def similar_cocktails(
        drink_id: int, cocktails: CocktailsDep, top_k: TopK = 5, alcohol: AlcoholFilter = "any"
    ) -> list[CocktailHit]:
        get_cocktail(drink_id, cocktails)
        return cocktails.recommender.similar(drink_id, top_k=top_k, alcohol=alcohol)

    # Static /books/* paths are registered before /books/{book_id}.
    @app.get("/books/genres")
    def book_genres(books: BooksDep) -> list[str]:
        """Genres in use, most common first."""
        return books.catalog.genres()

    @app.get("/books/moods")
    def book_moods(books: BooksDep) -> list[str]:
        return list(MOODS)

    @app.get("/books/index")
    def book_index(books: BooksDep) -> list[BookRef]:
        """Every book's id, title and first author, most rated first (for pickers)."""
        ordered = sorted(books.catalog, key=lambda b: (-b.ratings_count, b.book_id))
        return [BookRef(book_id=b.book_id, title=b.title, author=b.author) for b in ordered]

    @app.post("/books/search")
    def search_books(query: BookQuery, books: BooksDep) -> list[BookHit]:
        return books.recommender.search(query)

    @app.post("/books/because")
    def because_you_liked(request: BecauseYouLiked, books: BooksDep) -> list[BookHit]:
        """Books readers of the liked books also liked, with which liked book led to each."""
        return books.recommender.because(
            request.liked,
            request.top_k,
            exclude_genres=request.exclude_genres,
            exclude_authors=request.exclude_authors,
        )

    @app.get("/books/{book_id}")
    def get_book(book_id: int, books: BooksDep) -> Book:
        book = books.catalog.get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"Book {book_id} not found.")
        return book

    @app.get("/books/{book_id}/similar")
    def similar_books(
        book_id: int, books: BooksDep, top_k: TopK = 5, other_authors_only: bool = False
    ) -> list[BookHit]:
        """Readers who liked this book also liked these."""
        get_book(book_id, books)
        return books.recommender.similar(book_id, top_k, other_authors_only=other_authors_only)

    @app.get("/books/{book_id}/by-author")
    def more_by_author(book_id: int, books: BooksDep, top_k: TopK = 5) -> list[BookHit]:
        """More books by this book's first author."""
        book = get_book(book_id, books)
        return books.recommender.by_author(book.author, top_k, exclude_book_id=book_id)

    @app.post("/pairings/book-cocktail")
    def pair_book_cocktail(request: PairingRequest, pairer: PairingDep) -> BookPairing:
        """Cocktails for one book (`book_id`) or a reader's taste (`book_ids`, liked and
        avoided genres), with reasons. Children's and young-adult books, `alcohol_free` and
        an alcohol-free `cocktail_preferences` get alcohol-free recipes only."""
        try:
            return pairer.pair(request)
        except UnknownBookError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except PairingError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/evening/plan")
    def plan_evening(request: EveningRequest, evening: EveningDep) -> EveningPlan:
        """One evening for an occasion: an outfit (when fashion is served), a book and a drink
        the pairer pairs with it, each with its reason, plus a 'why it works' line. A part
        the server cannot plan is listed in `missing` with the reason."""
        return evening.planner.plan(request)

    @app.post("/evening/swap")
    def swap_evening_part(request: EveningSwap, evening: EveningDep) -> EveningPlan:
        """Change one part of the evening the client holds: another or a cheaper outfit piece,
        another book (its drink is re-paired) or another or an alcohol-free drink."""
        try:
            return evening.planner.swap(
                request.evening,
                request.part,
                slot=request.slot,
                mode=request.mode,
                refine=request.refine,
                tastes=request.tastes,
            )
        except EveningError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/products/{product_id}")
    def get_product(product_id: int, fashion: FashionDep) -> Product:
        product = fashion.catalog.get(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail=f"Product {product_id} not found.")
        return product

    @app.get("/products/{product_id}/image", response_class=FileResponse)
    def get_product_image(product_id: int, fashion: FashionDep) -> FileResponse:
        # product_id is an int, so the path cannot escape images_dir.
        path = fashion.images_dir / f"{product_id}.jpg"
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"No image for product {product_id}.")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/products/{product_id}/related")
    def related_products(
        product_id: int,
        fashion: FashionDep,
        kind: Literal["complement", "alternative"] = "complement",
        top_k: TopK = 10,
    ) -> list[ProductHit]:
        product = get_product(product_id, fashion)
        hits = _to_hits(fashion, fashion.graph.related(product_id, kind, top_k))
        reason = (
            f"Bought on the same day by the same shoppers as {product.name}; "
            "different product group."
            if kind == "complement"
            else f"Same specific Shopify category, product type and section as {product.name}, "
            "with compatible mapped audience attributes and a higher approximate price."
        )
        for hit in hits:
            hit.reasons = [reason]
        return hits

    @app.get("/products/{product_id}/similar")
    def similar_products(
        product_id: int, fashion: FashionDep, top_k: TopK = 10
    ) -> list[ProductHit]:
        """Products whose photos look most like this one's (CLIP item-to-item)."""
        get_product(product_id, fashion)
        similar = fashion.search.similar_to(product_id, top_k)
        if similar is None:
            raise HTTPException(
                status_code=404, detail=f"Product {product_id} has no photo in the image index."
            )
        return _to_hits(fashion, similar)

    @app.get("/products/{product_id}/graph")
    def product_graph(product_id: int, fashion: FashionDep, top_k: TopK = 12) -> ProductGraph:
        get_product(product_id, fashion)
        return ProductGraph.model_validate(fashion.graph.neighborhood(product_id, top_k))

    @app.post("/outfits")
    def build_outfit(query: OutfitQuery, fashion: FashionDep) -> Outfit:
        slots = tuple(dict.fromkeys(query.slots or ()))
        if query.slots is not None and len(slots) < 2:
            raise HTTPException(status_code=422, detail="Name at least two different slots.")
        request = OutfitRequest(
            occasion=query.occasion.strip(),
            budget=query.budget,
            audience=query.audience,
            slots=slots,
            max_item_price=query.max_item_price,
        )
        return fashion.outfits.outfit(request)

    @app.post("/outfits/swap")
    def swap_outfit_item(request: OutfitSwap, fashion: FashionDep) -> Outfit:
        try:
            swap = fashion.outfits.swap(request.outfit, request.slot, request.mode, request.refine)
        except OutfitError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return swap.outfit

    @app.get("/sections")
    def sections(fashion: FashionDep) -> list[str]:
        return fashion.catalog.distinct("section")

    @app.post("/search/text")
    def search_by_text(request: TextSearchRequest, fashion: FashionDep) -> list[ProductHit]:
        product_filter = ProductFilter(sections=(request.section,) if request.section else ())
        return _to_hits(
            fashion, fashion.search.by_text(request.query, request.top_k, product_filter)
        )

    @app.get("/search/examples")
    def query_photos(fashion: FashionDep) -> list[QueryPhoto]:
        """Example query photos; empty until scripts.fetch_query_photos has run."""
        if fashion.query_photos_dir is None:
            return []
        index = fashion.query_photos_dir / "query_photos.json"
        if not index.is_file():
            return []
        return [QueryPhoto.model_validate(row) for row in json.loads(index.read_text())]

    @app.get("/search/examples/{photo_id}/image", response_class=FileResponse)
    def query_photo_image(photo_id: int, fashion: FashionDep) -> FileResponse:
        # photo_id is an int, so the path cannot escape query_photos_dir.
        path = fashion.query_photos_dir / f"{photo_id}.jpg" if fashion.query_photos_dir else None
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail=f"No example photo {photo_id}.")
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/search/image")
    def search_by_image(
        file: UploadFile, fashion: FashionDep, top_k: TopK = 10
    ) -> list[ProductHit]:
        return _to_hits(fashion, fashion.search.by_image(_read_image(file), top_k))

    @app.get("/users")
    def list_users(fashion: FashionDep) -> list[int]:
        return fashion.customers.user_ids()

    @app.get("/demo/customers")
    def demo_customers(fashion: FashionDep) -> list[DemoCustomer]:
        return fashion.demo_customers

    @app.get("/users/{user_id}")
    def get_user_profile(user_id: int, fashion: FashionDep) -> UserProfile:
        user = fashion.customers.get_user(user_id)
        if user is None:
            raise UnknownUserError(user_id)
        purchases = [
            PurchasedProduct(purchase_date=p.purchase_date, product=product)
            for p in fashion.customers.purchases_for(user_id)
            if (product := fashion.catalog.get(p.product_id)) is not None
        ]
        return UserProfile(user=user, purchases=purchases)

    @app.get("/users/{user_id}/recommendations")
    def recommend(
        user_id: int,
        fashion: FashionDep,
        strategy: Literal["content", "collaborative", "graph", "repeat", "learned"] = "content",
        top_k: TopK = 10,
    ) -> list[ProductHit]:
        recommender: Recommender | None = {
            "content": fashion.content_based,
            "collaborative": fashion.collaborative,
            "graph": fashion.graph,
            "repeat": fashion.repeat,
            "learned": fashion.learned,
        }[strategy]
        if recommender is None:
            raise HTTPException(
                status_code=503, detail="Personalized ranking is not available yet."
            )
        hits = _to_hits(fashion, recommender.recommend(user_id, top_k))
        explainer = {
            "graph": fashion.graph,
            "collaborative": fashion.collaborative,
            "repeat": fashion.repeat,
            "learned": fashion.learned,
        }.get(strategy)
        if explainer is not None:
            for hit in hits:
                hit.reasons = explainer.reasons(user_id, hit.product.product_id)
        return hits

    @app.get("/evaluation", response_model_exclude_unset=True)
    def evaluation(fashion: FashionDep) -> Evaluation:
        """Offline evaluation results written by `scripts.evaluate --output`."""
        path = fashion.evaluation_path
        if path is None or not path.is_file():
            raise HTTPException(
                status_code=404,
                detail="No evaluation results yet: run "
                "`uv run python -m scripts.evaluate --qdrant-path .qdrant --output "
                "data/evaluation.json`.",
            )
        return Evaluation.model_validate(json.loads(path.read_text()))

    def assistant_for(services: Services, domain: Domain) -> AgentEngine:
        spec = domain_spec(domain)
        runtime = services.domains.get(domain)
        if spec is None or runtime is None:
            detail = spec.hint(services) if spec else f"Unknown domain {domain}."
            raise HTTPException(status_code=503, detail=detail)
        if runtime.assistant is None:
            raise HTTPException(status_code=503, detail=CHAT_DISABLED)
        return runtime.assistant

    turns = TurnSlots(settings.max_concurrent_turns)
    busy_response = {503: {"description": "Every agent-turn slot is taken; see Retry-After."}}

    @app.post("/chat", responses=busy_response)
    def chat(request: ChatRequest, services: ServicesDep) -> AssistantReply:
        """One agent turn in the requested domain. Send `decision` instead of a message to
        answer a pending proposal. 503 with `Retry-After` while every turn slot is taken."""
        assistant = assistant_for(services, request.domain)
        context = TurnContext.model_validate(request.model_dump(include=_CONTEXT_FIELDS))
        slot = turns.acquire()
        try:
            if request.decision is not None:
                return assistant.resume(request.decision, request.history, context)
            return assistant.reply(request.message, request.history, context)
        except Exception as error:
            raise _chat_error(error) from error
        finally:
            slot.release()

    @app.post("/chat/stream", responses=busy_response)
    def chat_stream(request: ChatRequest, services: ServicesDep) -> StreamingResponse:
        """The same turn as /chat, as NDJSON: one `step` event per agent step, then `reply`."""
        assistant = assistant_for(services, request.domain)
        context = TurnContext.model_validate(request.model_dump(include=_CONTEXT_FIELDS))
        slot = turns.acquire()

        def lines() -> Iterator[str]:
            try:
                events = assistant.stream(
                    request.message, request.history, context, decision=request.decision
                )
                for event in events:
                    yield event.model_dump_json() + "\n"
            except Exception as error:
                failure = _chat_error(error)
                yield (
                    json.dumps(
                        {"type": "error", "status": failure.status_code, "detail": failure.detail}
                    )
                    + "\n"
                )
            finally:
                slot.release()

        return SlotStreamingResponse(lines(), slot, media_type="application/x-ndjson")

    @app.post("/chat/feedback", status_code=204)
    def chat_feedback(rating: ChatFeedback) -> None:
        """Record a rating in the server log: the rating and tool names only. The message,
        answer and comment are not kept, and nothing serves feedback back over HTTP."""
        logger.info("chat feedback %s tools=%s", rating.rating, ",".join(rating.tools))

    if web_dist is None and services is None:
        settings = get_settings()
        web_dist = settings.web_dist if settings.serve_web else None
    if web_dist is not None:
        serve_web(app, web_dist)
    return app


_CONTEXT_FIELDS = set(TurnContext.model_fields)


def _chat_error(error: Exception) -> HTTPException:
    """Map agent failures to safe HTTP errors; provider details stay in server logs."""
    if isinstance(error, HTTPException):
        return error
    if isinstance(error, InvalidActionError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, IndexNotBuiltError):
        return HTTPException(status_code=503, detail=str(error))
    if getattr(error, "status_code", None) == 429:
        logger.warning("Shopping assistant provider is rate limited")
        return HTTPException(
            status_code=429, detail="The assistant is busy. Please try again shortly."
        )
    # Provider SDKs raise their own exception types (auth, rate limit, network). Log the
    # details server-side only: upstream messages can contain URLs or request ids.
    logger.error("Shopping assistant failed", exc_info=error)
    return HTTPException(status_code=502, detail="LLM provider request failed; see server logs.")
