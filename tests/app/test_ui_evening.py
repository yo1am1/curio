"""Plan my evening in the UI: the domain switch, the planner page, swaps, the approval
dialog, and evening chat with its cards and SaveEvening approval."""

import json

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from curio.app import ui
from curio.domains.evening.models import EveningRequest
from curio.domains.evening.planner import OUTFIT_NEEDS_FASHION
from tests.app.test_ui_books import HEALTH, ndjson
from tests.domains.evening.conftest import make_planner

EVENING_INFO = {
    "id": "evening",
    "label": "Evening",
    "enabled": True,
    "chat_enabled": True,
    "item_count": 3,
    "hint": None,
}


def plan_json(occasion="rainy Sunday in", *, fashion=True, **fields) -> dict:
    """A real plan from the planner over the test catalogs, as the API sends it."""
    planner = make_planner(fashion=fashion)
    return planner.plan(EveningRequest(occasion=occasion, **fields)).model_dump(mode="json")


def swapped_json(part="book", **fields) -> dict:
    planner = make_planner()
    plan = planner.plan(EveningRequest(occasion="rainy Sunday in"))
    return planner.swap(plan.state, part, **fields).model_dump(mode="json")


def health(evening: dict | None = None) -> dict:
    served = json.loads(json.dumps(HEALTH))
    served["domains"].append(evening or EVENING_INFO)
    return served


def reply(**fields) -> dict:
    plan = plan_json()
    products = [s["product"] for s in plan["outfit"]["outfit"]["slots"] if s["product"]]
    base = {
        "text": "A cosy evening.",
        "products": products,
        "intent": {"query": "rainy Sunday in"},
        "domain": "evening",
        "cocktails": [plan["drink"]["cocktail"]],
        "books": [plan["book"]["book"]],
        "status": "complete",
        "steps": [
            {"kind": "tool", "name": "PlanEvening", "summary": "Planned a cosy evening.",
             "status": "ok", "detail": {}, "duration_ms": 5},
        ],
        "grounding": {"status": "verified", "checked": 6, "issues": []},
        "suggestions": ["Another book", "Save the evening"],
        "evening": plan,
    }  # fmt: skip
    return base | fields


