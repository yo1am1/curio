"""Cocktail domain UI: domain switch, navigation, recipe search, pantry, dialog and chat."""

import json

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from clothes_recommender import ui
from clothes_recommender.chat.schemas import ChatRequest

MOJITO = {
    "drink_id": 1,
    "name": "Mojito",
    "alcoholic": "Alcoholic",
    "category": "Cocktail",
    "glass": "Highball glass",
    "instructions": "Muddle mint with sugar and lime. Add rum and soda.",
    "ingredients": [
        {"name": "Light rum", "measure": "2-3 oz "},
        {"name": "Lime", "measure": "Juice of 1"},
        {"name": "Mint", "measure": "2-4"},
        {"name": "Soda water", "measure": None},
    ],
    "image_url": "https://www.thecocktaildb.com/images/media/drink/mojito.jpg",
    "source_url": "https://www.thecocktaildb.com/",
}
VIRGIN = {
    **MOJITO,
    "drink_id": 2,
    "name": "Virgin Mojito",
    "alcoholic": "Non alcoholic",
    "ingredients": [{"name": "Lime", "measure": "1"}, {"name": "Mint", "measure": None}],
    "image_url": None,
}
HEALTH = {
    "status": "ok",
    "indexed_products": 2,
    "catalog_products": 2,
    "chat_enabled": True,
    "ranking_enabled": False,
    "cocktails_enabled": True,
    "cocktail_count": 425,
}
DOMAINS = [
    {"id": "fashion", "label": "Fashion", "enabled": True, "chat_enabled": True},
    {"id": "cocktails", "label": "Cocktails", "enabled": True, "chat_enabled": True},
]
STEP = {
    "kind": "tool",
    "name": "SearchCocktails",
    "summary": "Found 2 recipes.",
    "status": "ok",
    "detail": {},
    "duration_ms": 300,
}


def reply(**fields):
    base = {
        "text": "Try the **Mojito**.",
        "products": [],
        "cocktails": [MOJITO, VIRGIN],
        "domain": "cocktails",
        "intent": {"query": "mint"},
        "status": "complete",
        "steps": [STEP],
        "grounding": {"status": "verified", "checked": 1, "issues": []},
        "suggestions": ["Make it alcohol-free"],
    }
    return base | fields


def ndjson(body: dict) -> str:
    lines = [{"type": "step", "step": step} for step in body.get("steps", [])]
    lines.append({"type": "reply", "reply": body})
    return "\n".join(json.dumps(line) for line in lines) + "\n"


