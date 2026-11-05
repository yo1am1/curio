"""Every domain is optional at serving time: the API starts with any subset of prepared data.

These tests build services from real `Settings` over a temporary data directory, so they
cover the path `docker compose --profile demo up` and `scripts/quickstart.sh` take: recipes
prepared, no H&M data, no CLIP model and no Qdrant index.
"""

import io
import logging
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from clothes_recommender import api as api_module
from clothes_recommender import services as service_module
from clothes_recommender.api import CHAT_DISABLED, create_app
from clothes_recommender.config import Settings
from clothes_recommender.services import (
    COCKTAILS_MISSING,
    DOMAINS,
    FASHION_MISSING,
    DomainSpec,
    build_services,
)
from tests.conftest import cocktail_frame

FASHION_REQUESTS = [
    ("GET", "/products/1", None),
    ("GET", "/products/1/image", None),
    ("GET", "/products/1/related", None),
    ("GET", "/products/1/similar", None),
    ("GET", "/products/1/graph", None),
    ("GET", "/sections", None),
    ("POST", "/search/text", {"query": "white sneakers"}),
    ("GET", "/search/examples", None),
    ("GET", "/search/examples/1/image", None),
    ("GET", "/users", None),
    ("GET", "/users/1", None),
    ("GET", "/users/1/recommendations", None),
    ("GET", "/demo/customers", None),
    ("GET", "/evaluation", None),
    ("POST", "/chat", {"message": "white sneakers"}),  # domain defaults to fashion
    ("POST", "/chat/stream", {"message": "white sneakers", "domain": "fashion"}),
]


@pytest.fixture
def no_fashion_models(monkeypatch):
    """Fail loudly if anything tries to load CLIP or open the Qdrant index."""
    clip = Mock(side_effect=AssertionError("CLIP must not load without fashion data"))
    store = Mock(side_effect=AssertionError("Qdrant must not open without fashion data"))
    monkeypatch.setattr(service_module, "ClipEncoder", clip)
    monkeypatch.setattr(service_module.ProductVectorStore, "from_settings", store)
    return clip, store


def settings_for(tmp_path, **overrides) -> Settings:
    """Real settings over `tmp_path`, ignoring the developer's .env and LLM variables."""
    values = {"data_dir": tmp_path, "qdrant_path": tmp_path / ".qdrant", "llm_model": None}
    return Settings(_env_file=None, **(values | overrides))


@pytest.fixture
def cocktails_only(tmp_path, no_fashion_models) -> Settings:
    cocktail_frame().to_parquet(tmp_path / "cocktails.parquet")
    return settings_for(tmp_path)


@pytest.fixture
def started(cocktails_only, monkeypatch):
    """The production app factory, started from settings rather than ready-made services."""
    monkeypatch.setattr(api_module, "get_settings", lambda: cocktails_only)
    with TestClient(create_app()) as client:
        yield client


# --- Services ------------------------------------------------------------------------------


def test_cocktails_alone_load_without_clip_or_qdrant(cocktails_only, no_fashion_models, caplog):
    caplog.set_level(logging.WARNING)

    services = build_services(cocktails_only)

    assert list(services.domains) == ["cocktails"]
    assert services.fashion is None
    assert len(services.cocktails.catalog) == 8
    assert services.cocktails.assistant is None
    assert "the fashion domain is disabled" in caplog.text
    assert "docs/technical-guide.md#fashion-data" in caplog.text
    clip, store = no_fashion_models
    clip.assert_not_called()
    store.assert_not_called()
    assert not (cocktails_only.data_dir / ".qdrant").exists()
    services.close()


def test_no_prepared_domain_still_builds(tmp_path, no_fashion_models, caplog):
    services = build_services(settings_for(tmp_path))

    assert dict(services.domains) == {}
    assert "No domain is prepared" in caplog.text
    assert "scripts.prepare_cocktails" in caplog.text


def test_partial_fashion_data_is_not_served(tmp_path, no_fashion_models):
    (tmp_path / "products.parquet").touch()  # users and purchases are still missing

    assert build_services(settings_for(tmp_path)).fashion is None


def test_llm_builds_the_cocktail_assistant_without_fashion(cocktails_only, monkeypatch):
    from clothes_recommender.chat.cocktail_agent import CocktailAssistant
    from tests.chat.conftest import FakeToolCallingChatModel

    model = FakeToolCallingChatModel(messages=iter([]))
    monkeypatch.setattr(service_module, "build_chat_model", Mock(return_value=model))
    settings = cocktails_only.model_copy(update={"llm_model": "stub"})

    services = build_services(settings)

    assert services.llm_configured
    assert isinstance(services.cocktails.assistant, CocktailAssistant)


