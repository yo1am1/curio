"""The React app (web/) end to end: the real API serving the built app, the scripted LLM and
Chromium. Covers the landing on Plan my evening, a plan and a swap, a fashion chat turn with
its step trace and grounding badge, an approval narrowed before approving, and a book's drink
pairing. Screenshots go to the artifacts folder as `web-*.png`.

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
        self.page.wait_for_function(
            """() => [...document.querySelectorAll('img')]
                .filter(i => i.offsetParent !== null).every(i => i.complete)""",
            timeout=60_000,
        )

    def shot(self, name: str, *, full_page: bool = False) -> None:
        self.page.wait_for_timeout(600)  # let entrance animations settle
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
    # The switch routes per domain, and a deep link opens its page.
    switch.get_by_role("link", name="Books").click()
    expect(web.page).to_have_url(re.compile(r"/ask/books$"))
    expect(web.page.get_by_role("heading", name="Find your next great read.")).to_be_visible()
    web.open("/ask/cocktails")
    expect(web.page.get_by_role("heading", name="Mix something new tonight.")).to_be_visible()


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
    for path, name in (("/evening", "10-phone-evening"), ("/ask/fashion", "11-phone-fashion")):
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
