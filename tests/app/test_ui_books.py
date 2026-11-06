"""Book domain UI: the domain switch, Books page, book dialog and per-domain book chat."""

import json

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from curio.app import ui
from curio.app.schemas import ChatRequest

WIND = {
    "book_id": 1,
    "title": "The Name of the Wind",
    "authors": ["Patrick Rothfuss"],
    "series": "The Kingkiller Chronicle",
    "series_position": "1",
    "year": 2007,
    "language": "eng",
    "average_rating": 4.55,
    "ratings_count": 400_000,
    "pages": 662,
    "description": "Told in Kvothe's own voice.",
    "genres": ["Fantasy"],
    "tags": ["epic fantasy", "magic"],
    "image_url": "https://images.gr-assets.com/books/1270352123m/186074.jpg",
    "source_url": "https://www.goodreads.com/book/show/186074",
    "readers": 3948,
}
KINGS = {
    **WIND,
    "book_id": 3,
    "title": "The Way of Kings",
    "authors": ["Brandon Sanderson"],
    "series": "The Stormlight Archive",
    "average_rating": 4.64,
    "pages": 1007,
    "image_url": None,
}
HEALTH = {
    "status": "ok",
    "indexed_products": 0,
    "catalog_products": 0,
    "chat_enabled": False,
    "ranking_enabled": False,
    "fashion_enabled": False,
    "cocktails_enabled": True,
    "cocktail_count": 425,
    "books_enabled": True,
    "book_count": 10_000,
    "llm_configured": True,
    "domains": [
        {"id": "fashion", "label": "Fashion", "enabled": False, "chat_enabled": False,
         "item_count": 0, "hint": "Prepare fashion."},
        {"id": "cocktails", "label": "Cocktails", "enabled": True, "chat_enabled": True,
         "item_count": 425, "hint": None},
        {"id": "books", "label": "Books", "enabled": True, "chat_enabled": True,
         "item_count": 10_000, "hint": None},
    ],
}  # fmt: skip
STEP = {
    "kind": "tool",
    "name": "SearchBooks",
    "summary": "Found 2 books.",
    "status": "ok",
    "detail": {},
    "duration_ms": 300,
}


def hit(book: dict, *reasons: str) -> dict:
    return {"book": book, "score": 0.5, "reasons": list(reasons)}