def test_enabled_domains_skips_prepared_data(tmp_path, no_fashion_models, caplog):
    caplog.set_level(logging.INFO)
    cocktail_frame().to_parquet(tmp_path / "cocktails.parquet")
    for name in ("products", "users", "purchases"):
        (tmp_path / f"{name}.parquet").touch()  # fashion looks prepared, but is not selected

    services = build_services(settings_for(tmp_path, enabled_domains="cocktails"))

    assert list(services.domains) == ["cocktails"]
    assert "ENABLED_DOMAINS skips fashion" in caplog.text
    clip, store = no_fashion_models
    clip.assert_not_called()
    store.assert_not_called()


def test_enabled_domains_parses_a_comma_separated_list(monkeypatch):
    monkeypatch.setenv("ENABLED_DOMAINS", " Cocktails, fashion ,")
    assert Settings(_env_file=None).enabled_domains == ("cocktails", "fashion")
    monkeypatch.setenv("ENABLED_DOMAINS", "")
    assert Settings(_env_file=None).enabled_domains is None


def test_unknown_enabled_domain_fails_fast(tmp_path):
    with pytest.raises(ValueError, match=r"unknown domains \['books'\]; known: cocktails, fashion"):
        build_services(settings_for(tmp_path, enabled_domains="cocktails,books"))


def test_a_failing_domain_closes_the_ones_already_loaded(cocktails_only, monkeypatch):
    first = Mock(assistant=None)
    error = RuntimeError("synthetic load failure")
    registry = (
        DomainSpec("fashion", "Fashion", "prepare it", Mock, lambda *_: first),
        DomainSpec("cocktails", "Cocktails", "prepare it", Mock, Mock(side_effect=error)),
    )
    monkeypatch.setattr(service_module, "DOMAINS", registry)

    with pytest.raises(RuntimeError, match="synthetic load failure"):
        build_services(cocktails_only)

    first.close.assert_called_once_with()


def test_registry_is_ordered_and_unique():
    ids = [spec.id for spec in DOMAINS]
    assert ids == ["fashion", "cocktails"]
    assert len(set(ids)) == len(ids)
    assert all(spec.setup_hint and spec.label for spec in DOMAINS)


# --- API -----------------------------------------------------------------------------------


def test_api_starts_with_only_cocktails_and_reports_each_domain(started):
    health = started.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert (body["fashion_enabled"], body["cocktails_enabled"]) == (False, True)
    assert (body["catalog_products"], body["indexed_products"], body["cocktail_count"]) == (0, 0, 8)
    assert (body["chat_enabled"], body["ranking_enabled"], body["llm_configured"]) == (
        False,
        False,
        False,
    )
    assert (
        started.get("/domains").json()
        == body["domains"]
        == [
            {
                "id": "fashion",
                "label": "Fashion",
                "enabled": False,
                "chat_enabled": False,
                "item_count": 0,
                "hint": FASHION_MISSING,
            },
            {
                "id": "cocktails",
                "label": "Cocktails",
                "enabled": True,
                "chat_enabled": False,
                "item_count": 8,
                "hint": CHAT_DISABLED,
            },
        ]
    )


def test_cocktail_endpoints_work_without_fashion(started):
    hits = started.post("/cocktails/search", json={"query": "mojito"}).json()
    assert hits[0]["cocktail"]["name"] == "Mojito"
    assert started.get("/cocktails/1").json()["name"] == "Mojito"
    assert started.post("/cocktails/pantry", json={"ingredients": ["Gin", "Tonic water"]}).json()


@pytest.mark.parametrize(("method", "path", "body"), FASHION_REQUESTS)
def test_fashion_endpoints_are_503_with_how_to_prepare(started, method, path, body):
    response = started.request(method, path, json=body)
    assert response.status_code == 503
    assert response.json() == {"detail": FASHION_MISSING}


def test_image_search_is_503_without_fashion(started):
    png = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(png, format="PNG")
    response = started.post("/search/image", files={"file": ("red.png", png.getvalue())})
    assert response.status_code == 503
    assert response.json() == {"detail": FASHION_MISSING}


def test_cocktail_chat_without_llm_says_how_to_enable_it(started):
    response = started.post("/chat", json={"message": "a mojito", "domain": "cocktails"})
    assert response.status_code == 503
    assert response.json() == {"detail": CHAT_DISABLED}


def test_api_starts_with_no_domain_at_all(tmp_path, no_fashion_models, monkeypatch):
    monkeypatch.setattr(api_module, "get_settings", lambda: settings_for(tmp_path))
    with TestClient(create_app()) as client:
        health = client.get("/health").json()
        assert [d["enabled"] for d in health["domains"]] == [False, False]
        assert health["domains"][1]["hint"] == COCKTAILS_MISSING
        assert client.get("/cocktails/categories").status_code == 503
