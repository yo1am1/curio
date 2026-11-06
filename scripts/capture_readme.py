"""Capture the curated README screenshots (docs/screenshots/curio-*.webp, rounded corners).

Start the stack on prepared data first; chat needs an LLM, and the README captions say when
the scripted stub in tests/e2e/llm_stub.py was used:

    uv run python -m tests.e2e.llm_stub --port 8790
    LLM_PROVIDER=openai LLM_MODEL=stub LLM_BASE_URL=http://127.0.0.1:8790/v1 LLM_API_KEY=x \
        uv run uvicorn curio.app.api:create_app --factory
    uv run streamlit run src/curio/app/ui/main.py
    uv run python -m scripts.capture_readme --out docs/screenshots

It writes the five screenshots the README shows (the overview animation comes from
scripts.record_overview). `--only agent` refreshes the agent's step trace and the approval
card, `--only photo` the photo search, `--only pairing` the book → cocktail pairing (books
and cocktails prepared) and `--only evening` Plan my evening (fashion, books and cocktails
prepared; no LLM needed).
"""

import argparse
import re
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from scripts.readme_images import save_rounded_webp

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--ui-url", default="http://127.0.0.1:8501")
parser.add_argument("--out", type=Path, default=Path("docs/screenshots"))
parser.add_argument("--executable-path", help="Chromium binary (default: Playwright's).")
parser.add_argument(
    "--only",
    nargs="+",
    choices=["agent", "photo", "pairing", "evening"],
    help="Capture only these groups (default: all). Books need scripts.prepare_books.",
)
ARGS = parser.parse_args()
UI = ARGS.ui_url
OCCASION = "Date night under €100"
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
    save_rounded_webp(im, OUT / name)


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


def scroll_main(p, y):
    p.evaluate(
        """y => {
            const scrollers = document.querySelectorAll('[data-testid="stMain"], section');
            for (const el of [document.scrollingElement, ...scrollers]) if (el) el.scrollTop = y;
        }""",
        y,
    )
    p.wait_for_timeout(500)


def desk(b, h=1100):
    return b.new_context(viewport={"width": 1440, "height": h}, device_scale_factor=2).new_page()


def agent(b) -> None:
    """The agent's step trace with its grounding check, then the approval card."""
    p = desk(b, 1250)
    p.goto(UI)
    settle(p)
    # A visit opens on Plan my evening; choosing Fashion lands on its chat (Ask Curio).
    p.get_by_test_id("stButtonGroup").first.get_by_text("Fashion", exact=True).click()
    settle(p)
    send(p, "Black trousers under €30")
    p.get_by_text(re.compile(r"How Curio worked")).last.click()
    settle(p)
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
    card.evaluate("el => el.scrollIntoView({block: 'center'})")
    p.wait_for_timeout(500)
    save(p, "curio-approval")


def photo(b) -> None:
    """Photo search with an example garment."""
    p = desk(b, 1250)
    p.goto(f"{UI}/discover")
    settle(p)
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


def pairing(b) -> None:
    """Book → cocktail pairing in the book dialog: The Great Gatsby's drinks and reasons."""
    p = desk(b, 1350)
    p.goto(f"{UI}/books")
    settle(p)
    p.get_by_label("Describe a book").fill("The Great Gatsby")
    p.get_by_role("button", name="Find books").click()
    settle(p)
    p.locator('[class*="st-key-book_card_search_"]').first.get_by_role(
        "button", name="View The Great Gatsby"
    ).click()
    settle(p)
    dialog = p.get_by_role("dialog")
    dialog.get_by_role("button", name="Pair a drink").click()
    settle(p)
    dialog.locator(".st-key-book_pairing").evaluate("el => el.scrollIntoView({block: 'end'})")
    p.wait_for_timeout(500)
    save(p, "curio-pairing")


def evening(b) -> None:
    """Plan my evening, whole page: the occasion, its theme, why it works and the three cards."""
    p = desk(b, 1450)
    p.goto(f"{UI}/evening")
    settle(p)
    box = p.locator(".st-key-evening_occasion input")
    box.fill(OCCASION)
    box.press("Enter")
    settle(p)
    p.locator(".st-key-evening_plan_button button").click()
    settle(p)
    save(p, "curio-evening")


GROUPS = {"agent": agent, "photo": photo, "pairing": pairing, "evening": evening}

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=ARGS.executable_path)
    for name in ARGS.only or GROUPS:
        GROUPS[name](browser)
    browser.close()
print(f"Wrote README screenshots to {OUT}")
