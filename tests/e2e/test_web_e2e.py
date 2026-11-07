"""The React app (web/) end to end: the real API serving the built app, the scripted LLM and
Chromium. Covers the landing on Plan my evening, a plan and a swap, a fashion chat turn with
its step trace and grounding badge, an approval narrowed before approving, a book's drink
pairing, and the browse pages: the fashion feed and its strategies, description and photo
search, product connections, model quality, recipe search, "What can I make?", book search,
"Because you liked…", browse by mood, the reading list's cocktail and the Compare dialogs.
Screenshots go to the artifacts folder as `web-*.png`.

The API serves `web/dist` itself (`curio.app.web`), so this reuses the session's API. The
build is made with `npm run build` when `web/dist` is missing and `web/node_modules` exists;
otherwise these tests skip.
"""

import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import DATA_DIR, ROOT

WEB = ROOT / "web"
DIST = WEB / "dist"
BOOKS_READY = (DATA_DIR / "books.parquet").exists() and (DATA_DIR / "cocktails.parquet").exists()


@pytest.fixture(scope="module")
def web_url(api_url: str) -> str:
    if not (DIST / "index.html").exists():
        npm = shutil.which("npm")
        if npm is None or not (WEB / "node_modules").exists():
            pytest.skip("web/dist is not built (run `npm ci && npm run build` in web/)")
        subprocess.run([npm, "run", "build"], cwd=WEB, check=True)
    page = httpx.get(f"{api_url}/evening", headers={"accept": "text/html"}, trust_env=False)
    if page.status_code != 200 or '<div id="root">' not in page.text:
        pytest.skip("The API does not serve the React app (SERVE_WEB=false?)")
    return api_url


class Web:
    """A browser page on the React app, with named screenshots and request capture."""

    def __init__(self, page: Page, base: str, artifacts: Path) -> None:
        self.page = page
        self.base = base
        self.artifacts = artifacts
        self.errors: list[str] = []
        self.chat_bodies: list[dict] = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on("request", self._capture)

    def _capture(self, request) -> None:
        if request.url.endswith("/chat/stream") and request.method == "POST":
            self.chat_bodies.append(json.loads(request.post_data or "{}"))

    def open(self, path: str = "/") -> "Web":
        self.page.goto(f"{self.base}{path}")
        return self

    def send(self, text: str) -> None:
        turns = self.page.get_by_test_id("assistant-turn").count()
        box = self.page.get_by_label("Message Curio")
        box.fill(text)
        box.press("Enter")
        expect(self.page.get_by_test_id("assistant-turn")).to_have_count(turns + 1, timeout=120_000)

    def photos(self) -> None:
        """Wait for the photos in view (lazy photos below the fold load when scrolled to)."""
        self.page.wait_for_function(
            """() => [...document.querySelectorAll('img')]
                .filter(i => {
                    const box = i.getBoundingClientRect();
                    return i.offsetParent !== null && box.bottom > 0
                        && box.top < window.innerHeight;
                })
                .every(i => i.complete)""",
            timeout=60_000,
        )

    def scroll_through(self) -> None:
        """Bring every lazy photo into view once, then return to the top."""
        self.page.evaluate(
            """async () => {
                const step = window.innerHeight * 0.8;
                for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
                    window.scrollTo(0, y);
                    await new Promise((r) => setTimeout(r, 150));
                }
            }"""
        )
        self.photos()
        self.page.evaluate("window.scrollTo(0, 0)")

    def shot(self, name: str, *, full_page: bool = False) -> None:
        self.page.wait_for_timeout(600)  # let entrance animations settle
        if full_page:
            self.scroll_through()
        self.photos()
        self.page.screenshot(path=self.artifacts / f"web-{name}.png", full_page=full_page)

    def session(self) -> dict:
        return self.page.evaluate("() => JSON.parse(localStorage.getItem('curio.session.v1'))")

    def no_horizontal_scroll(self) -> bool:
        return self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


@pytest.fixture
def web(browser, web_url: str, artifacts: Path) -> Iterator[Web]:
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    driver = Web(context.new_page(), web_url, artifacts)
    yield driver
    context.close()
    assert not driver.errors, f"Browser errors: {driver.errors}"


@pytest.fixture
def web_phone(browser, web_url: str, artifacts: Path) -> Iterator[Web]:
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    driver = Web(context.new_page(), web_url, artifacts)
    yield driver
    context.close()
    assert not driver.errors, f"Browser errors: {driver.errors}"


