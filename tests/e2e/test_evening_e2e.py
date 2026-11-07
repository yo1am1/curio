"""Plan my evening end to end: the real catalogs through the API, the agent and the app.

Needs the prepared H&M data and photo index, the books and the cocktails. Writes the real
plans for several occasions to `evening-plans.json` in the artifacts folder. The quick-start
case (books and cocktails, no fashion, no LLM) runs on its own API.
"""

import json
import re

import httpx
import pytest
from playwright.sync_api import expect

from curio.domains.books.taxonomy import EXCLUDED_SHELVES
from curio.domains.cocktails.recommend import CRUDE_NAMES
from tests.e2e.conftest import DATA_DIR

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "books.parquet").exists() or not (DATA_DIR / "cocktails.parquet").exists(),
    reason="Books or cocktails not prepared",
)
OCCASIONS = [
    {"occasion": "rainy Sunday in"},
    {"occasion": "date night"},
    {"occasion": "date night under €100"},
    {"occasion": "book club, alcohol-free"},
    {"occasion": "summer garden party", "audience": "women"},
    {"occasion": "first date dinner", "audience": "men", "budget": 80},
    {"occasion": "murder mystery night"},
    {"occasion": "game night with friends"},
    {"occasion": "New Year's Eve party", "budget": 60},
    {"occasion": "dinner with my parents"},
    {"occasion": "business dinner"},
    {"occasion": "kids birthday party"},
    {"occasion": "family movie night"},
    {"occasion": "baby shower"},
    {"occasion": "girls' night in"},
]
# Occasions that must never be romantic or glamorous, and those that must be alcohol-free.
NEVER_ROMANTIC = {
    "dinner with my parents",
    "business dinner",
    "kids birthday party",
    "family movie night",
    "baby shower",
}
ALCOHOL_FREE = {
    "book club, alcohol-free",
    "dinner with my parents",
    "kids birthday party",
    "family movie night",
    "baby shower",
}


@pytest.fixture(scope="module")
def api(api_url: str):
    with httpx.Client(base_url=api_url, timeout=180, trust_env=False) as client:
        yield client


