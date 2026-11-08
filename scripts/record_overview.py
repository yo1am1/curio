"""Record the README overview animation (docs/screenshots/curio-overview.webp) from a stack.

Start the stack as in scripts/capture_readme.py (the scripted LLM stub and the API serving
the built React app), then run:

    uv run python -m scripts.record_overview --out docs/screenshots/curio-overview.webp

The tour opens on Plan my evening (a date night under €100: three cards, why it works and
one swap), then asks the fashion agent and opens its step trace with the grounding check,
narrows and approves a save in the approval drawer, pairs The Great Gatsby with a drink and
ends on the same dialog in dark mode. Chrome's screencast (over CDP) records the frames, and
ffmpeg turns them into an animated WebP with transparent rounded corners (like every README
image), keeping each frame's real duration. GIF would need a 1-bit alpha that defeats its
frame-diff compression. Each scene shows a caption so the animation reads without sound or
narration.
"""

import argparse
import base64
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

from scripts.readme_images import corner_mask

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--url", default="http://127.0.0.1:8000", help="The API serving the app.")
parser.add_argument("--out", type=Path, default=Path("docs/screenshots/curio-overview.webp"))
parser.add_argument("--executable-path", help="Chromium binary (default: Playwright's).")
parser.add_argument("--width", type=int, default=960, help="Animation width in pixels.")
parser.add_argument("--fps", type=int, default=10)
ARGS = parser.parse_args()
URL = ARGS.url.rstrip("/")
OCCASION = "Date night under €100"
QUESTION = "Black trousers under €30"
VIEWPORT = {"width": 1280, "height": 800}
MAX_FRAME_SECONDS = 4.0
# Nothing loading, no turn streaming, and every photo in view loaded (or given up on).
SETTLED = """() =>
    !document.querySelector('[aria-busy="true"], [data-testid="running-turn"]') &&
    [...document.querySelectorAll('img')]
        .filter(i => {
            const box = i.getBoundingClientRect();
            return i.offsetParent !== null && box.bottom > 0 && box.top < window.innerHeight;
        })
        .every(i => i.complete)"""
CAPTION = """text => {
    let el = document.getElementById('curio-tour-caption');
    if (!el) {
        el = document.createElement('div');
        el.id = 'curio-tour-caption';
        el.style.cssText = `position:fixed;left:50%;bottom:22px;transform:translateX(-50%);
            z-index:2147483647;padding:10px 18px;border-radius:999px;background:rgba(32,28,26,.88);
            color:#fff;font:600 17px/1.3 system-ui,sans-serif;letter-spacing:.01em;
            box-shadow:0 6px 24px rgba(0,0,0,.25);pointer-events:none;white-space:nowrap`;
        document.body.appendChild(el);
    }
    el.textContent = text;
}"""


def settle(p: Page) -> None:
    p.wait_for_timeout(300)
    p.wait_for_function(SETTLED, timeout=120_000)
    p.wait_for_timeout(300)


def caption(p: Page, text: str) -> None:
    p.evaluate(CAPTION, text)


def glide_to(p: Page, locator, offset: int = 24) -> None:
    """Scroll the page smoothly so `locator` starts `offset` pixels from the top."""
    locator.evaluate(
        """(el, offset) => window.scrollTo({
            top: el.getBoundingClientRect().top + window.scrollY - offset,
            behavior: 'smooth'})""",
        offset,
    )
    p.wait_for_timeout(900)


def ask(p: Page, text: str, *, typed: bool = True) -> None:
    turns = p.get_by_test_id("assistant-turn")
    before = turns.count()
    box = p.get_by_label("Message Curio")
    box.click()
    if typed:
        box.press_sequentially(text, delay=28)
        p.wait_for_timeout(300)
    else:
        box.fill(text)
    box.press("Enter")
    expect(turns).to_have_count(before + 1, timeout=120_000)
    settle(p)


def plan(p: Page, *, typed: bool = True) -> None:
    box = p.get_by_label("Occasion", exact=True)
    box.click()
    if typed:
        box.press_sequentially(OCCASION, delay=30)
    else:
        box.fill(OCCASION)
    box.press("Enter")
    expect(p.get_by_test_id("evening-plan")).to_be_visible(timeout=120_000)
    settle(p)


def pages_of(p: Page, domain: str):
    return p.get_by_role("navigation", name=f"{domain} pages")


def switch_to(p: Page, domain: str) -> None:
    """Choose a domain in the header switch (it opens on the domain's main page)."""
    p.get_by_role("navigation", name="Catalog").get_by_role("link", name=domain).click()
    settle(p)


