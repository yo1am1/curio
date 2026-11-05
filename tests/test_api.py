import io
import json
from collections.abc import Iterator
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from clothes_recommender.api import create_app
from clothes_recommender.catalog import CustomerData, ProductCatalog
from clothes_recommender.chat.assistant import AssistantReply
from clothes_recommender.chat.intent import SearchIntent
from clothes_recommender.recommenders.collaborative import CollaborativeRecommender
from clothes_recommender.recommenders.content_based import ContentBasedRecommender
from clothes_recommender.recommenders.graph import GraphRecommender
from clothes_recommender.recommenders.popularity import PopularityRecommender
from clothes_recommender.recommenders.repeat import RepeatPurchaseRecommender
from clothes_recommender.search import ProductSearch
from clothes_recommender.services import FashionServices, Services
from tests.conftest import serve, with_cocktails, with_fashion


class EchoAssistant:
    def reply(self, message, history, context=None) -> AssistantReply:
        return AssistantReply(
            text=f"echo: {message}", products=[], intent=SearchIntent(query=message)
        )


class BrokenAssistant:
    def reply(self, message, history, context=None) -> AssistantReply:
        raise ConnectionError("gateway down")


@pytest.fixture
def services(sample_catalog, vector_store, fake_encoder, tmp_path) -> Services:
    users = pd.DataFrame({"user_id": [1, 2], "age": [34, 27]})
    purchases = pd.DataFrame(
        [(1, 1, date(2020, 9, 2)), (2, 1, date(2020, 9, 3)), (2, 3, date(2020, 9, 4))],
        columns=["user_id", "product_id", "purchase_date"],
    )
    Image.new("RGB", (4, 6), "red").save(tmp_path / "3.jpg")
    customers = CustomerData(users, purchases)
    popularity = PopularityRecommender(customers.purchases)
    content_based = ContentBasedRecommender(customers, sample_catalog, vector_store, popularity)
    # The sampled customers double as the whole crowd in this tiny fixture.
    crowd = purchases.rename(columns={"user_id": "shopper_id"})
    collaborative = CollaborativeRecommender(customers, sample_catalog, crowd, popularity)
    fashion = FashionServices(
        catalog=sample_catalog,
        customers=customers,
        store=vector_store,
        search=ProductSearch(fake_encoder, vector_store),
        content_based=content_based,
        collaborative=collaborative,
        graph=GraphRecommender(
            customers,
            sample_catalog,
            popularity,
            pd.DataFrame([(1, 3, 2, 1.5)], columns=["product_id", "other_id", "count", "lift"]),
        ),
        repeat=RepeatPurchaseRecommender(customers, popularity),
        images_dir=tmp_path,
        assistant=None,
    )
    return serve(fashion)


@pytest.fixture
def client(services) -> Iterator[TestClient]:
    with TestClient(create_app(services)) as client:
        yield client


def test_health_reports_index_size_and_chat_status(client) -> None:
    from clothes_recommender.api import CHAT_DISABLED
    from clothes_recommender.services import COCKTAILS_MISSING

    assert client.get("/health").json() == {
        "status": "ok",
        "indexed_products": 6,
        "catalog_products": 6,
        "chat_enabled": False,
        "ranking_enabled": False,
        "fashion_enabled": True,
        "cocktails_enabled": False,
        "cocktail_count": 0,
        "llm_configured": False,
        "domains": [
            {
                "id": "fashion",
                "label": "Fashion",
                "enabled": True,
                "chat_enabled": False,
                "item_count": 6,
                "hint": CHAT_DISABLED,
            },
            {
                "id": "cocktails",
                "label": "Cocktails",
                "enabled": False,
                "chat_enabled": False,
                "item_count": 0,
                "hint": COCKTAILS_MISSING,
            },
        ],
    }


def test_get_product_and_missing_product(client) -> None:
    assert client.get("/products/3").json()["name"] == "Floral Summer Dress"
    assert client.get("/products/999").status_code == 404