def reply(**fields):
    base = {
        "text": "Try **The Way of Kings**.",
        "products": [],
        "cocktails": [],
        "books": [KINGS, WIND],
        "domain": "books",
        "intent": {"query": "fantasy"},
        "status": "complete",
        "steps": [STEP],
        "grounding": {"status": "verified", "checked": 2, "issues": []},
        "suggestions": ["More by Brandon Sanderson"],
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
    state: dict = {"health": json.loads(json.dumps(HEALTH)), "replies": []}

    def handle(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, path, dict(request.url.params), body))
        if path == "/health":
            return httpx.Response(200, json=state["health"])
        if path == "/books/genres":
            return httpx.Response(200, json=["Fantasy", "Mystery", "Horror"])
        if path == "/books/moods":
            return httpx.Response(200, json=["Cosy", "Dark"])
        if path == "/books/index":
            refs = [{"book_id": b["book_id"], "title": b["title"], "author": b["authors"][0]}
                    for b in (KINGS, WIND)]  # fmt: skip
            return httpx.Response(200, json=refs)
        if path == "/books/search":
            hits = state.get("search_hits") or [hit(KINGS, "960 readers who liked it")]
            return httpx.Response(200, json=hits)
        if path == "/books/because":
            return httpx.Response(200, json=[hit(KINGS, "Because you liked The Name of the Wind")])
        if path == "/books/1":
            return httpx.Response(200, json=WIND)
        if path == "/books/3":
            return httpx.Response(200, json=state.get("book_3", KINGS))
        if path == "/books/1/similar":
            return httpx.Response(200, json=[hit(KINGS, "960 readers who liked it")])
        if path in ("/books/1/by-author", "/books/3/similar", "/books/3/by-author"):
            return httpx.Response(200, json=[])
        if path == "/chat/stream":
            return httpx.Response(200, text=ndjson(state["replies"].pop(0)))
        raise AssertionError(f"Unexpected API call: {request.url}")

    client = httpx.Client(base_url="http://offline.test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(ui.client, "api_client", lambda: client)
    ui.book_index.clear()
    yield calls, state
    client.close()


def paths(calls) -> list[str]:
    return [path for _, path, _, _ in calls]


def bodies(calls, path: str) -> list:
    return [body for _, p, _, body in calls if p == path]


def run_main():
    from curio.app import ui

    ui.main()


def book_app(script: str, **state) -> AppTest:
    app = AppTest.from_string(f"from curio.app import ui; ui.{script}", default_timeout=10)
    app.session_state["domain"] = "books"
    for key, value in state.items():
        app.session_state[key] = value
    return app


# --- Domain switch -------------------------------------------------------------------------


def test_books_is_the_third_domain_in_the_switch(api):
    app = AppTest.from_function(run_main, default_timeout=10).run()
    assert not app.exception
    switch = app.segmented_control(key="domain_switch")
    assert switch.options == ["Cocktails", "Books"]
    assert switch.value == "cocktails"  # the first served domain
    switch.set_value("books").run()
    assert not app.exception
    assert app.title[0].value == "Find your next great read."
    assert app.chat_input[0].proto.placeholder == "Describe a book or ask a follow-up…"
    assert app.caption[-1].value == (
        "Book data: goodbooks-10k from Goodreads (CC BY-SA 4.0) · 10,000 books · "
        "Ratings are Goodreads averages, not Curio's. · Some covers: Open Library"
    )
    assert app.toggle(key="session_ask_shortlist").label == "Ask before adding to my reading list"


def test_books_not_served_are_marked_unavailable(api):
    _, state = api
    state["health"]["domains"][2] |= {"enabled": False, "hint": "Run scripts.prepare_books."}
    app = AppTest.from_function(run_main, default_timeout=10)
    app.session_state["domain"] = "books"
    app.run()
    assert not app.exception
    assert app.session_state["domain"] == "cocktails"
    hint = next(c for c in app.caption if c.value == "Books are not available")
    assert hint.proto.help == "Run scripts.prepare_books."


def run_navigation():
    import streamlit as st

    from curio.app import ui

    st.session_state["routes"] = {
        name: [page.url_path for page in ui.domain_pages("books", available)]
        for name, available in {"both": ["cocktails", "books"], "none": ["cocktails"]}.items()
    }


def test_navigation_registers_the_books_page_only_when_served():
    app = AppTest.from_function(run_navigation, default_timeout=10).run()
    assert not app.exception
    assert "books" in app.session_state["routes"]["both"]
    assert "books" not in app.session_state["routes"]["none"]
    assert ui.PAGE_DOMAINS["books"] == "books"


# --- Books page ----------------------------------------------------------------------------


def test_search_sends_filters_and_remembered_tastes_and_renders_cards(api):
    calls, _ = api
    memory = {"liked_genres": ["Fantasy"], "avoided_genres": ["Horror"], "avoided_authors": []}
    app = book_app("books_page()", book_preferences=memory).run()
    assert not app.exception
    assert "/books/search" not in paths(calls)
    app.text_input(key="book_query").set_value("epic magic")
    app.multiselect(key="book_genres").set_value(["Fantasy"])
    app.selectbox(key="book_mood").set_value("Dark")
    app.selectbox(key="book_pages").set_value(400)
    app.button(key="book_search_submit").click().run()
    assert not app.exception
    assert bodies(calls, "/books/search")[-1] == {
        "query": "epic magic",
        "genres": ["Fantasy"],
        "mood": "Dark",
        "max_pages": 400,
        "boost_genres": ["Fantasy"],
        "top_k": 12,
        "exclude_genres": ["Horror"],
        "exclude_authors": [],
    }
    assert any(t.value == "The Way of Kings" for t in app.text)
    assert any(c.value == "960 readers who liked it" for c in app.caption)
    assert any(b.label == "View The Way of Kings" for b in app.button)
    # No cover: a placeholder that names the book instead of a broken image.
    assert any(c.value == "The Way of Kings" for c in app.caption)


def test_examples_run_a_search_and_covers_use_the_larger_image(api):
    calls, _ = api
    app = book_app("show_books([" + repr(WIND) + "], context='t')").run()
    urls = [image.proto.imgs[0].url for image in app.get("image")]
    assert urls == ["https://images.gr-assets.com/books/1270352123l/186074.jpg"]
    app = book_app("books_page()").run()
    app.button(key="book_example_1").click().run()
    assert not app.exception
    assert bodies(calls, "/books/search")[-1]["query"] == "Short classic novels"


def test_because_you_liked_starts_from_the_reading_list(api):
    calls, _ = api
    app = book_app("books_page()", book_shortlist=[WIND]).run()
    app.segmented_control(key="book_mode").set_value("because").run()
    assert not app.exception
    assert app.multiselect(key="book_loved").value == [1]
    assert bodies(calls, "/books/because")[-1] == {
        "liked": [1],
        "top_k": 12,
        "exclude_genres": [],
        "exclude_authors": [],
    }
    assert any(c.value == "Because you liked The Name of the Wind" for c in app.caption)
    app.multiselect(key="book_loved").set_value([]).run()
    assert any("Add at least one book you loved" in c.value for c in app.caption)


def test_browse_by_mood_searches_without_a_query(api):
    calls, _ = api
    app = book_app("books_page()").run()
    app.segmented_control(key="book_mode").set_value("browse").run()
    assert "/books/search" not in paths(calls)
    app.pills(key="browse_mood").set_value("Cosy").run()
    assert not app.exception
    body = bodies(calls, "/books/search")[-1]
    assert (body["mood"], body["genres"], "query" in body) == ("Cosy", [], False)
    assert any(h.value == "Cosy" for h in app.subheader)


def test_book_dialog_shows_facts_related_books_saves_and_hands_off(api):
    calls, _ = api
    app = book_app("book_details(1)").run()
    assert not app.exception
    assert ("GET", "/books/1/similar", {"top_k": "5", "other_authors_only": "true"}, None) in calls
    assert any(h.value == "The Name of the Wind" for h in app.subheader)
    # Markdown characters are escaped, so "#1" shows literally.
    assert any(c.value == "2007 · 662 pages · The Kingkiller Chronicle \\#1" for c in app.caption)
    assert any(t.value == "Told in Kvothe's own voice." for t in app.text)
    assert any(t.value == "The Way of Kings · ★ 4.64 · 960 readers who liked it" for t in app.text)
    assert any(c.value == "No other books by this author in the catalog." for c in app.caption)
    assert any(c.value == ui.BOOK_CREDIT for c in app.caption)
    app.button(key="book_detail_save_1").click().run()
    assert [b["book_id"] for b in app.session_state["book_shortlist"]] == [1]
    app.button(key="book_detail_ask_1").click().run()
    assert app.session_state["chat_queued_books"] == (
        "Tell me about The Name of the Wind (book 1)."
    )


# --- Covers from Open Library --------------------------------------------------------------

ISBN = "9780765326355"
OPEN_LIBRARY = f"https://covers.openlibrary.org/b/isbn/{ISBN}-M.jpg?default=false"


@pytest.fixture
def covers(monkeypatch):
    """Open Library answers (`state["status"]`: a status code or an exception) and the URLs
    asked; caches and the outage pause are reset."""
    asked: list[str] = []
    state: dict = {"status": 200}

    def head(url, **kwargs):
        asked.append(url)
        if isinstance(state["status"], Exception):
            raise state["status"]
        content = {"content-type": "image/jpeg"} if state["status"] == 200 else {}
        return httpx.Response(state["status"], headers=content, request=httpx.Request("HEAD", url))

    monkeypatch.setattr(httpx, "head", head)
    monkeypatch.setattr(ui.books, "_cover_outage_until", 0.0)
    ui._open_library_cover.clear()
    yield asked, state
    ui._open_library_cover.clear()


def images(app) -> list[str]:
    return [img.url for element in app.get("image") for img in element.proto.imgs]


def test_a_book_without_a_goodreads_cover_shows_the_open_library_one(api, covers):
    _, state = api
    asked, answer = covers
    state["book_3"] = {**KINGS, "isbn": ISBN}
    app = book_app("book_details(3)").run()
    assert not app.exception
    assert images(app) == [OPEN_LIBRARY] and asked == [OPEN_LIBRARY]
    credit = f"Cover: [Open Library](https://openlibrary.org/isbn/{ISBN})"
    assert any(c.value == credit for c in app.caption)
    # Open Library has none (404): the placeholder with the title, never a broken image.
    ui._open_library_cover.clear()
    answer["status"] = 404
    app = book_app("book_details(3)").run()
    assert not app.exception and images(app) == []
    assert any(c.value == "The Way of Kings" for c in app.caption)  # the placeholder
    # Without an ISBN (a catalog prepared before ISBNs were kept) nothing is asked.
    state["book_3"] = KINGS
    asked.clear()
    app = book_app("book_details(3)").run()
    assert images(app) == [] and asked == []


def test_open_library_covers_fall_back_to_the_placeholder(covers):
    asked, state = covers
    kings = {"title": "The Way of Kings", "image_url": None, "isbn": ISBN}
    assert ui.open_library_cover(kings) == OPEN_LIBRARY
    assert ui.open_library_cover(kings) == OPEN_LIBRARY and asked == [OPEN_LIBRARY]  # cached
    ui._open_library_cover.clear()
    state["status"] = 404  # Open Library has no cover: the placeholder
    assert ui.open_library_cover(kings) is None
    # No checked ISBN, no request: the URL is never taken from the API.
    for isbn in (None, "", "0765326353", "9780765326355/../x"):
        assert ui.open_library_cover({**kings, "isbn": isbn}) is None
    assert len(asked) == 2
    # A network failure is not cached and pauses the lookups.
    ui._open_library_cover.clear()
    state["status"] = httpx.ConnectError("offline")
    assert ui.open_library_cover(kings) is None
    state["status"] = 200
    assert ui.open_library_cover(kings) is None and len(asked) == 3


# --- Book chat -----------------------------------------------------------------------------


def test_book_chat_payload_carries_domain_tastes_reading_list_and_policy(api):
    calls, state = api
    state["replies"] = [reply(), reply(text="Both are long.", books=[])]
    app = book_app(
        "agent_home()",
        book_preferences={"avoided_authors": ["Dan Brown"], "liked_genres": []},
        book_shortlist=[WIND],
        chat_history_cocktails=[{"role": "user", "content": "A cocktail turn"}],
    ).run()
    assert not app.exception
    app.toggle(key="session_ask_shortlist").set_value(False).run()
    app.chat_input[0].set_value("Epic fantasy").run()
    assert not app.exception
    assert bodies(calls, "/chat/stream")[-1] == {
        "message": "Epic fantasy",
        "history": [],
        "domain": "books",
        "book_preferences": {"avoided_authors": ["Dan Brown"]},
        "book_shortlist": [1],
        "auto_approve": ["SaveBooks"],
    }
    assert any("Titles and ratings checked against the catalog" in m.value for m in app.markdown)
    assert any(c.value == "Brandon Sanderson · ★ 4.64" for c in app.caption)
    app.button(key="chat_book_save_home_1_3").click().run()
    assert [b["book_id"] for b in app.session_state["book_shortlist"]] == [1, 3]

    app.chat_input[0].set_value("Which is longer?").run()
    followup = bodies(calls, "/chat/stream")[-1]
    assert followup["history"][-1]["book_ids"] == [3, 1]
    assert ChatRequest.model_validate(followup).domain == "books"
    assert app.session_state["chat_history_cocktails"] == [
        {"role": "user", "content": "A cocktail turn"}
    ]
    assert len(app.session_state["chat_history_books"]) == 4


def proposal(tool: str, args: dict, **extra) -> dict:
    action = {
        "id": "a1",
        "tool": tool,
        "args": args,
        "request": "save",
        "issued_at": 1,
        "signature": "sig",
        "summary": f"Proposed: {tool}.",
        "products": [],
    } | extra
    return reply(text="May I?", status="needs_approval", grounding=None, pending_action=action)


def test_save_books_approval_narrows_and_fills_the_reading_list(api):
    calls, state = api
    applied = reply(
        text="Saved.", books=[KINGS], applied=[{"kind": "book_shortlist", "book_ids": [3]}]
    )
    state["replies"] = [proposal("SaveBooks", {"book_ids": [3, 1]}, books=[KINGS, WIND]), applied]
    app = book_app("agent_home()").run()
    app.chat_input[0].set_value("save both").run()
    assert [c.label for c in app.checkbox] == [
        "The Way of Kings · Brandon Sanderson",
        "The Name of the Wind · Patrick Rothfuss",
    ]
    app.checkbox(key="approve_home_1_book_ids_1").uncheck().run()
    app.button(key="approve_home_1").click().run()
    assert not app.exception
    assert bodies(calls, "/chat/stream")[-1]["decision"]["edited_args"] == {"book_ids": [3]}
    assert [b["book_id"] for b in app.session_state["book_shortlist"]] == [3]
    assert any(m.value == "**Reading list · 1**" for m in app.markdown)


def test_reading_tastes_approval_and_memory_chips(api):
    calls, state = api
    args = {
        "liked_genres": ["Fantasy"],
        "avoided_genres": ["Horror"],
        "avoided_authors": ["Dan Brown"],
    }
    applied = reply(
        text="Noted.",
        books=[],
        applied=[
            {
                "kind": "book_preferences",
                "book_preferences": {
                    "liked_genres": ["Fantasy"],
                    "avoided_genres": [],
                    "avoided_authors": ["Dan Brown"],
                },
            }
        ],
    )
    state["replies"] = [proposal("RememberReadingTastes", args), applied]
    app = book_app("agent_home()").run()
    app.chat_input[0].set_value("I love fantasy, no horror, no Dan Brown").run()
    assert [c.label for c in app.checkbox] == [
        "Likes Fantasy",
        "No Horror",
        "No books by Dan Brown",
    ]
    app.checkbox(key="approve_home_1_avoided_genres_Horror").uncheck().run()
    app.button(key="approve_home_1").click().run()
    assert not app.exception
    assert bodies(calls, "/chat/stream")[-1]["decision"]["edited_args"] == {"avoided_genres": []}
    assert app.session_state["book_preferences"] == {
        "liked_genres": ["Fantasy"],
        "avoided_genres": [],
        "avoided_authors": ["Dan Brown"],
    }
    chips = [b.label for b in app.button if b.key.startswith("forget_book_")]
    assert chips == ["Likes Fantasy", "No Dan Brown"]
    app.button(key="forget_book_avoided_authors_Dan Brown").click().run()
    assert app.session_state["book_preferences"]["avoided_authors"] == []


def test_reading_list_panel_removes_and_asks_curio(api):
    app = book_app("book_session_panel()", book_shortlist=[WIND, KINGS]).run()
    assert not app.exception
    app.button(key="reading_list_ask").click().run()
    assert app.session_state["chat_queued_books"] == ui.BOOK_COPY.compare_prompt
    app.button(key="reading_list_remove_1").click().run()
    assert [b["book_id"] for b in app.session_state["book_shortlist"]] == [3]


def test_reading_list_is_capped(api):
    app = book_app(
        "add_book(" + repr(WIND) + ")", book_shortlist=[{"book_id": i} for i in range(100, 120)]
    )
    app.run()
    assert not app.exception
    assert len(app.session_state["book_shortlist"]) == 20


def test_cards_and_the_dialog_show_the_author_not_the_illustrator_or_translator(api):
    _, state = api
    potter = {**KINGS, "title": "Harry Potter", "authors": ["J.K. Rowling", "Mary GrandPré"]}
    ove = {**KINGS, "book_id": 4, "title": "A Man Called Ove",
           "authors": ["Fredrik Backman", "Henning Koch"]}  # fmt: skip
    state["search_hits"] = [hit(potter), hit(ove)]
    app = book_app("books_page()").run()
    app.text_input(key="book_query").set_value("magic")
    app.button(key="book_search_submit").click().run()
    assert not app.exception
    captions = [c.value for c in app.caption]
    assert "J.K. Rowling" in captions and "Fredrik Backman" in captions
    assert not any("GrandPré" in c or "Henning Koch" in c for c in captions)
    state["book_3"] = potter
    dialog = book_app("book_details(3)").run()
    assert any(m.value == "by J.K. Rowling" for m in dialog.markdown)