@pytest.mark.skipif(not BOOKS_READY, reason="Books or cocktails not prepared")
def test_a_visit_lands_on_plan_my_evening(web):
    web.open("/")
    expect(web.page).to_have_url(re.compile(r"/evening$"))
    expect(web.page.get_by_role("heading", name="Plan my evening", level=1)).to_be_visible()
    switch = web.page.get_by_role("navigation", name="Catalog")
    for label in ("Fashion", "Cocktails", "Books", "Evening"):
        expect(switch.get_by_role("link", name=label)).to_be_visible()
    expect(switch.get_by_role("link", name="Evening")).to_have_attribute("aria-current", "page")
    expect(web.page.get_by_text("Pick an occasion above to see a plan.")).to_be_visible()
    web.shot("01-landing")
    # The switch opens each domain on its main page, with the domain's pages beside its chat.
    for label, path, heading in (
        ("Books", "/reading", "Books"),
        ("Cocktails", "/drinks", "Cocktails"),
        ("Fashion", "/fashion", "For you"),
    ):
        switch.get_by_role("link", name=label).click()
        expect(web.page).to_have_url(re.compile(rf"{path}$"))
        expect(web.page.get_by_role("heading", name=heading, level=1)).to_be_visible()
        pages = web.page.get_by_role("navigation", name=f"{label} pages")
        expect(pages.get_by_role("link", name="Ask Curio")).to_be_visible()
    # A deep link opens its page, also after a reload (the API's SPA fallback).
    web.open("/ask/cocktails")
    expect(web.page.get_by_role("heading", name="Mix something new tonight.")).to_be_visible()
    for path, heading in (
        ("/reading/moods", "Books"),
        ("/drinks/pantry", "Cocktails"),
        ("/fashion/quality", "Model quality"),
    ):
        web.open(path)
        web.page.reload()
        expect(web.page.get_by_role("heading", name=heading, level=1)).to_be_visible()


@pytest.mark.skipif(not BOOKS_READY, reason="Books or cocktails not prepared")
def test_plan_an_evening_swap_the_book_and_save_it(web):
    web.open("/evening")
    web.page.get_by_role("button", name="Rainy Sunday in", exact=True).click()
    web.page.get_by_role("button", name="Plan my evening").click()
    plan = web.page.get_by_test_id("evening-plan")
    expect(plan).to_be_visible(timeout=120_000)
    expect(plan.get_by_text("Why it works")).to_be_visible()
    for part in ("outfit", "book", "drink"):
        expect(web.page.get_by_test_id(f"evening-{part}")).to_be_visible()
    assert web.page.get_by_test_id("evening-outfit").locator("[data-slot]").count() >= 2
    book = web.page.get_by_test_id("evening-book")
    title = book.locator("p").first.inner_text()
    web.shot("02-evening-plan", full_page=True)

    with web.page.expect_response(lambda r: r.url.endswith("/evening/swap")) as swap:
        book.get_by_role("button", name="Another book").click()
    assert swap.value.ok
    expect(book.locator("p").first).not_to_have_text(title, timeout=30_000)
    web.shot("03-evening-swapped-book", full_page=True)

    web.page.get_by_role("button", name="Save evening").click()
    dialog = web.page.get_by_role("dialog", name="Save this evening?")
    expect(dialog).to_be_visible()
    ticks = dialog.get_by_role("checkbox")
    count = ticks.count()
    assert count >= 3
    ticks.first.uncheck()  # narrow: leave the first piece out
    web.shot("04-evening-save")
    dialog.get_by_role("button", name="Approve").click()
    expect(dialog).to_be_hidden()
    session = web.session()
    saved = (
        len(session["shortlist"]) + len(session["readingList"]) + len(session["cocktailShortlist"])
    )
    assert saved == count - 1
    assert len(session["readingList"]) == 1 and len(session["cocktailShortlist"]) == 1