def test_product_image_is_served_from_images_dir(client) -> None:
    response = client.get("/products/3/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert client.get("/products/1/image").status_code == 404


def test_text_search_returns_scored_products(client) -> None:
    hits = client.post("/search/text", json={"query": "Floral Summer Dress", "top_k": 2}).json()
    assert len(hits) == 2
    assert hits[0]["product"]["product_id"] == 3


def test_image_search_accepts_images_and_rejects_other_files(client) -> None:
    png = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(png, format="PNG")
    response = client.post("/search/image", files={"file": ("red.png", png.getvalue())})
    assert response.status_code == 200
    assert len(response.json()) > 0

    response = client.post("/search/image", files={"file": ("notes.txt", b"not an image")})
    assert response.status_code == 400

    truncated = png.getvalue()[:-20]
    response = client.post("/search/image", files={"file": ("cut.png", truncated)})
    assert response.status_code == 400


def test_user_profile_lists_purchases_with_products(client) -> None:
    assert client.get("/users").json() == [1, 2]
    profile = client.get("/users/2").json()
    assert [p["product"]["product_id"] for p in profile["purchases"]] == [3, 1]
    assert client.get("/users/404").status_code == 404


def test_demo_examples_are_real_and_leave_all_users_available(client) -> None:
    users_before = client.get("/users").json()
    response = client.get("/demo/customers")
    assert response.status_code == 200
    examples = response.json()
    ids = [example["user_id"] for example in examples]
    # The fixture's customers are too small to qualify; tests/test_demo.py covers selection.
    assert len(ids) <= 10
    assert len(ids) == len(set(ids))
    assert set(ids) <= set(users_before)
    for example in examples:
        assert example["label"] and example["description"]
        profile = client.get(f"/users/{example['user_id']}").json()
        assert example["focus_product_id"] in {
            purchase["product"]["product_id"] for purchase in profile["purchases"]
        }
    assert client.get("/users").json() == users_before
    assert client.get("/demo/customers").json() == examples


@pytest.mark.parametrize("strategy", ["content", "collaborative", "graph"])
def test_recommendations_exclude_purchases(client, strategy) -> None:
    response = client.get("/users/1/recommendations", params={"strategy": strategy, "top_k": 3})
    assert response.status_code == 200
    ids = [hit["product"]["product_id"] for hit in response.json()]
    assert ids and 1 not in ids


def test_collaborative_recommends_what_shoppers_bought_together_with_reasons(client) -> None:
    hits = client.get("/users/1/recommendations", params={"strategy": "collaborative"}).json()
    # Shopper 2 bought product 1, like user 1, and also product 3.
    assert hits[0]["product"]["product_id"] == 3
    assert hits[0]["reasons"] == ["1 shopper who bought Slim Jeans (blue) also bought this."]
    assert client.get("/users/1/similar").status_code == 404  # replaced by per-hit reasons


def test_graph_recommendations_include_evidence_and_reject_unknown_users(client) -> None:
    hits = client.get("/users/1/recommendations", params={"strategy": "graph"}).json()
    linked = next(hit for hit in hits if hit["product"]["product_id"] == 3)
    assert linked["reasons"]
    assert client.get("/users/404/recommendations", params={"strategy": "graph"}).status_code == 404


def test_graph_neighborhood_has_valid_edges_and_bounded_requests(client) -> None:
    response = client.get("/products/1/graph", params={"top_k": 2})
    assert response.status_code == 200
    graph = response.json()
    ids = {node["id"] for node in graph["nodes"]}
    assert "product:1" in ids
    assert graph["edges"]
    assert all(edge["source"] in ids and edge["target"] in ids for edge in graph["edges"])
    assert client.get("/products/404/graph").status_code == 404
    assert client.get("/products/1/graph", params={"top_k": 51}).status_code == 422


@pytest.mark.parametrize("kind", ["complement", "alternative"])
def test_related_products_validate_products_and_relation(client, kind) -> None:
    response = client.get("/products/1/related", params={"kind": kind})
    assert response.status_code == 200
    assert all(hit["product"]["product_id"] != 1 for hit in response.json())
    assert client.get("/products/404/related", params={"kind": kind}).status_code == 404
    assert client.get("/products/1/related", params={"kind": "anything"}).status_code == 422


def test_chat_is_503_without_llm(client) -> None:
    assert client.post("/chat", json={"message": "hi"}).status_code == 503


def test_chat_returns_assistant_reply(services) -> None:
    with TestClient(create_app(with_fashion(services, assistant=EchoAssistant()))) as client:
        response = client.post("/chat", json={"message": "hi", "history": []})
        assert response.json()["text"] == "echo: hi"


def test_chat_maps_provider_failure_to_502(services) -> None:
    with TestClient(create_app(with_fashion(services, assistant=BrokenAssistant()))) as client:
        response = client.post("/chat", json={"message": "hi"})
        assert response.status_code == 502
        assert "gateway down" not in response.json()["detail"]


def test_text_search_can_be_limited_to_a_section(client) -> None:
    assert client.get("/sections").json() == ["Ladieswear", "Menswear", "Sport"]
    hits = client.post(
        "/search/text", json={"query": "Floral Summer Dress", "section": "Menswear"}
    ).json()
    assert hits and {hit["product"]["section"] for hit in hits} == {"Menswear"}


def test_similar_products_exclude_the_product_itself(services, sample_products) -> None:
    unindexed = sample_products[0].model_copy(update={"product_id": 7})
    catalog = ProductCatalog(pd.DataFrame([p.model_dump() for p in [*sample_products, unindexed]]))
    with TestClient(create_app(with_fashion(services, catalog=catalog))) as client:
        hits = client.get("/products/3/similar", params={"top_k": 3}).json()
        assert len(hits) == 3
        assert 3 not in {hit["product"]["product_id"] for hit in hits}
        assert client.get("/products/404/similar").status_code == 404
        response = client.get("/products/7/similar")
        assert response.status_code == 404
        assert "image index" in response.json()["detail"]


def test_query_photos_are_listed_and_served(services, tmp_path) -> None:
    photos = tmp_path / "query_photos"
    photos.mkdir()
    Image.new("RGB", (4, 6), "blue").save(photos / "900000001.jpg")
    labels = [
        {"photo_id": 900000001, "name": "Denim jacket", "product_type": "Jacket", "colour": "Blue"}
    ]
    (photos / "query_photos.json").write_text(json.dumps(labels))
    with TestClient(create_app(with_fashion(services, query_photos_dir=photos))) as client:
        assert client.get("/search/examples").json() == labels
        image = client.get("/search/examples/900000001/image")
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/jpeg"
        assert client.get("/search/examples/1/image").status_code == 404


def test_query_photos_are_empty_until_fetched(client) -> None:
    assert client.get("/search/examples").json() == []
    assert client.get("/search/examples/1/image").status_code == 404


def test_evaluation_is_served_when_present(services, tmp_path) -> None:
    path = tmp_path / "evaluation.json"
    with TestClient(create_app(with_fashion(services, evaluation_path=path))) as client:
        missing = client.get("/evaluation")
        assert missing.status_code == 404
        assert "scripts.evaluate" in missing.json()["detail"]
        path.write_text(json.dumps({"top_k": 12, "results": {}}))
        assert client.get("/evaluation").json() == {"top_k": 12, "results": {}}


def test_repeat_strategy_includes_purchase_and_grounded_reason(client):
    response = client.get("/users/1/recommendations", params={"strategy": "repeat", "top_k": 3})
    assert response.status_code == 200
    assert response.json()[0]["product"]["product_id"] == 1
    assert response.json()[0]["reasons"] == ["Buy again: last purchased on 2020-09-02."]
    assert (
        client.get("/users/404/recommendations", params={"strategy": "repeat"}).status_code == 404
    )


def test_learned_strategy_requires_model_and_returns_grounded_hits(services):
    import numpy as np

    from clothes_recommender.recommenders.learned import FEATURES, LearnedRecommender

    with TestClient(create_app(services)) as client:
        assert (
            client.get("/users/1/recommendations", params={"strategy": "learned"}).status_code
            == 503
        )
    ranker = LearnedRecommender(
        services.fashion.customers,
        services.fashion.catalog,
        services.fashion.collaborative,
        PopularityRecommender(services.fashion.customers.purchases),
        np.zeros(len(FEATURES)),
        date(2020, 9, 4),
    )
    with TestClient(create_app(with_fashion(services, learned=ranker))) as client:
        assert client.get("/health").json()["ranking_enabled"]
        hits = client.get("/users/1/recommendations", params={"strategy": "learned"}).json()
        assert hits and all(hit["reasons"] for hit in hits)
        assert (
            client.get("/users/404/recommendations", params={"strategy": "learned"}).status_code
            == 404
        )


def test_chat_returns_retryable_rate_limit_without_provider_details(services):
    class RateLimitedError(RuntimeError):
        status_code = 429

    class RateLimitedAssistant:
        def reply(self, message, history, context=None):
            raise RateLimitedError("private provider details")

    with TestClient(create_app(with_fashion(services, assistant=RateLimitedAssistant()))) as client:
        response = client.post("/chat", json={"message": "hi"})
    assert response.status_code == 429
    assert response.json() == {"detail": "The assistant is busy. Please try again shortly."}


def _agent_services(services, *replies):
    from langchain_core.messages import AIMessage

    from clothes_recommender.chat.assistant import ShoppingAssistant
    from tests.chat.conftest import FakeToolCallingChatModel

    model = FakeToolCallingChatModel(messages=iter([AIMessage(**reply) for reply in replies]))
    assistant = ShoppingAssistant(model, services.fashion.search, services.fashion.catalog)
    return with_fashion(services, assistant=assistant)


def _intent(**args):
    return {
        "content": "",
        "tool_calls": [{"name": "SearchIntent", "args": {"query": "", **args}, "id": "i"}],
    }


def test_chat_stream_sends_steps_then_reply(services):
    agent = _agent_services(services, _intent(wants_products=False), {"content": "Hello!"})
    with TestClient(create_app(agent)) as client:
        response = client.post("/chat/stream", json={"message": "hi"})
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["step", "step", "reply"]
    assert events[0]["step"]["name"] == "Understand request"
    assert events[-1]["reply"]["text"] == "Hello!"


def test_chat_stream_reports_provider_errors_as_safe_events(services):
    with TestClient(
        create_app(with_fashion(services, assistant=BrokenStreamAssistant()))
    ) as client:
        response = client.post("/chat/stream", json={"message": "hi"})
    (event,) = [json.loads(line) for line in response.text.splitlines()]
    assert event == {
        "type": "error",
        "status": 502,
        "detail": "LLM provider request failed; see server logs.",
    }


class BrokenStreamAssistant:
    def stream(self, message, history, context=None, *, decision=None):
        raise ConnectionError("gateway down")
        yield


def test_chat_approval_round_trip_and_tamper_rejection(services):
    proposal = {
        "content": "",
        "tool_calls": [{"name": "SaveToShortlist", "args": {"product_ids": [1]}, "id": "t"}],
    }
    agent = _agent_services(
        services, _intent(wants_products=False), proposal, {"content": "Saved it."}
    )
    with TestClient(create_app(agent)) as client:
        reply = client.post("/chat", json={"message": "save it"}).json()
        assert reply["status"] == "needs_approval"
        pending = reply["pending_action"]
        forged = {**pending, "args": {"product_ids": [2]}}
        rejected = client.post("/chat", json={"decision": {"action": forged, "approved": True}})
        assert rejected.status_code == 409
        done = client.post("/chat", json={"decision": {"action": pending, "approved": True}})
        assert done.json()["applied"] == [
            {"kind": "shortlist", "product_ids": [1], "preferences": None}
        ]
        assert client.post("/chat", json={"message": " "}).status_code == 422


def test_chat_feedback_is_recorded_and_summarized(client):
    rating = {"rating": "down", "message": "jeans", "answer": "Try these.", "tools": ["Search"]}
    assert client.post("/chat/feedback", json=rating).status_code == 204
    assert client.post("/chat/feedback", json={**rating, "rating": "up"}).status_code == 204
    summary = client.get("/chat/feedback").json()
    assert (summary["up"], summary["down"]) == (1, 1)
    assert summary["recent_down"][0]["message"] == "jeans"


# --- Cocktail domain ---------------------------------------------------------------------

COCKTAILS_MISSING = "Cocktail recipes are not prepared: run scripts.prepare_cocktails."


@pytest.fixture
def cocktail_services(services, cocktail_catalog) -> Services:
    from clothes_recommender.cocktails.recommend import CocktailRecommender
    from clothes_recommender.services import CocktailServices

    cocktails = CocktailServices(cocktail_catalog, CocktailRecommender(cocktail_catalog))
    return serve(services.fashion, cocktails)


@pytest.fixture
def cocktail_client(cocktail_services) -> Iterator[TestClient]:
    with TestClient(create_app(cocktail_services)) as client:
        yield client


def _tool_call(name, args, call_id="t"):
    return {"content": "", "tool_calls": [{"name": name, "args": args, "id": call_id}]}


def _cocktail_agent_services(cocktail_services, *replies, signer=None):
    from langchain_core.messages import AIMessage

    from clothes_recommender.chat.cocktail_agent import CocktailAssistant
    from tests.chat.conftest import FakeToolCallingChatModel

    model = FakeToolCallingChatModel(messages=iter([AIMessage(**reply) for reply in replies]))
    assistant = CocktailAssistant(
        model,
        cocktail_services.cocktails.catalog,
        cocktail_services.cocktails.recommender,
        signer=signer,
    )
    return with_cocktails(cocktail_services, assistant=assistant)


def test_domains_and_health_report_the_cocktail_domain(client, cocktail_services) -> None:
    assert [
        (d["id"], d["label"], d["enabled"], d["chat_enabled"])
        for d in client.get("/domains").json()
    ] == [("fashion", "Fashion", True, False), ("cocktails", "Cocktails", False, False)]
    agent = _cocktail_agent_services(cocktail_services)
    with TestClient(create_app(agent)) as cocktails:
        health = cocktails.get("/health").json()
        assert (health["cocktails_enabled"], health["cocktail_count"]) == (True, 8)
        assert cocktails.get("/domains").json()[1] == {
            "id": "cocktails",
            "label": "Cocktails",
            "enabled": True,
            "chat_enabled": True,
            "item_count": 8,
            "hint": None,
        }
        assert health["domains"] == cocktails.get("/domains").json()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/cocktails/ingredients", None),
        ("GET", "/cocktails/categories", None),
        ("GET", "/cocktails/1", None),
        ("GET", "/cocktails/1/similar", None),
        ("POST", "/cocktails/search", {"query": "mojito"}),
        ("POST", "/cocktails/pantry", {"ingredients": ["lime"]}),
        ("POST", "/chat", {"message": "a mojito", "domain": "cocktails"}),
        ("POST", "/chat/stream", {"message": "a mojito", "domain": "cocktails"}),
    ],
)
def test_cocktail_endpoints_are_503_until_recipes_are_prepared(client, method, path, body):
    response = client.request(method, path, json=body)
    assert response.status_code == 503
    assert response.json() == {"detail": COCKTAILS_MISSING}