def plan(api, **body) -> dict:
    response = api.post("/evening/plan", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def paired_drinks(api, result: dict) -> set[int]:
    """Every drink the pairer pairs with the plan's book, in any of its sets."""
    body = {
        "book_id": result["book"]["book"]["book_id"],
        "alcohol_free": result["state"]["alcohol_free"],
        "top_k": 5,
    }
    first = api.post("/pairings/book-cocktail", json=body).json()
    found = set()
    for seed in range(first["sets"]):
        result_ = api.post("/pairings/book-cocktail", json=body | {"seed": seed}).json()
        found |= {p["cocktail"]["drink_id"] for p in result_["pairings"]}
    return found


# --- API on the real data ---------------------------------------------------------------------


def test_real_plans_for_several_occasions(api, artifacts):
    excluded = set(EXCLUDED_SHELVES)
    table = []
    for body in OCCASIONS:
        result = plan(api, **body)
        occasion = body["occasion"]
        assert result["book"] and result["drink"] and result["outfit"], (body, result["missing"])
        outfit = result["outfit"]["outfit"]
        # The evening is complete exactly when its outfit is.
        assert result["status"] == ("complete" if outfit["status"] == "complete" else "partial")
        if budget := result["state"]["budget"]:
            assert outfit["total"] <= budget
        drink, book = result["drink"]["cocktail"], result["book"]["book"]
        assert drink["drink_id"] in paired_drinks(api, result), body
        # Content safety: no sexual or abuse shelf, no crude drink name.
        assert not set(book["tags"]) & excluded, (occasion, book["title"], book["tags"])
        assert drink["name"].casefold() not in CRUDE_NAMES, (occasion, drink["name"])
        if occasion in NEVER_ROMANTIC:
            assert result["theme"]["id"] not in ("romantic", "glamorous"), occasion
        if occasion in ALCOHOL_FREE:
            assert result["state"]["alcohol_free"], occasion
        if result["state"]["alcohol_free"]:
            assert drink["alcoholic"] == "Non alcoholic", occasion
        # Natural evidence ("you mentioned date night"), never "“Date night” mentions …".
        assert result["theme"]["evidence"] and " mentions " not in result["theme"]["evidence"]
        table.append(
            {
                "occasion": occasion,
                "request": body,
                "theme": result["theme"]["id"],
                "evidence": result["theme"]["evidence"],
                "alcohol_free": result["state"]["alcohol_free"],
                "outfit": [
                    f"{s['label']}: {s['product']['display_name']} €{s['product']['price']:.2f}"
                    for s in outfit["slots"]
                    if s["product"]
                ],
                "outfit_total": outfit["total"],
                "status": result["status"],
                "book": f"{book['title']} by {book['authors'][0]} ★{book['average_rating']}",
                "book_ratings": book["ratings_count"],
                "drink": f"{drink['name']} ({drink['alcoholic']})",
                "notes": result["notes"],
                "why": result["why"],
            }
        )
    (artifacts / "evening-plans.json").write_text(json.dumps(table, indent=2, ensure_ascii=False))
    themes = {row["occasion"]: row["theme"] for row in table}
    assert themes["rainy Sunday in"] == "cosy"
    assert themes["date night"] == "romantic"
    assert themes["date night under €100"] == "romantic"
    assert themes["book club, alcohol-free"] == "thoughtful"
    assert themes["summer garden party"] == "summery"
    assert themes["murder mystery night"] == "mysterious"
    assert themes["kids birthday party"] == "playful"
    books = {row["occasion"]: row["book"] for row in table}
    assert books["date night"].startswith("Pride and Prejudice")
    # Well-known books: an evening reads one at least 100,000 readers rated.
    assert all(row["book_ratings"] >= 100_000 for row in table), books


def test_a_new_visit_opens_on_plan_my_evening_with_the_engine_brand(web):
    web.open("/")
    expect(web.page).to_have_url(re.compile(r"/evening$"))
    expect(web.page.get_by_role("heading", name="Plan my evening", level=1)).to_be_visible()
    expect(web.page.get_by_text("Trustworthy recommendation agents")).to_be_visible()
    switch = web.page.get_by_role("navigation", name="Catalog")
    switch.get_by_role("link", name="Books").click()
    expect(web.page.get_by_role("heading", name="Books", level=1)).to_be_visible()
    web.page.get_by_role("navigation", name="Books pages").get_by_role(
        "link", name="Ask Curio"
    ).click()
    expect(web.page.get_by_role("heading", name="Find your next great read.")).to_be_visible()
    # Choosing Evening lands on the planner page, not on its chat.
    switch.get_by_role("link", name="Evening").click()
    expect(web.page).to_have_url(re.compile(r"/evening$"))
    expect(web.page.get_by_role("heading", name="Plan my evening", level=1)).to_be_visible()


def test_swaps_on_the_real_data(api):
    first = plan(api, occasion="rainy Sunday in")
    state = first["state"]
    book = api.post("/evening/swap", json={"evening": state, "part": "book"}).json()
    assert book["book"]["book"]["book_id"] != first["book"]["book"]["book_id"]
    assert book["drink"]["cocktail"]["drink_id"] in paired_drinks(api, book)
    slot = first["outfit"]["outfit"]["slots"][0]
    cheaper = api.post(
        "/evening/swap",
        json={"evening": state, "part": "outfit", "slot": slot["slot"], "mode": "cheaper"},
    ).json()
    new = cheaper["outfit"]["outfit"]["slots"][0]["product"]
    assert new["price"] < slot["product"]["price"]
    free = api.post(
        "/evening/swap", json={"evening": state, "part": "drink", "mode": "alcohol_free"}
    ).json()
    assert free["drink"]["cocktail"]["alcoholic"] == "Non alcoholic"
    assert free["book"]["book"]["book_id"] == first["book"]["book"]["book_id"]


# --- Browser (the React app) -------------------------------------------------------------


def plan_on_page(web, occasion_chip: str | None = None, text: str | None = None) -> None:
    web.open("/evening")
    if occasion_chip:
        web.page.get_by_role("group", name="Occasions").get_by_role(
            "button", name=occasion_chip, exact=True
        ).click()
    if text:
        web.page.get_by_label("Occasion", exact=True).fill(text)


def plan_now(web):
    web.page.get_by_role("button", name="Plan my evening").click()
    plan = web.page.get_by_test_id("evening-plan")
    expect(plan).to_be_visible(timeout=120_000)
    return plan


def test_rainy_sunday_in_on_the_planner_page(web):
    plan_on_page(web, "Rainy Sunday in")
    plan = plan_now(web)
    for part in ("outfit", "book", "drink"):
        expect(web.page.get_by_test_id(f"evening-{part}")).to_be_visible()
    expect(plan.get_by_text("Why it works")).to_be_visible()
    expect(plan.get_by_text("Cosy", exact=True)).to_be_visible()
    web.shot("evening-01-rainy-sunday", full_page=True)
    book = web.page.get_by_test_id("evening-book")
    title = book.locator("p").first.inner_text()
    book.get_by_role("button", name="Another book").click()
    expect(book.locator("p").first).not_to_have_text(title, timeout=30_000)
    web.page.get_by_role("button", name="Save evening").click()
    dialog = web.page.get_by_role("dialog", name="Save this evening?")
    expect(dialog.get_by_text("→ reading list", exact=False)).to_be_visible()
    web.shot("evening-02-save-approval")
    dialog.get_by_role("button", name="Approve").click()
    expect(web.page.get_by_role("dialog")).to_have_count(0)
    session = web.session()
    assert len(session["readingList"]) == 1 and len(session["cocktailShortlist"]) == 1
    assert len(session["shortlist"]) >= 2


def test_book_club_alcohol_free_on_the_planner_page(web):
    plan_on_page(web, "Book club")
    web.page.get_by_role("switch", name="Alcohol-free").check()
    plan_now(web)
    assert web.page.get_by_role("switch", name="Alcohol-free").is_checked()
    drink = web.page.get_by_test_id("evening-drink")
    expect(drink.get_by_text("Alcohol-free", exact=True).first).to_be_visible()
    expect(drink.get_by_role("button", name="Alcohol-free")).to_be_disabled()
    web.shot("evening-03-book-club-alcohol-free", full_page=True)


def test_date_night_under_100_in_chat_with_a_swap_and_an_approved_save(web):
    web.open("/evening")
    web.page.get_by_role("navigation", name="Evening pages").get_by_role(
        "link", name="Ask Curio"
    ).click()
    expect(web.page.get_by_role("heading", name="Plan my evening.")).to_be_visible()
    web.send("Plan a date night under €100")
    turn = web.last_turn()
    card = turn.get_by_test_id("evening-plan")
    expect(card).to_be_visible()
    expect(card.get_by_text("of €100.00", exact=False)).to_be_visible()
    expect(card.get_by_text("Romantic", exact=True)).to_be_visible()
    expect(turn.get_by_test_id("grounding-badge")).to_have_text("Prices, titles and drinks checked")
    web.shot("evening-04-chat-date-night", full_page=True)
    web.click_and_answer(card.get_by_role("button", name="A cheaper shoes"))
    expect(web.page.get_by_role("log", name="Conversation")).to_contain_text("Cheaper shoes")
    expect(web.last_turn()).to_contain_text("A cheaper shoes")
    web.send("Save the evening")
    drawer = web.page.get_by_test_id("approval-drawer")
    expect(drawer.get_by_role("heading", name="Needs your approval")).to_be_visible()
    expect(drawer.get_by_role("checkbox")).to_have_count(6)  # 4 pieces, a book, a drink
    web.shot("evening-05-chat-save-approval")
    drawer.get_by_role("button", name="Approve").click()
    expect(drawer).to_be_hidden(timeout=120_000)
    expect(web.page.get_by_text("each part is saved to its own list", exact=False)).to_be_visible()
    counts = web.page.get_by_test_id("session-panel")
    for label, value in (("Pieces", "4"), ("Books", "1"), ("Drinks", "1")):
        expect(counts.locator("dl div", has_text=label).locator("dd")).to_have_text(value)
    web.shot("evening-06-chat-saved", full_page=True)


def test_an_invented_drink_in_an_evening_answer_falls_back(web):
    web.open("/ask/evening")
    web.send("Plan a murder mystery night, surprise me")
    turn = web.last_turn()
    expect(turn.get_by_test_id("grounding-badge")).to_have_text(
        "Unverified answer replaced with catalog facts"
    )
    expect(turn.get_by_text("from the catalogs:", exact=False)).to_be_visible()
    expect(web.page.get_by_text("Negroni", exact=False)).to_have_count(0)
    web.shot("evening-07-grounding-fallback", full_page=True)


def test_evening_on_a_phone(web_phone):
    plan_on_page(web_phone, "Date night")
    plan_now(web_phone)
    expect(web_phone.page.get_by_test_id("evening-drink")).to_be_visible()
    assert web_phone.no_horizontal_scroll()
    web_phone.shot("evening-08-phone", full_page=True)


# --- Quick start without fashion -------------------------------------------------------------


@pytest.fixture
def demo_api(demo_api_url: str):
    with httpx.Client(base_url=demo_api_url, timeout=60, trust_env=False) as client:
        yield client


def test_quick_start_plans_the_book_and_drink_and_says_the_outfit_needs_fashion(demo_api):
    domains = {d["id"]: d for d in demo_api.get("/domains").json()}
    evening = domains["evening"]
    assert (evening["enabled"], evening["chat_enabled"], evening["item_count"]) == (True, False, 2)
    assert "fashion catalogue" in evening["hint"]
    result = demo_api.post("/evening/plan", json={"occasion": "rainy Sunday in"}).json()
    assert result["status"] == "partial" and result["outfit"] is None
    assert result["missing"][0]["part"] == "outfit"
    assert "fashion catalogue" in result["missing"][0]["reason"]
    assert result["book"] and result["drink"]
    assert result["why"].endswith("· no outfit yet")


def test_quick_start_planner_page_without_fashion(demo_web):
    demo_web.open("/")
    page = demo_web.page
    expect(page).to_have_url(re.compile(r"/evening$"))
    expect(page.get_by_text("The outfit needs the fashion catalogue", exact=False)).to_be_visible()
    fashion = page.get_by_role("navigation", name="Catalog").get_by_text("Fashion", exact=True)
    expect(fashion.locator("xpath=..")).to_have_attribute("aria-disabled", "true")
    page.get_by_role("group", name="Occasions").get_by_role(
        "button", name="Rainy Sunday in", exact=True
    ).click()
    plan_now(demo_web)
    expect(
        page.get_by_test_id("evening-outfit").get_by_text("fashion catalogue", exact=False)
    ).to_be_visible()
    expect(page.get_by_test_id("evening-book")).to_be_visible()
    expect(page.get_by_test_id("evening-drink")).to_be_visible()
    demo_web.shot("evening-09-quickstart-no-fashion", full_page=True)
