"""Agent conversation UI: streamed trace, approvals, quick replies, shortlist and feedback."""

import json

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from clothes_recommender import ui

COAT = {
    "product_id": 1,
    "name": "Wool coat",
    "price": 45.0,
    "product_type": "Coat",
    "colour": "Blue",
    "section": "Ladieswear",
    "description": "Warm coat.",
}
SCARF = {**COAT, "product_id": 2, "name": "Scarf", "price": 9.99, "product_type": "Scarf"}
HEALTH = {"status": "ok", "indexed_products": 2, "catalog_products": 2, "chat_enabled": True}
STEP = {
    "kind": "tool",
    "name": "SearchCatalog",
    "summary": "Found 1 matches for “coat”.",
    "status": "ok",
    "detail": {},
    "duration_ms": 420,
}


def reply(**fields):
    base = {
        "text": "Try the **Wool coat** at €45.00.",
        "products": [COAT],
        "intent": {"query": "coat"},
        "status": "complete",
        "steps": [STEP],
        "grounding": {"status": "verified", "checked": 1, "issues": []},
        "suggestions": ["Something cheaper"],
    }
    return base | fields


def stream(*replies):
    """Responses for successive /chat/stream calls, as NDJSON step + reply events."""
    queue = list(replies)

    def respond(_):
        body = queue.pop(0)
        lines = [{"type": "step", "step": step} for step in body.get("steps", [])]
        lines.append({"type": "reply", "reply": body})
        return "\n".join(json.dumps(line) for line in lines) + "\n"

    return respond