def test_cocktail_chat_is_503_without_llm(cocktail_client) -> None:
    response = cocktail_client.post("/chat", json={"message": "hi", "domain": "cocktails"})
    assert response.status_code == 503
    assert "LLM_MODEL" in response.json()["detail"]


def test_cocktail_lookups(cocktail_client) -> None:
    ingredients = cocktail_client.get("/cocktails/ingredients").json()
    assert ingredients[:3] == ["Espresso", "Gin", "Ginger ale"]
    assert cocktail_client.get("/cocktails/categories").json() == [
        "Cocktail",
        "Ordinary Drink",
        "Soft Drink",
    ]
    mojito = cocktail_client.get("/cocktails/1").json()
    assert mojito["name"] == "Mojito"
    assert mojito["ingredients"][0] == {"name": "Light rum", "measure": "2 oz"}
    assert mojito["source_url"] == "https://www.thecocktaildb.com/"
    assert cocktail_client.get("/cocktails/999").status_code == 404
    assert cocktail_client.get("/cocktails/abc").status_code == 422


def test_similar_cocktails_validate_drink_and_parameters(cocktail_client) -> None:
    hits = cocktail_client.get("/cocktails/1/similar", params={"top_k": 2}).json()
    assert len(hits) == 2 and all(hit["cocktail"]["drink_id"] != 1 for hit in hits)
    assert hits[0]["reasons"][0].startswith("Shares ")
    free = cocktail_client.get("/cocktails/1/similar", params={"alcohol": "non_alcoholic"}).json()
    assert [hit["cocktail"]["drink_id"] for hit in free] == [3, 6]
    assert cocktail_client.get("/cocktails/999/similar").status_code == 404
    assert cocktail_client.get("/cocktails/1/similar", params={"top_k": 51}).status_code == 422
    assert cocktail_client.get("/cocktails/1/similar", params={"alcohol": "x"}).status_code == 422


