"""Browser end to end: real clicks through every page, saving a screenshot at each step."""

import re

from playwright.sync_api import expect


def visible_photos(driver, minimum: int) -> None:
    driver.page.wait_for_function(
        """minimum => {
            const photos = [...document.querySelectorAll('[data-testid="stImage"] img')]
                .filter(img => img.offsetParent !== null);
            return photos.length >= minimum && photos.every(i => i.complete && i.naturalWidth > 0);
        }""",
        arg=minimum,
        timeout=120_000,
    )


def chat_turns(driver) -> int:
    return driver.page.get_by_test_id("stChatMessage").count()


def test_fashion_feed_shows_personal_picks_with_evidence(ui):
    ui.open("fashion")
    expect(ui.page.get_by_role("heading", name="For you")).to_be_visible()
    cards = ui.page.locator('[class*="st-key-product_card_recommendations_"]')
    expect(cards).to_have_count(12)
    # Purchase-based picks are often unphotographed (the photo index covers about a quarter
    # of the catalog); those cards show a placeholder rather than a broken image.
    visible_photos(ui, 0)
    ui.shot("01-fashion-feed")
    ui.click(ui.page.get_by_role("button", name="How picks are chosen"))
    for strategy in ("Similar shoppers", "Purchase connections", "Visual style"):
        ui.click(ui.page.get_by_text(strategy, exact=True))
        expect(cards).to_have_count(12)
    ui.page.keyboard.press("Escape")
    visible_photos(ui, 6)  # visual style only recommends photographed products
    ui.shot("02-fashion-feed-visual-style")


def test_discover_by_description_and_by_photo(ui):
    ui.open("discover")
    ui.click(ui.key("text_example_1").get_by_role("button"))  # "white sneakers"
    visible_photos(ui, 6)
    names = ui.key("text_search_results").locator('[data-testid="stText"]').all_inner_texts()
    assert len(names) >= 6
    ui.shot("03-discover-description")
    ui.click(ui.page.get_by_text("Photo", exact=True))
    ui.click(ui.page.get_by_text("Or pick an example photo"))
    ui.click(ui.page.locator('[class*="st-key-query_photo_"] button'))
    visible_photos(ui, 6)
    expect(ui.page.get_by_role("heading", name="Visually similar pieces")).to_be_visible()
    ui.shot("04-discover-photo")


def test_product_dialog_hands_off_to_the_assistant(ui):
    ui.open("discover")
    ui.click(ui.key("text_example_0").get_by_role("button"))
    visible_photos(ui, 3)
    ui.click(ui.page.get_by_role("button", name=re.compile(r"^View ")))
    dialog = ui.page.get_by_role("dialog")
    expect(dialog.get_by_text("Explore this piece")).to_be_visible()
    ui.shot("05-product-dialog")
    ui.click(dialog.get_by_role("button", name=re.compile("Ask Curio what goes with it")))
    panel = ui.key("assistant_panel")
    expect(panel).to_be_visible()
    expect(panel.get_by_text("Find related pieces").first).to_be_attached()
    ui.shot("06-popup-what-goes-with-it")


def test_product_connections_graph(ui):
    ui.open("connections")
    expect(ui.page.get_by_role("heading", name="Explore the connections")).to_be_visible()
    ui.page.locator("canvas, svg.marks").first.wait_for(timeout=60_000)
    visible_photos(ui, 1)
    ui.shot("07-product-connections", full_page=True)


def test_model_quality_page(ui):
    ui.open("quality")
    expect(ui.page.get_by_role("heading", name="Model quality")).to_be_visible()
    expect(ui.page.get_by_text("Ranking quality · MAP@12")).to_be_visible()
    ui.shot("08-model-quality", full_page=True)


