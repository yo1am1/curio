"""Regression tests for the v1.0 pre-release UI audit (F2, VAL-1, SAFE-2, UI-1 to UI-6)."""

import json

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from clothes_recommender import ui
from clothes_recommender.chat.schemas import ChatRequest
from clothes_recommender.cocktails.domain import CocktailPreferences

COAT = {
    "product_id": 1,
    "name": "Wool coat",
    "price": 45.0,
    "product_type": "Coat",
    "colour": "Blue",
    "section": "Ladieswear",
}
SCARF = {**COAT, "product_id": 2, "name": "Scarf", "price": 9.99}
SHIRLEY = {
    "drink_id": 3,
    "name": "Shirley Temple",
    "alcoholic": "Optional alcohol",
    "category": "Soft Drink",
    "glass": "Highball glass",
    "instructions": "Pour.",
    "ingredients": [{"name": "Ginger ale", "measure": None}],
    "image_url": None,
    "source_url": "https://www.thecocktaildb.com/",
}
HEALTH = {
    "status": "ok",
    "indexed_products": 2,
    "catalog_products": 2,
    "chat_enabled": True,
    "cocktails_enabled": True,
    "cocktail_count": 3,
}
STEP = {
    "kind": "tool",
    "name": "SearchCatalog",
    "summary": "Found 1 match.",
    "status": "ok",
    "detail": {},
    "duration_ms": 100,
}


def reply(**fields):
    base = {
        "text": "Try the Wool coat.",
        "products": [COAT],
        "intent": {"query": "coat"},
        "status": "complete",
        "steps": [STEP],
        "grounding": {"status": "verified", "checked": 1, "issues": []},
        "suggestions": [],
    }
    return base | fields


def ndjson(body: dict) -> str:
    lines = [{"type": "step", "step": step} for step in body.get("steps", [])]
    lines.append({"type": "reply", "reply": body})
    return "\n".join(json.dumps(line) for line in lines) + "\n"