def test_fashion_chat_streams_its_steps_and_is_verified(web):
    web.open("/ask/fashion")
    expect(web.page.get_by_role("heading", name="Find your next favorite.")).to_be_visible()
    box = web.page.get_by_label("Message Curio")
    box.fill("Black trousers under €30")
    box.press("Enter")
    # The live trace appears while the turn streams.
    expect(web.page.get_by_test_id("running-turn")).to_be_visible(timeout=30_000)
    turn = web.page.get_by_test_id("assistant-turn").last
    expect(turn).to_be_visible(timeout=120_000)
    badge = turn.get_by_test_id("grounding-badge")
    expect(badge).to_have_text("Prices verified against the catalog")
    trace = turn.get_by_role("button", name=re.compile(r"How Curio worked · \d+ actions? · "))
    trace.click()
    for label in ("Plan", "Search catalog", "Grounding check"):
        expect(turn.get_by_text(label, exact=True)).to_be_visible()
    cards = turn.get_by_test_id("product-card")
    assert cards.count() >= 3
    prices = [
        float(m.group(1)) for t in cards.all_inner_texts() if (m := re.search(r"€([\d.]+)", t))
    ]
    assert len(prices) >= 3 and max(prices) <= 30
    web.shot("05-fashion-trace", full_page=True)
    # The request carried no domain: fashion requests look as they always did.
    assert web.chat_bodies[0]["message"] == "Black trousers under €30"
    assert "domain" not in web.chat_bodies[0]


def test_fashion_approval_narrowed_before_approving(web):
    web.open("/ask/fashion")
    web.send("Red dress")
    web.send("Save the first two")
    drawer = web.page.get_by_test_id("approval-drawer")
    expect(drawer.get_by_role("heading", name="Needs your approval")).to_be_visible()
    ticks = drawer.get_by_role("checkbox")
    expect(ticks).to_have_count(2)
    kept = drawer.locator("label").first.inner_text()
    ticks.last.uncheck()
    web.shot("06-approval-narrowed")
    drawer.get_by_role("button", name="Approve").click()
    expect(drawer).to_be_hidden(timeout=120_000)
    panel = web.page.get_by_test_id("session-panel")
    expect(panel.get_by_role("heading", name=re.compile(r"Shortlist · 1"))).to_be_visible()
    expect(web.page.get_by_text("Approved.", exact=True)).to_be_visible()
    decision = web.chat_bodies[-1]["decision"]
    assert decision["approved"] is True
    proposed = decision["action"]["args"]["product_ids"]
    assert decision["edited_args"] == {"product_ids": proposed[:1]}
    session = web.session()
    assert [p["product_id"] for p in session["shortlist"]] == proposed[:1]
    assert session["shortlist"][0]["display_name"] in kept
    web.shot("07-approval-applied", full_page=True)


@pytest.mark.skipif(not BOOKS_READY, reason="Books or cocktails not prepared")
def test_pair_a_drink_with_a_book(web):
    web.open("/ask/books")
    web.send("Fantasy like The Name of the Wind")
    turn = web.page.get_by_test_id("assistant-turn").last
    expect(turn.get_by_test_id("grounding-badge")).to_be_visible()
    turn.get_by_test_id("book-card").first.get_by_role("button").first.click()
    dialog = web.page.get_by_role("dialog")
    expect(dialog.get_by_role("heading", level=2)).to_be_visible()
    expect(dialog.get_by_text("Readers also liked")).to_be_visible()
    dialog.get_by_role("button", name="Pair a drink").click()
    drinks = dialog.get_by_test_id("pairing-drink")
    expect(drinks.first).to_be_visible(timeout=60_000)
    web.shot("08-book-pairing")
    dialog.get_by_role("switch", name="Alcohol-free only").check()
    expect(dialog.get_by_text("Alcoholic", exact=True)).to_have_count(0, timeout=30_000)
    expect(drinks.first.get_by_text("Alcohol-free", exact=True)).to_be_visible()
    # A drink's recipe opens from the pairing, with a way back to the book.
    drinks.first.get_by_role("button", name=re.compile(r"^Open the .+ recipe$")).click()
    expect(dialog.get_by_role("heading", name="Ingredients")).to_be_visible()
    expect(dialog.get_by_text(re.compile("TheCocktailDB"))).to_be_visible()
    dialog.get_by_role("button", name="Back").click()
    expect(dialog.get_by_role("button", name="Pair a drink")).to_be_visible()
    web.page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    # The book agent pairs a drink too, with the same pairer.
    web.send("What should I drink with Dune?")
    turn = web.page.get_by_test_id("assistant-turn").last
    expect(turn.get_by_test_id("pairing-drink").first).to_be_visible()
    web.shot("09-book-chat-pairing", full_page=True)