def test_fashion_agent_search_trace_and_self_correction(ui):
    ui.open()
    ui.send("Black trousers under €30")
    trace = ui.page.get_by_text(re.compile(r"How Curio worked · \d+ actions?"))
    expect(trace.first).to_be_visible()
    expect(ui.page.get_by_text("Prices verified against the catalog")).to_be_visible()
    prices = ui.key("agent_conversation").locator('[class*="st-key-chat_product_home_1_"]')
    assert prices.count() >= 3
    captions = prices.locator('[data-testid="stCaptionContainer"]').all_inner_texts()
    amounts = [float(m.group(1)) for c in captions if (m := re.search(r"€([\d.]+)", c))]
    assert len(amounts) >= 3 and max(amounts) <= 30
    ui.click(trace)
    ui.shot("09-agent-search-trace")
    ui.send("Which is the best deal?")
    expect(ui.page.get_by_text("Self-corrected to match the catalog")).to_be_visible()
    ui.shot("10-agent-self-correction")


def test_fashion_agent_human_in_the_loop(ui):
    ui.open()
    ui.send("White sneakers")
    ui.click(
        ui.page.locator('[class*="st-key-chat_suggestion_home_1_"]').filter(
            has_text="Shortlist the"
        )
    )
    card = ui.page.locator('[class*="st-key-approval_home_"]').last
    expect(card.get_by_text("Needs your approval")).to_be_visible()
    ticks = card.get_by_role("checkbox")
    if ticks.count() > 1:  # narrow the proposal: keep only the first piece
        ticks.last.click(force=True)
        ui.settle()
    ui.shot("11-approval-proposed")
    ui.click(card.get_by_role("button", name="Approve"))
    expect(ui.page.get_by_text("Shortlist · 1")).to_be_visible()
    ui.shot("12-approval-applied")

    ui.send("I need an outfit")
    options = ui.page.locator('[class*="st-key-chat_options_home_"] button')
    expect(options).to_have_count(3)
    ui.shot("13-clarifying-question")
    ui.click(options.filter(has_text="Weekend casual"))

    ui.send("Remember that my budget is up to €40")
    card = ui.page.locator('[class*="st-key-approval_home_"]').last
    ui.click(card.get_by_role("button", name="Approve"))
    expect(
        ui.key("session_memory").get_by_role("button", name=re.compile("Under €40"))
    ).to_be_visible()
    ui.send("Blue jeans")
    plan = ui.page.get_by_text(re.compile(r"under €40 \(memory: max price\)"))
    ui.click(ui.page.get_by_text(re.compile(r"How Curio worked")).last)
    expect(plan.last).to_be_visible()
    ui.shot("14-memory-applied")


def test_declined_proposal_changes_nothing(ui):
    ui.open()
    ui.send("Red dress")
    ui.send("Save the first two")
    card = ui.page.locator('[class*="st-key-approval_home_"]').last
    ui.click(card.get_by_role("button", name="Decline"))
    expect(ui.page.get_by_text("Shortlist · 0")).to_be_visible()
    expect(ui.page.get_by_text("nothing was changed")).to_be_visible()


def test_personal_picks_from_a_linked_history(ui):
    ui.open()
    ui.click(ui.key("session_shopper_select"))
    ui.click(ui.page.get_by_role("option").nth(1))
    ui.send("Picks from my shopping history")
    expect(ui.page.get_by_text(re.compile("Ranked \\d+ picks from the linked"))).to_be_attached()
    picks = ui.page.locator('[class*="st-key-chat_product_home_1_"]')
    assert picks.count() >= 3
    visible_photos(ui, 0)  # purchase-based picks are often outside the photo index
    ui.shot("15-personal-picks")


