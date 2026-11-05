"""Production service construction with fake models and an in-memory vector store."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from clothes_recommender import services as service_module
from clothes_recommender.api import create_app
from clothes_recommender.catalog import CustomerData
from clothes_recommender.vector_store import ProductVectorStore


@pytest.fixture
def construction(monkeypatch, tmp_path, sample_catalog):
    purchases = pd.DataFrame(
        [(1, 1, date(2020, 9, 1))],
        columns=["user_id", "product_id", "purchase_date"],
    )
    customers = CustomerData(pd.DataFrame({"user_id": [1], "age": [30]}), purchases)
    settings = SimpleNamespace(
        data_dir=tmp_path,
        enabled_domains=None,
        products_path=tmp_path / "products.parquet",
        users_path=tmp_path / "users.parquet",
        purchases_path=tmp_path / "purchases.parquet",
        shopper_purchases_path=tmp_path / "shopper_purchases.parquet",
        co_purchases_path=tmp_path / "co_purchases.parquet",
        ranker_path=tmp_path / "ranker.json",
        clip_model_name="fake",
        llm_configured=False,
        images_dir=tmp_path / "images",
        query_photos_dir=tmp_path / "query_photos",
        evaluation_path=tmp_path / "evaluation.json",
        cocktails_path=tmp_path / "cocktails.parquet",
        books_path=tmp_path / "books.parquet",
        book_neighbours_path=tmp_path / "book_neighbours.parquet",
    )
    # Fashion is served once prepare_data's files exist; their loaders are faked below.
    for path in (settings.products_path, settings.users_path, settings.purchases_path):
        path.touch()
    encoder = Mock(dimension=2)
    encoder.encode_texts.return_value = np.array([[1.0, 0.0]], dtype=np.float32)
    client = QdrantClient(":memory:")
    store = ProductVectorStore(client, "test-services")
    close = Mock(wraps=store.close)
    monkeypatch.setattr(store, "close", close)
    monkeypatch.setattr(service_module.ProductCatalog, "from_parquet", lambda _: sample_catalog)
    monkeypatch.setattr(service_module.CustomerData, "from_parquet", lambda *_: customers)
    monkeypatch.setattr(service_module.ProductVectorStore, "from_settings", lambda _: store)
    monkeypatch.setattr(service_module, "ClipEncoder", Mock(return_value=encoder))
    monkeypatch.setattr(
        service_module,
        "load_shopper_purchases",
        Mock(return_value=purchases.rename(columns={"user_id": "shopper_id"})),
    )
    yield SimpleNamespace(settings=settings, encoder=encoder, store=store, close=close)
    client.close()


def test_existing_collection_must_match_loaded_encoder(construction):
    construction.store.ensure_collection(3)

    with pytest.raises(ValueError, match=r"3-d vectors.*2-d vectors.*--recreate"):
        service_module.build_services(construction.settings)

    construction.encoder.load.assert_called_once_with()
    construction.close.assert_called_once_with()


def test_compatible_collection_remains_open_and_searchable(construction, sample_products):
    construction.store.ensure_collection(2)
    construction.store.upsert([sample_products[0]], np.array([[1.0, 0.0]], dtype=np.float32))

    services = service_module.build_services(construction.settings)

    assert services.fashion.store is construction.store
    assert [hit.product_id for hit in services.fashion.search.by_text("jeans")] == [1]
    assert services.fashion.store.count() == 1
    construction.close.assert_not_called()


def test_missing_collection_is_not_created_and_search_stays_unavailable(construction, monkeypatch):
    ensure = Mock(wraps=construction.store.ensure_collection)
    monkeypatch.setattr(construction.store, "ensure_collection", ensure)

    services = service_module.build_services(construction.settings)

    assert not services.fashion.store.exists()
    ensure.assert_not_called()
    with TestClient(create_app(services)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["indexed_products"] == 0
        response = client.post("/search/text", json={"query": "jeans"})
        assert response.status_code == 503
        assert "scripts.build_index" in response.json()["detail"]
    assert not services.fashion.store.exists()
    construction.close.assert_not_called()


@pytest.mark.parametrize("stage", ["encoder_constructor", "encoder_load", "shoppers", "graph"])
def test_initialization_failure_closes_owned_store(construction, monkeypatch, stage):
    error = RuntimeError("synthetic initialization failure")
    if stage == "encoder_constructor":
        service_module.ClipEncoder.side_effect = error
    elif stage == "encoder_load":
        construction.encoder.load.side_effect = error
    elif stage == "shoppers":
        service_module.load_shopper_purchases.side_effect = error
    else:
        monkeypatch.setattr(service_module, "GraphRecommender", Mock(side_effect=error))

    with pytest.raises(RuntimeError, match="synthetic initialization failure") as raised:
        service_module.build_services(construction.settings)

    assert raised.value is error
    construction.close.assert_called_once_with()


@pytest.mark.parametrize("version,width", [(1, 10), (2, 15)])
def test_service_loads_versioned_ranker(construction, version, width):
    from clothes_recommender.recommenders.learned import RankerModel

    model = RankerModel(
        feature_version=version,
        weights=[0.0] * width,
        trained_through=date(2020, 8, 31),
        training_window_ends=[date(2020, 8, 17), date(2020, 8, 24), date(2020, 8, 31)],
        training_rows=10,
        training_positives=1,
    )
    construction.settings.ranker_path.write_text(model.model_dump_json())
    services = service_module.build_services(construction.settings)
    assert services.fashion.learned is not None
    assert services.fashion.learned.recommend(1)


def test_prepared_cocktails_enable_the_cocktail_domain(construction):
    from tests.conftest import cocktail_frame

    cocktail_frame().to_parquet(construction.settings.cocktails_path)

    services = service_module.build_services(construction.settings)

    assert len(services.cocktails.catalog) == 8
    assert services.cocktails.recommender.similar(1, top_k=1)
    assert services.cocktails.assistant is None  # no LLM configured


def test_missing_cocktails_disable_only_that_domain(construction, caplog):
    services = service_module.build_services(construction.settings)

    assert services.cocktails is None
    assert services.fashion is not None
    assert "scripts.prepare_cocktails" in caplog.text
    with TestClient(create_app(services)) as client:
        assert client.get("/cocktails/categories").status_code == 503
        assert client.get("/health").json()["cocktails_enabled"] is False


def test_broken_cocktail_file_does_not_break_fashion(construction, caplog):
    construction.settings.cocktails_path.write_bytes(b"not a parquet file")

    services = service_module.build_services(construction.settings)

    assert services.cocktails is None
    assert "the cocktail domain is disabled" in caplog.text
    assert services.fashion is not None
    construction.close.assert_not_called()


def test_llm_builds_both_assistants_with_one_model_and_signer(construction, monkeypatch):
    from clothes_recommender.chat.cocktail_agent import CocktailAssistant
    from tests.chat.conftest import FakeToolCallingChatModel
    from tests.conftest import cocktail_frame

    cocktail_frame().to_parquet(construction.settings.cocktails_path)
    model = FakeToolCallingChatModel(messages=iter([]))
    build_model = Mock(return_value=model)
    monkeypatch.setattr(service_module, "build_chat_model", build_model)
    construction.settings.llm_configured = True
    construction.settings.agent_signing_key = None

    services = service_module.build_services(construction.settings)

    build_model.assert_called_once_with(construction.settings)
    assert isinstance(services.cocktails.assistant, CocktailAssistant)
    assert services.cocktails.assistant._signer is services.fashion.assistant._signer
