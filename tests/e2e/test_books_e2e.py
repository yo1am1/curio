"""Books end to end: the real goodbooks-10k catalog through the API, the agent and the app.

Needs `uv run python -m scripts.prepare_books` (data/books.parquet and
data/book_neighbours.parquet) besides the data the rest of the suite uses.
"""

import json
import re

import httpx
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import DATA_DIR, results

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


# --- Browser (the React app) ------------------------------------------------------------


def test_books_page_search_dialog_because_and_browse(web):
    web.open("/reading")
    web.page.get_by_label("Describe a book").fill("Fantasy like The Name of the Wind")
    web.page.get_by_role("button", name="Find books").click()
    cards = results(web, "Matching books").get_by_test_id("book-card")
    expect(cards.nth(5)).to_be_visible()
    expect(cards.first).to_contain_text(re.compile("readers who liked The Name of the Wind"))
    web.shot("books-01-search", full_page=True)

    cards.first.get_by_role("button", name=re.compile(r"^View ")).click()
    dialog = web.page.get_by_role("dialog")
    expect(dialog.get_by_text("Readers also liked")).to_be_visible()
    expect(dialog.get_by_text(re.compile(r"^More by "))).to_be_visible()
    expect(dialog.get_by_text(re.compile("goodbooks-10k"))).to_be_visible()
    web.shot("books-02-book-dialog")
    dialog.get_by_role("button", name="Save to reading list").click()
    expect(dialog.get_by_role("button", name="On your reading list")).to_be_visible()
    dialog.get_by_role("button", name="Close").click()
    expect(dialog).to_have_count(0)

    web.page.get_by_role("link", name="Because you liked…").click()
    because = results(web, "Readers like you also loved").get_by_test_id("book-card")
    expect(because.first).to_contain_text(re.compile("Because you liked"))
    web.shot("books-03-because-you-liked", full_page=True)

    web.page.get_by_role("link", name="Browse by mood").click()
    web.page.get_by_role("radio", name="Cosy").click()
    browse = results(web, "Cosy").get_by_test_id("book-card")
    expect(browse.first).to_contain_text(re.compile("Mood: Cosy"))
    web.shot("books-04-browse-cosy", full_page=True)


def approve(web, *, contains: re.Pattern | None = None) -> None:
    drawer = web.page.get_by_test_id("approval-drawer")
    expect(drawer.get_by_role("heading", name="Needs your approval")).to_be_visible()
    if contains:
        expect(drawer).to_contain_text(contains)
    drawer.get_by_role("button", name="Approve").click()
    expect(drawer).to_be_hidden(timeout=120_000)


def test_book_agent_end_to_end(web):
    web.open("/")
    web.page.get_by_role("navigation", name="Catalog").get_by_role("link", name="Books").click()
    web.page.get_by_role("navigation", name="Books pages").get_by_role(
        "link", name="Ask Curio"
    ).click()
    expect(web.page.get_by_role("heading", name="Find your next great read.")).to_be_visible()
    web.send("Fantasy like The Name of the Wind")
    turn = web.last_turn()
    expect(turn.get_by_test_id("grounding-badge")).to_have_text(
        "Titles and ratings checked against the catalog"
    )
    assert turn.get_by_test_id("book-card").count() >= 3
    turn.get_by_role("button", name=re.compile(r"How Curio worked")).click()
    expect(turn.get_by_text("Search books", exact=True)).to_be_visible()
    web.shot("books-05-agent-search", full_page=True)

    web.send("Which is the highest rated?")
    expect(web.last_turn().get_by_test_id("grounding-badge")).to_have_text(
        "Self-corrected to match the catalog"
    )
    web.shot("books-06-agent-self-correction")

    web.send("Save the first one")
    web.shot("books-07-approval-proposed")
    approve(web)
    panel = web.page.get_by_test_id("session-panel")
    expect(panel.get_by_role("heading", name=re.compile(r"Reading list · 1"))).to_be_visible()

    web.send("Remember I never read horror")
    approve(web, contains=re.compile("No horror", re.I))
    memory = panel.get_by_role("list", name="Remembered for this session")
    expect(memory.get_by_role("button", name=re.compile("No horror", re.I))).to_be_visible()
    web.send("Something dark")
    turn = web.last_turn()
    turn.get_by_role("button", name=re.compile(r"How Curio worked")).click()
    expect(turn.get_by_text(re.compile("avoiding Horror")).first).to_be_visible()
    web.shot("books-08-memory-applied", full_page=True)

    web.send("Recommend me a book")
    groups = web.last_turn().get_by_role("group")
    options = groups.filter(has_not=web.page.get_by_role("button", name="Helpful")).filter(
        has_not=web.page.get_by_role("link")
    )
    expect(options.first.get_by_role("button")).to_have_count(3)
    web.shot("books-09-clarifying-question")

    # Cocktails keep their own conversation.
    web.open("/ask/cocktails")
    expect(web.page.get_by_role("heading", name=re.compile("Mix something"))).to_be_visible()
    expect(web.turns()).to_have_count(0)


def test_phone_books_page_does_not_scroll_sideways(web_phone):
    web_phone.open("/reading")
    web_phone.page.get_by_label("Describe a book").fill("Short classic novels")
    web_phone.page.get_by_role("button", name="Find books").click()
    expect(results(web_phone, "Matching books").get_by_test_id("book-card").first).to_be_visible()
    assert web_phone.no_horizontal_scroll()
    web_phone.shot("books-10-phone-search")