def test_cocktail_domain_end_to_end(ui):
    ui.open()
    ui.click(ui.page.get_by_test_id("stButtonGroup").first.get_by_text("Cocktails", exact=True))
    expect(ui.page.get_by_role("heading", name=re.compile("Mix something"))).to_be_visible()
    ui.send("What can I make with gin, tonic and lime?")
    expect(
        ui.page.get_by_text(re.compile(r"\((contains alcohol|alcohol-free)\)")).first
    ).to_be_visible()
    visible_photos(ui, 2)
    ui.shot("16-cocktail-chat-pantry")
    ui.send("Save it")
    card = ui.page.locator('[class*="st-key-approval_home_"]').last
    ui.click(card.get_by_role("button", name="Approve"))
    expect(ui.page.get_by_text("Shortlist · 1")).to_be_visible()
    ui.shot("17-cocktail-saved")

    ui.send("Remember I don't drink alcohol")
    card = ui.page.locator('[class*="st-key-approval_home_"]').last
    ui.click(card.get_by_role("button", name="Approve"))
    ui.send("Something fruity")
    last = chat_turns(ui) - 1
    drinks = ui.page.locator(f'[class*="st-key-chat_cocktail_home_{last}_"]')
    expect(drinks.first).to_be_visible()
    assert all("Alcoholic" not in text for text in drinks.all_inner_texts())
    ui.shot("18-cocktail-alcohol-free-memory")

    # Fashion keeps its own conversation and shortlist.
    ui.click(ui.page.get_by_test_id("stButtonGroup").first.get_by_text("Fashion", exact=True))
    expect(ui.page.get_by_role("heading", name="Find your next favorite.")).to_be_visible()
    assert chat_turns(ui) <= 1


def test_cocktails_page_search_pantry_and_recipe(ui):
    ui.open("cocktails")
    ui.page.get_by_label("Describe a drink").fill("Something fresh with lime and mint")
    ui.click(ui.page.get_by_role("button", name="Find drinks"))
    visible_photos(ui, 3)
    names = ui.page.locator('[class*="st-key-cocktail_card_search_"] [data-testid="stText"]')
    assert names.count() >= 3
    ui.shot("19-cocktails-search")
    ui.click(ui.page.get_by_role("button", name=re.compile(r"^View ")))
    dialog = ui.page.get_by_role("dialog")
    expect(dialog.get_by_text("Please drink responsibly.")).to_be_visible()
    expect(dialog.get_by_text(re.compile("TheCocktailDB"))).to_be_visible()
    ui.shot("20-recipe-dialog")
    ui.page.keyboard.press("Escape")
    ui.settle()
    ui.click(ui.page.get_by_text("What can I make?", exact=True))
    picker = ui.page.get_by_label("Ingredients you have")
    for ingredient in ("Gin", "Tonic water", "Lime"):
        picker.click()
        ui.page.keyboard.type(ingredient)
        ui.page.get_by_role("option", name=re.compile(f"^{ingredient}$", re.I)).first.click()
        ui.settle()
    ui.page.keyboard.press("Escape")
    ui.settle()
    expect(ui.page.get_by_role("heading", name="Drinks you can make")).to_be_visible()
    ui.shot("21-cocktails-pantry", full_page=True)


def test_phone_layouts_do_not_scroll_sideways(phone):
    for path, name in (
        ("", "22-phone-home"),
        ("fashion", "23-phone-feed"),
        ("cocktails", "24-phone-cocktails"),
    ):
        phone.open(path)
        assert phone.no_horizontal_scroll(), path
        phone.shot(name)
    # Phones get a compact launcher, and the page's last content scrolls clear of it.
    launcher = phone.key("assistant_launcher").locator("button")
    expect(launcher).to_have_accessible_name(re.compile("Ask Curio"))
    button = launcher.bounding_box()
    assert button["width"] <= 64, button
    phone.page.evaluate(
        """() => {
            const scrollers = document.querySelectorAll('[data-testid="stMain"], section');
            for (const el of [document.scrollingElement, ...scrollers])
                if (el) el.scrollTop = el.scrollHeight;
        }"""
    )
    phone.page.wait_for_timeout(500)
    note = phone.key("catalog_note").bounding_box()
    assert note["y"] + note["height"] <= button["y"], (note, button)
    phone.shot("25-phone-cocktails-bottom")
