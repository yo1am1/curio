"""Capture the curated README screenshots (docs/screenshots/curio-*.webp, rounded corners).

Start the stack on prepared data first: the API serves the built React app (`npm run build`
in web/) on its own port. Chat needs an LLM, and the README captions say when the scripted
stub in tests/e2e/llm_stub.py was used:

    uv run python -m tests.e2e.llm_stub --port 8790
    LLM_PROVIDER=openai LLM_MODEL=stub LLM_BASE_URL=http://127.0.0.1:8790/v1 LLM_API_KEY=x \
        uv run uvicorn curio.app.api:create_app --factory
    uv run python -m scripts.capture_readme --out docs/screenshots

It writes the six screenshots the README shows (the overview animation comes from
scripts.record_overview). `--only agent` refreshes the agent's step trace and the approval
drawer, `--only photo` the photo search, `--only connections` the product connections graph,
`--only pairing` the book → cocktail pairing (books and cocktails prepared) and `--only
evening` Plan my evening (fashion, books and cocktails prepared; no LLM needed).
"""

import argparse
import re
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, expect, sync_playwright

from scripts.readme_images import save_rounded_webp

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--url", default="http://127.0.0.1:8000", help="The API serving the app.")
parser.add_argument("--out", type=Path, default=Path("docs/screenshots"))
parser.add_argument("--executable-path", help="Chromium binary (default: Playwright's).")
parser.add_argument(
    "--only",
    nargs="+",
    choices=["agent", "photo", "connections", "pairing", "evening"],
    help="Capture only these groups (default: all). Books need scripts.prepare_books.",
)
ARGS = parser.parse_args()
URL = ARGS.url.rstrip("/")
OCCASION = "Date night under €100"
OUT = ARGS.out
OUT.mkdir(parents=True, exist_ok=True)
# Nothing loading, no turn streaming, and every photo in view loaded (or given up on).
SETTLED = """() =>
    !document.querySelector('[aria-busy="true"], [data-testid="running-turn"]') &&
    [...document.querySelectorAll('img')]
        .filter(i => {
            const box = i.getBoundingClientRect();
            return i.offsetParent !== null && box.bottom > 0 && box.top < window.innerHeight;
        })
        .every(i => i.complete)"""


def settle(p: Page) -> None:
    p.wait_for_timeout(500)
    p.wait_for_function(SETTLED, timeout=120_000)
    p.wait_for_timeout(500)


def save(p: Page, name: str, width: int = 1440, clip: dict | None = None) -> None:
    p.mouse.move(2, 2)
    p.evaluate("document.activeElement && document.activeElement.blur()")
    settle(p)
    raw = OUT / f"{name}.png"
    p.screenshot(path=raw, clip=clip)
    im = Image.open(raw).convert("RGB")
    raw.unlink()
    im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    save_rounded_webp(im, OUT / name)


def region(p: Page, top, bottom, pad: int = 28) -> dict:
    """A full-page clip from `top`'s top edge to `bottom`'s bottom edge, `pad` around both."""
    scroll = p.evaluate("window.scrollY")
    first, last = top.bounding_box(), bottom.bounding_box()
    y = max(0, first["y"] + scroll - pad)
    return {"x": 0, "y": y, "width": 1440, "height": last["y"] + scroll + last["height"] + pad - y}


def save_region(p: Page, name: str, top, bottom, pad: int = 28) -> None:
    """Like `save`, for a part of the page taller or shorter than the window."""
    p.mouse.move(2, 2)
    p.evaluate("document.activeElement && document.activeElement.blur()")
    settle(p)
    clip = region(p, top, bottom, pad)
    raw = OUT / f"{name}.png"
    p.screenshot(path=raw, clip=clip, full_page=True)
    im = Image.open(raw).convert("RGB")
    raw.unlink()
    im = im.resize((1440, round(im.height * 1440 / im.width)), Image.LANCZOS)
    save_rounded_webp(im, OUT / name)


def scroll_to(p: Page, locator, offset: int = 24) -> None:
    """Scroll the page so `locator` starts `offset` pixels below the sticky header."""
    locator.evaluate(
        """(el, offset) => {
            const header = document.querySelector('header');
            const top = header && getComputedStyle(header).position === 'sticky'
                ? header.getBoundingClientRect().height : 0;
            window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - top - offset);
        }""",
        offset,
    )
    p.wait_for_timeout(400)


