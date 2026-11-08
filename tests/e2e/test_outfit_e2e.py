"""Outfit builder end to end: the real H&M photo index and co-purchase graph through the API,
the agent (scripted LLM over HTTP) and the browser, with screenshots."""

import json
import re

import httpx
import pytest
from playwright.sync_api import expect

from curio.domains.fashion.outfits import SLOT_TYPES

OCCASIONS = [
    ({"occasion": "summer wedding", "budget": 80, "audience": "women"}, "Dress"),
    ({"occasion": "summer wedding", "budget": 80, "audience": "men"}, "Blazer"),
    ({"occasion": "office", "budget": 60, "audience": "women"}, "Blazer"),
    ({"occasion": "beach holiday", "budget": 40, "audience": "men"}, "Shorts"),
    ({"occasion": "gym", "budget": 50, "audience": "women"}, "Sneakers"),
    ({"occasion": "winter weekend", "budget": 90, "audience": "women"}, None),
]

pytestmark = pytest.mark.fashion


@pytest.fixture(scope="module")
def api(api_url: str):
    with httpx.Client(base_url=api_url, timeout=120, trust_env=False) as client:
        yield client


def stream(api: httpx.Client, body: dict) -> list[dict]:
    with api.stream("POST", "/chat/stream", json=body) as response:
        assert response.status_code == 200
        return [json.loads(line) for line in response.iter_lines() if line.strip()]


# --- API ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("body", "expected_type"), OCCASIONS)
def test_outfits_on_the_real_index_are_real_outfits(api, body, expected_type):
    outfit = api.post("/outfits", json=body).json()
    assert outfit["status"] == "complete", outfit
    pieces = [slot["product"] for slot in outfit["slots"]]
    assert 3 <= len(pieces) <= 4
    # One garment of the slot's kind per slot: never four pairs of trousers.
    for slot, product in zip(outfit["slots"], pieces, strict=True):
        assert product["product_type"] in SLOT_TYPES[slot["slot"]], (slot["slot"], product)
        assert slot["reason"]
    assert len({p["product_group"] for p in pieces}) >= 3
    assert len({p["product_type"] for p in pieces}) == len(pieces)
    assert outfit["total"] == round(sum(p["price"] for p in pieces), 2)
    assert outfit["total"] <= body["budget"]
    if expected_type:
        assert expected_type in {p["product_type"] for p in pieces}
    audience = {"women": {"Ladieswear", "Divided", "Sport"}, "men": {"Menswear", "Sport"}}
    assert {p["section"] for p in pieces} <= audience[body["audience"]]


def test_a_wearer_named_in_the_occasion_sets_the_audience(api):
    # Review repro: without an audience this used to plan a women's dress and heels.
    body = {"occasion": "wedding outfit for my husband", "budget": 150}
    outfit = api.post("/outfits", json=body).json()
    assert outfit["audience"] == "men", outfit
    sections = {slot["product"]["section"] for slot in outfit["slots"] if slot["product"]}
    assert sections and sections <= {"Menswear", "Sport"}
    assert not any("best match" in note for note in outfit["notes"])


def test_a_tight_budget_reports_which_slot_does_not_fit(api):
    body = {"occasion": "wedding", "budget": 8, "audience": "women"}
    outfit = api.post("/outfits", json=body).json()
    assert outfit["status"] == "partial" and outfit["total"] <= 8
    gaps = [slot for slot in outfit["slots"] if slot["product"] is None]
    assert gaps and gaps[0]["issue"].startswith("The budget cannot cover the")
    assert outfit["minimum"] > 8


def test_cheaper_wedding_shoes_stay_wedding_shoes_and_say_what_they_are(api):
    # Review repro: three "cheaper shoes" swaps ended at "Yaz flip flop (1)" for €1.99, with
    # the reason "Best match for “elegant heeled sandals”".
    body = {"occasion": "summer wedding", "budget": 80, "audience": "women"}
    outfit = api.post("/outfits", json=body).json()
    for _ in range(6):
        swap = {"outfit": outfit["state"], "slot": "shoes", "mode": "cheaper"}
        swapped = api.post("/outfits/swap", json=swap).json()
        shoes = next(s for s in swapped["slots"] if s["slot"] == "shoes")
        assert shoes["product"]["product_type"] not in ("Flip flop", "Sneakers"), shoes
        if shoes["product"]["product_type"] not in ("Sandals", "Heels"):
            assert not shoes["reason"].startswith("Best match"), shoes
        if swapped["state"] == outfit["state"]:
            break  # nothing cheaper suits a wedding
        outfit = swapped


