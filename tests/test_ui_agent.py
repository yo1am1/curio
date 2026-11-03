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