@pytest.mark.skipif(not BOOKS_READY, reason="Books or cocktails not prepared")
def test_phone_layouts_do_not_scroll_sideways(web_phone):
    for path, name in (
        ("/evening", "10-phone-evening"),
        ("/ask/fashion", "11-phone-fashion"),
        ("/fashion", "30-phone-for-you"),
        ("/fashion/discover", "31-phone-discover"),
        ("/fashion/connections", "32-phone-connections"),
        ("/fashion/quality", "33-phone-quality"),
        ("/drinks", "34-phone-cocktails"),
        ("/drinks/pantry", "35-phone-pantry"),
        ("/reading", "36-phone-books"),
        ("/reading/because", "37-phone-because"),
        ("/reading/moods", "38-phone-moods"),
    ):
        web_phone.open(path)
        expect(web_phone.page.get_by_role("navigation", name="Catalog")).to_be_visible()
        assert web_phone.no_horizontal_scroll(), path
        web_phone.shot(name)
    web_phone.open("/evening")
    web_phone.page.get_by_role("button", name="Book club", exact=True).click()
    web_phone.page.get_by_role("button", name="Plan my evening").click()
    expect(web_phone.page.get_by_test_id("evening-plan")).to_be_visible(timeout=120_000)
    assert web_phone.no_horizontal_scroll()
    web_phone.shot("12-phone-evening-plan", full_page=True)


# --- Browse pages ------------------------------------------------------------------------


def results(web: Web, title: str):
    """The results section headed `title`, once its cards are there."""
    section = web.page.get_by_role("region", name=title)
    expect(section).to_be_visible(timeout=60_000)
    expect(section).to_have_attribute("aria-busy", "false", timeout=60_000)
    return section


def test_fashion_feed_switches_strategy_with_evidence(web):
    with web.page.expect_response(re.compile(r"/users/\d+/recommendations\?strategy=")) as first:
        web.open("/fashion")
    assert first.value.ok
    expect(web.page.get_by_role("heading", name="For you", level=1)).to_be_visible()
    shopper = web.page.get_by_role("combobox", name="Shopping history")
    expect(shopper).to_contain_text("#")
    feed = results(web, "Personalized ranking")
    expect(feed.get_by_test_id("product-card")).to_have_count(12)
    web.shot("20-for-you", full_page=True)

    with web.page.expect_response(re.compile(r"strategy=repeat")) as repeat:
        web.page.get_by_role("radio", name="Buy again").click()
    assert repeat.value.ok
    feed = results(web, "Buy again")
    card = feed.get_by_test_id("product-card").first
    expect(card).to_contain_text("Buy again: last purchased on")
    card.get_by_role("button", name="Why this pick").click()
    expect(card.get_by_text(re.compile(r"Ranking score: -?\d"))).to_be_visible()
    # The explanation, and Model quality linked from it (outside the navigation).
    web.page.get_by_role("button", name="How picks are chosen").click()
    web.page.get_by_role("link", name=re.compile("How each approach scores")).click()
    expect(web.page).to_have_url(re.compile(r"/fashion/quality$"))
    expect(web.page.get_by_role("heading", name="Model quality", level=1)).to_be_visible()
    expect(web.page.get_by_role("table")).to_contain_text("Buy again + best-sellers")
    expect(web.page.get_by_role("img", name=re.compile("MAP@12 by strategy"))).to_be_visible()
    web.shot("21-model-quality", full_page=True)


def test_describe_a_piece(web):
    web.open("/fashion/discover")
    with web.page.expect_request(lambda r: r.url.endswith("/search/text")) as request:
        web.page.get_by_role("button", name="white sneakers").click()
    assert request.value.post_data_json == {"query": "white sneakers", "top_k": 9, "section": None}
    found = results(web, "Matching pieces")
    cards = found.get_by_test_id("product-card")
    expect(cards).to_have_count(9)
    assert any("Sneakers" in text for text in cards.all_inner_texts())
    # A filter applies at once to the search on screen.
    with web.page.expect_request(lambda r: r.url.endswith("/search/text")) as request:
        web.page.get_by_label("Section").select_option("Menswear")
    assert request.value.post_data_json["section"] == "Menswear"
    expect(results(web, "Matching pieces").get_by_test_id("product-card").first).to_be_visible()
    web.shot("22-discover-text", full_page=True)


