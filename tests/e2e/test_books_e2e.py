"""Books end to end: the real goodbooks-10k catalog through the API, the UI and the agent.

Needs `uv run python -m scripts.prepare_books` (data/books.parquet and
data/book_neighbours.parquet) besides the data the rest of the suite uses.
"""

import json
import re

import httpx
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import DATA_DIR
from tests.e2e.test_ui_e2e import chat_turns, visible_photos

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "books.parquet").exists()
    or not (DATA_DIR / "book_neighbours.parquet").exists(),
    reason="Books not prepared: run scripts.prepare_books",
)


@pytest.fixture(scope="module")
def api(api_url: str):
    with httpx.Client(base_url=api_url, timeout=120, trust_env=False) as client:
        yield client


def titles(hits: list[dict]) -> list[str]:
    return [hit["book"]["title"] for hit in hits]


# --- API ---------------------------------------------------------------------------------


def test_health_reports_the_book_domain(api):
    health = api.get("/health").json()
    assert (health["books_enabled"], health["book_count"]) == (True, 10_000)
    books = next(d for d in health["domains"] if d["id"] == "books")
    assert books["enabled"] and books["chat_enabled"] and books["item_count"] == 10_000


def test_fantasy_like_a_title_comes_from_its_readers(api):
    hits = api.post(
        "/books/search", json={"query": "fantasy like The Name of the Wind", "top_k": 8}
    ).json()
    assert len(hits) == 8
    assert "The Name of the Wind" not in titles(hits)
    assert "The Wise Man's Fear" not in titles(hits)  # its own series is left out
    assert all("Fantasy" in hit["book"]["genres"] for hit in hits)
    assert all("readers who liked The Name of the Wind" in hit["reasons"][0] for hit in hits)
    authors = {hit["book"]["authors"][0] for hit in hits}
    assert "Brandon Sanderson" in authors and len(authors) >= 3


def test_short_classic_novels_are_short_classics(api):
    hits = api.post("/books/search", json={"query": "short classic novels", "top_k": 10}).json()
    assert len(hits) == 10
    for hit in hits:
        assert "Classics" in hit["book"]["genres"]
        assert hit["book"]["pages"] is not None and hit["book"]["pages"] <= 250
    assert {"Of Mice and Men", "Animal Farm", "The Great Gatsby"} & set(titles(hits))


def _search(api, query: str, top_k: int = 6) -> list[dict]:
    hits = api.post("/books/search", json={"query": query, "top_k": top_k}).json()
    assert len(hits) == top_k, query
    return hits


def test_golden_queries_find_the_topic_not_the_request_words(api):
    """The review's probes: request words ("gift", "mom", "loves", "long", "2") once matched
    titles; the topic now decides."""
    gardening = _search(api, "a gift for my mom who loves gardening", 4)
    for hit in gardening:
        assert {"gardening", "botany", "farming"} & set(hit["book"]["tags"]), hit["book"]["title"]
    dragons = _search(api, "something for a 10 year old who loves dragons")
    for hit in dragons:
        assert "Children's" in hit["book"]["genres"] and "dragons" in hit["book"]["tags"]
    war = _search(api, "best books about world war 2")
    for hit in war:
        assert {"wwii", "world war ii", "ww2"} & set(hit["book"]["tags"]), hit["book"]["title"]
    flight = _search(api, "funny books for a long flight")
    assert all(any(r.startswith("Mood: Funny") for r in hit["reasons"]) for hit in flight)
    for hits in (gardening, dragons, war, flight):
        reasons = [r for hit in hits for r in hit["reasons"]]
        assert not any(r.startswith("Title matches") for r in reasons), reasons
    shown = {t for hits in (gardening, dragons, war, flight) for t in titles(hits)}
    assert not shown & {
        "The Girl with All the Gifts",
        "The Gift of the Magi",
        "The History of Love",
        "P.S. I Love You",
        "The Girl with the Dragon Tattoo",
        "Night World, No. 2",
        "Persepolis 2: The Story of a Return",
        "A Long Way Down",
        "The Long Haul",
    }


def test_one_author_or_series_cannot_fill_the_results(api):
    from collections import Counter

    fantasy = _search(api, "fantasy", 10)
    assert max(Counter(h["book"]["authors"][0] for h in fantasy).values()) <= 2
    potter = [h["book"]["title"] for h in fantasy if h["book"]["series"] == "Harry Potter"]
    assert potter == ["Harry Potter and the Sorcerer's Stone"]
    seeded = _search(api, "fantasy like The Name of the Wind")
    authors = Counter(h["book"]["authors"][0] for h in seeded)
    assert authors["Brandon Sanderson"] <= 2 and len(authors) >= 5
    assert not {"The Well of Ascension", "The Hero of Ages"} & set(titles(seeded))
    # Naming the series keeps all of it.
    assert len([h for h in _search(api, "harry potter", 8) if h["book"]["series"]]) == 8


def test_books_without_a_goodreads_cover_carry_an_isbn_for_open_library(api):
    hits = api.post("/books/search", json={"query": "the lies of locke lamora", "top_k": 1})
    lamora = hits.json()[0]["book"]
    assert lamora["title"] == "The Lies of Locke Lamora"
    assert lamora["image_url"] is None and lamora["isbn"] == "9780553804676"


def test_readers_also_liked_because_and_by_author(api):
    similar = api.get("/books/192/similar", params={"other_authors_only": True}).json()
    assert similar and all(h["book"]["authors"][0] != "Patrick Rothfuss" for h in similar)
    because = api.post("/books/because", json={"liked": [192, 2], "top_k": 6}).json()
    assert len(because) == 6
    assert all(h["reasons"][0].startswith("Because you liked") for h in because)
    more = api.get("/books/192/by-author").json()
    assert "The Wise Man's Fear" in titles(more)
    assert all(h["book"]["book_id"] != 192 for h in more)