def test_cocktail_search_and_its_validation(cocktail_client) -> None:
    hits = cocktail_client.post(
        "/cocktails/search", json={"query": "mojito", "alcohol": "non_alcoholic"}
    ).json()
    assert [hit["cocktail"]["drink_id"] for hit in hits] == [3]
    assert "Alcohol-free" in hits[0]["reasons"]
    for body in (
        {"query": "x" * 201},
        {"query": "mojito", "top_k": 51},
        {"query": "mojito", "alcohol": "sometimes"},
        {"ingredients": [f"i{n}" for n in range(11)]},
    ):
        assert cocktail_client.post("/cocktails/search", json=body).status_code == 422


def test_cocktail_pantry_and_its_validation(cocktail_client) -> None:
    body = {"ingredients": ["lime", "mint", "sugar", "soda"], "max_missing": 0}
    (match,) = cocktail_client.post("/cocktails/pantry", json=body).json()
    assert match["cocktail"]["name"] == "Virgin Mojito"
    assert match["missing"] == [] and match["coverage"] == 1.0
    assert cocktail_client.post("/cocktails/pantry", json={"ingredients": []}).json() == []
    for invalid in (
        {"ingredients": ["lime"], "max_missing": 4},
        {"ingredients": ["lime"], "top_k": 0},
        {"ingredients": [""]},
        {"ingredients": ["x"] * 31},
        {},
    ):
        assert cocktail_client.post("/cocktails/pantry", json=invalid).status_code == 422