@pytest.fixture
def api(monkeypatch):
    calls: list[tuple[str, str, object]] = []
    state: dict = {"health": health(), "plan": plan_json(), "swap": swapped_json(), "replies": []}

    def handle(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, path, body))
        if path == "/health":
            return httpx.Response(200, json=state["health"])
        if path == "/evening/plan":
            return httpx.Response(200, json=state["plan"])
        if path == "/evening/swap":
            return httpx.Response(200, json=state["swap"])
        if path.startswith("/products/") and path.endswith("/image"):
            return httpx.Response(404, json={"detail": "No image."})
        if path == "/chat/stream":
            return httpx.Response(200, text=ndjson(state["replies"].pop(0)))
        raise AssertionError(f"Unexpected API call: {request.url}")

    client = httpx.Client(base_url="http://offline.test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(ui, "api_client", lambda: client)
    ui.product_photo.clear()
    yield calls, state
    client.close()


def bodies(calls, path: str) -> list:
    return [body for _, p, body in calls if p == path]


def run_main():
    from curio.app import ui

    ui.main()


def evening_app(script: str, **state) -> AppTest:
    app = AppTest.from_string(f"from curio.app import ui; ui.{script}", default_timeout=10)
    app.session_state["domain"] = "evening"
    app.session_state["service_health"] = health()
    for key, value in state.items():
        app.session_state[key] = value
    return app


def keys(app) -> set[str]:
    return {b.key for b in app.button}


# --- Domain switch --------------------------------------------------------------------------


def test_evening_is_a_fourth_domain_with_its_own_icon_and_landing(api):
    app = AppTest.from_function(run_main, default_timeout=10).run()
    assert not app.exception
    switch = app.segmented_control(key="domain_switch")
    assert switch.options == ["Cocktails", "Books", "Evening"]
    assert ui.DOMAIN_VIEWS["evening"].label == ":material/bedtime: Evening"
    labels = {view.label.split(" ", 1)[0] for view in ui.DOMAIN_VIEWS.values()}
    assert len(labels) == len(ui.DOMAIN_VIEWS)  # every domain has a distinct icon
    switch.set_value("evening").run()
    assert not app.exception
    assert app.title[0].value == "Plan my evening."
    assert app.toggle(key="session_ask_shortlist").label == "Ask before saving an evening"
    assert app.caption[-1].value == ui.EVENING_CREDIT


def test_evening_not_served_is_marked_unavailable_with_the_missing_part(api):
    _, state = api
    hint = "Plan my evening needs the book catalog and the cocktail recipes: run it."
    state["health"] = health(EVENING_INFO | {"enabled": False, "hint": hint})
    app = AppTest.from_function(run_main, default_timeout=10).run()
    assert app.segmented_control(key="domain_switch").options == ["Cocktails", "Books"]
    caption = next(c for c in app.caption if c.value == "Evening is not available")
    assert caption.proto.help == hint


def run_navigation():
    import streamlit as st

    from curio.app import ui

    pages = ui.domain_pages("evening", ["cocktails", "books", "evening"])
    st.session_state["routes"] = {page.url_path: page.title for page in pages}


def test_navigation_registers_the_planner_page():
    app = AppTest.from_function(run_navigation, default_timeout=10).run()
    assert not app.exception
    assert app.session_state["routes"]["evening"] == "Plan my evening"
    assert ui.PAGE_DOMAINS["evening"] == "evening"


# --- The planner page ----------------------------------------------------------------------------


def test_occasion_chips_budget_and_alcohol_free_plan_an_evening_of_three_cards(api):
    calls, _ = api
    app = evening_app(
        "evening_page()",
        cocktail_preferences={"avoided_ingredients": ["Egg"]},
        book_preferences={"avoided_genres": ["Horror"]},
    ).run()
    assert not app.exception
    assert app.title[0].value == "Plan my evening"
    app.button_group(key="evening_occasion_chip").set_value("Rainy Sunday in").run()
    assert app.text_input(key="evening_occasion").value == "Rainy Sunday in"
    app.toggle(key="evening_use_budget").set_value(True).run()
    app.number_input(key="evening_budget").set_value(80).run()
    app.toggle(key="evening_alcohol_free").set_value(True).run()
    app.button(key="evening_plan_button").click().run()
    assert not app.exception
    assert bodies(calls, "/evening/plan") == [
        {
            "occasion": "Rainy Sunday in",
            "book_preferences": {"avoided_genres": ["Horror"]},
            "cocktail_preferences": {"avoided_ingredients": ["Egg"]},
            "budget": 80.0,
            "alcohol_free": True,
        }
    ]
    texts = {t.value for t in app.text}
    assert {"Chunky Knit Sweater", "The Name of the Wind", "Honey Bee"} <= texts
    assert any(
        m.value.startswith(":material/auto_awesome: **Why it works** · Rainy") for m in app.markdown
    )
    assert {"evening_another_book_page", "evening_another_drink_page", "evening_save_page"} <= keys(
        app
    )
    assert "evening_swap_page_top" in keys(app) and "evening_cheaper_page_top" in keys(app)
    assert any("Cosy" in m.value and "badge" in m.value for m in app.markdown)


def test_an_empty_occasion_asks_for_one(api):
    calls, _ = api
    app = evening_app("evening_page()").run()
    app.button(key="evening_plan_button").click().run()
    assert bodies(calls, "/evening/plan") == []
    assert app.warning[0].value.startswith("Tell Curio the occasion first")


def test_swap_buttons_send_the_state_and_show_the_new_plan(api):
    calls, state = api
    app = evening_app("evening_page()", evening_plan=state["plan"]).run()
    app.button(key="evening_another_book_page").click().run()
    assert not app.exception
    body = bodies(calls, "/evening/swap")[-1]
    assert body == {"evening": state["plan"]["state"], "part": "book", "mode": "other"}
    assert app.session_state["evening_plan"] == state["swap"]
    app.session_state["evening_plan"] = state["plan"]
    app.run()
    app.button(key="evening_alcohol_free_page").click().run()
    assert bodies(calls, "/evening/swap")[-1]["mode"] == "alcohol_free"
    app.session_state["evening_plan"] = state["plan"]
    app.run()
    app.button(key="evening_cheaper_page_top").click().run()
    assert bodies(calls, "/evening/swap")[-1] | {"evening": None} == {
        "evening": None,
        "part": "outfit",
        "slot": "top",
        "mode": "cheaper",
    }


def test_save_evening_goes_through_approval_and_fills_each_list(api):
    _, state = api
    app = evening_app("evening_page()", evening_plan=state["plan"]).run()
    app.button(key="evening_save_page").click().run()
    assert not app.exception
    boxes = {c.key: c for c in app.checkbox}
    assert set(boxes) == {
        "save_evening_product_21",
        "save_evening_product_12",
        "save_evening_product_22",
        "save_evening_book_4",
        "save_evening_drink_9",
    }
    boxes["save_evening_drink_9"].uncheck().run()
    app.button(key="save_evening_approve").click().run()
    assert not app.exception
    assert [p["product_id"] for p in app.session_state["shortlist"]] == [21, 12, 22]
    assert [b["book_id"] for b in app.session_state["book_shortlist"]] == [4]
    assert "cocktail_shortlist" not in app.session_state  # the drink was unticked
    assert "evening_saving" not in app.session_state  # the dialog closed


def test_declining_the_save_changes_nothing(api):
    _, state = api
    app = evening_app("evening_page()", evening_plan=state["plan"]).run()
    app.button(key="evening_save_page").click().run()
    app.button(key="save_evening_decline").click().run()
    assert not app.exception
    for saved in ("shortlist", "book_shortlist", "cocktail_shortlist"):
        assert saved not in app.session_state
    assert "evening_saving" not in app.session_state


def test_without_fashion_the_outfit_card_says_why(api):
    plan = plan_json(fashion=False)
    served = health(EVENING_INFO | {"item_count": 2, "hint": OUTFIT_NEEDS_FASHION})
    app = evening_app("evening_page()", evening_plan=plan, service_health=served).run()
    assert not app.exception
    assert any(i.value == OUTFIT_NEEDS_FASHION for i in app.info)
    assert any(c.value == f":material/block: {ui.plain(OUTFIT_NEEDS_FASHION)}" for c in app.caption)
    assert "evening_swap_page_top" not in keys(app)
    assert {"The Name of the Wind", "Honey Bee"} <= {t.value for t in app.text}


# --- Evening chat --------------------------------------------------------------------------------


def test_evening_chat_shows_the_plan_cards_and_sends_the_state_back(api):
    calls, state = api
    state["replies"] = [reply(), reply(text="Another book, re-paired.", evening=state["swap"])]
    app = evening_app("agent_home()").run()
    app.button(key="chat_starter_home_0").click().run()
    assert not app.exception
    first = bodies(calls, "/chat/stream")[0]
    assert (first["domain"], first["message"]) == ("evening", "Rainy Sunday in")
    assert "evening" not in first
    assert "evening_another_book_chat_home_1" in keys(app)
    assert app.session_state["chat_evening"] == state["plan"]["state"]
    # The cards replace the plain product, book and drink lists.
    assert not any(b.key and b.key.startswith("chat_view_home_1") for b in app.button)
    app.button(key="evening_another_book_chat_home_1").click().run()
    assert not app.exception
    second = bodies(calls, "/chat/stream")[1]
    assert second["message"] == "Another book"
    assert second["evening"] == state["plan"]["state"]
    assert app.session_state["chat_evening"] == state["swap"]["state"]


def test_save_evening_in_chat_is_an_approval_card_that_can_be_narrowed(api):
    calls, state = api
    plan = state["plan"]
    pieces = [s["product"] for s in plan["outfit"]["outfit"]["slots"] if s["product"]]
    pending = {
        "id": "a1",
        "tool": "SaveEvening",
        "args": {"product_ids": [21, 12, 22], "book_ids": [4], "drink_ids": [9]},
        "request": "Save the evening",
        "issued_at": 1,
        "signature": "s",
        "summary": "Proposed: Would save 3 pieces, a book and a drink.",
        "products": pieces,
        "books": [plan["book"]["book"]],
        "cocktails": [plan["drink"]["cocktail"]],
    }
    proposal = reply(
        text="Shall I save this evening?", status="needs_approval", pending_action=pending,
        evening=None, suggestions=[],
    )  # fmt: skip
    applied = {"kind": "evening", "product_ids": [21, 12], "book_ids": [4], "drink_ids": [9]}
    done = reply(text="Saved.", applied=[applied], evening=None)
    state["replies"] = [proposal, done]
    app = evening_app("agent_home()").run()
    app.chat_input[0].set_value("Save the evening").run()
    assert not app.exception
    labels = {c.label for c in app.checkbox}
    assert "The Name of the Wind · Patrick Rothfuss" in labels
    assert "Honey Bee · Alcoholic" in labels
    app.checkbox(key="approve_home_1_product_ids_22").uncheck().run()
    app.button(key="approve_home_1").click().run()
    assert not app.exception
    decision = bodies(calls, "/chat/stream")[-1]["decision"]
    assert decision["edited_args"] == {"product_ids": [21, 12]}
    assert [p["product_id"] for p in app.session_state["shortlist"]] == [21, 12]
    assert [b["book_id"] for b in app.session_state["book_shortlist"]] == [4]
    assert [c["drink_id"] for c in app.session_state["cocktail_shortlist"]] == [9]


def test_the_evening_payload_carries_every_domains_tastes_and_lists(api):
    app = evening_app(
        "agent_home()",
        preferences={"max_price": 40, "sections": []},
        book_preferences={"liked_genres": ["Fantasy"]},
        cocktail_preferences={"alcohol_free": True},
        shortlist=[{"product_id": 7}],
        book_shortlist=[{"book_id": 4}],
        cocktail_shortlist=[{"drink_id": 9}],
        chat_evening={"occasion": "date night", "theme": "romantic"},
        approval_policy={"ask_shortlist": False},
    ).run()
    assert not app.exception
    calls, state = api
    state["replies"] = [reply()]
    app.chat_input[0].set_value("Another drink").run()
    sent = bodies(calls, "/chat/stream")[-1]
    assert sent["domain"] == "evening"
    assert sent["preferences"] == {"max_price": 40}
    assert sent["book_preferences"] == {"liked_genres": ["Fantasy"]}
    assert sent["cocktail_preferences"] == {"alcohol_free": True}
    assert (sent["shortlist"], sent["book_shortlist"], sent["cocktail_shortlist"]) == (
        [7],
        [4],
        [9],
    )
    assert sent["evening"] == {"occasion": "date night", "theme": "romantic"}
    assert sent["auto_approve"] == ["SaveEvening"]


def test_new_conversation_forgets_the_evening_state(api):
    app = evening_app("agent_home()", chat_evening={"occasion": "x", "theme": "cosy"}).run()
    app.button(key="chat_reset_home").click().run()
    assert "chat_evening" not in app.session_state
