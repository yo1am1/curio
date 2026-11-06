"""Plan my evening end to end: the real catalogs through the API, the agent and the UI.

Needs the prepared H&M data and photo index, the books and the cocktails. Writes the real
plans for several occasions to `evening-plans.json` in the artifacts folder. The quick-start
case (books and cocktails, no fashion, no LLM) runs on its own API.
"""

import json

import httpx
import pytest
from playwright.sync_api import expect

from curio.domains.books.taxonomy import EXCLUDED_SHELVES
from curio.domains.cocktails.recommend import CRUDE_NAMES
from tests.e2e.conftest import DATA_DIR
from tests.e2e.test_ui_e2e import visible_photos

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


def test_a_new_visit_opens_on_plan_my_evening_with_the_engine_brand(ui):
    ui.open()
    expect(ui.page.get_by_role("heading", name="Plan my evening", exact=True)).to_be_visible()
    expect(ui.page.get_by_text("TRUSTWORTHY RECOMMENDATION AGENTS")).to_be_visible()
    ui.click(ui.page.get_by_test_id("stButtonGroup").first.get_by_text("Books"))
    expect(ui.page.get_by_role("heading", name="Find your next great read.")).to_be_visible()
    ui.click(ui.page.get_by_test_id("stButtonGroup").first.get_by_text("Evening"))
    expect(ui.page.get_by_role("heading", name="Plan my evening", exact=True)).to_be_visible()


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


# --- Browser ---------------------------------------------------------------------------------


def plan_on_page(ui, occasion_chip: str | None = None, text: str | None = None) -> None:
    ui.open("evening")
    if occasion_chip:
        ui.click(ui.key("evening_occasion_chip").locator("button", has_text=occasion_chip))
    if text:
        box = ui.key("evening_occasion").get_by_role("textbox")
        box.fill(text)
        box.press("Enter")
        ui.settle()


def test_rainy_sunday_in_on_the_planner_page(ui):
    plan_on_page(ui, "Rainy Sunday in")
    ui.click(ui.key("evening_plan_button").get_by_role("button"))
    page = ui.page
    for part in ("outfit", "book", "drink"):
        expect(ui.key(f"evening_{part}_page")).to_be_visible()
    expect(page.get_by_text("Why it works", exact=False)).to_be_visible()
    expect(
        ui.key("evening_theme_page").locator(".stMarkdownBadge", has_text="Cosy")
    ).to_be_visible()
    visible_photos(ui, 5)  # three pieces, the cover and the drink
    ui.shot("80-evening-rainy-sunday")
    book = ui.key("evening_book_page").locator('[data-testid="stText"]').first.inner_text()
    ui.click(ui.key("evening_another_book_page").get_by_role("button"))
    expect(ui.key("evening_book_page").locator('[data-testid="stText"]').first).not_to_have_text(
        book
    )
    ui.click(ui.key("evening_save_page").get_by_role("button"))
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_text("→ reading list", exact=False)).to_be_visible()
    ui.shot("81-evening-save-approval")
    ui.click(dialog.get_by_role("button", name="Approve"))
    expect(page.get_by_role("dialog")).to_have_count(0)


def test_book_club_alcohol_free_on_the_planner_page(ui):
    plan_on_page(ui, "Book club")
    ui.click(ui.key("evening_alcohol_free").get_by_text("Alcohol-free"))
    ui.click(ui.key("evening_plan_button").get_by_role("button"))
    drink = ui.key("evening_drink_page")
    expect(drink.get_by_text("Alcohol-free", exact=True).first).to_be_visible()
    expect(drink.get_by_role("button", name="Alcohol-free")).to_be_disabled()
    visible_photos(ui, 4)
    ui.shot("82-evening-book-club-alcohol-free")


def test_date_night_under_100_in_chat_with_a_swap_and_an_approved_save(ui):
    ui.ask("Evening")
    expect(ui.page.get_by_role("heading", name="Plan my evening.")).to_be_visible()
    ui.send("Plan a date night under €100")
    card = ui.key("chat_evening_home_1")
    expect(card).to_be_visible()
    expect(card.get_by_text("of €100.00", exact=False)).to_be_visible()
    expect(card.locator(".stMarkdownBadge", has_text="Romantic")).to_be_visible()
    expect(ui.page.get_by_text("Prices, titles and drinks checked")).to_be_visible()
    visible_photos(ui, 5)
    ui.shot("83-evening-chat-date-night")
    ui.click(ui.key("evening_cheaper_chat_home_1_shoes").get_by_role("button"))
    expect(ui.page.get_by_test_id("stChatMessage").last).to_contain_text("A cheaper shoes")
    ui.send("Save the evening")
    approval = ui.key("approval_home_5")
    expect(approval.get_by_text("Needs your approval")).to_be_visible()
    expect(approval.get_by_role("checkbox")).to_have_count(6)  # 4 pieces, a book, a drink
    ui.shot("84-evening-chat-save-approval")
    ui.click(approval.get_by_role("button", name="Approve"))
    expect(ui.page.get_by_text("each part is saved to its own list")).to_be_visible()
    expect(ui.key("agent_session").get_by_text("4 pieces · 1 books · 1 drinks")).to_be_visible()
    ui.shot("85-evening-chat-saved")


def test_an_invented_drink_in_an_evening_answer_falls_back(ui):
    ui.ask("Evening")
    ui.send("Plan a murder mystery night, surprise me")
    expect(ui.page.get_by_text("Unverified answer replaced with catalog facts")).to_be_visible()
    expect(ui.page.get_by_text("from the catalogs:", exact=False)).to_be_visible()
    expect(ui.page.get_by_text("Negroni", exact=False)).to_have_count(0)
    ui.shot("86-evening-grounding-fallback")


def test_evening_on_a_phone(phone):
    phone.open("evening")
    phone.click(phone.key("evening_occasion_chip").locator("button", has_text="Date night"))
    phone.click(phone.key("evening_plan_button").get_by_role("button"))
    expect(phone.key("evening_drink_page")).to_be_visible()
    assert phone.no_horizontal_scroll()
    phone.key("evening_theme_page").scroll_into_view_if_needed()
    phone.shot("87-phone-evening")


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


def test_quick_start_planner_page_without_fashion(demo_ui):
    ui = demo_ui.open("evening")
    page = ui.page
    expect(
        page.get_by_text("The outfit needs the fashion catalogue", exact=False).first
    ).to_be_visible()
    ui.click(ui.key("evening_occasion_chip").locator("button", has_text="Rainy Sunday in"))
    ui.click(ui.key("evening_plan_button").get_by_role("button"))
    expect(
        ui.key("evening_outfit_page").get_by_text("fashion catalogue", exact=False)
    ).to_be_visible()
    expect(ui.key("evening_book_page")).to_be_visible()
    expect(ui.key("evening_drink_page")).to_be_visible()
    visible_photos(ui, 2)
    ui.shot("88-quickstart-evening-no-fashion")
