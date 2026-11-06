"""Book → cocktail pairing in the UI: the book dialog, the Books page card and book chat."""

import json

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from curio import ui
from tests.test_ui_books import HEALTH, WIND, ndjson, reply

OLD_FASHIONED = {
    "drink_id": 269,
    "name": "Old Fashioned",
    "alcoholic": "Alcoholic",
    "category": "Cocktail",
    "glass": "Old-fashioned glass",
    "instructions": "Stir.",
    "ingredients": [{"name": "Bourbon", "measure": "2 oz"}],
    "image_url": "https://www.thecocktaildb.com/images/media/drink/old.jpg",
    "source_url": "https://www.thecocktaildb.com/",
}
HONEY_BEE = OLD_FASHIONED | {
    "drink_id": 154,
    "name": "Honey Bee",
    "glass": "Margarita glass",
    "ingredients": [{"name": "White Rum", "measure": "2 oz"}, {"name": "Honey", "measure": None}],
    "image_url": "https://www.thecocktaildb.com/images/media/drink/bee.jpg",
}
REASON = (
    "The Name of the Wind (Fantasy · shelved as epic fantasy) → something honeyed, herbal or "
    "spiced: Honey Bee (Honey)"
)


def pairing(**fields) -> dict:
    base = {
        "profile": {
            "source": "The Name of the Wind",
            "themes": [
                {
                    "id": "fantasy",
                    "label": "Fantasy and magic",
                    "style": "something honeyed, herbal or spiced",
                    "weight": 1.0,
                    "evidence": "Fantasy · shelved as epic fantasy",
                    "books": 0,
                }
            ],
            "summary": "The Name of the Wind leans Fantasy and magic",
            "alcohol_free": False,
            "alcohol_free_reason": None,
            "book_ids": [1],
        },
        "pairings": [
            {"cocktail": HONEY_BEE, "score": 0.69, "theme": "fantasy", "reasons": [REASON]},
            {
                "cocktail": OLD_FASHIONED,
                "score": 0.4,
                "theme": "fantasy",
                "reasons": ["Also bitter"],
            },
        ],
        "seed": 0,
        "sets": 3,
        "note": "Please drink responsibly.",
    }
    return base | fields