def send(p: Page, text: str) -> None:
    turns = p.get_by_test_id("assistant-turn")
    before = turns.count()
    box = p.get_by_label("Message Curio")
    box.fill(text)
    box.press("Enter")
    expect(turns).to_have_count(before + 1, timeout=120_000)
    settle(p)


def desk(b, h: int = 1100) -> Page:
    context = b.new_context(
        viewport={"width": 1440, "height": h}, device_scale_factor=2, reduced_motion="reduce"
    )
    return context.new_page()


def agent(b) -> None:
    """The agent's step trace with its grounding check, then the approval drawer."""
    p = desk(b, 1250)
    p.goto(f"{URL}/ask/fashion")
    send(p, "Black trousers under €30")
    turn = p.get_by_test_id("assistant-turn").last
    turn.get_by_role("button", name=re.compile(r"How Curio worked")).click()
    scroll_to(p, p.get_by_text("Black trousers under €30", exact=True).last)
    save(p, "curio-agent")
    # Approval: propose saving two pieces, untick one.
    turn.get_by_role("button", name=re.compile("^Shortlist the")).first.click()
    drawer = p.get_by_test_id("approval-drawer")
    expect(drawer.get_by_role("heading", name="Needs your approval")).to_be_visible(timeout=120_000)
    drawer.get_by_role("checkbox").last.uncheck()
    p.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    save(p, "curio-approval")


def photo(b) -> None:
    """Photo search with an example garment."""
    p = desk(b, 1250)
    p.goto(f"{URL}/fashion/discover")
    p.get_by_role("radio", name="Photo").click()
    examples = p.get_by_role("list", name="Example photos").get_by_role("button")
    examples.nth(2).click()
    found = p.get_by_role("region", name="Visually similar pieces")
    expect(found).to_have_attribute("aria-busy", "false", timeout=120_000)
    examples.nth(2).scroll_into_view_if_needed()
    p.evaluate("window.scrollTo(0, 0)")
    save_region(p, "curio-discover-photo", p.get_by_role("radio", name="Photo"), found)


def connections(b) -> None:
    """Product connections: a piece's co-purchase and similarity graph, one neighbour lit."""
    p = desk(b, 1150)
    p.goto(f"{URL}/fashion/connections")
    nodes = p.get_by_test_id("graph-node")
    expect(nodes.nth(3)).to_be_visible(timeout=120_000)
    settle(p)
    top = p.get_by_role("heading", name="Explore the connections")
    bottom = p.get_by_role("button", name=re.compile(r"Relationships · \d+"))
    save_region(p, "curio-connections", top, bottom)


def pairing(b) -> None:
    """Book → cocktail pairing in the book dialog: The Great Gatsby's drinks and reasons."""
    p = desk(b, 1250)
    p.goto(f"{URL}/reading")
    p.get_by_label("Describe a book").fill("The Great Gatsby")
    p.get_by_role("button", name="Find books").click()
    found = p.get_by_role("region", name="Matching books")
    expect(found).to_have_attribute("aria-busy", "false", timeout=120_000)
    found.get_by_role("button", name="View The Great Gatsby", exact=True).first.click()
    dialog = p.get_by_role("dialog")
    dialog.get_by_role("button", name="Pair a drink").click()
    drinks = dialog.get_by_test_id("pairing-drink")
    expect(drinks).to_have_count(3, timeout=120_000)
    settle(p)
    dialog.get_by_role("button", name="Another pairing").evaluate(
        "el => el.scrollIntoView({block: 'start'})"
    )
    p.wait_for_timeout(300)
    drinks.last.evaluate("el => el.scrollIntoView({block: 'end'})")
    save(p, "curio-pairing")


def evening(b) -> None:
    """Plan my evening, whole page: the occasion, its theme, why it works and the three cards."""
    p = desk(b, 1400)
    p.goto(f"{URL}/evening")
    box = p.get_by_label("Occasion", exact=True)
    box.fill(OCCASION)
    box.press("Enter")
    expect(p.get_by_test_id("evening-plan")).to_be_visible(timeout=120_000)
    settle(p)
    top = p.get_by_role("group", name="Occasions")
    save_region(p, "curio-evening", top, p.get_by_role("button", name="Save evening"))


GROUPS = {
    "agent": agent,
    "photo": photo,
    "connections": connections,
    "pairing": pairing,
    "evening": evening,
}

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=ARGS.executable_path)
    for name in ARGS.only or GROUPS:
        GROUPS[name](browser)
    browser.close()
print(f"Wrote README screenshots to {OUT}")
