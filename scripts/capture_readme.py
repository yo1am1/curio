"""Capture the curated README screenshots (docs/screenshots/curio-*.jpg).

Start the stack on prepared data first; chat needs an LLM, and the README captions say when
the scripted stub in tests/e2e/llm_stub.py was used:

    uv run python -m tests.e2e.llm_stub --port 8790
    LLM_PROVIDER=openai LLM_MODEL=stub LLM_BASE_URL=http://127.0.0.1:8790/v1 LLM_API_KEY=x \
        uv run uvicorn clothes_recommender.api:create_app --factory
    uv run streamlit run src/clothes_recommender/ui.py
    uv run python -m scripts.capture_readme --out docs/screenshots

Example customer 3 is used because all of its default picks have photos in the partial index.
"""

import argparse
import re
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--ui-url", default="http://127.0.0.1:8501")
parser.add_argument("--out", type=Path, default=Path("docs/screenshots"))
parser.add_argument("--executable-path", help="Chromium binary (default: Playwright's).")
ARGS = parser.parse_args()
UI = ARGS.ui_url
OUT = ARGS.out
OUT.mkdir(parents=True, exist_ok=True)
DONE = """() =>
    !document.querySelector('[data-stale="true"]') &&
    !document.querySelector('[data-testid="stStatusWidgetRunningIcon"]')"""
PHOTOS = """() =>
    [...document.querySelectorAll('[data-testid="stImage"] img')]
        .filter(i => i.offsetParent !== null)
        .every(i => i.complete && i.naturalWidth > 0)"""


def settle(p):
    p.wait_for_timeout(700)
    p.wait_for_function(DONE, timeout=120000)
    p.wait_for_function(PHOTOS, timeout=120000)
    p.wait_for_timeout(700)


def save(p, name, width=1440, clip=None):
    p.mouse.move(2, 2)
    p.evaluate("document.activeElement && document.activeElement.blur()")
    p.wait_for_timeout(400)
    raw = OUT / f"{name}.png"
    p.screenshot(path=raw, clip=clip)
    im = Image.open(raw).convert("RGB")
    raw.unlink()
    im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(OUT / f"{name}.jpg", quality=84, optimize=True, progressive=True)


def show_last_answer(p):
    """Scroll the chat panel so the last assistant answer starts at its top."""
    p.evaluate("""() => {
        const log = document.querySelector('[class*="st-key-chat_log_home"]');
        const scroller = [log, ...log.querySelectorAll('*')].find(
            el => el.scrollHeight > el.clientHeight + 4
                && getComputedStyle(el).overflowY !== 'visible');
        const msgs = log.querySelectorAll('[data-testid="stChatMessage"]');
        const last = msgs[msgs.length - 1];
        const offset = last.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
        scroller.scrollTop += offset - 8;
    }""")
    p.wait_for_timeout(500)


def send(p, text):
    box = p.get_by_test_id("stChatInputTextArea").first
    box.fill(text)
    box.press("Enter")
    settle(p)


def pick_example(p, index):
    p.get_by_role("button", name="Shopping history", exact=True).click()
    body = p.get_by_test_id("stPopoverBody")
    body.locator('[data-testid="stSelectbox"]:visible').first.click()
    p.get_by_role("option").nth(index).click()
    settle(p)
    p.keyboard.press("Escape")
    settle(p)