def test_search_with_an_example_photo(web):
    web.open("/fashion/discover")
    web.page.get_by_role("radio", name="Photo").click()
    examples = web.page.get_by_role("list", name="Example photos").get_by_role("button")
    expect(examples.first).to_be_visible()
    with web.page.expect_request(lambda r: "/search/image" in r.url) as request:
        examples.first.click()
    upload = request.value
    assert upload.method == "POST"
    assert upload.headers["content-type"].startswith("multipart/form-data; boundary=")
    found = results(web, "Visually similar pieces")
    expect(found.get_by_test_id("product-card")).to_have_count(9)
    expect(web.page.get_by_text("Your photo")).to_be_visible()
    web.shot("23-discover-photo", full_page=True)


def test_connections_graph_opens_a_product(web):
    web.open("/fashion/connections")
    nodes = web.page.get_by_test_id("graph-node")
    expect(nodes.first).to_be_visible(timeout=60_000)
    assert nodes.count() >= 3
    neighbor = web.page.locator('[data-testid="graph-node"][data-kind="product"]:not([data-focus])')
    expect(neighbor.first).to_be_visible()
    neighbor.first.hover()
    web.page.wait_for_timeout(500)
    web.shot("24-connections")
    label = neighbor.first.get_attribute("aria-label") or ""
    name = label.split(" (")[0]
    with web.page.expect_response(re.compile(r"/products/\d+$")) as opened:
        neighbor.first.click()
    assert opened.value.ok
    dialog = web.page.get_by_role("dialog")
    expect(dialog.get_by_role("button", name="Save to shortlist")).to_be_visible()
    expect(dialog.get_by_role("heading", level=2)).to_be_visible()
    web.page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    # The related products follow the selected piece.
    expect(web.page.get_by_role("heading", name=f"Related products · {name}")).to_be_visible()
    web.page.get_by_role("button", name=re.compile(r"Relationships · \d+")).click()
    expect(web.page.get_by_role("table")).to_contain_text("Bought together")


@pytest.mark.skipif(not BOOKS_READY, reason="Books or cocktails not prepared")
def test_search_recipes_with_filters(web):
    web.open("/drinks")
    box = web.page.get_by_role("combobox", name="Must include")
    box.fill("Lime")
    expect(web.page.get_by_role("option", name="Lime", exact=True)).to_be_visible()
    box.press("Enter")
    box.press("Escape")
    web.page.get_by_role("radio", name="Alcohol-free").click()
    with web.page.expect_request(lambda r: r.url.endswith("/cocktails/search")) as request:
        web.page.get_by_role("button", name="Something with rum and lime").click()
    body = request.value.post_data_json
    assert body["ingredients"] == ["Lime"] and body["alcohol"] == "non_alcoholic"
    found = results(web, "Matching drinks")
    cards = found.get_by_test_id("cocktail-card")
    expect(cards.first).to_be_visible()
    for text in cards.all_inner_texts():
        assert "Alcohol-free" in text
    web.shot("25-cocktail-search", full_page=True)


@pytest.mark.skipif(not BOOKS_READY, reason="Books or cocktails not prepared")
def test_what_can_i_make(web):
    web.open("/drinks/pantry")
    expect(web.page.get_by_text("Add at least one ingredient")).to_be_visible()
    box = web.page.get_by_role("combobox", name="Ingredients you have")
    for name in ("Gin", "Lime Juice", "Tonic Water"):
        box.fill(name)
        expect(web.page.get_by_role("option", name=name, exact=True)).to_be_visible()
        box.press("Enter")
    box.press("Escape")
    with web.page.expect_request(lambda r: r.url.endswith("/cocktails/pantry")) as request:
        web.page.get_by_role("radio", name="1", exact=True).click()
    assert request.value.post_data_json == {
        "ingredients": ["Gin", "Lime Juice", "Tonic Water"],
        "alcohol": "any",
        "max_missing": 1,
        "top_k": 9,
    }
    found = results(web, "Drinks you can make")
    first = found.get_by_test_id("cocktail-card").first
    meter = first.get_by_role("meter")
    expect(meter).to_have_accessible_name(re.compile(r"You have \d of \d ingredients"))
    expect(first.get_by_role("list", name="Ingredients you have")).to_contain_text("Gin")
    web.shot("26-pantry", full_page=True)


