"""API end to end: real data, real CLIP index, real recommenders and the HTTP LLM path."""

import io
import json

import httpx
import pytest
from PIL import Image


@pytest.fixture(scope="module")
def api(api_url: str):
    with httpx.Client(base_url=api_url, timeout=120, trust_env=False) as client:
        yield client


def stream(api: httpx.Client, body: dict) -> list[dict]:
    with api.stream("POST", "/chat/stream", json=body) as response:
        assert response.status_code == 200
        return [json.loads(line) for line in response.iter_lines() if line.strip()]


def test_health_reports_both_prepared_domains(api):
    health = api.get("/health").json()
    assert health["catalog_products"] > 10_000
    assert health["indexed_products"] > 1_000
    assert health["chat_enabled"] and health["cocktails_enabled"]
    assert health["cocktail_count"] == 425
    domains = {d["id"]: d for d in api.get("/domains").json()}
    assert domains["fashion"]["chat_enabled"] and domains["cocktails"]["chat_enabled"]


def test_clip_text_search_understands_garments(api):
    hits = api.post("/search/text", json={"query": "black dress", "top_k": 10}).json()
    assert len(hits) == 10
    dresses = [h for h in hits if "dress" in h["product"]["product_type"].lower()]
    assert len(dresses) >= 6, [h["product"]["product_type"] for h in hits]
    assert sum(h["product"]["colour"] == "Black" for h in hits) >= 6


def test_image_search_finds_the_photographed_product(api):
    hit = api.post("/search/text", json={"query": "striped t-shirt", "top_k": 1}).json()[0]
    pid = hit["product"]["product_id"]
    photo = api.get(f"/products/{pid}/image")
    assert photo.headers["content-type"] == "image/jpeg"
    found = api.post("/search/image", files={"file": ("q.jpg", photo.content)}).json()
    assert found[0]["product"]["product_id"] == pid
    assert found[0]["score"] > 0.99


def test_corrupt_upload_is_rejected(api):
    response = api.post("/search/image", files={"file": ("q.jpg", b"not an image")})
    assert response.status_code == 400
    tiny = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(tiny, format="PNG")
    assert api.post("/search/image", files={"file": ("q.png", tiny.getvalue())}).is_success


@pytest.mark.parametrize("strategy", ["learned", "repeat", "collaborative", "content", "graph"])
def test_every_strategy_recommends_with_evidence(api, strategy):
    if strategy == "learned" and not api.get("/health").json()["ranking_enabled"]:
        pytest.skip("No trained ranker in this data directory")
    customer = api.get("/demo/customers").json()[0]["user_id"]
    hits = api.get(
        f"/users/{customer}/recommendations", params={"strategy": strategy, "top_k": 12}
    ).json()
    assert len(hits) == 12
    assert len({h["product"]["product_id"] for h in hits}) == 12
    if strategy != "content":
        assert all(h["reasons"] for h in hits)


def test_fashion_turn_streams_steps_and_grounded_answer(api):
    events = stream(api, {"message": "Black trousers under €30"})
    steps = [e["step"]["name"] for e in events if e["type"] == "step"]
    reply = events[-1]["reply"]
    assert steps[:2] == ["Understand request", "SearchCatalog"]
    assert reply["products"] and all(p["price"] <= 30 for p in reply["products"])
    assert reply["grounding"]["status"] == "verified"
    assert reply["suggestions"]


def test_invented_price_is_repaired_before_the_shopper_sees_it(api):
    first = stream(api, {"message": "Black trousers under €30"})[-1]["reply"]
    history = [
        {"role": "user", "content": "Black trousers under €30"},
        {
            "role": "assistant",
            "content": first["text"],
            "product_ids": [p["product_id"] for p in first["products"][:5]],
        },
    ]
    reply = stream(api, {"message": "Which is the best deal?", "history": history})[-1]["reply"]
    assert reply["grounding"]["status"] == "repaired"
    assert "€" in reply["text"]
    names = [
        s["step"]["name"]
        for s in stream(api, {"message": "Which is the best deal?", "history": history})
        if s["type"] == "step"
    ]
    assert "Self-correction" in names


