"""The quick start, end to end: an API with only the cocktail recipes, the real UI, a browser.

This is what `docker compose --profile demo up` and `scripts/quickstart.sh` serve: no H&M
data, no image index, no LLM. Fashion must be reported unavailable, every fashion endpoint
must answer 503 with how to prepare it, and the UI must open on cocktails, never blank.
"""

import httpx
import pytest
from playwright.sync_api import expect


@pytest.fixture
def quickstart_api(quickstart_api_url: str):
    with httpx.Client(base_url=quickstart_api_url, timeout=30) as client:
        yield client


def test_api_serves_cocktails_only(quickstart_api):
    health = quickstart_api.get("/health").json()
    assert (health["fashion_enabled"], health["cocktails_enabled"]) == (False, True)
    assert health["cocktail_count"] == 425
    assert health["llm_configured"] is False
    domains = {d["id"]: d for d in quickstart_api.get("/domains").json()}
    assert not domains["fashion"]["enabled"]
    assert "fashion-data" in domains["fashion"]["hint"]
    assert domains["cocktails"]["enabled"] and not domains["cocktails"]["chat_enabled"]
    assert "LLM_MODEL" in domains["cocktails"]["hint"]
    for path in ("/products/1", "/sections", "/demo/customers"):
        response = quickstart_api.get(path)
        assert response.status_code == 503
        assert "fashion catalog is not prepared" in response.json()["detail"]
    hits = quickstart_api.post("/cocktails/search", json={"query": "mojito"}).json()
    assert hits[0]["cocktail"]["name"] == "Mojito"


def test_ui_opens_on_cocktails_and_explains_the_rest(quickstart_ui):
    ui = quickstart_ui.open()
    page = ui.page
    expect(page.get_by_role("heading", name="Mix something new tonight.")).to_be_visible()
    expect(page.get_by_text("Fashion is not available")).to_be_visible()
    expect(page.get_by_text("search recipes on the Cocktails page")).to_be_visible()
    expect(page.get_by_text("To enable chat, set LLM_MODEL", exact=False)).to_be_visible()
    expect(page.get_by_test_id("stChatInput")).to_have_count(0)
    # No fashion page is reachable: the menu has the cocktail pages only.
    expect(page.get_by_role("link", name="Cocktails", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Discover", exact=True)).to_have_count(0)
    ui.shot("quickstart-01-home")

    ui.open("cocktails")
    ui.click(ui.key("cocktail_example_0").get_by_role("button"))  # "Margarita"
    cards = page.locator('[class*="st-key-cocktail_card_"]')
    expect(cards.first).to_be_visible()
    expect(page.get_by_text("Recipe data: TheCocktailDB", exact=False).first).to_be_visible()
    ui.shot("quickstart-02-cocktails")


def test_fashion_deep_link_falls_back_to_a_working_page(quickstart_ui):
    ui = quickstart_ui.open("discover")
    expect(ui.page.get_by_role("heading", name="Mix something new tonight.")).to_be_visible()
    ui.shot("quickstart-03-fashion-deep-link")