@pytest.mark.skipif(not BOOKS_READY, reason="Books or cocktails not prepared")
def test_book_pages_and_the_reading_list_cocktail(web):
    web.open("/reading")
    expect(web.page.get_by_test_id("reading-list-pairing")).to_have_count(0)
    with web.page.expect_request(lambda r: r.url.endswith("/books/search")) as request:
        web.page.get_by_role("button", name="A cosy mystery").click()
    assert request.value.post_data_json["query"] == "A cosy mystery"
    found = results(web, "Matching books")
    expect(found.get_by_test_id("book-card")).to_have_count(12)
    found.get_by_role("button", name=re.compile("Save .* to your reading list")).first.click()

    # The reading list's cocktail appears once the list has a book.
    card = web.page.get_by_test_id("reading-list-pairing")
    expect(card.get_by_test_id("pairing-drink").first).to_be_visible(timeout=60_000)
    web.shot("27-books-search", full_page=True)

    # Because you liked…: starts from the reading list; add a title from the picker.
    web.page.get_by_role("link", name="Because you liked…").click()
    box = web.page.get_by_role("combobox", name="Books you loved")
    chosen = web.page.get_by_role("list", name="Chosen books you loved")
    expect(chosen.get_by_role("listitem")).to_have_count(1)
    box.fill("The Hobbit")
    expect(web.page.get_by_role("option").first).to_contain_text("The Hobbit")
    with web.page.expect_request(lambda r: r.url.endswith("/books/because")) as request:
        box.press("Enter")
    assert len(request.value.post_data_json["liked"]) == 2
    box.press("Escape")
    found = results(web, "Readers like you also loved")
    expect(found.get_by_test_id("book-card").first).to_be_visible()
    web.shot("28-because", full_page=True)

    # Browse by mood.
    web.page.get_by_role("link", name="Browse by mood").click()
    with web.page.expect_request(lambda r: r.url.endswith("/books/search")) as request:
        web.page.get_by_role("radio", name="Cosy").click()
    assert request.value.post_data_json["mood"] == "Cosy"
    found = results(web, "Cosy")
    expect(found.get_by_test_id("book-card").first).to_be_visible()
    web.shot("29-moods", full_page=True)


@pytest.mark.skipif(not BOOKS_READY, reason="Books or cocktails not prepared")
def test_compare_saved_pieces_and_drinks(web):
    web.open("/fashion")
    feed = results(web, "Personalized ranking")
    for _ in range(2):
        feed.get_by_role("button", name=re.compile("Save .* to your shortlist")).first.click()
    web.page.get_by_role("button", name=re.compile("^Your session")).click()
    sheet = web.page.get_by_role("dialog", name="Your session")
    expect(sheet.get_by_role("list", name="Saved pieces").get_by_role("listitem")).to_have_count(2)
    sheet.get_by_role("button", name="Compare").click()
    dialog = web.page.get_by_role("dialog")
    expect(dialog.get_by_role("heading", name="Compare pieces")).to_be_visible()
    expect(dialog.get_by_test_id("compare-piece")).to_have_count(2)
    expect(dialog.get_by_text("Lowest price").first).to_be_visible()
    web.shot("40-compare-pieces")
    web.page.keyboard.press("Escape")

    web.open("/drinks")
    web.page.get_by_role("button", name="Margarita").click()
    found = results(web, "Matching drinks")
    for _ in range(2):
        found.get_by_role("button", name=re.compile("Save .* to your shortlist")).first.click()
    web.page.get_by_role("button", name=re.compile("^Your session")).click()
    sheet = web.page.get_by_role("dialog", name="Your session")
    sheet.get_by_role("button", name="Compare").click()
    dialog = web.page.get_by_role("dialog")
    expect(dialog.get_by_role("heading", name="Compare drinks")).to_be_visible()
    expect(dialog.get_by_test_id("compare-drink")).to_have_count(2)
    web.shot("41-compare-drinks")
    # A drink opens from the comparison, with a way back.
    dialog.get_by_role("button", name=re.compile("^View ")).first.click()
    expect(dialog.get_by_role("heading", name="Ingredients")).to_be_visible()
    dialog.get_by_role("button", name="Back").click()
    expect(dialog.get_by_role("heading", name="Compare drinks")).to_be_visible()