def tour(p: Page) -> None:
    # 1. Plan my evening: three domains composed into one plan (a visit opens here).
    caption(p, "Plan my evening: one occasion, three domains composed")
    p.wait_for_timeout(1000)
    plan(p)
    caption(p, "An outfit, a book and a drink paired with that book, each with its reason")
    glide_to(p, p.get_by_test_id("evening-plan"))
    p.wait_for_timeout(2700)
    caption(p, "Swap any part: another drink, paired with the same book")
    drink = p.get_by_test_id("evening-drink")
    drink.get_by_role("button", name="Another drink").click()
    settle(p)
    p.wait_for_timeout(2200)

    # 2. The agent: plan, tools, a verified answer, every step shown.
    switch_to(p, "Fashion")
    pages_of(p, "Fashion").get_by_role("link", name="Ask Curio").click()
    settle(p)
    caption(p, "Ask in plain words: the agent plans, calls catalog tools and verifies")
    p.wait_for_timeout(800)
    ask(p, QUESTION)
    turn = p.get_by_test_id("assistant-turn").last
    turn.get_by_role("button", name=re.compile(r"How Curio worked")).click()
    p.wait_for_timeout(300)
    glide_to(p, p.get_by_text(QUESTION, exact=True).last)
    caption(p, "Every step is shown; catalog facts are rendered from the catalog")
    p.wait_for_timeout(3300)

    # 3. Approval: saving is only proposed; untick a piece, then approve.
    turn.get_by_role("button", name=re.compile("^Shortlist the")).first.click()
    drawer = p.get_by_test_id("approval-drawer")
    expect(drawer.get_by_role("heading", name="Needs your approval")).to_be_visible(timeout=120_000)
    settle(p)
    caption(p, "Nothing is saved until you approve; untick what you don't want")
    p.wait_for_timeout(1300)
    drawer.get_by_role("checkbox").last.uncheck()
    p.wait_for_timeout(1100)
    drawer.get_by_role("button", name="Approve").click()
    expect(drawer).to_be_hidden(timeout=120_000)
    settle(p)
    caption(p, "Approved: one piece saved, the other left out")
    p.wait_for_timeout(1800)

    # 4. Book → drink pairing.
    switch_to(p, "Books")
    caption(p, "Books: collaborative filtering on 6M ratings")
    p.get_by_label("Describe a book").press_sequentially("The Great Gatsby", delay=25)
    p.get_by_role("button", name="Find books").click()
    found = p.get_by_role("region", name="Matching books")
    expect(found).to_have_attribute("aria-busy", "false", timeout=120_000)
    settle(p)
    p.wait_for_timeout(300)
    found.get_by_role("button", name="View The Great Gatsby", exact=True).first.click()
    dialog = p.get_by_role("dialog")
    settle(p)
    caption(p, "Book → drink: Tom Buchanan's gin rickeys, with the evidence")
    dialog.get_by_role("button", name="Pair a drink").click()
    drinks = dialog.get_by_test_id("pairing-drink")
    expect(drinks).to_have_count(3, timeout=120_000)
    settle(p)
    drinks.last.evaluate("el => el.scrollIntoView({block: 'end', behavior: 'smooth'})")
    p.wait_for_timeout(3000)

    # 5. A glimpse of dark mode, which follows the system.
    p.emulate_media(color_scheme="dark")
    caption(p, "Light and dark themes follow your system")
    p.wait_for_timeout(2600)


def to_webp(frames: list[tuple[Path, float]], stop: float, out: Path, workdir: Path) -> None:
    """Assemble timestamped frames into an animated WebP with rounded transparent corners."""
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["ffconcat version 1.0"]
    # The screencast sends no frames while the page is still, so the last frame lasts until
    # the tour stopped.
    ends = [stamp for _, stamp in frames[1:]] + [max(stop, frames[-1][1] + 1.5)]
    for (path, stamp), following in zip(frames, ends, strict=True):
        # Cap each frame so a slow step never stalls the tour.
        duration = min(max(following - stamp, 0.02), MAX_FRAME_SECONDS)
        lines += [f"file '{path.name}'", f"duration {duration:.3f}"]
    lines.append(f"file '{frames[-1][0].name}'")
    playlist = workdir / "frames.ffconcat"
    playlist.write_text("\n".join(lines) + "\n")
    height = round(VIEWPORT["height"] * ARGS.width / VIEWPORT["width"])
    mask = workdir / "mask.png"
    corner_mask((ARGS.width, height)).save(mask)
    graph = (
        f"[0:v]fps={ARGS.fps},scale={ARGS.width}:{height}:flags=lanczos,format=rgba[v];"
        "[1:v]format=gray[m];[v][m]alphamerge=shortest=1"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(playlist),
            "-loop",
            "1",
            "-i",
            str(mask),
            "-filter_complex",
            graph,
            "-c:v",
            "libwebp_anim",
            "-quality",
            "78",
            "-compression_level",
            "6",
            "-loop",
            "0",
            str(out),
        ],
        check=True,
    )


def main() -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg (with libwebp) is required to build the animation")
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        workdir = Path(tmp)
        frames: list[tuple[Path, float]] = []
        browser = pw.chromium.launch(executable_path=ARGS.executable_path)
        # Warm the models and caches first, so the recording shows answers, not cold starts.
        warm = browser.new_context(viewport=VIEWPORT).new_page()
        warm.goto(f"{URL}/evening")
        plan(warm, typed=False)
        warm.goto(f"{URL}/ask/fashion")
        ask(warm, QUESTION, typed=False)
        warm.context.close()
        page = browser.new_context(viewport=VIEWPORT, color_scheme="light").new_page()
        # Open the app before recording, so the first frame is Plan my evening, not a skeleton.
        page.goto(f"{URL}/")
        expect(page.get_by_role("heading", name="Plan my evening", level=1)).to_be_visible()
        settle(page)
        page.wait_for_timeout(800)  # the page's entrance animation
        page.evaluate("window.scrollTo(0, 0)")
        cdp = page.context.new_cdp_session(page)

        def on_frame(event: dict) -> None:
            path = workdir / f"frame{len(frames):05d}.jpg"
            path.write_bytes(base64.b64decode(event["data"]))
            frames.append((path, event["metadata"]["timestamp"]))
            cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

        cdp.on("Page.screencastFrame", on_frame)
        cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 90,
                "maxWidth": VIEWPORT["width"],
                "maxHeight": VIEWPORT["height"],
                "everyNthFrame": 1,
            },
        )
        try:
            tour(page)
            stop = time.time()  # screencast timestamps are wall-clock seconds
        finally:
            cdp.send("Page.stopScreencast")
            browser.close()
        if not frames:
            raise SystemExit("No frames were recorded")
        to_webp(frames, stop, ARGS.out, workdir)
    size = ARGS.out.stat().st_size / 1_000_000
    print(f"Wrote {ARGS.out} ({size:.1f} MB, {len(frames)} frames)")


main()