@pytest.fixture
def api(monkeypatch):
    calls = {"bodies": [], "feedback": []}
    responders = {}

    def handle(request):
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json=HEALTH)
        if path.endswith("/image"):
            return httpx.Response(404, json={"detail": "No image"})
        if path == "/demo/customers":
            return httpx.Response(
                200,
                json=[
                    {
                        "user_id": 7,
                        "label": "Coat shopper",
                        "description": "",
                        "focus_product_id": 1,
                    }
                ],
            )
        if path == "/chat/stream":
            calls["bodies"].append(json.loads(request.content))
            return httpx.Response(200, text=responders["chat"](request))
        if path == "/chat/feedback":
            calls["feedback"].append(json.loads(request.content))
            return httpx.Response(204)
        if path == "/products/2":
            return httpx.Response(200, json=SCARF)
        raise AssertionError(f"Unexpected API call: {request.url}")

    client = httpx.Client(base_url="http://offline.test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(ui, "api_client", lambda: client)
    ui.product_photo.clear()
    return calls, responders


def home():
    return AppTest.from_string(
        "from clothes_recommender import ui; ui.agent_home()", default_timeout=10
    )


def test_trace_grounding_badge_and_suggestion_chip(api):
    calls, responders = api
    responders["chat"] = stream(reply(), reply(text="The Scarf is €9.99.", products=[SCARF]))
    app = home().run()
    app.chat_input[0].set_value("A warm coat").run()
    assert not app.exception
    tree = str(app.main)
    assert "How Curio worked · 1 action · 0.4s" in tree
    # The live progress bubble is replaced by the finished turn.
    assert tree.count("name='assistant'") == 1
    assert any("Prices verified against the catalog" in m.value for m in app.markdown)
    app.button(key="chat_suggestion_home_1_0").click().run()
    assert not app.exception
    assert calls["bodies"][-1]["message"] == "Something cheaper"
    assert calls["bodies"][-1]["history"][-1]["product_ids"] == [1]
    assert len(app.session_state["chat_history"]) == 4


def test_clarification_options_send_the_chosen_answer(api):
    calls, responders = api
    question = reply(
        text="Who is it for?",
        products=[],
        status="needs_input",
        grounding=None,
        suggestions=[],
        clarification={"question": "Who is it for?", "options": ["Women", "Men"]},
    )
    responders["chat"] = stream(question, reply())
    app = home().run()
    app.chat_input[0].set_value("An outfit").run()
    app.button(key="chat_option_home_1_1").click().run()
    assert not app.exception
    assert calls["bodies"][-1]["message"] == "Men"


def test_approval_can_be_narrowed_and_applies_the_effect(api):
    calls, responders = api
    action = {
        "id": "a1",
        "tool": "SaveToShortlist",
        "args": {"product_ids": [1, 2]},
        "request": "save both",
        "issued_at": 1,
        "signature": "sig",
        "summary": "Proposed: Would save 2 to the shortlist: Wool coat, Scarf.",
        "products": [COAT, SCARF],
    }
    proposal = reply(
        text="I'd like to save these pieces to your shortlist.",
        products=[COAT, SCARF],
        status="needs_approval",
        grounding=None,
        suggestions=[],
        pending_action=action,
    )
    approved = reply(
        text="Saved the Scarf.",
        products=[SCARF],
        applied=[{"kind": "shortlist", "product_ids": [2], "preferences": None}],
    )
    responders["chat"] = stream(proposal, approved)
    app = home().run()
    app.chat_input[0].set_value("save both").run()
    app.checkbox(key="approve_home_1_product_ids_1").uncheck().run()
    app.button(key="approve_home_1").click().run()
    assert not app.exception
    decision = calls["bodies"][-1]["decision"]
    assert decision["approved"] is True
    assert decision["edited_args"] == {"product_ids": [2]}
    assert decision["action"]["signature"] == "sig"
    assert [p["product_id"] for p in app.session_state["shortlist"]] == [2]
    assert app.session_state["chat_history"][1]["resolution"] == "Approved."
    # The resolved card no longer offers buttons, and the next request carries the shortlist.
    assert not any(b.key == "approve_home_1" for b in app.button)
    assert any(c.value == "Approved." for c in app.caption)
    responders["chat"] = stream(reply())
    app.chat_input[0].set_value("compare").run()
    assert calls["bodies"][-1]["shortlist"] == [2]


def test_session_panel_controls_policy_memory_and_personalization(api):
    calls, responders = api
    responders["chat"] = stream(reply())
    app = home()
    app.session_state["preferences"] = {"sections": ["Menswear"], "colours": [], "max_price": 40}
    app.run()
    app.toggle(key="session_ask_shortlist").set_value(False).run()
    app.selectbox(key="session_shopper_select").set_value(7).run()
    app.button(key="forget_max_price_40").click().run()
    app.chat_input[0].set_value("jeans").run()
    assert not app.exception
    body = calls["bodies"][-1]
    assert body["auto_approve"] == ["SaveToShortlist"]
    assert body["shopper_id"] == 7
    assert body["preferences"] == {"sections": ["Menswear"]}


def test_saving_from_a_card_and_sending_feedback(api):
    calls, responders = api
    responders["chat"] = stream(reply())
    app = home().run()
    app.chat_input[0].set_value("A warm coat").run()
    app.button(key="chat_save_home_1_1").click().run()
    assert not app.exception
    assert [p["product_id"] for p in app.session_state["shortlist"]] == [1]
    assert app.button(key="chat_save_home_1_1").disabled

    rate = AppTest.from_string(
        "from clothes_recommender import ui\n"
        "import streamlit as st\n"
        "st.session_state['feedback_home_1'] = 0\n"
        "ui.send_feedback(1, 'home')"
    )
    rate.session_state["chat_history"] = app.session_state["chat_history"]
    rate.run()
    assert not rate.exception
    assert calls["feedback"] == [
        {
            "rating": "down",
            "message": "A warm coat",
            "answer": "Try the **Wool coat** at €45.00.",
            "product_ids": [1],
            "tools": ["SearchCatalog"],
        }
    ]


def test_stream_errors_do_not_poison_history(api):
    _, responders = api
    responders["chat"] = lambda _: json.dumps(
        {"type": "error", "status": 429, "detail": "The assistant is busy."}
    )
    app = home().run()
    app.chat_input[0].set_value("coat").run()
    assert not app.exception
    assert app.error[0].value == "The assistant is busy."
    assert app.session_state["chat_history"] == []


SHOE = {**COAT, "product_id": 3, "name": "Block heels", "price": 24.99, "product_type": "Heels"}
OUTFIT_STATE = {
    "occasion": "summer wedding",
    "budget": 80.0,
    "audience": "women",
    "pieces": [{"slot": "outerwear", "product_id": 1}, {"slot": "shoes", "product_id": 3}],
    "skipped": [],
}
OUTFIT = {
    "occasion": "summer wedding",
    "audience": "women",
    "budget": 80.0,
    "total": 69.99,
    "status": "partial",
    "slots": [
        {"slot": "outerwear", "label": "Outerwear", "product": COAT, "reason": "Best match."},
        {
            "slot": "shoes",
            "label": "Shoes",
            "product": SHOE,
            "reason": "Bought together with the Wool coat in 3 baskets.",
        },
        {
            "slot": "bag",
            "label": "Bag",
            "product": None,
            "issue": "The budget cannot cover the bag: the cheapest match costs €14.99.",
            "cheapest": 14.99,
        },
    ],
    "notes": ["A complete outfit needs at least €84.98."],
    "minimum": 84.98,
    "state": OUTFIT_STATE,
}
OUTFIT_STEPS = [
    {**STEP, "kind": "plan", "name": "Plan outfit", "summary": "Planned 3 slots."},
    {**STEP, "name": "Fill slot", "summary": "Filled outerwear: Wool coat · €45.00."},
    {**STEP, "name": "BuildOutfit", "summary": "Filled 2 of 3 slots for €69.99."},
]


def outfit_reply(**fields):
    return (
        reply(
            text="Here's the outfit.",
            products=[COAT, SHOE, SCARF],
            steps=OUTFIT_STEPS,
            outfit=OUTFIT,
            suggestions=["Cheaper outerwear"],
        )
        | fields
    )


def test_outfit_card_shows_slots_reasons_gaps_and_the_budget(api):
    _, responders = api
    responders["chat"] = stream(outfit_reply())
    app = home().run()
    app.chat_input[0].set_value("Summer wedding outfit under €80").run()
    assert not app.exception
    assert any("Outfit · summer wedding" in m.value for m in app.markdown)
    captions = [c.value for c in app.caption]
    assert "Bought together with the Wool coat in 3 baskets." in captions
    assert any("The budget cannot cover the bag" in c for c in captions)
    assert "A complete outfit needs at least €84.98." in captions
    (bar,) = app.get("progress")
    assert bar.proto.text == "€69.99 of €80.00 · €10.01 left" and bar.proto.value == 87
    keys = {button.key for button in app.button}
    assert {"outfit_swap_home_1_shoes", "outfit_cheaper_home_1_outerwear"} <= keys
    assert "outfit_save_home_1" in keys and "outfit_swap_home_1_bag" not in keys
    # The scarf is not part of the outfit, so it keeps its product card.
    assert "chat_view_home_1_2" in keys and "chat_view_home_1_1" not in keys
    assert app.session_state["chat_outfit"] == OUTFIT_STATE
    trace = [m.value for m in app.markdown if "**Plan outfit**" in m.value]
    assert trace and "Planned 3 slots." in trace[0]


def test_outfit_buttons_send_follow_ups_with_the_outfit(api):
    calls, responders = api
    responders["chat"] = stream(outfit_reply(), reply(), reply())
    app = home().run()
    app.chat_input[0].set_value("Summer wedding outfit").run()
    app.button(key="outfit_swap_home_1_shoes").click().run()
    assert not app.exception
    body = calls["bodies"][-1]
    assert body["message"] == "Swap the shoes in my outfit"
    assert body["outfit"] == OUTFIT_STATE
    assert body["history"][-1]["product_ids"] == [1, 3, 2]
    # Only the latest turn offers actions.
    assert "outfit_swap_home_1_shoes" not in [b.key for b in app.button]


def test_save_outfit_and_cheaper_buttons(api):
    calls, responders = api
    responders["chat"] = stream(outfit_reply(), outfit_reply(), reply())
    app = home().run()
    app.chat_input[0].set_value("Summer wedding outfit").run()
    app.button(key="outfit_cheaper_home_1_outerwear").click().run()
    assert calls["bodies"][-1]["message"] == "Cheaper outerwear for my outfit"
    app.button(key="outfit_save_home_3").click().run()
    assert not app.exception
    assert calls["bodies"][-1]["message"] == ui.SAVE_OUTFIT


def test_new_conversation_forgets_the_outfit(api):
    _, responders = api
    responders["chat"] = stream(outfit_reply())
    app = home().run()
    app.chat_input[0].set_value("Summer wedding outfit").run()
    assert "chat_outfit" in app.session_state
    app.button(key="chat_reset_home").click().run()
    assert not app.exception
    assert "chat_outfit" not in app.session_state


def test_outfit_starter_chip_is_offered(api):
    calls, responders = api
    responders["chat"] = stream(reply())
    app = home().run()
    starter = app.button(key="chat_starter_home_3")
    assert starter.label == "Outfit for an occasion"
    starter.click().run()
    assert calls["bodies"][-1]["message"] == "Outfit for an occasion"
    assert "outfit" not in calls["bodies"][-1]


def test_budget_line_reads_left_over_or_total():
    assert ui.budget_line(69.99, 80) == "€69.99 of €80.00 · €10.01 left"
    assert ui.budget_line(80.5, 80) == "€80.50 of €80.00 · €0.50 over"
    assert ui.budget_line(12.5, None) == "Total €12.50"