def test_a_piece_of_another_type_than_asked_says_so(api):
    # Review repro: a men's "first date dinner" got a bracelet "Best match for “leather belt”".
    body = {"occasion": "first date dinner", "budget": 60, "audience": "men"}
    outfit = api.post("/outfits", json=body).json()
    accessory = next(s for s in outfit["slots"] if s["slot"] == "accessory")
    if accessory["product"] and accessory["product"]["product_type"] != "Belt":
        kind = accessory["product"]["product_type"].lower()
        assert re.search(rf"\b{re.escape(kind)}\b.*not a belt", accessory["reason"]), accessory


def test_swaps_stay_within_the_budget_and_never_repeat(api):
    body = {"occasion": "summer wedding", "budget": 80, "audience": "women"}
    outfit = api.post("/outfits", json=body).json()
    shoes = {s["slot"]: s for s in outfit["slots"]}["shoes"]["product"]
    cheaper = api.post(
        "/outfits/swap", json={"outfit": outfit["state"], "slot": "shoes", "mode": "cheaper"}
    ).json()
    new = {s["slot"]: s for s in cheaper["slots"]}["shoes"]["product"]
    assert new["price"] < shoes["price"] and cheaper["total"] < outfit["total"]
    seen = {shoes["product_id"], new["product_id"]}
    state = cheaper["state"]
    for _ in range(3):
        other = api.post("/outfits/swap", json={"outfit": state, "slot": "shoes"}).json()
        piece = {s["slot"]: s for s in other["slots"]}["shoes"]["product"]
        assert other["total"] <= 80
        assert piece["product_id"] not in seen
        seen.add(piece["product_id"])
        state = other["state"]


def test_agent_builds_an_outfit_with_a_step_per_slot_and_a_verified_total(api):
    events = stream(api, {"message": "Build me a summer outfit for a wedding under €80"})
    steps = [e["step"] for e in events if e["type"] == "step"]
    names = [s["name"] for s in steps]
    assert names[:2] == ["Understand request", "Plan outfit"]
    assert names.count("Fill slot") == 4
    assert "Planned 4 slots for “summer wedding”" in steps[1]["summary"]
    reply = events[-1]["reply"]
    assert reply["grounding"]["status"] == "verified"
    assert f"€{reply['outfit']['total']:.2f}" in reply["text"]
    assert len(reply["products"]) == 4


# --- Browser (the React app) -----------------------------------------------------------------


def test_outfit_builder_swap_and_save_with_approval(web):
    web.open("/ask/fashion")
    web.click_and_answer(
        web.page.get_by_role("group", name="Try one of these").get_by_role(
            "button", name="Outfit for an occasion"
        )
    )
    choice = web.last_turn().get_by_role("button", name="Summer wedding under €80")
    expect(choice).to_be_visible()
    options = (
        web.last_turn()
        .get_by_role("group")
        .filter(has=web.page.get_by_role("button", name="Summer wedding under €80"))
    )
    expect(options.get_by_role("button")).to_have_count(3)
    web.click_and_answer(choice)

    turn = web.last_turn()
    card = turn.get_by_test_id("outfit-card")
    expect(card.get_by_role("heading", name=re.compile(r"Outfit · summer wedding"))).to_be_visible()
    expect(card.locator("li")).to_have_count(4)
    for label in ("Dress", "Shoes", "Bag", "Accessory"):
        expect(card.get_by_text(label, exact=True)).to_be_visible()
    expect(card.get_by_text(re.compile(r"of €80\.00 · €[\d.]+ left"))).to_be_visible()
    card.scroll_into_view_if_needed()
    web.shot("outfit-01-built")
    turn.get_by_role("button", name=re.compile(r"How Curio worked")).click()
    expect(turn.get_by_text(re.compile(r"Filled shoes: ")).first).to_be_visible()
    web.shot("outfit-02-trace", full_page=True)

    web.click_and_answer(card.get_by_role("button", name="Ask Curio for a cheaper shoes"))
    turn = web.last_turn()
    card = turn.get_by_test_id("outfit-card")
    expect(card.get_by_text(re.compile(r"of €80\.00"))).to_be_visible()
    turn.get_by_role("button", name=re.compile(r"How Curio worked")).click()
    expect(turn.get_by_text(re.compile("Swapped shoes")).first).to_be_visible()
    card.scroll_into_view_if_needed()
    web.shot("outfit-03-cheaper-shoes")

    web.click_and_answer(card.get_by_role("button", name="Save outfit"))
    drawer = web.page.get_by_test_id("approval-drawer")
    expect(drawer.get_by_role("heading", name="Needs your approval")).to_be_visible()
    ticks = drawer.get_by_role("checkbox")
    expect(ticks).to_have_count(4)
    ticks.last.uncheck()  # keep the outfit without its accessory
    web.shot("outfit-04-save-approval")
    drawer.get_by_role("button", name="Approve").click()
    expect(drawer).to_be_hidden(timeout=120_000)
    panel = web.page.get_by_test_id("session-panel")
    expect(panel.get_by_role("heading", name=re.compile(r"Shortlist · 3"))).to_be_visible()
    web.shot("outfit-05-saved", full_page=True)
