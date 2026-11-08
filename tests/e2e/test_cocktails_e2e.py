"""Cocktails in the React app end to end: the real recipe catalog through the API, the
scripted LLM and Chromium.

Covers the cocktail chat (a pantry question, a shortlist approval, a remembered alcohol-free
taste applied to the next search, conversations kept per domain) and a recipe's dialog. The
recipe search filters and "What can I make?" are in `test_web_e2e.py`. Screenshots go to the
artifacts folder as `web-cocktails-*.png`.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import DATA_DIR, results

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "cocktails.parquet").exists(), reason="Cocktails not prepared"
)


def approve(web) -> None:
    drawer = web.page.get_by_test_id("approval-drawer")
    expect(drawer.get_by_role("heading", name="Needs your approval")).to_be_visible()
    drawer.get_by_role("button", name="Approve").click()
    expect(drawer).to_be_hidden(timeout=120_000)


@pytest.mark.fashion
def test_cocktail_chat_end_to_end(web):
    web.open("/drinks")
    web.page.get_by_role("navigation", name="Cocktails pages").get_by_role(
        "link", name="Ask Curio"
    ).click()
    expect(web.page.get_by_role("heading", name="Mix something new tonight.")).to_be_visible()
    web.send("What can I make with gin, tonic and lime?")
    turn = web.last_turn()
    expect(
        turn.get_by_text(re.compile(r"\((contains alcohol|alcohol-free)\)")).first
    ).to_be_visible()
    expect(turn.get_by_test_id("cocktail-card").first).to_be_visible()
    web.shot("cocktails-01-chat-pantry", full_page=True)

    web.send("Save it")
    approve(web)
    panel = web.page.get_by_test_id("session-panel")
    expect(panel.get_by_role("heading", name=re.compile(r"Shortlist · 1"))).to_be_visible()
    expect(panel.get_by_role("list", name="Saved drinks").get_by_role("listitem")).to_have_count(1)
    web.shot("cocktails-02-saved")

    web.send("Remember I don't drink alcohol")
    approve(web)
    expect(
        panel.get_by_role("list", name="Remembered for this session").get_by_role("button")
    ).not_to_have_count(0)
    web.send("Something fruity")
    drinks = web.last_turn().get_by_test_id("cocktail-card")
    expect(drinks.first).to_be_visible()
    for text in drinks.all_inner_texts():
        assert "Alcoholic" not in text and "Alcohol-free" in text, text
    assert web.chat_bodies[-1]["cocktail_preferences"]
    web.shot("cocktails-03-alcohol-free-memory", full_page=True)

    # Fashion keeps its own conversation and shortlist.
    web.page.get_by_role("navigation", name="Catalog").get_by_role("link", name="Fashion").click()
    web.page.get_by_role("navigation", name="Fashion pages").get_by_role(
        "link", name="Ask Curio"
    ).click()
    expect(web.page.get_by_role("heading", name="Find your next favorite.")).to_be_visible()
    expect(web.turns()).to_have_count(0)
    shortlist = web.page.get_by_test_id("session-panel").get_by_role(
        "heading", name=re.compile(r"Shortlist · 0")
    )
    expect(shortlist).to_be_visible()


def test_recipe_dialog_from_a_search(web):
    web.open("/drinks")
    box = web.page.get_by_label("Describe a drink")
    box.fill("Something fresh with lime and mint")
    with web.page.expect_request(lambda r: r.url.endswith("/cocktails/search")):
        box.press("Enter")
    found = results(web, "Matching drinks")
    cards = found.get_by_test_id("cocktail-card")
    expect(cards.nth(2)).to_be_visible()
    cards.first.get_by_role("button", name=re.compile(r"^View ")).click()
    dialog = web.page.get_by_role("dialog")
    expect(dialog.get_by_role("heading", name="Ingredients")).to_be_visible()
    expect(dialog.get_by_text("Please drink responsibly.", exact=False)).to_be_visible()
    expect(dialog.get_by_text(re.compile("TheCocktailDB"))).to_be_visible()
    web.shot("cocktails-04-recipe-dialog")
    web.page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