def test_book_turn_streams_a_grounded_answer(api):
    body = {"domain": "books", "message": "Fantasy like The Name of the Wind"}
    with api.stream("POST", "/chat/stream", json=body) as response:
        events = [json.loads(line) for line in response.iter_lines() if line.strip()]
    steps = [e["step"]["name"] for e in events if e["type"] == "step"]
    reply = events[-1]["reply"]
    assert steps[:2] == ["Understand request", "SearchBooks"]
    assert reply["domain"] == "books" and reply["books"] and not reply["products"]
    assert reply["grounding"]["status"] == "verified"


# --- UI ----------------------------------------------------------------------------------


def test_books_page_search_dialog_because_and_browse(ui):
    ui.open("books")
    ui.page.get_by_label("Describe a book").fill("Fantasy like The Name of the Wind")
    ui.click(ui.page.get_by_role("button", name="Find books"))
    cards = ui.page.locator('[class*="st-key-book_card_search_"]')
    expect(cards.first).to_be_visible()
    assert cards.count() >= 6
    expect(
        cards.first.get_by_text(re.compile("readers who liked The Name of the Wind"))
    ).to_be_visible()
    visible_photos(ui, 3)
    ui.shot("26-books-search")

    ui.click(cards.first.get_by_role("button", name=re.compile(r"^View ")))
    dialog = ui.page.get_by_role("dialog")
    expect(dialog.get_by_text("Readers also liked")).to_be_visible()
    expect(dialog.get_by_text(re.compile(r"^More by "))).to_be_visible()
    expect(dialog.get_by_text(re.compile("goodbooks-10k"))).to_be_visible()
    ui.shot("27-book-dialog")
    ui.click(dialog.get_by_role("button", name="Save to reading list"))
    expect(dialog.get_by_role("button", name="On your reading list")).to_be_visible()
    ui.click(dialog.get_by_label("Close"))
    expect(dialog).to_have_count(0)

    ui.click(ui.page.get_by_text("Because you liked…", exact=True))
    expect(ui.page.get_by_role("heading", name="Readers like you also loved")).to_be_visible()
    because = ui.page.locator('[class*="st-key-book_card_because_"]')
    expect(because.first.get_by_text(re.compile("^Because you liked"))).to_be_visible()
    ui.shot("28-books-because-you-liked", full_page=True)

    ui.click(ui.page.get_by_text("Browse by mood", exact=True))
    ui.click(ui.key("browse_mood").get_by_text("Cosy", exact=True))
    browse = ui.page.locator('[class*="st-key-book_card_browse_"]')
    expect(browse.first.get_by_text(re.compile("^Mood: Cosy"))).to_be_visible()
    ui.shot("29-books-browse-cosy")


def test_book_agent_end_to_end(ui):
    ui.open()
    ui.click(ui.page.get_by_test_id("stButtonGroup").first.get_by_text("Books", exact=True))
    expect(ui.page.get_by_role("heading", name="Find your next great read.")).to_be_visible()
    ui.send("Fantasy like The Name of the Wind")
    expect(ui.page.get_by_text("Titles and ratings checked against the catalog")).to_be_visible()
    books = ui.key("agent_conversation").locator('[class*="st-key-chat_book_home_1_"]')
    assert books.count() >= 3
    visible_photos(ui, 3)
    ui.click(ui.page.get_by_text(re.compile(r"How Curio worked")).last)
    ui.shot("30-book-agent-search")

    ui.send("Which is the highest rated?")
    expect(ui.page.get_by_text("Self-corrected to match the catalog")).to_be_visible()
    ui.shot("31-book-agent-self-correction")

    ui.send("Save the first one")
    card = ui.page.locator('[class*="st-key-approval_home_"]').last
    expect(card.get_by_text("Needs your approval")).to_be_visible()
    ui.shot("32-book-approval-proposed")
    ui.click(card.get_by_role("button", name="Approve"))
    expect(ui.page.get_by_text("Reading list · 1")).to_be_visible()

    ui.send("Remember I never read horror")
    card = ui.page.locator('[class*="st-key-approval_home_"]').last
    expect(card.get_by_text("No Horror")).to_be_visible()
    ui.click(card.get_by_role("button", name="Approve"))
    expect(
        ui.key("session_memory").get_by_role("button", name=re.compile("No Horror"))
    ).to_be_visible()
    ui.send("Something dark")
    ui.click(ui.page.get_by_text(re.compile(r"How Curio worked")).last)
    expect(ui.page.get_by_text(re.compile("avoiding Horror")).last).to_be_visible()
    ui.shot("33-book-memory-applied")

    ui.send("Recommend me a book")
    options = ui.page.locator('[class*="st-key-chat_options_home_"] button')
    expect(options).to_have_count(3)
    ui.shot("34-book-clarifying-question")

    # Cocktails keep their own conversation.
    ui.click(ui.page.get_by_test_id("stButtonGroup").first.get_by_text("Cocktails", exact=True))
    expect(ui.page.get_by_role("heading", name=re.compile("Mix something"))).to_be_visible()
    assert chat_turns(ui) <= 1


def test_phone_books_page_does_not_scroll_sideways(phone):
    phone.open("books")
    phone.page.get_by_label("Describe a book").fill("Short classic novels")
    phone.click(phone.page.get_by_role("button", name="Find books"))
    expect(phone.page.locator('[class*="st-key-book_card_search_"]').first).to_be_visible()
    assert phone.no_horizontal_scroll()
    phone.shot("35-phone-books")
