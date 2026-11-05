"""Book → cocktail pairing end to end: the real catalogs through the API, the agent and the UI.

Needs the prepared books (scripts.prepare_books) and cocktails (scripts.prepare_cocktails).
Writes the real top-3 pairings of famous books to `pairings.json` in the artifacts folder.
"""

import json
import re

import httpx
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import DATA_DIR
from tests.e2e.test_ui_e2e import visible_photos

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "books.parquet").exists() or not (DATA_DIR / "cocktails.parquet").exists(),
    reason="Books or cocktails not prepared",
)
PATH = "/pairings/book-cocktail"
WHISKEY = ("bourbon", "whiskey", "whisky", "scotch", "rye")
FAMOUS = [
    "The Great Gatsby",
    "The Big Sleep",
    "Murder on the Orient Express",
    "Gone Girl",
    "Pride and Prejudice",
    "Dracula",
    "The Name of the Wind",
    "Dune",
    "Casino Royale",
    "A Christmas Carol",
    "The Old Man and the Sea",
    "Under the Tuscan Sun",
    "Where the Wild Things Are",
    "Harry Potter and the Sorcerer's Stone",
]


@pytest.fixture(scope="module")
def api(api_url: str):
    with httpx.Client(base_url=api_url, timeout=120, trust_env=False) as client:
        yield client


@pytest.fixture(scope="module")
def book_ids(api) -> dict[str, int]:
    index = api.get("/books/index").json()
    found: dict[str, int] = {}
    for ref in index:  # most rated first: the famous edition wins
        found.setdefault(ref["title"], ref["book_id"])
    return {title: found[title] for title in FAMOUS}


