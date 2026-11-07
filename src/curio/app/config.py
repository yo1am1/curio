"""Application settings, read from environment variables and an optional `.env` file."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    # env_ignore_empty: `LLM_MODEL=` in a copied .env.example must mean "not set", not "".
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    # --- Data -------------------------------------------------------------
    data_dir: Path = Path("data")
    # Comma-separated domain ids to serve (e.g. "cocktails"). Unset: every domain whose data
    # is prepared. Domains outside the list are not loaded, even when their data exists.
    enabled_domains: Annotated[tuple[str, ...] | None, NoDecode] = None

    # --- Embeddings -------------------------------------------------------
    clip_model_name: str = "laion/CLIP-ViT-B-32-laion2B-s34B-b79K"

    # --- Vector store -----------------------------------------------------
    # With QDRANT_URL unset, Qdrant runs embedded and persists to QDRANT_PATH,
    # so the demo works without any external service.
    qdrant_url: str | None = None
    qdrant_api_key: SecretStr | None = None
    qdrant_path: Path = Path(".qdrant")
    qdrant_collection: str = "products"

    # --- LLM (any LangChain provider; OpenAI-compatible gateways via base URL)
    llm_provider: str = "openai"
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 60.0
    # Wall-clock budget of one agent turn, checked before every model call. Raise it only for
    # slow models (a relayed model in `evals/`); the default matches `TURN_DEADLINE_SECONDS`.
    agent_turn_deadline_seconds: float = Field(default=90.0, gt=0)
    # Signs the agent's approval proposals. Unset: a random per-process key, so pending
    # proposals expire on restart. Set it when running several API workers.
    agent_signing_key: SecretStr | None = None

    # --- Web app ----------------------------------------------------------
    # The built React app (`npm run build` in web/). The API serves it beside its own routes
    # whenever the folder holds a build; SERVE_WEB=false turns that off.
    web_dist: Path = Path("web/dist")
    serve_web: bool = True

    @field_validator("enabled_domains", mode="before")
    @classmethod
    def _split_domains(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip().lower() for part in value.split(",") if part.strip())
        return value

    @property
    def products_path(self) -> Path:
        return self.data_dir / "products.parquet"

    @property
    def users_path(self) -> Path:
        return self.data_dir / "users.parquet"

    @property
    def purchases_path(self) -> Path:
        return self.data_dir / "purchases.parquet"

    @property
    def shopper_purchases_path(self) -> Path:
        """Every shopper's purchases of catalog products, under anonymous ids."""
        return self.data_dir / "shopper_purchases.parquet"

    @property
    def co_purchases_path(self) -> Path:
        return self.data_dir / "co_purchases.parquet"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def query_photos_dir(self) -> Path:
        """Photos of garments outside the catalog, for trying image search."""
        return self.data_dir / "query_photos"

    @property
    def ranker_path(self) -> Path:
        return self.data_dir / "ranker.json"

    @property
    def evaluation_path(self) -> Path:
        return self.data_dir / "evaluation.json"

    @property
    def cocktails_path(self) -> Path:
        """Recipes built by `scripts.prepare_cocktails`; the cocktail domain needs this file."""
        return self.data_dir / "cocktails.parquet"

    @property
    def books_path(self) -> Path:
        """Books built by `scripts.prepare_books`; the book domain needs this file."""
        return self.data_dir / "books.parquet"

    @property
    def book_neighbours_path(self) -> Path:
        """Readers-also-liked links from `scripts.prepare_books`; also required."""
        return self.data_dir / "book_neighbours.parquet"

    @property
    def llm_configured(self) -> bool:
        return self.llm_model is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