@pytest.fixture
def api(monkeypatch):
    """Mocked API; `routes` maps a path to (status, json) and `chat` holds NDJSON bodies."""
    calls: list[tuple[str, object]] = []
    routes: dict[str, tuple[int, object]] = {
        "/health": (200, HEALTH),
        "/domains": (
            200,
            [
                {"id": "fashion", "label": "Fashion", "enabled": True, "chat_enabled": True},
                {"id": "cocktails", "label": "Cocktails", "enabled": True, "chat_enabled": True},
            ],
        ),
        "/demo/customers": (200, []),
        "/users": (200, [7]),
        "/users/7": (200, {"user": {"user_id": 7}, "purchases": []}),
        "/users/7/recommendations": (200, [{"product": COAT, "score": 0.5, "reasons": []}]),
        "/sections": (200, ["Ladieswear"]),
        "/search/text": (200, []),
        "/search/examples": (200, []),
        "/products/1": (200, COAT),
        "/products/2": (200, SCARF),
        "/cocktails/ingredients": (200, ["Gin", "Lime"]),
        "/cocktails/categories": (200, ["Cocktail"]),
        "/cocktails/pantry": (200, []),
        "/cocktails/search": (200, []),
    }
    chat: list[str] = []

    def handle(request):
        path = request.url.path
        calls.append((path, json.loads(request.content) if request.content else None))
        if path.endswith("/image"):
            return httpx.Response(404, json={"detail": "No image"})
        if path == "/chat/stream":
            return httpx.Response(200, text=chat.pop(0))
        if path in routes:
            status, payload = routes[path]
            return httpx.Response(status, json=payload)
        raise AssertionError(f"Unexpected API call: {request.url}")

    client = httpx.Client(base_url="http://offline.test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(ui, "api_client", lambda: client)
    ui.product_photo.clear()
    yield calls, routes, chat
    client.close()


def script(body: str, **state) -> AppTest:
    app = AppTest.from_string(f"from clothes_recommender import ui\n{body}", default_timeout=10)
    for key, value in state.items():
        app.session_state[key] = value
    return app


# --- F2 / VAL-1: fashion memory stays within the API's preference limits --------------------


def test_fashion_memory_is_capped_to_schema_limits_keeping_the_newest_values(api):
    effect = {
        "kind": "preferences",
        "preferences": {"sections": ["Sport", "Ladieswear"], "colours": ["Beige", "Grey", "Red"]},
    }
    app = script(
        f"ui.apply_effects({{'applied': [{effect!r}]}})",
        preferences={"sections": ["Menswear", "Divided"], "colours": ["Black", "White", "Blue"]},
    ).run()
    assert not app.exception
    memory = app.session_state["preferences"]
    assert memory["sections"] == ["Divided", "Sport", "Ladieswear"]
    assert memory["colours"] == ["White", "Blue", "Beige", "Grey", "Red"]
    # Every later chat request still validates.
    ChatRequest.model_validate({"message": "hi", "preferences": memory})
    toasts = [t.value for t in app.toast]
    assert any("Menswear" in t and "Black" in t for t in toasts), toasts


def test_reapproved_value_moves_to_newest_instead_of_being_dropped(api):
    effect = {"kind": "preferences", "preferences": {"sections": ["Menswear", "Sport"]}}
    app = script(
        f"ui.apply_effects({{'applied': [{effect!r}]}})",
        preferences={"sections": ["Menswear", "Divided", "Ladieswear"]},
    ).run()
    assert app.session_state["preferences"]["sections"] == ["Ladieswear", "Menswear", "Sport"]
    assert any("Divided" in t.value for t in app.toast)


def test_memory_within_limits_does_not_announce_dropped_values(api):
    effect = {"kind": "preferences", "preferences": {"colours": ["Red"]}}
    app = script(
        f"ui.apply_effects({{'applied': [{effect!r}]}})", preferences={"colours": ["Blue"]}
    ).run()
    assert app.session_state["preferences"]["colours"] == ["Blue", "Red"]
    assert [t.value for t in app.toast] == ["Curio will remember that for this session."]


# --- SAFE-2: a newly approved avoided ingredient is never silently dropped ------------------


def test_new_avoided_ingredient_is_kept_when_tastes_are_full(api):
    old = [f"Ing{i}" for i in range(10)]
    effect = {
        "kind": "cocktail_preferences",
        "cocktail_preferences": {
            "alcohol_free": False,
            "liked_ingredients": [],
            "avoided_ingredients": ["Egg"],
        },
    }
    app = script(
        f"ui.apply_effects({{'applied': [{effect!r}]}})",
        cocktail_preferences={"avoided_ingredients": old},
    ).run()
    assert not app.exception
    avoided = app.session_state["cocktail_preferences"]["avoided_ingredients"]
    assert avoided == [*old[1:], "Egg"]
    CocktailPreferences.model_validate(app.session_state["cocktail_preferences"])
    assert any("Ing0" in t.value for t in app.toast)


# --- UI-1: a decision is resolved only after the API accepted it ----------------------------


def proposal() -> dict:
    action = {
        "id": "a1",
        "tool": "SaveToShortlist",
        "args": {"product_ids": [1]},
        "request": "save it",
        "issued_at": 1,
        "signature": "sig",
        "summary": "Proposed: Would save Wool coat.",
        "products": [COAT],
    }
    return reply(
        text="May I save it?",
        status="needs_approval",
        grounding=None,
        pending_action=action,
    )


def test_failed_approval_keeps_the_card_active_with_the_error(api):
    _, _, chat = api
    expired = "This proposal has expired. Please ask again."
    chat += [
        ndjson(proposal()),
        json.dumps({"type": "error", "status": 409, "detail": expired}),
        ndjson(reply(text="Saved.", applied=[{"kind": "shortlist", "product_ids": [1]}])),
    ]
    app = script("ui.agent_home()").run()
    app.chat_input[0].set_value("save it").run()
    app.button(key="approve_home_1").click().run()
    assert not app.exception
    history = app.session_state["chat_history"]
    assert len(history) == 2
    assert not history[1].get("resolution")
    assert app.session_state["shortlist"] == []
    assert any(e.value == expired for e in app.error)
    assert any(b.key == "approve_home_1" for b in app.button)
    # A retry goes through and resolves the card.
    app.button(key="approve_home_1").click().run()
    assert not app.exception
    history = app.session_state["chat_history"]
    assert history[1]["resolution"] == "Approved."
    assert [p["product_id"] for p in app.session_state["shortlist"]] == [1]
    assert not any(e.value == expired for e in app.error)


# --- UI-2: customer pages explain themselves without example customers ----------------------


def test_fashion_page_browses_all_customers_when_there_are_no_examples(api):
    calls, _, _ = api
    app = script("ui.recommendations_tab()").run()
    assert not app.exception
    assert app.toggle(key="recommendation_user_browse_all").value is True
    assert ("/users/7/recommendations", None) in calls
    assert any(t.value == "Wool coat" for t in app.text)


@pytest.mark.parametrize("tab", ["recommendations_tab", "knowledge_graph_tab"])
def test_customer_pages_show_an_empty_state_in_the_page_body(api, tab):
    _, routes, _ = api
    routes["/users"] = (200, [])
    app = script(f"ui.{tab}()").run()
    assert not app.exception
    assert any(ui.NO_CUSTOMER_SELECTED in i.value for i in app.main.info)


# --- UI-3: the popup flag does not leak from pages without the popup ------------------------


def test_popup_flag_queued_on_the_home_page_is_cleared(api):
    app = AppTest.from_function(_run_main, default_timeout=10)
    app.session_state["assistant_popup"] = True
    app.run()
    assert not app.exception
    assert "assistant_popup" not in app.session_state or not app.session_state["assistant_popup"]


def _run_main():
    from clothes_recommender import ui

    ui.main()


# --- UI-4: shortlist controls and photos have meaningful accessible names --------------------


def test_shortlist_remove_buttons_have_text_and_photos_have_names(api, monkeypatch):
    monkeypatch.setattr(ui, "product_photo", lambda _: _png())
    app = script("ui.fashion_session_panel()", shortlist=[COAT]).run()
    assert not app.exception
    remove = app.button(key="shortlist_remove_1")
    assert remove.label == "Remove"
    captions = [image.proto.imgs[0].caption for image in app.get("image")]
    assert captions == ["Wool coat"]


def _png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 3), "navy").save(buffer, format="PNG")
    return buffer.getvalue()