def pair(api, **body) -> dict:
    response = api.post(PATH, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def ingredients(item: dict) -> set[str]:
    return {i["name"].lower() for i in item["cocktail"]["ingredients"]}


# --- API: golden checks on the real data ------------------------------------------------------


def test_real_top_three_pairings_of_famous_books(api, book_ids, artifacts):
    table = {}
    for title, book_id in book_ids.items():
        result = pair(api, book_id=book_id)
        assert len(result["pairings"]) == 3, title
        for item in result["pairings"]:
            assert 1 <= len(item["reasons"]) <= 3
            assert item["cocktail"]["category"] != "Shot"
        table[title] = {
            "profile": result["profile"]["summary"],
            "alcohol_free": result["profile"]["alcohol_free"],
            "pairings": [
                {"drink": p["cocktail"]["name"], "why": p["reasons"][0]} for p in result["pairings"]
            ],
        }
    (artifacts / "pairings.json").write_text(json.dumps(table, indent=2, ensure_ascii=False))


def test_a_noir_mystery_gets_a_whiskey_forward_drink(api, book_ids):
    result = pair(api, book_id=book_ids["The Big Sleep"])
    assert result["profile"]["themes"][0]["id"] == "noir"
    whiskey = [
        p for p in result["pairings"] if any(w in i for i in ingredients(p) for w in WHISKEY)
    ]
    assert whiskey
    assert "→ a bitter, spirit-forward whiskey drink" in whiskey[0]["reasons"][0]


@pytest.mark.parametrize(
    "title", ["Where the Wild Things Are", "Harry Potter and the Sorcerer's Stone"]
)
def test_a_childrens_book_gets_only_alcohol_free_drinks(api, book_ids, title):
    first = pair(api, book_id=book_ids[title], alcohol_free=False)
    for seed in range(first["sets"]):
        result = pair(api, book_id=book_ids[title], seed=seed, top_k=5)
        assert result["profile"]["alcohol_free"]
        assert result["note"].startswith("Alcohol-free recipes only")
        assert result["pairings"]
        assert all(p["cocktail"]["alcoholic"] == "Non alcoholic" for p in result["pairings"])


def test_the_great_gatsby_gets_a_jazz_age_drink(api, book_ids):
    result = pair(api, book_id=book_ids["The Great Gatsby"])
    first = result["pairings"][0]
    assert first["cocktail"]["name"] in {"Gin Rickey", "French 75", "Mint Julep", "Sidecar"}
    assert "The Great Gatsby" in first["reasons"][0]
    assert {"gin", "champagne"} & set().union(*(ingredients(p) for p in result["pairings"]))


def test_reader_taste_another_pairing_and_avoided_genres(api, book_ids):
    body = {
        "book_ids": [
            book_ids[t] for t in ("The Big Sleep", "Murder on the Orient Express", "Dracula")
        ],
        "avoided_genres": ["Horror"],
        "liked_genres": ["Fantasy"],
    }
    result = pair(api, **body)
    themes = [t["id"] for t in result["profile"]["themes"]]
    assert {"noir", "fantasy"} <= set(themes) and "horror" not in themes
    noir = next(t for t in result["profile"]["themes"] if t["id"] == "noir")
    assert noir["evidence"] == "2 of 3 books"
    reasons = [p["reasons"][0] for p in result["pairings"]]
    # Each reason cites the reading list, a liked genre or an author's own drink (lore).
    prefixes = ("Your reading list leans", "You like Fantasy", "The Big Sleep is by")
    assert all(r.startswith(prefixes) for r in reasons), reasons
    another = pair(api, **body, seed=1)
    shown = {p["cocktail"]["drink_id"] for p in result["pairings"]}
    assert shown.isdisjoint(p["cocktail"]["drink_id"] for p in another["pairings"])
    remembered = pair(api, **body, cocktail_preferences={"alcohol_free": True})
    assert all(p["cocktail"]["alcoholic"] == "Non alcoholic" for p in remembered["pairings"])
    assert api.post(PATH, json={"book_id": 99_999}).status_code == 404
    assert api.post(PATH, json={"book_id": 1, "seed": 99_999}).status_code == 422


# --- Agent ----------------------------------------------------------------------------------------


def stream(api, message: str, **context) -> tuple[list[str], dict]:
    body = {"domain": "books", "message": message} | context
    with api.stream("POST", "/chat/stream", json=body) as response:
        events = [json.loads(line) for line in response.iter_lines() if line.strip()]
    return [e["step"]["name"] for e in events if e["type"] == "step"], events[-1]["reply"]


def test_the_book_agent_pairs_a_drink_with_verified_names(api):
    steps, reply = stream(api, "What should I drink with The Big Sleep?")
    assert steps[:2] == ["Understand request", "PairCocktail"]
    assert reply["grounding"]["status"] == "verified"
    names = [c["name"] for c in reply["cocktails"]]
    assert len(names) == 3 and names == [
        p["cocktail"]["name"] for p in reply["pairing"]["pairings"]
    ]
    assert all(f"**{name}**" in reply["text"] for name in names)
    assert "Another pairing" in reply["suggestions"]


def test_an_invented_drink_is_caught_and_replaced_by_the_paired_drinks(api):
    _, reply = stream(api, "Surprise me with a drink for The Big Sleep")
    assert reply["grounding"]["status"] == "fallback"
    assert any("Negroni" in issue for issue in reply["grounding"]["issues"])
    assert "Negroni" not in reply["text"]
    assert all(c["name"] in reply["text"] for c in reply["cocktails"])


# --- UI -------------------------------------------------------------------------------------------


def open_book(ui, title: str, *, reload: bool = True):
    """Search the Books page for `title` and open its dialog; without `reload` the session
    (reading list, memory) carries on."""
    if reload:
        ui.open("books")
    ui.page.get_by_label("Describe a book").fill(title)
    ui.click(ui.page.get_by_role("button", name="Find books"))
    card = ui.page.locator('[class*="st-key-book_card_search_"]').filter(has_text=title).first
    ui.click(card.get_by_role("button", name=f"View {title}"))
    return ui.page.get_by_role("dialog")


def test_pair_a_drink_in_the_book_dialog(ui, artifacts):
    dialog = open_book(ui, "The Great Gatsby")
    ui.click(dialog.get_by_role("button", name="Pair a drink"))
    drinks = dialog.locator('[class*="st-key-pairing_drink_book_"]')
    expect(drinks).to_have_count(3)
    expect(drinks.get_by_test_id("stText").get_by_text("Gin Rickey", exact=True)).to_be_visible()
    expect(dialog.get_by_text(re.compile("Tom Buchanan mixes gin rickeys"))).to_be_visible()
    visible_photos(ui, 4)
    dialog.locator(".st-key-book_pairing").scroll_into_view_if_needed()
    ui.shot("70-pairing-book-dialog")

    first = drinks.first.get_by_test_id("stText").first.inner_text()
    ui.click(dialog.get_by_role("button", name="Another pairing"))
    expect(drinks.first.get_by_test_id("stText").first).not_to_have_text(first)
    ui.click(dialog.get_by_text("Alcohol-free only"))
    expect(dialog.get_by_text(re.compile("Alcohol-free recipes only"))).to_be_visible()
    badges = drinks.get_by_text(re.compile(r"Alcohol-free$"))
    expect(badges).to_have_count(drinks.count())
    ui.shot("71-pairing-alcohol-free")

    # A drink opens on the Cocktails page (a dialog cannot open another dialog).
    name = drinks.first.get_by_test_id("stText").first.inner_text()
    ui.click(drinks.first.get_by_role("button", name="Recipe"))
    recipe = ui.page.get_by_role("dialog")
    expect(recipe.get_by_role("heading", name=name)).to_be_visible()
    expect(recipe.get_by_text("Ingredients")).to_be_visible()
    assert "/cocktails" in ui.page.url


def test_reading_list_cocktail_card_and_chat_starter(ui):
    ui.open("books")
    for title in ("The Big Sleep", "The Maltese Falcon"):
        dialog = open_book(ui, title, reload=False)
        ui.click(dialog.get_by_role("button", name="Save to reading list"))
        expect(dialog.get_by_role("button", name="On your reading list")).to_be_visible()
        ui.click(dialog.get_by_label("Close"))
    # Closing a dialog does not rerun the page; the next interaction shows the new list.
    ui.click(ui.page.get_by_role("button", name="Find books"))
    card = ui.key("pairing_reading_list")
    expect(card.get_by_role("heading", name="Your reading list's cocktail")).to_be_visible()
    expect(card.locator('[class*="st-key-pairing_drink_reading_list_"]')).to_have_count(3)
    expect(card).to_contain_text("Your reading list (2 books) leans Mystery and noir")
    expect(card).to_contain_text("Your reading list leans Mystery and noir (2 of 2 books)")
    card.scroll_into_view_if_needed()
    visible_photos(ui, 3)
    ui.shot("72-pairing-reading-list-card")

    ui.click(ui.page.get_by_role("link", name="Ask Curio"))
    ui.click(ui.page.get_by_role("button", name="Pair a cocktail with my reading list"))
    pairing = ui.page.locator('[class*="st-key-chat_pairing_home_"]').last
    expect(pairing.locator('[class*="st-key-pairing_drink_chat_home_"]')).to_have_count(3)
    expect(ui.page.get_by_text("Titles and ratings checked against the catalog")).to_be_visible()
    ui.click(ui.page.get_by_role("button", name="Another pairing"))
    expect(ui.page.locator('[class*="st-key-chat_pairing_home_"]')).to_have_count(2)
    ui.shot("73-pairing-chat", full_page=True)


def test_phone_pairing_dialog_fits_the_screen(phone):
    dialog = open_book(phone, "Dracula")
    phone.click(dialog.get_by_role("button", name="Pair a drink"))
    expect(dialog.locator('[class*="st-key-pairing_drink_book_"]')).to_have_count(3)
    assert phone.no_horizontal_scroll()
    dialog.locator(".st-key-book_pairing").scroll_into_view_if_needed()
    phone.shot("74-phone-pairing")