def test_chat_rejects_unknown_domains(cocktail_client) -> None:
    response = cocktail_client.post("/chat", json={"message": "hi", "domain": "wine"})
    assert response.status_code == 422


def test_cocktail_chat_stream_sends_steps_then_reply(cocktail_services) -> None:
    agent = _cocktail_agent_services(
        cocktail_services,
        _tool_call("SearchCocktails", {"query": "mojito", "alcohol": "non_alcoholic"}),
        {"content": "Try the Virgin Mojito (alcohol-free)."},
    )
    with TestClient(create_app(agent)) as client:
        response = client.post("/chat/stream", json={"message": "mojito", "domain": "cocktails"})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["step"] * 4 + ["reply"]
    assert events[1]["step"]["name"] == "SearchCocktails"
    reply = events[-1]["reply"]
    assert reply["domain"] == "cocktails" and reply["products"] == []
    assert [c["drink_id"] for c in reply["cocktails"]] == [3]
    assert reply["grounding"]["status"] == "verified"


def test_cocktail_approval_round_trip_and_cross_domain_replay(cocktail_services) -> None:
    from langchain_core.messages import AIMessage

    from clothes_recommender.chat.assistant import ShoppingAssistant
    from clothes_recommender.chat.guardrails import ActionSigner
    from tests.chat.conftest import FakeToolCallingChatModel

    signer = ActionSigner(b"k" * 32)
    agent = _cocktail_agent_services(
        cocktail_services,
        _tool_call("SaveCocktails", {"drink_ids": [1, 3]}),
        {"content": "Saved the Mojito."},
        signer=signer,
    )
    fashion_model = FakeToolCallingChatModel(messages=iter([AIMessage(**_intent())]))
    agent = with_fashion(
        agent,
        assistant=ShoppingAssistant(
            fashion_model, agent.fashion.search, agent.fashion.catalog, signer=signer
        ),
    )
    with TestClient(create_app(agent)) as client:
        reply = client.post("/chat", json={"message": "save both", "domain": "cocktails"}).json()
        assert reply["status"] == "needs_approval"
        pending = reply["pending_action"]
        assert pending["tool"] == "SaveCocktails"
        assert [c["drink_id"] for c in pending["cocktails"]] == [1, 3]

        decision = {"action": pending, "approved": True, "edited_args": {"drink_ids": [1]}}
        replayed = client.post("/chat", json={"decision": decision})  # default domain: fashion
        assert replayed.status_code == 409
        assert replayed.json()["detail"] == "This proposal belongs to a different assistant."
        relabelled = {**decision, "action": {**pending, "tool": "SaveToShortlist"}}
        assert client.post("/chat", json={"decision": relabelled}).status_code == 409
        widened = {**decision, "edited_args": {"drink_ids": [1, 2]}}
        assert (
            client.post("/chat", json={"decision": widened, "domain": "cocktails"}).status_code
            == 409
        )

        done = client.post("/chat", json={"decision": decision, "domain": "cocktails"}).json()
        assert done["applied"] == [
            {"kind": "cocktail_shortlist", "drink_ids": [1], "cocktail_preferences": None}
        ]
        assert done["text"] == "Saved the Mojito."


