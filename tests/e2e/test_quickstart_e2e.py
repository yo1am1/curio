"""The quick start, end to end: an API with only the cocktail recipes, serving the React app,
and a browser.

This is what `DEMO_BOOKS=0 docker compose --profile demo up` and `CURIO_BOOKS=0
scripts/quickstart.sh` serve: no H&M data, no image index, no LLM. Fashion must be reported
unavailable, every fashion endpoint must answer 503 with how to prepare it, and the app must
land on the cocktail pages, never blank, with fashion shown as unavailable and its hint.
"""

import re

import httpx
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import results


@pytest.fixture
def quickstart_api(quickstart_api_url: str):
    with httpx.Client(base_url=quickstart_api_url, timeout=30, trust_env=False) as client:
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


def test_app_lands_on_cocktails_and_explains_the_rest(quickstart_web):
    web = quickstart_web.open("/")
    page = web.page
    expect(page).to_have_url(re.compile(r"/drinks$"))
    expect(page.get_by_role("heading", name="Cocktails", level=1)).to_be_visible()
    # Fashion, books and the evening are shown as unavailable, each with its hint.
    switch = page.get_by_role("navigation", name="Catalog")
    expect(switch.get_by_role("link")).to_have_count(1)
    expect(switch.get_by_role("link", name="Cocktails")).to_have_attribute("aria-current", "page")
    fashion = switch.locator('[aria-disabled="true"]', has_text="Fashion")
    expect(fashion).to_be_visible()
    hint = page.locator(f"#{fashion.get_attribute('aria-describedby')}")
    expect(hint).to_contain_text("Fashion is not available.")
    expect(hint).to_contain_text("fashion-data")
    fashion.focus()
    expect(hint).to_be_visible()
    web.shot("quickstart-01-landing")

    page.get_by_role("button", name="Margarita").click()
    cards = results(web, "Matching drinks").get_by_test_id("cocktail-card")
    expect(cards.first).to_be_visible()
    expect(page.get_by_text("Recipe data: TheCocktailDB", exact=False).first).to_be_visible()
    web.shot("quickstart-02-cocktails", full_page=True)

    # Chat explains how to enable it, and offers no message box.
    page.get_by_role("navigation", name="Cocktails pages").get_by_role(
        "link", name="Ask Curio"
    ).click()
    expect(page.get_by_role("heading", name="Mix something new tonight.")).to_be_visible()
    expect(page.get_by_text("search recipes on the Cocktails page", exact=False)).to_be_visible()
    expect(page.get_by_text("To enable chat, set LLM_MODEL", exact=False)).to_be_visible()
    expect(page.get_by_label("Message Curio")).to_have_count(0)
    web.shot("quickstart-03-chat-off")


def test_fashion_deep_link_falls_back_to_a_working_page(quickstart_web):
    web = quickstart_web
    for path in ("/fashion/discover", "/ask/fashion", "/evening"):
        web.open(path)
        expect(web.page).to_have_url(re.compile(r"/drinks$"))
        expect(web.page.get_by_role("heading", name="Cocktails", level=1)).to_be_visible()
    web.shot("quickstart-04-fashion-deep-link")
