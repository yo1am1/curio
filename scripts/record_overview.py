"""Record the README overview animation (docs/screenshots/curio-overview.webp) from a stack.

Start the stack as in scripts/capture_readme.py (the scripted LLM stub, the API and the UI),
then run:

    uv run python -m scripts.record_overview --out docs/screenshots/curio-overview.webp

Chrome's screencast (over CDP) records the frames of a short tour, and ffmpeg turns them into
an animated WebP with transparent rounded corners (like every README image), keeping each
frame's real duration. GIF would need a 1-bit alpha that defeats its frame-diff compression.
Each scene shows a caption so the animation reads without sound or narration.
"""

import argparse
import base64
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from scripts.readme_images import corner_mask

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--ui-url", default="http://127.0.0.1:8501")
parser.add_argument("--out", type=Path, default=Path("docs/screenshots/curio-overview.webp"))
parser.add_argument("--executable-path", help="Chromium binary (default: Playwright's).")
parser.add_argument("--width", type=int, default=960, help="Animation width in pixels.")
parser.add_argument("--fps", type=int, default=10)
ARGS = parser.parse_args()
UI = ARGS.ui_url
VIEWPORT = {"width": 1280, "height": 800}
MAX_FRAME_SECONDS = 4.0
DONE = """() =>
    !document.querySelector('[data-stale="true"]') &&
    !document.querySelector('[data-testid="stStatusWidgetRunningIcon"]')"""
PHOTOS = """() =>
    [...document.querySelectorAll('[data-testid="stImage"] img')]
        .filter(i => i.offsetParent !== null)
        .every(i => i.complete && i.naturalWidth > 0)"""
CAPTION = """text => {
    let el = document.getElementById('curio-tour-caption');
    if (!el) {
        el = document.createElement('div');
        el.id = 'curio-tour-caption';
        el.style.cssText = `position:fixed;left:50%;bottom:22px;transform:translateX(-50%);
            z-index:100000;padding:10px 18px;border-radius:999px;background:rgba(32,28,26,.88);
            color:#fff;font:600 17px/1.3 system-ui,sans-serif;letter-spacing:.01em;
            box-shadow:0 6px 24px rgba(0,0,0,.25);pointer-events:none;white-space:nowrap`;
        document.body.appendChild(el);
    }
    el.textContent = text;
}"""


def settle(p) -> None:
    p.wait_for_timeout(400)
    p.wait_for_function(DONE, timeout=120000)
    p.wait_for_function(PHOTOS, timeout=120000)
    p.wait_for_timeout(300)


def caption(p, text: str) -> None:
    p.evaluate(CAPTION, text)


def ask(p, text: str) -> None:
    box = p.get_by_test_id("stChatInputTextArea").first
    box.click()
    box.press_sequentially(text, delay=35)
    p.wait_for_timeout(300)
    box.press("Enter")
    settle(p)


def scroll_chat_to_last(p) -> None:
    p.evaluate("""() => {
        const log = document.querySelector('[class*="st-key-chat_log_home"]');
        if (!log) return;
        const scroller = [log, ...log.querySelectorAll('*')].find(
            el => el.scrollHeight > el.clientHeight + 4
                && getComputedStyle(el).overflowY !== 'visible');
        const msgs = log.querySelectorAll('[data-testid="stChatMessage"]');
        const last = msgs[msgs.length - 1];
        if (!scroller || !last) return;
        const offset = last.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
        scroller.scrollTo({top: scroller.scrollTop + offset - 8, behavior: 'smooth'});
    }""")
    p.wait_for_timeout(900)


def tour(p) -> None:
    # 1. Fashion agent: plan, tools, verified answer (main() has loaded the home page).
    caption(p, "Ask in plain words: the agent plans, calls catalog tools and verifies")
    p.wait_for_timeout(1200)
    ask(p, "Black trousers under €30")
    caption(p, "Every answer shows its steps; prices and IDs are checked against the tools")
    p.get_by_text(re.compile(r"How Curio worked")).last.click()
    settle(p)
    scroll_chat_to_last(p)
    p.wait_for_timeout(2600)

    # 2. Outfit builder under one budget.
    caption(p, "A whole outfit under one budget, with a reason for every piece")
    ask(p, "Build me a summer outfit for a wedding under €80")
    p.locator('[class*="st-key-outfit_home_"]').last.evaluate(
        "el => el.scrollIntoView({block: 'start', behavior: 'smooth'})"
    )
    p.wait_for_timeout(3200)

    # 3. Books, then a cocktail to read with.
    p.goto(f"{UI}/books")
    settle(p)
    caption(p, "Books: 10,000 titles, collaborative filtering on 6M ratings")
    p.get_by_label("Describe a book").press_sequentially("The Great Gatsby", delay=35)
    p.get_by_role("button", name="Find books").click()
    settle(p)
    p.wait_for_timeout(800)
    p.locator('[class*="st-key-book_card_search_"]').first.get_by_role(
        "button", name="View The Great Gatsby"
    ).click()
    settle(p)
    p.wait_for_timeout(900)
    caption(p, "Cross-domain: pour a drink to read with, and say why")
    dialog = p.get_by_role("dialog")
    dialog.get_by_role("button", name="Pair a drink").click()
    settle(p)
    dialog.locator(".st-key-book_pairing").evaluate(
        "el => el.scrollIntoView({block: 'end', behavior: 'smooth'})"
    )
    p.wait_for_timeout(3600)

    # 4. Cocktails: what can I make?
    p.goto(f"{UI}/cocktails")
    settle(p)
    caption(p, "Cocktails: what can I make with what I have?")
    p.get_by_text("What can I make?", exact=True).first.click()
    settle(p)
    picker = p.get_by_label("Ingredients you have")
    for ingredient in ("Gin", "Tonic water", "Lime"):
        picker.click()
        p.keyboard.type(ingredient, delay=35)
        p.get_by_role("option", name=re.compile(f"^{ingredient}$", re.I)).first.click()
        settle(p)
    p.keyboard.press("Escape")
    settle(p)
    p.mouse.wheel(0, 380)
    p.wait_for_timeout(3000)


def to_webp(frames: list[tuple[Path, float]], out: Path, workdir: Path) -> None:
    """Assemble timestamped frames into an animated WebP with rounded transparent corners."""
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["ffconcat version 1.0"]
    ends = [stamp for _, stamp in frames[1:]] + [frames[-1][1] + 1.5]
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
        warm.goto(UI)
        settle(warm)
        ask(warm, "Black trousers under €30")
        warm.context.close()
        page = browser.new_context(viewport=VIEWPORT).new_page()
        # Open the home page before recording, so the first frame is the app, not a skeleton.
        page.goto(UI)
        settle(page)
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
        finally:
            cdp.send("Page.stopScreencast")
            browser.close()
        if not frames:
            raise SystemExit("No frames were recorded")
        to_webp(frames, ARGS.out, workdir)
    size = ARGS.out.stat().st_size / 1_000_000
    print(f"Wrote {ARGS.out} ({size:.1f} MB, {len(frames)} frames)")


main()