def test_chat_accepts_cocktail_context_and_history(services) -> None:
    class RecordingAssistant:
        def __init__(self):
            self.calls = []

        def reply(self, message, history, context=None):
            self.calls.append((history, context))
            return AssistantReply(text="ok", products=[], intent=SearchIntent(query=message))

    assistant = RecordingAssistant()
    with TestClient(create_app(with_fashion(services, assistant=assistant))) as client:
        body = {
            "message": "hi",
            "history": [{"role": "assistant", "content": "Here", "drink_ids": [1, 2]}],
            "cocktail_preferences": {"alcohol_free": True, "avoided_ingredients": ["Gin"]},
            "cocktail_shortlist": [1],
            "auto_approve": ["SaveCocktails", "RememberTastes"],
        }
        assert client.post("/chat", json=body).status_code == 200
        too_many = {**body, "cocktail_shortlist": list(range(1, 22))}
        assert client.post("/chat", json=too_many).status_code == 422
    ((history, context),) = assistant.calls
    assert history[0].drink_ids == [1, 2]
    assert context.cocktail_preferences.alcohol_free
    assert context.cocktail_shortlist == [1]
    assert context.auto_approve == ["SaveCocktails", "RememberTastes"]


def test_malformed_cocktail_decisions_are_409_not_502(cocktail_services) -> None:
    from clothes_recommender.chat.guardrails import ActionSigner

    signer = ActionSigner(b"k" * 32)
    agent = _cocktail_agent_services(
        cocktail_services, _tool_call("SaveCocktails", {"drink_ids": [1, 3]}), signer=signer
    )
    with TestClient(create_app(agent)) as client:
        pending = client.post("/chat", json={"message": "save", "domain": "cocktails"}).json()[
            "pending_action"
        ]
        unhashable = {"action": pending, "approved": True, "edited_args": {"drink_ids": [{"x": 1}]}}
        non_ascii = {"action": {**pending, "signature": "é"}, "approved": True}
        for decision in (unhashable, non_ascii):
            body = {"decision": decision, "domain": "cocktails"}
            response = client.post("/chat", json=body)
            assert response.status_code == 409
            (event,) = [
                json.loads(line)
                for line in client.post("/chat/stream", json=body).text.splitlines()
            ]
            assert event["type"] == "error" and event["status"] == 409


def test_zero_denominator_measure_does_not_fail_the_cocktail_turn(cocktail_services) -> None:
    agent = _cocktail_agent_services(
        cocktail_services,
        _tool_call("SearchCocktails", {"query": "mojito"}),
        {"content": "Try the Mojito [id 1]."},
    )
    with TestClient(create_app(agent)) as client:
        body = {"message": "I have 1/00 oz of rum", "domain": "cocktails"}
        response = client.post("/chat", json=body)
    assert response.status_code == 200
    assert response.json()["grounding"]["status"] == "verified"