def scroll_main(p, y):
    p.evaluate(
        """y => {
            const scrollers = document.querySelectorAll('[data-testid="stMain"], section');
            for (const el of [document.scrollingElement, ...scrollers]) if (el) el.scrollTop = y;
        }""",
        y,
    )
    p.wait_for_timeout(500)


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=ARGS.executable_path)

    def desk(h=1100):
        return b.new_context(
            viewport={"width": 1440, "height": h}, device_scale_factor=2
        ).new_page()

    # Feed: default personalized ranking for a fully photographed example.
    p = desk()
    p.goto(f"{UI}/fashion")
    settle(p)
    pick_example(p, 2)
    save(p, "curio-fashion")

    # Agent: search with trace, grounding badge and cards.
    p = desk(1250)
    p.goto(UI)
    settle(p)
    send(p, "Black trousers under €30")
    show_last_answer(p)
    save(p, "curio-agent")
    # Approval: propose saving two pieces, untick one.
    p.locator('[class*="st-key-chat_suggestion_home_1_"]').filter(
        has_text="Shortlist the"
    ).first.click()
    settle(p)
    card = p.locator('[class*="st-key-approval_home_"]').last
    card.get_by_role("checkbox").last.click(force=True)
    settle(p)
    save(p, "curio-approval")
    card.get_by_role("button", name="Approve").click()
    settle(p)
    send(p, "Remember that my budget is up to €40")
    p.locator('[class*="st-key-approval_home_"]').last.get_by_role("button", name="Approve").click()
    settle(p)
    send(p, "Blue jeans")
    p.get_by_text(re.compile(r"How Curio worked")).last.click()
    settle(p)
    save(p, "curio-memory")

    # Home (empty) desktop.
    p = desk()
    p.goto(UI)
    settle(p)
    save(p, "curio-home")

    # Discover by description and photo.
    p = desk(1250)
    p.goto(f"{UI}/discover")
    settle(p)
    p.locator(".st-key-text_example_0 button").click()
    settle(p)
    scroll_main(p, 360)
    save(p, "curio-discover-text")
    p.get_by_text("Photo", exact=True).click()
    settle(p)
    p.get_by_text("Or pick an example photo").click()
    settle(p)
    p.locator('[class*="st-key-query_photo_"] button').nth(2).click()
    settle(p)
    p.get_by_role("heading", name="Visually similar pieces").scroll_into_view_if_needed()
    p.wait_for_timeout(500)
    scroll_main(p, 0)
    res = p.locator(".st-key-photo_search_results").bounding_box()
    scroll_main(p, max(0, res["y"] - 140))
    settle(p)
    save(p, "curio-discover-photo")

    # Product dialog with bought-together pieces, opened from the example feed.
    p = desk()
    p.goto(f"{UI}/fashion")
    settle(p)
    pick_example(p, 2)
    p.get_by_role("button", name=re.compile("^View ")).first.click()
    settle(p)
    dialog = p.get_by_role("dialog")
    dialog.get_by_text("Bought together", exact=True).click()
    settle(p)
    if dialog.get_by_text("No related pieces").count():
        dialog.get_by_text("Similar styles", exact=True).click()
        settle(p)
    save(p, "curio-product-detail")

    # Product connections.
    p = desk(1500)
    p.goto(f"{UI}/connections")
    settle(p)
    pick_example(p, 2)
    p.locator("canvas, svg.marks").first.wait_for()
    p.wait_for_timeout(1500)
    save(p, "curio-connections")

    # Cocktail agent with remembered alcohol-free taste.
    p = desk(1250)
    p.goto(UI)
    settle(p)
    p.get_by_test_id("stButtonGroup").first.get_by_text("Cocktails", exact=True).click()
    settle(p)
    send(p, "Remember I don't drink alcohol")
    p.locator('[class*="st-key-approval_home_"]').last.get_by_role("button", name="Approve").click()
    settle(p)
    send(p, "Something fruity")
    p.get_by_text(re.compile(r"How Curio worked")).last.click()
    settle(p)
    save(p, "curio-cocktail-agent")

    # Cocktails page: search, pantry.
    p = desk(1250)
    p.goto(f"{UI}/cocktails")
    settle(p)
    p.get_by_label("Describe a drink").fill("Something fresh with lime and mint")
    p.get_by_role("button", name="Find drinks").click()
    settle(p)
    scroll_main(p, 330)
    save(p, "curio-cocktails")
    p.get_by_text("What can I make?", exact=True).first.click()
    settle(p)
    picker = p.get_by_label("Ingredients you have")
    for ing in ("Gin", "Tonic water", "Lime"):
        picker.click()
        p.keyboard.type(ing)
        p.get_by_role("option", name=re.compile(f"^{ing}$", re.I)).first.click()
        settle(p)
    p.keyboard.press("Escape")
    settle(p)
    scroll_main(p, 330)
    save(p, "curio-cocktails-pantry")

    # Phone: home and feed.
    m = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2).new_page()
    m.goto(UI)
    settle(m)
    save(m, "curio-home-mobile", width=780)
    m.goto(f"{UI}/fashion")
    settle(m)
    pick_example(m, 2)
    scroll_main(m, 0)
    save(m, "curio-mobile", width=780)
    b.close()
print(f"Wrote README screenshots to {OUT}")