@pytest.fixture
def api(monkeypatch):
    """Mocked API: `calls` records (method, path, params, body); `state` overrides answers."""
    calls: list[tuple[str, str, dict, object]] = []
    state: dict = {"health": dict(HEALTH), "domains": DOMAINS, "replies": []}

    def handle(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, path, dict(request.url.params), body))
        if path in state.get("errors", {}):
            status, detail = state["errors"][path]
            return httpx.Response(status, json={"detail": detail})
        if path == "/health":
            return httpx.Response(200, json=state["health"])
        if path == "/domains":
            return httpx.Response(200, json=state["domains"])
        if path.endswith("/image"):
            return httpx.Response(404, json={"detail": "No image"})
        if path == "/demo/customers":
            return httpx.Response(200, json=[])
        if path == "/cocktails/ingredients":
            return httpx.Response(200, json=["Gin", "Light rum", "Lime", "Mint", "Soda water"])
        if path == "/cocktails/categories":
            return httpx.Response(200, json=["Cocktail", "Ordinary Drink"])
        if path == "/cocktails/search":
            hits = [{"cocktail": MOJITO, "score": 0.9, "reasons": ["Contains lime, mint"]}]
            return httpx.Response(200, json=hits)
        if path == "/cocktails/pantry":
            matches = [
                {"cocktail": VIRGIN, "have": ["Lime", "Mint"], "missing": [], "coverage": 1.0},
                {
                    "cocktail": MOJITO,
                    "have": ["Lime", "Mint"],
                    "missing": ["Light rum", "Soda water"],
                    "coverage": 0.5,
                },
            ]
            return httpx.Response(200, json=matches)
        if path == "/cocktails/1/similar":
            return httpx.Response(
                200, json=[{"cocktail": VIRGIN, "score": 0.7, "reasons": ["Shares lime, mint"]}]
            )
        if path in ("/cocktails/1", "/cocktails/2"):
            return httpx.Response(200, json=MOJITO if path.endswith("1") else VIRGIN)
        if path == "/chat/stream":
            return httpx.Response(200, text=ndjson(state["replies"].pop(0)))
        if path == "/chat/feedback":
            return httpx.Response(204)
        raise AssertionError(f"Unexpected API call: {request.url}")

    client = httpx.Client(base_url="http://offline.test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(ui, "api_client", lambda: client)
    ui.product_photo.clear()
    yield calls, state
    client.close()


def paths(calls) -> list[str]:
    return [path for _, path, _, _ in calls]


def bodies(calls, path: str) -> list:
    return [body for _, p, _, body in calls if p == path]


def run_main():
    from clothes_recommender import ui

    ui.main()


def cocktail_app(script: str, **state) -> AppTest:
    app = AppTest.from_string(
        f"from clothes_recommender import ui; ui.{script}", default_timeout=10
    )
    app.session_state["domain"] = "cocktails"
    for key, value in state.items():
        app.session_state[key] = value
    return app


# --- Domain switch and navigation ------------------------------------------------------------


def test_header_switch_defaults_to_fashion_and_switches_home_to_cocktails(api):
    calls, _ = api
    app = AppTest.from_function(run_main, default_timeout=10).run()
    assert not app.exception
    switch = app.segmented_control(key="domain_switch")
    assert switch.value == "fashion"
    assert switch.options == ["Fashion", "Cocktails"]
    assert not switch.disabled
    assert app.title[0].value == "Find your next favorite."
    # Fashion requests do not depend on the cocktail endpoints.
    assert not any(path.startswith(("/domains", "/cocktails")) for path in paths(calls))

    switch.set_value("cocktails").run()
    assert not app.exception
    assert app.session_state["domain"] == "cocktails"
    assert app.title[0].value == "Mix something new tonight."
    assert any("TheCocktailDB" in caption.value for caption in app.caption)
    assert any(b.key == "chat_starter_home_2" for b in app.button)
    assert app.chat_input[0].proto.placeholder == "Describe a drink or ask a follow-up…"
    # The switch keeps its value across reruns, and back again restores fashion.
    app.run()
    assert app.segmented_control(key="domain_switch").value == "cocktails"
    app.segmented_control(key="domain_switch").set_value("fashion").run()
    assert app.title[0].value == "Find your next favorite."


def test_cocktails_option_is_disabled_with_a_hint_when_the_catalog_is_missing(api):
    _, state = api
    state["health"] = {**HEALTH, "cocktails_enabled": False, "cocktail_count": 0}
    app = AppTest.from_function(run_main, default_timeout=10)
    app.session_state["domain"] = "cocktails"
    app.run()
    assert not app.exception
    switch = app.segmented_control(key="domain_switch")
    assert switch.disabled
    assert switch.value == "fashion"
    assert app.session_state["domain"] == "fashion"
    hint = next(c for c in app.caption if c.value == "Cocktails are not available")
    assert "prepare_cocktails" in hint.proto.help


def test_health_without_cocktail_fields_keeps_the_fashion_app(api):
    _, state = api
    state["health"] = {"status": "ok", "indexed_products": 2, "chat_enabled": False}
    app = AppTest.from_function(run_main, default_timeout=10).run()
    assert not app.exception
    assert app.segmented_control(key="domain_switch").disabled
    assert app.title[0].value == "Find your next favorite."


COCKTAILS_ONLY = {
    **HEALTH,
    "indexed_products": 0,
    "catalog_products": 0,
    "chat_enabled": False,
    "fashion_enabled": False,
    "llm_configured": False,
    "domains": [
        {
            "id": "fashion",
            "label": "Fashion",
            "enabled": False,
            "chat_enabled": False,
            "item_count": 0,
            "hint": "The fashion catalog is not prepared on this server: see the guide.",
        },
        {
            "id": "cocktails",
            "label": "Cocktails",
            "enabled": True,
            "chat_enabled": False,
            "item_count": 425,
            "hint": "Chat is disabled: set LLM_MODEL in .env.",
        },
    ],
}
FASHION_PATHS = ("/demo/customers", "/products", "/sections", "/users", "/search", "/evaluation")


@pytest.mark.parametrize("stored", [None, "fashion"])
def test_without_fashion_the_app_opens_on_cocktails(api, stored):
    calls, state = api
    state["health"] = COCKTAILS_ONLY
    app = AppTest.from_function(run_main, default_timeout=10)
    if stored:
        app.session_state["domain"] = stored  # e.g. remembered from before fashion went away
    app.run()
    assert not app.exception
    assert app.session_state["domain"] == "cocktails"
    switch = app.segmented_control(key="domain_switch")
    assert (switch.value, switch.options, switch.disabled) == ("cocktails", ["Cocktails"], True)
    hint = next(c for c in app.caption if c.value == "Fashion is not available")
    assert "fashion catalog is not prepared" in hint.proto.help
    assert app.title[0].value == "Mix something new tonight."
    # Chat says how to enable it instead of failing, and nothing asks for fashion data.
    assert any("search recipes on the Cocktails page" in info.value for info in app.info)
    assert any("LLM_MODEL" in caption.value for caption in app.caption)
    assert not app.chat_input
    assert not any(path.startswith(FASHION_PATHS) for path in paths(calls))
    assert app.caption[-1].value.startswith("Recipe data: TheCocktailDB · 425 recipes")


def test_cocktails_page_works_without_fashion(api):
    calls, state = api
    state["health"] = COCKTAILS_ONLY
    app = cocktail_app("cocktails_page()").run()
    # The search box is in a form: like a shopper, submit it (Streamlit 1.67's AppTest no longer
    # applies a form's values without the submit, as the browser never did).
    app.text_input(key="cocktail_query").input("mint")
    app.button(key="cocktail_search_submit").click().run()
    assert not app.exception
    assert bodies(calls, "/cocktails/search")[-1]["query"] == "mint"
    assert not any(path.startswith(FASHION_PATHS) for path in paths(calls))


def test_without_any_domain_the_app_explains_how_to_start(api):
    calls, state = api
    domains = [
        {**d, "enabled": False, "hint": f"Prepare {d['id']}."} for d in COCKTAILS_ONLY["domains"]
    ]
    state["health"] = {**COCKTAILS_ONLY, "cocktails_enabled": False, "domains": domains}
    app = AppTest.from_function(run_main, default_timeout=10).run()
    assert not app.exception
    assert app.title[0].value == "Almost ready."
    assert app.code[0].value == ui.QUICK_START
    assert any("Prepare fashion." in m.value for m in app.markdown)
    assert any("Prepare cocktails." in m.value for m in app.markdown)
    assert paths(calls) == ["/health"]


def test_served_domains_reads_new_and_legacy_health():
    assert ui.available_domains(COCKTAILS_ONLY) == ["cocktails"]
    assert ui.available_domains({**HEALTH}) == ["fashion", "cocktails"]
    legacy = ui.served_domains({"status": "ok", "chat_enabled": True})
    assert legacy["fashion"]["chat_enabled"] is True
    assert legacy["cocktails"] == {"id": "cocktails", "enabled": False, "chat_enabled": None}
    unknown = {"domains": [{"id": "music", "enabled": True}, *COCKTAILS_ONLY["domains"]]}
    assert ui.available_domains(unknown) == ["cocktails"]  # a newer server's extra domain


def run_navigation():
    import streamlit as st

    from clothes_recommender import ui

    def describe(pages):
        return [(page.title, page.url_path, page.visibility) for page in pages]

    st.session_state["pages"] = {
        "fashion": describe(ui.domain_pages("fashion", ["fashion", "cocktails"])),
        "cocktails": describe(ui.domain_pages("cocktails", ["fashion", "cocktails"])),
        "disabled": describe(ui.domain_pages("fashion", ["fashion"])),
        "cocktails_only": describe(ui.domain_pages("cocktails", ["cocktails"])),
    }


def test_navigation_shows_each_domains_pages_and_keeps_every_route():
    app = AppTest.from_function(run_navigation).run()
    assert not app.exception
    pages = app.session_state["pages"]

    def visible(name):
        return [(title, path) for title, path, shown in pages[name] if shown == "visible"]

    assert visible("fashion") == [
        ("Ask Curio", ""),
        ("Fashion", "fashion"),
        ("Discover", "discover"),
        ("Product connections", "connections"),
    ]
    assert visible("cocktails") == [("Ask Curio", ""), ("Cocktails", "cocktails")]
    routes = {path for _, path, _ in pages["cocktails"]}
    assert {"fashion", "discover", "connections", "quality", "assistant", "cocktails"} <= routes
    hidden = {path for _, path, shown in pages["fashion"] if shown == "hidden"}
    assert hidden == {"quality", "assistant", "cocktails"}
    assert "cocktails" not in {path for _, path, _ in pages["disabled"]}
    # Without fashion data, no fashion route is registered: deep links fall back to home.
    assert {path for _, path, _ in pages["cocktails_only"]} == {"", "cocktails", "assistant"}
    assert all(ui.PAGE_DOMAINS[path] == "fashion" for path in ("fashion", "discover", "quality"))


def test_catalog_note_follows_the_domain(api):
    app = AppTest.from_function(run_main, default_timeout=10)
    app.session_state["domain"] = "cocktails"
    app.run()
    assert not app.exception
    note = app.caption[-1].value
    assert note == "Recipe data: TheCocktailDB · 425 recipes · Please drink responsibly."


# --- Cocktails page ------------------------------------------------------------------------


def test_search_waits_for_criteria_then_sends_the_query_and_renders_cards(api):
    calls, _ = api
    app = cocktail_app("cocktails_page()").run()
    assert not app.exception
    assert app.multiselect(key="cocktail_ingredients").options == [
        "Gin",
        "Light rum",
        "Lime",
        "Mint",
        "Soda water",
    ]
    assert "/cocktails/search" not in paths(calls)

    app.text_input(key="cocktail_query").set_value("fresh minty")
    app.multiselect(key="cocktail_ingredients").set_value(["Lime"])
    app.segmented_control(key="cocktail_alcohol").set_value("alcoholic")
    app.selectbox(key="cocktail_category").set_value("Cocktail")
    app.button(key="cocktail_search_submit").click().run()
    assert not app.exception
    assert bodies(calls, "/cocktails/search")[-1] == {
        "query": "fresh minty",
        "ingredients": ["Lime"],
        "alcohol": "alcoholic",
        "category": "Cocktail",
        "top_k": 12,
    }
    assert any(text.value == "Mojito" for text in app.text)
    assert any(c.value == "Cocktail · Highball glass" for c in app.caption)
    assert any(c.value == "Contains lime, mint" for c in app.caption)
    # The https photo URL is handed to the browser instead of being fetched by the UI.
    urls = [image.proto.imgs[0].url for image in app.get("image")]
    assert MOJITO["image_url"] in urls
    assert any(b.label == "View Mojito" for b in app.button)


def test_search_example_runs_immediately(api):
    calls, _ = api
    app = cocktail_app("cocktails_page()").run()
    app.button(key="cocktail_example_0").click().run()
    assert not app.exception
    assert bodies(calls, "/cocktails/search")[-1]["query"] == "Margarita"


def test_pantry_sends_ingredients_and_shows_have_and_missing_chips(api):
    calls, _ = api
    app = cocktail_app("cocktails_page()").run()
    app.segmented_control(key="cocktail_mode").set_value("pantry").run()
    assert not app.exception
    assert "/cocktails/pantry" not in paths(calls)
    app.multiselect(key="pantry_ingredients").set_value(["Lime", "Mint"]).run()
    app.slider(key="pantry_max_missing").set_value(3).run()
    assert not app.exception
    assert bodies(calls, "/cocktails/pantry")[-1] == {
        "ingredients": ["Lime", "Mint"],
        "alcohol": "any",
        "max_missing": 3,
        "top_k": 9,
    }
    markdown = [m.value for m in app.markdown]
    assert ":green-badge[:material/check: Lime] :green-badge[:material/check: Mint]" in markdown
    assert ":gray-badge[:material/add: Light rum] :gray-badge[:material/add: Soda water]" in (
        markdown
    )
    assert any(c.value == "You can make this now." for c in app.caption)
    # The alcohol-free drink without a photo gets a placeholder rather than a broken image.
    assert any(c.value == "No photo available." for c in app.caption)


def test_unprepared_catalog_explains_how_to_prepare_it(api):
    _, state = api
    detail = "Cocktail recipes are not prepared: run scripts.prepare_cocktails."
    state["errors"] = {"/cocktails/ingredients": (503, detail)}
    app = cocktail_app("cocktails_page()").run()
    assert not app.exception
    assert app.error[0].value == detail
    assert not app.multiselect


def test_recipe_dialog_lists_measures_credit_similar_drinks_and_saves(api):
    calls, _ = api
    app = cocktail_app("cocktails_page()").run()
    app.button(key="cocktail_example_0").click().run()
    app.button(key="cocktail_view_search_1_0").click().run()
    assert not app.exception
    assert ("GET", "/cocktails/1", {}, None) in calls
    assert ("GET", "/cocktails/1/similar", {"top_k": "5", "alcohol": "any"}, None) in calls
    assert any(header.value == "Mojito" for header in app.subheader)
    recipe = "- 2-3 oz Light rum\n- Juice of 1 Lime\n- 2-4 Mint\n- Soda water"
    assert any(m.value == recipe for m in app.markdown)
    assert any(t.value.startswith("Muddle mint") for t in app.text)
    assert any(t.value == "Virgin Mojito · Alcohol-free · Shares lime, mint" for t in app.text)
    assert any(c.value == ui.RECIPE_CREDIT for c in app.caption)
    app.button(key="cocktail_detail_save_1").click().run()
    assert not app.exception
    assert [c["drink_id"] for c in app.session_state["cocktail_shortlist"]] == [1]


def test_recipe_dialog_hands_off_to_cocktail_chat(api):
    app = AppTest.from_string(
        "from clothes_recommender import ui; ui.cocktail_details(1)", default_timeout=10
    )
    app.session_state["domain"] = "cocktails"
    app.run()
    app.button(key="cocktail_detail_ask_1").click().run()
    assert not app.exception
    assert app.session_state["chat_queued_cocktails"] == "Tell me about the Mojito (drink 1)."
    assert app.session_state["assistant_popup"] is True
    assert "chat_queued" not in app.session_state


# --- Cocktail chat --------------------------------------------------------------------------


def test_cocktail_chat_payload_carries_domain_tastes_shortlist_and_policy(api):
    calls, state = api
    state["replies"] = [reply(), reply(text="Both use mint.", cocktails=[])]
    app = cocktail_app(
        "agent_home()",
        cocktail_preferences={"alcohol_free": True, "liked_ingredients": ["Mint"]},
        cocktail_shortlist=[VIRGIN],
        chat_history=[{"role": "user", "content": "A fashion turn"}],
    ).run()
    assert not app.exception
    app.toggle(key="session_ask_shortlist").set_value(False).run()
    app.chat_input[0].set_value("Something minty").run()
    assert not app.exception
    body = bodies(calls, "/chat/stream")[-1]
    assert body == {
        "message": "Something minty",
        "history": [],
        "domain": "cocktails",
        "cocktail_preferences": {"alcohol_free": True, "liked_ingredients": ["Mint"]},
        "cocktail_shortlist": [2],
        "auto_approve": ["SaveCocktails"],
    }
    assert any("Checked against the recipes" in m.value for m in app.markdown)
    assert any(t.value == "Mojito" for t in app.text)
    assert any(c.value == "Alcoholic · Highball glass" for c in app.caption)
    app.button(key="chat_drink_save_home_1_1").click().run()
    assert [c["drink_id"] for c in app.session_state["cocktail_shortlist"]] == [2, 1]

    app.chat_input[0].set_value("Compare them").run()
    assert not app.exception
    followup = bodies(calls, "/chat/stream")[-1]
    assert followup["history"][-1]["drink_ids"] == [1, 2]
    request = ChatRequest.model_validate(followup)
    assert request.domain == "cocktails"
    assert request.cocktail_preferences.alcohol_free is True
    assert "product_ids" not in followup["history"][-1]
    # Fashion history is untouched by the cocktail conversation.
    assert app.session_state["chat_history"] == [{"role": "user", "content": "A fashion turn"}]
    assert len(app.session_state["chat_history_cocktails"]) == 4


def test_starter_prompt_sends_a_cocktail_message(api):
    calls, state = api
    state["replies"] = [reply()]
    app = cocktail_app("agent_home()").run()
    starters = [b.label for b in app.button if b.key.startswith("chat_starter_home_")]
    assert starters == list(ui.DOMAIN_VIEWS["cocktails"].copy.starters)
    app.button(key="chat_starter_home_1").click().run()
    assert not app.exception
    assert bodies(calls, "/chat/stream")[-1]["message"] == "Alcohol-free drinks"


def test_cocktail_chat_unavailable_has_a_recovery_path(api):
    calls, state = api
    state["domains"] = [{**DOMAINS[1], "chat_enabled": False}]
    app = cocktail_app("agent_home()").run()
    assert not app.exception
    assert any("search recipes on the Cocktails page" in info.value for info in app.info)
    assert not app.chat_input
    assert "/chat/stream" not in paths(calls)


def proposal(tool: str, args: dict, **extra) -> dict:
    action = {
        "id": "a1",
        "tool": tool,
        "args": args,
        "request": "remember",
        "issued_at": 1,
        "signature": "sig",
        "summary": f"Proposed: {tool}.",
        "products": [],
    } | extra
    return reply(
        text="May I?",
        status="needs_approval",
        grounding=None,
        suggestions=[],
        pending_action=action,
    )


def test_save_cocktails_approval_can_be_narrowed_and_applies_to_the_cocktail_shortlist(api):
    calls, state = api
    action = proposal(
        "SaveCocktails", {"drink_ids": [1, 2], "note": ""}, cocktails=[MOJITO, VIRGIN]
    )
    applied = reply(
        text="Saved the Virgin Mojito.",
        cocktails=[VIRGIN],
        applied=[{"kind": "cocktail_shortlist", "drink_ids": [2]}],
    )
    state["replies"] = [action, applied]
    app = cocktail_app("agent_home()").run()
    app.chat_input[0].set_value("save both").run()
    labels = [c.label for c in app.checkbox]
    assert labels == ["Mojito · Alcoholic", "Virgin Mojito · Alcohol-free"]
    app.checkbox(key="approve_home_1_drink_ids_1").uncheck().run()
    app.button(key="approve_home_1").click().run()
    assert not app.exception
    body = bodies(calls, "/chat/stream")[-1]
    assert body["domain"] == "cocktails"
    assert body["decision"]["edited_args"] == {"drink_ids": [2]}
    assert body["decision"]["action"]["signature"] == "sig"
    assert [c["drink_id"] for c in app.session_state["cocktail_shortlist"]] == [2]
    assert "shortlist" not in app.session_state
    assert any(t.value == "Virgin Mojito" for t in app.text)  # the session panel shortlist


def test_remember_tastes_approval_edits_values_and_memory_chips_can_be_forgotten(api):
    calls, state = api
    args = {"alcohol_free": True, "liked_ingredients": ["Mint", "Lime"], "avoided_ingredients": []}
    applied = reply(
        text="Noted.",
        cocktails=[],
        applied=[
            {
                "kind": "cocktail_preferences",
                "cocktail_preferences": {
                    "alcohol_free": False,
                    "liked_ingredients": ["Lime"],
                    "avoided_ingredients": [],
                },
            }
        ],
    )
    state["replies"] = [proposal("RememberTastes", args), applied]
    app = cocktail_app("agent_home()", cocktail_preferences={"avoided_ingredients": ["Gin"]}).run()
    app.chat_input[0].set_value("I like mint and lime, no alcohol").run()
    assert [c.label for c in app.checkbox] == [
        "Likes Mint",
        "Likes Lime",
        "Alcohol-free drinks only",
    ]
    app.checkbox(key="approve_home_1_liked_ingredients_Mint").uncheck()
    app.checkbox(key="approve_home_1_alcohol_free_True").uncheck().run()
    app.button(key="approve_home_1").click().run()
    assert not app.exception
    edits = bodies(calls, "/chat/stream")[-1]["decision"]["edited_args"]
    assert edits == {"liked_ingredients": ["Lime"], "alcohol_free": False}
    memory = app.session_state["cocktail_preferences"]
    assert memory == {"avoided_ingredients": ["Gin"], "liked_ingredients": ["Lime"]}
    chips = [b.label for b in app.button if b.key.startswith("forget_cocktail_")]
    assert chips == ["Likes Lime", "Avoids Gin"]
    app.button(key="forget_cocktail_avoided_ingredients_Gin").click().run()
    assert app.session_state["cocktail_preferences"]["avoided_ingredients"] == []


def test_alcohol_free_memory_chip_and_shortlist_compare(api):
    app = cocktail_app(
        "cocktail_session_panel()",
        cocktail_preferences={"alcohol_free": True},
        cocktail_shortlist=[MOJITO, VIRGIN],
    ).run()
    assert not app.exception
    assert app.button(key="forget_cocktail_alcohol_free_True").label == "Alcohol-free"
    app.button(key="cocktail_shortlist_compare").click().run()
    assert not app.exception
    assert any(c.value == "In every recipe: lime, mint" for c in app.caption)
    assert any(m.value == "- 1 Lime\n- Mint" for m in app.markdown)
    app.run()  # close the dialog
    app.button(key="forget_cocktail_alcohol_free_True").click().run()
    assert app.session_state["cocktail_preferences"]["alcohol_free"] is False
    app.button(key="cocktail_shortlist_remove_1").click().run()
    assert [c["drink_id"] for c in app.session_state["cocktail_shortlist"]] == [2]


def test_cocktail_popup_uses_cocktail_copy_and_its_own_history(api):
    calls, state = api
    state["replies"] = [reply()]
    app = cocktail_app("assistant_popup()", assistant_popup=True).run()
    assert not app.exception
    assert any(
        c.value == "Find drinks. Refine by ingredient, glass or alcohol." for c in app.caption
    )
    assert any(c.value == ui.RECIPE_CREDIT for c in app.caption)
    app.chat_input(key="chat_input_popup").set_value("Mint please").run()
    assert not app.exception
    assert bodies(calls, "/chat/stream")[-1]["domain"] == "cocktails"
    assert any(b.key == "chat_drink_view_popup_1_1" for b in app.button)
    app.button(key="chat_reset_popup").click().run()
    assert app.session_state["chat_history_cocktails"] == []


def test_fashion_payload_omits_the_domain(api):
    calls, state = api
    state["replies"] = [reply(products=[], cocktails=[], domain="fashion")]
    app = AppTest.from_string("from clothes_recommender import ui; ui.chat_tab()").run()
    app.chat_input[0].set_value("A coat").run()
    assert not app.exception
    assert bodies(calls, "/chat/stream")[-1] == {"message": "A coat", "history": []}
    assert "/domains" not in paths(calls)