def test_cocktail_shortlist_remove_button_has_text(api):
    app = script("ui.cocktail_session_panel()", domain="cocktails", cocktail_shortlist=[SHIRLEY])
    app.run()
    assert not app.exception
    assert app.button(key="cocktail_shortlist_remove_3").label == "Remove"


# --- UI-5: switching mode keeps what the shopper entered -------------------------------------


def test_pantry_and_search_inputs_survive_a_mode_switch(api):
    app = script("ui.cocktails_page()", domain="cocktails").run()
    app.text_input(key="cocktail_query").set_value("minty")
    app.button(key="cocktail_search_submit").click().run()
    app.segmented_control(key="cocktail_mode").set_value("pantry").run()
    app.multiselect(key="pantry_ingredients").set_value(["Gin"]).run()
    app.segmented_control(key="cocktail_mode").set_value("search").run()
    app.run()
    assert app.text_input(key="cocktail_query").value == "minty"
    app.segmented_control(key="cocktail_mode").set_value("pantry").run()
    app.run()
    assert not app.exception
    assert app.multiselect(key="pantry_ingredients").value == ["Gin"]


def test_discover_description_survives_a_mode_switch(api):
    app = script("ui.discovery_page()").run()
    app.text_input(key="text_query").set_value("red dress")
    app.button(key="description_search_submit").click().run()
    app.segmented_control(key="discovery_mode").set_value("Photo").run()
    app.run()
    app.segmented_control(key="discovery_mode").set_value("Description").run()
    app.run()
    assert not app.exception
    assert app.text_input(key="text_query").value == "red dress"


# --- UI-6: the shortlist toast counts only the pieces actually added --------------------------


def test_effect_toast_counts_only_added_items(api):
    full = [{**COAT, "product_id": 100 + i} for i in range(ui.MAX_SHORTLIST)]
    effect = {"kind": "shortlist", "product_ids": [1, 2]}
    app = script(f"ui.apply_effects({{'applied': [{effect!r}]}})", shortlist=full).run()
    assert not app.exception
    toasts = [t.value for t in app.toast]
    assert toasts == [f"Your shortlist holds up to {ui.MAX_SHORTLIST} pieces."]


def test_effect_toast_skips_duplicates_in_its_count(api):
    effect = {"kind": "shortlist", "product_ids": [1, 2]}
    app = script(f"ui.apply_effects({{'applied': [{effect!r}]}})", shortlist=[COAT]).run()
    assert [t.value for t in app.toast] == ["Saved 1 piece to your shortlist."]


def test_cocktail_effect_toast_counts_only_added_drinks(api):
    effect = {"kind": "cocktail_shortlist", "drink_ids": [3]}
    app = script(
        f"ui.apply_effects({{'applied': [{effect!r}], 'cocktails': [{SHIRLEY!r}]}})",
        cocktail_shortlist=[SHIRLEY],
    ).run()
    assert not app.exception
    assert [t.value for t in app.toast] == []


# --- Alcohol-free semantics: "Optional alcohol" is labelled "Alcohol optional" ---------------


def test_optional_alcohol_is_labelled_alcohol_optional():
    assert ui.alcohol_label(SHIRLEY) == "Alcohol optional"