def test_approval_round_trip_and_tampering(api):
    first = stream(api, {"message": "White sneakers"})[-1]["reply"]
    ids = [p["product_id"] for p in first["products"][:5]]
    history = [
        {"role": "user", "content": "White sneakers"},
        {"role": "assistant", "content": first["text"], "product_ids": ids},
    ]
    proposal = api.post("/chat", json={"message": "Save the first two", "history": history}).json()
    assert proposal["status"] == "needs_approval"
    action = proposal["pending_action"]
    widened = {**action, "args": {"product_ids": [*action["args"]["product_ids"], 1]}}
    forged = api.post("/chat", json={"decision": {"action": widened, "approved": True}})
    assert forged.status_code == 409
    keep = action["args"]["product_ids"][:1]
    done = api.post(
        "/chat",
        json={
            "decision": {"action": action, "approved": True, "edited_args": {"product_ids": keep}},
            "history": history,
        },
    ).json()
    assert done["applied"][0]["product_ids"] == keep


def test_personal_picks_use_the_linked_history(api):
    customer = api.get("/demo/customers").json()[0]["user_id"]
    reply = api.post(
        "/chat", json={"message": "Picks from my shopping history", "shopper_id": customer}
    ).json()
    assert any(s["name"] == "PersonalPicks" and s["status"] == "ok" for s in reply["steps"])
    assert reply["products"]


def test_cocktail_endpoints_on_the_real_catalog(api):
    hits = api.post("/cocktails/search", json={"query": "margarita"}).json()
    assert hits[0]["cocktail"]["name"] == "Margarita"
    free = api.post(
        "/cocktails/search", json={"query": "fruity", "alcohol": "non_alcoholic"}
    ).json()
    assert free and all(h["cocktail"]["alcoholic"] == "Non alcoholic" for h in free)
    pantry = api.post(
        "/cocktails/pantry", json={"ingredients": ["gin", "tonic water", "lime"]}
    ).json()
    assert pantry and pantry[0]["coverage"] > 0.5
    similar = api.get(f"/cocktails/{hits[0]['cocktail']['drink_id']}/similar").json()
    assert similar and all(h["reasons"] for h in similar)


def test_cocktail_turn_and_cross_domain_replay(api):
    reply = api.post(
        "/chat",
        json={"message": "What can I make with gin, tonic and lime?", "domain": "cocktails"},
    ).json()
    assert reply["domain"] == "cocktails" and reply["cocktails"]
    assert any(s["name"] == "WhatCanIMake" for s in reply["steps"])
    history = [
        {"role": "user", "content": "What can I make with gin, tonic and lime?"},
        {
            "role": "assistant",
            "content": reply["text"],
            "drink_ids": [c["drink_id"] for c in reply["cocktails"][:5]],
        },
    ]
    proposal = api.post(
        "/chat", json={"message": "Save it", "domain": "cocktails", "history": history}
    ).json()
    action = proposal["pending_action"]
    assert action["tool"] == "SaveCocktails"
    replayed = api.post("/chat", json={"decision": {"action": action, "approved": True}})
    assert replayed.status_code == 409  # a cocktail proposal can't be applied as fashion
    done = api.post(
        "/chat",
        json={"domain": "cocktails", "decision": {"action": action, "approved": True}},
    ).json()
    assert done["applied"][0]["kind"] == "cocktail_shortlist"


def test_feedback_is_recorded(api):
    rating = {"rating": "up", "message": "jeans", "answer": "Try these.", "tools": ["Search"]}
    assert api.post("/chat/feedback", json=rating).status_code == 204
    assert api.get("/chat/feedback").status_code == 405  # nothing serves feedback back