@pytest.fixture
def api(monkeypatch):
    calls: list[tuple[str, str, object]] = []
    state: dict = {"pairing": pairing(), "replies": []}

    def handle(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, path, body))
        if path == "/health":
            return httpx.Response(200, json=HEALTH)
        if path == "/pairings/book-cocktail":
            return httpx.Response(200, json=state["pairing"])
        if path == "/books/1":
            return httpx.Response(200, json=WIND)
        if path in ("/books/1/similar", "/books/1/by-author", "/cocktails/154/similar"):
            return httpx.Response(200, json=[])
        if path == "/cocktails/154":
            return httpx.Response(200, json=HONEY_BEE)
        if path == "/cocktails/categories":
            return httpx.Response(200, json=["Cocktail"])
        if path == "/cocktails/ingredients":
            return httpx.Response(200, json=["Gin", "Honey"])
        if path == "/books/genres":
            return httpx.Response(200, json=["Fantasy"])
        if path == "/books/moods":
            return httpx.Response(200, json=["Cosy"])
        if path == "/chat/stream":
            return httpx.Response(200, text=ndjson(state["replies"].pop(0)))
        raise AssertionError(f"Unexpected API call: {request.url}")

    client = httpx.Client(base_url="http://offline.test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(ui, "api_client", lambda: client)
    yield calls, state
    client.close()


def pairing_bodies(calls) -> list:
    return [body for _, path, body in calls if path == "/pairings/book-cocktail"]


def app_for(script: str, *, served: bool = True, **state) -> AppTest:
    app = AppTest.from_string(f"from curio import ui; ui.{script}", default_timeout=10)
    app.session_state["domain"] = "books"
    if served:
        app.session_state["service_health"] = HEALTH
    for key, value in state.items():
        app.session_state[key] = value
    return app


def test_book_dialog_pairs_a_drink_with_photos_reasons_toggle_and_another(api):
    calls, _ = api
    app = app_for(
        "book_details(1)",
        book_preferences={"avoided_genres": ["Horror"]},
        cocktail_preferences={"alcohol_free": False, "avoided_ingredients": ["Egg"]},
    ).run()
    assert not app.exception
    assert pairing_bodies(calls) == []  # nothing is poured until asked
    app.button(key="book_detail_pair_1").click().run()
    assert not app.exception
    assert pairing_bodies(calls)[-1] == {
        "book_id": 1,
        "avoided_genres": ["Horror"],
        "cocktail_preferences": {"avoided_ingredients": ["Egg"]},
        "alcohol_free": False,
        "seed": 0,
    }
    assert any("Pour me something to read with" in m.value for m in app.markdown)
    assert {t.value for t in app.text} >= {"Honey Bee", "Old Fashioned"}
    assert any(c.value == ui.plain(REASON) for c in app.caption)
    assert any(c.value == "The Name of the Wind leans Fantasy and magic" for c in app.caption)
    urls = [img.url for element in app.get("image") for img in element.proto.imgs]
    assert "https://www.thecocktaildb.com/images/media/drink/bee.jpg" in urls
    app.button(key="pair_another_book_1").click().run()
    assert pairing_bodies(calls)[-1]["seed"] == 1
    app.toggle(key="pair_alcohol_free_book_1").set_value(True).run()
    assert pairing_bodies(calls)[-1]["alcohol_free"] is True
    app.button(key="pairing_save_book_1_154").click().run()
    assert [c["drink_id"] for c in app.session_state["cocktail_shortlist"]] == [154]


def test_the_recipe_button_in_a_dialog_hands_off_to_the_cocktails_page(api):
    app = app_for("book_details(1)", **{"pair_open_1": True}).run()
    app.button(key="pairing_recipe_book_1_154").click().run()
    assert not app.exception
    assert app.session_state["open_drink"] == 154
    assert app.session_state["domain"] == "cocktails"


def test_the_cocktails_page_opens_a_handed_over_drink(api):
    calls, _ = api
    app = app_for("cocktails_page()", open_drink=154).run()
    assert not app.exception
    assert ("GET", "/cocktails/154", None) in calls
    assert "open_drink" not in app.session_state
    assert any(h.value == "Honey Bee" for h in app.subheader)


def test_another_pairing_is_disabled_with_one_set_and_empty_results_say_so(api):
    _, state = api
    state["pairing"] = pairing(sets=1, pairings=[])
    app = app_for("book_details(1)", **{"pair_open_1": True}).run()
    assert app.button(key="pair_another_book_1").disabled
    assert any("No recipe fits this profile yet" in i.value for i in app.info)


def test_no_pair_button_without_cocktails(api):
    health = json.loads(json.dumps(HEALTH))
    health["domains"][1]["enabled"] = False
    app = app_for("book_details(1)", service_health=health).run()
    assert not any(b.key == "book_detail_pair_1" for b in app.button)


def test_books_page_shows_the_reading_lists_cocktail(api):
    calls, _ = api
    app = app_for(
        "books_page()",
        book_shortlist=[WIND],
        book_preferences={"liked_genres": ["Fantasy"]},
        cocktail_preferences={"alcohol_free": True},
    ).run()
    assert not app.exception
    assert any(h.value == "Your reading list's cocktail" for h in app.subheader)
    assert pairing_bodies(calls)[-1] == {
        "book_ids": [1],
        "liked_genres": ["Fantasy"],
        "cocktail_preferences": {"alcohol_free": True},
        "alcohol_free": False,
        "seed": 0,
    }
    # Clicking a drink outside a dialog opens its recipe straight away.
    app.button(key="pairing_recipe_reading_list_154").click().run()
    assert not app.exception
    assert ("GET", "/cocktails/154", None) in calls


@pytest.mark.parametrize("state", [{}, {"book_preferences": {"avoided_genres": ["Horror"]}}])
def test_no_reading_list_card_without_a_list_or_liked_genres(api, state):
    calls, _ = api
    app = app_for("books_page()", **state).run()
    assert not any(h.value == "Your reading list's cocktail" for h in app.subheader)
    assert pairing_bodies(calls) == []


def test_book_chat_renders_the_pairing_and_sends_cocktail_tastes(api):
    calls, state = api
    state["replies"] = [
        reply(
            text="Pour a **Honey Bee** (drink #154).",
            books=[],
            cocktails=[HONEY_BEE, OLD_FASHIONED],
            pairing=pairing(),
            suggestions=["Another pairing"],
        ),
        reply(text="Sure.", books=[]),
    ]
    app = app_for("agent_home()", cocktail_preferences={"alcohol_free": True}).run()
    assert not app.exception
    starters = [b.label for b in app.button if b.key and b.key.startswith("chat_starter_")]
    assert "Pair a cocktail with my reading list" in starters
    app.chat_input[0].set_value("What should I drink with The Name of the Wind?").run()
    assert not app.exception
    sent = [body for _, path, body in calls if path == "/chat/stream"][-1]
    assert sent["cocktail_preferences"] == {"alcohol_free": True}
    keys = {getattr(element, "key", None) for element in app.button}
    assert "pairing_recipe_chat_home_1_154" in keys
    assert not any(k and k.startswith("chat_drink_view_") for k in keys)  # no duplicate cards
    assert any(c.value == ui.plain(REASON) for c in app.caption)
    app.button(key="chat_suggestion_home_1_0").click().run()
    followup = [body for _, path, body in calls if path == "/chat/stream"][-1]
    assert followup["message"] == "Another pairing"
    assert followup["history"][-1]["drink_ids"] == [154, 269]
