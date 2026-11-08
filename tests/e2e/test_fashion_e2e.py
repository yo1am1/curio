"""Fashion in the React app end to end: the real catalog, CLIP index, recommenders and ranker
through the API, the scripted LLM and Chromium.

Covers the product dialog's hand-off to the assistant, the agent's self-correction, the
human in the loop (a narrowed shortlist approval, a clarifying question, a remembered budget
applied to the next search), a declined proposal and personal picks from a linked shopping
history. The feed, Discover, Product connections, Model quality, the step trace and the
approval drawer are in `test_web_e2e.py`. Screenshots go to the artifacts folder as
`web-fashion-*.png`.
"""

import re

from playwright.sync_api import expect

from tests.e2e.conftest import results


def open_trace(turn) -> None:
    turn.get_by_role("button", name=re.compile(r"How Curio worked")).click()


def test_product_dialog_hands_off_to_the_assistant(web):
    web.open("/fashion/discover")
    web.page.get_by_role("button", name="white sneakers").click()
    found = results(web, "Matching pieces")
    found.get_by_test_id("product-card").first.get_by_role(
        "button", name=re.compile(r"^View ")
    ).click()
    dialog = web.page.get_by_role("dialog")
    expect(dialog.get_by_role("region", name="Explore this piece")).to_be_visible()
    web.shot("fashion-01-product-dialog")
    dialog.get_by_role("button", name=re.compile("Ask Curio what goes with it")).click()
    expect(web.page).to_have_url(re.compile(r"/ask/fashion$"))
    web.wait_for_turns(1)
    turn = web.last_turn()
    open_trace(turn)
    expect(turn.get_by_text("Find related pieces", exact=True)).to_be_visible()
    web.shot("fashion-02-what-goes-with-it", full_page=True)


def test_self_correction_of_an_invented_price(web):
    web.open("/ask/fashion")
    web.send("Black trousers under €30")
    # The answer cites its facts; the server renders them, so nothing typed is left to check.
    expect(web.last_turn().get_by_test_id("grounding-badge")).to_have_text(
        re.compile(r"^\d+ facts from the catalog$")
    )
    expect(web.last_turn()).not_to_contain_text(re.compile(r"\{\w+:"))
    web.send("Which is the best deal?")
    expect(web.last_turn().get_by_test_id("grounding-badge")).to_have_text(
        "Self-corrected to match the catalog"
    )
    web.shot("fashion-03-self-correction", full_page=True)


def test_human_in_the_loop_approval_question_and_memory(web):
    web.open("/ask/fashion")
    web.send("White sneakers")
    web.page.get_by_role("button", name=re.compile("^Shortlist the")).first.click()
    drawer = web.page.get_by_test_id("approval-drawer")
    expect(drawer.get_by_role("heading", name="Needs your approval")).to_be_visible(timeout=120_000)
    ticks = drawer.get_by_role("checkbox")
    if ticks.count() > 1:  # narrow the proposal: keep only the first piece
        for index in range(1, ticks.count()):
            ticks.nth(index).uncheck()
    web.shot("fashion-04-approval-proposed")
    drawer.get_by_role("button", name="Approve").click()
    expect(drawer).to_be_hidden(timeout=120_000)
    panel = web.page.get_by_test_id("session-panel")
    expect(panel.get_by_role("heading", name=re.compile(r"Shortlist · 1"))).to_be_visible()
    expect(web.page.get_by_text("Approved.", exact=True)).to_be_visible()

    web.send("I need an outfit")
    question = web.last_turn().get_by_role("button", name="Weekend casual")
    expect(question).to_be_visible()
    options = (
        web.last_turn()
        .get_by_role("group")
        .filter(has=web.page.get_by_role("button", name="Weekend casual"))
    )
    expect(options.get_by_role("button")).to_have_count(3)
    web.shot("fashion-05-clarifying-question")
    web.click_and_answer(question)

    web.send("Remember that my budget is up to €40")
    drawer.get_by_role("button", name="Approve").click()
    expect(drawer).to_be_hidden(timeout=120_000)
    memory = panel.get_by_role("list", name="Remembered for this session")
    expect(memory.get_by_role("button", name=re.compile("Under €40"))).to_be_visible()
    web.send("Blue jeans")
    turn = web.last_turn()
    open_trace(turn)
    expect(turn.get_by_text(re.compile(r"under €40 \(memory: max price\)"))).to_be_visible()
    web.shot("fashion-06-memory-applied", full_page=True)


def test_declined_proposal_changes_nothing(web):
    web.open("/ask/fashion")
    web.send("Red dress")
    web.send("Save the first two")
    drawer = web.page.get_by_test_id("approval-drawer")
    expect(drawer.get_by_role("heading", name="Needs your approval")).to_be_visible()
    drawer.get_by_role("button", name="Decline").click()
    expect(drawer).to_be_hidden(timeout=120_000)
    panel = web.page.get_by_test_id("session-panel")
    expect(panel.get_by_role("heading", name=re.compile(r"Shortlist · 0"))).to_be_visible()
    expect(web.page.get_by_text("nothing was changed", exact=False).last).to_be_visible()
    assert web.chat_bodies[-1]["decision"]["approved"] is False
    assert not (web.session() or {}).get("shortlist")


def test_personal_picks_from_a_linked_history(web):
    web.open("/ask/fashion")
    shopper = web.page.get_by_label("Personalize with a shopping history")
    expect(shopper.locator("option")).not_to_have_count(1)
    shopper.select_option(index=2)
    web.send("Picks from my shopping history")
    turn = web.last_turn()
    assert web.chat_bodies[-1]["shopper_id"] == web.session()["shopperId"]
    open_trace(turn)
    expect(turn.get_by_text(re.compile(r"Ranked \d+ picks from the linked"))).to_be_visible()
    assert turn.get_by_test_id("product-card").count() >= 3
    web.shot("fashion-07-personal-picks", full_page=True)
