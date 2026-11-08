"""End-to-end fixtures: the real API serving the React app, a browser and a scripted LLM.

The suite runs only on request:

    uv run pytest -m e2e tests/e2e

It needs prepared data. With the cocktails and the books (`scripts.prepare_cocktails`,
`scripts.prepare_books`; no H&M download), the API serves cocktails, books and Plan my
evening, and every test runs except those marked `fashion`, which skip. With the H&M sample,
its photos and the Qdrant image index as well, the API serves every domain and the whole
suite runs. CI runs the first setup (`.github/workflows/ci.yml`, job `e2e-demo`) with
E2E_REQUIRE_DATA=1, which turns a missing cocktail or book file into an error instead of
skipped tests.

The API serves the built app (`web/dist`, see `curio.app.web`) on its own port, so every
browser test opens the API's URL. The build is made with `npm run build` when `web/dist` is
missing and `web/node_modules` exists; otherwise the browser tests skip.

Environment: E2E_DATA_DIR (default `data`), E2E_QDRANT_PATH (default `.qdrant`),
E2E_ARTIFACTS (screenshot folder, default `e2e-artifacts`), E2E_CHROMIUM (browser binary),
E2E_PORTS (a port range such as `8100-8199` for the servers; default: any free port).
Embedded Qdrant allows one process, so stop any other API using the same index first.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

from tests.e2e.llm_stub import serve

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("E2E_DATA_DIR", ROOT / "data"))
QDRANT_PATH = Path(os.environ.get("E2E_QDRANT_PATH", ROOT / ".qdrant"))
ARTIFACTS = Path(os.environ.get("E2E_ARTIFACTS", ROOT / "e2e-artifacts"))
WEB = ROOT / "web"
DIST = WEB / "dist"


DEMO_FILES = ("cocktails.parquet", "books.parquet", "book_neighbours.parquet")


def demo_ready() -> bool:
    """The cocktails and books are prepared (the demo's data, no H&M download)."""
    return all((DATA_DIR / name).exists() for name in DEMO_FILES)


def fashion_ready() -> bool:
    """The H&M sample and its Qdrant image index are prepared."""
    return (DATA_DIR / "products.parquet").exists() and QDRANT_PATH.exists()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    e2e = [i for i in items if "tests/e2e/" in str(i.fspath).replace(os.sep, "/")]
    for item in e2e:
        item.add_marker(pytest.mark.e2e)
        if item.get_closest_marker("fashion") and not fashion_ready():
            item.add_marker(pytest.mark.skip(reason="Needs the H&M data and its image index"))
    selected = [i for i in e2e if "e2e" in (config.getoption("markexpr") or "")]
    if selected and os.environ.get("E2E_REQUIRE_DATA") == "1" and not demo_ready():
        missing = [name for name in DEMO_FILES if not (DATA_DIR / name).exists()]
        raise pytest.UsageError(f"E2E_REQUIRE_DATA=1 but {DATA_DIR} lacks {', '.join(missing)}")


_TAKEN: set[int] = set()


def _free_port() -> int:
    """A free local port: from E2E_PORTS ("8100-8199") when set, else any the OS gives.
    A port handed out once is not handed out again in this session."""
    first, _, last = os.environ.get("E2E_PORTS", "").partition("-")
    candidates = range(int(first), int(last or first) + 1) if first else [0] * 20
    for candidate in candidates:
        if candidate in _TAKEN:
            continue
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            port = probe.getsockname()[1]
        if port not in _TAKEN:
            _TAKEN.add(port)
            return port
    raise RuntimeError(f"No free port in E2E_PORTS={os.environ.get('E2E_PORTS')}")


def _wait(url: str, timeout: float, process: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{url} exited early:\n{log.read_text()[-3000:]}")
        try:
            if httpx.get(url, timeout=2, trust_env=False).is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise TimeoutError(f"{url} did not start; see {log}")


@pytest.fixture(scope="session")
def artifacts() -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    return ARTIFACTS


@pytest.fixture(scope="session")
def llm_stub() -> Iterator[str]:
    port = _free_port()
    server = serve(port)
    yield f"http://127.0.0.1:{port}/v1"
    server.shutdown()


@contextmanager
def _api(env: dict[str, str], log: Path, cwd: Path = ROOT) -> Iterator[str]:
    """The real API (`uvicorn ... --factory`) on a free port, configured by `env` (and by a
    `.env` file in `cwd`, as in production)."""
    port = _free_port()
    env = {
        **os.environ,
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "WEB_DIST": str(DIST),
    } | env
    with log.open("w") as out:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "curio.app.api:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=cwd,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait(f"{url}/health", 600, process, log)
        yield url
    finally:
        process.terminate()
        process.wait(30)


@pytest.fixture(scope="session")
def api_url(llm_stub: str, artifacts: Path) -> Iterator[str]:
    """The API with the scripted LLM: every domain when the H&M data is prepared, else
    cocktails, books and Plan my evening (without its outfit)."""
    if fashion_ready():
        domains = ""
    elif demo_ready():
        domains = "cocktails,books"
    else:
        pytest.skip(f"E2E data not prepared in {DATA_DIR}: see tests/e2e/conftest.py")
    env = {
        "DATA_DIR": str(DATA_DIR),
        "QDRANT_PATH": str(QDRANT_PATH),
        "QDRANT_URL": "",
        "ENABLED_DOMAINS": domains,
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "curio-e2e-stub",
        "LLM_BASE_URL": llm_stub,
        "LLM_API_KEY": "e2e-not-a-secret",
        "AGENT_SIGNING_KEY": "e2e-signing-key-0123456789abcdef",
    }
    with _api(env, artifacts / "api.log") as url:
        yield url


@pytest.fixture(scope="session")
def quickstart_api_url(artifacts: Path, tmp_path_factory) -> Iterator[str]:
    """The quick start: only the recipe file, no H&M data, no image index and no LLM.

    It opens no Qdrant index, so it runs beside the full-data API above.
    """
    recipes = DATA_DIR / "cocktails.parquet"
    if not recipes.exists():
        pytest.skip(f"E2E data not prepared: {recipes}")
    data = tmp_path_factory.mktemp("quickstart-data")
    shutil.copy(recipes, data / "cocktails.parquet")
    env = {
        "DATA_DIR": str(data),
        "QDRANT_PATH": str(data / ".qdrant"),
        "QDRANT_URL": "",
        "ENABLED_DOMAINS": "",
        "LLM_MODEL": "",
    }
    # Started in the data folder, so a developer's .env (an LLM, other domains) cannot leak in.
    with _api(env, artifacts / "quickstart-api.log", cwd=data) as url:
        yield url


@pytest.fixture(scope="session")
def demo_api_url(artifacts: Path, tmp_path_factory) -> Iterator[str]:
    """The quick-start demo (books on by default): recipes and books, no H&M data, no
    image index and no LLM. It opens no Qdrant index, so it runs beside the full-data API."""
    files = ("cocktails.parquet", "books.parquet", "book_neighbours.parquet")
    if missing := [name for name in files if not (DATA_DIR / name).exists()]:
        pytest.skip(f"E2E data not prepared: {', '.join(missing)}")
    data = tmp_path_factory.mktemp("demo-data")
    for name in files:
        shutil.copy(DATA_DIR / name, data / name)
    env = {
        "DATA_DIR": str(data),
        "QDRANT_PATH": str(data / ".qdrant"),
        "QDRANT_URL": "",
        "ENABLED_DOMAINS": "cocktails,books",
        "LLM_MODEL": "",
    }
    with _api(env, artifacts / "demo-api.log", cwd=data) as url:
        yield url


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("E2E_CHROMIUM") or (
        "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
    )
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable)
        yield browser
        browser.close()


@pytest.fixture(scope="session")
def web_dist() -> Path:
    """The built React app, built once when it is missing and the dependencies are there."""
    if not (DIST / "index.html").exists():
        npm = shutil.which("npm")
        if npm is None or not (WEB / "node_modules").exists():
            pytest.skip("web/dist is not built (run `npm ci && npm run build` in web/)")
        subprocess.run([npm, "run", "build"], cwd=WEB, check=True)
    return DIST


def _serves_web(url: str) -> str:
    page = httpx.get(f"{url}/evening", headers={"accept": "text/html"}, trust_env=False)
    if page.status_code != 200 or '<div id="root">' not in page.text:
        pytest.skip("The API does not serve the React app (SERVE_WEB=false?)")
    return url


@pytest.fixture(scope="session")
def web_url(web_dist: Path, api_url: str) -> str:
    return _serves_web(api_url)


@pytest.fixture(scope="session")
def quickstart_web_url(web_dist: Path, quickstart_api_url: str) -> str:
    return _serves_web(quickstart_api_url)


@pytest.fixture(scope="session")
def demo_web_url(web_dist: Path, demo_api_url: str) -> str:
    return _serves_web(demo_api_url)


class Web:
    """A browser page on the React app, with named screenshots and request capture."""

    def __init__(self, page, base: str, artifacts: Path) -> None:
        self.page = page
        self.base = base
        self.artifacts = artifacts
        self.errors: list[str] = []
        self.chat_bodies: list[dict] = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on("console", self._console)
        page.on("request", self._capture)

    def _console(self, message) -> None:
        # The API's Content-Security-Policy must allow everything the app loads.
        if message.type == "error" and "Content Security Policy" in message.text:
            self.errors.append(message.text)

    def _capture(self, request) -> None:
        if request.url.endswith("/chat/stream") and request.method == "POST":
            self.chat_bodies.append(json.loads(request.post_data or "{}"))

    def open(self, path: str = "/") -> "Web":
        self.page.goto(f"{self.base}{path}")
        return self

    def turns(self):
        return self.page.get_by_test_id("assistant-turn")

    def last_turn(self):
        return self.turns().last

    def wait_for_turns(self, count: int) -> None:
        expect(self.turns()).to_have_count(count, timeout=120_000)
        expect(self.page.get_by_test_id("running-turn")).to_have_count(0, timeout=120_000)

    def send(self, text: str) -> None:
        """Send `text` in the chat and wait for its answer."""
        turns = self.turns().count()
        box = self.page.get_by_label("Message Curio")
        box.fill(text)
        box.press("Enter")
        self.wait_for_turns(turns + 1)

    def click_and_answer(self, locator) -> None:
        """Click a control that sends a chat message, and wait for the answer."""
        turns = self.turns().count()
        locator.click()
        self.wait_for_turns(turns + 1)

    def photos(self) -> None:
        """Wait for the photos in view (lazy photos below the fold load when scrolled to)."""
        self.page.wait_for_function(
            """() => [...document.querySelectorAll('img')]
                .filter(i => {
                    const box = i.getBoundingClientRect();
                    return i.offsetParent !== null && box.bottom > 0
                        && box.top < window.innerHeight;
                })
                .every(i => i.complete)""",
            timeout=60_000,
        )

    def scroll_through(self) -> None:
        """Bring every lazy photo into view once, then return to the top."""
        self.page.evaluate(
            """async () => {
                const step = window.innerHeight * 0.8;
                for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
                    window.scrollTo(0, y);
                    await new Promise((r) => setTimeout(r, 150));
                }
            }"""
        )
        self.photos()
        self.page.evaluate("window.scrollTo(0, 0)")

    def shot(self, name: str, *, full_page: bool = False) -> None:
        self.page.wait_for_timeout(600)  # let entrance animations settle
        if full_page:
            self.scroll_through()
        self.photos()
        self.page.screenshot(path=self.artifacts / f"web-{name}.png", full_page=full_page)

    def session(self) -> dict:
        return self.page.evaluate("() => JSON.parse(localStorage.getItem('curio.session.v1'))")

    def no_horizontal_scroll(self) -> bool:
        return self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def results(web: Web, title: str):
    """The results section headed `title`, once its cards are there."""
    section = web.page.get_by_role("region", name=title)
    expect(section).to_be_visible(timeout=60_000)
    expect(section).to_have_attribute("aria-busy", "false", timeout=60_000)
    return section


@contextmanager
def _driver(browser, url: str, artifacts: Path, **context) -> Iterator[Web]:
    context = browser.new_context(**({"viewport": {"width": 1440, "height": 1000}} | context))
    driver = Web(context.new_page(), url, artifacts)
    yield driver
    context.close()
    assert not driver.errors, f"Browser errors: {driver.errors}"


PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2}


@pytest.fixture
def web(browser, web_url: str, artifacts: Path) -> Iterator[Web]:
    with _driver(browser, web_url, artifacts) as driver:
        yield driver


@pytest.fixture
def web_phone(browser, web_url: str, artifacts: Path) -> Iterator[Web]:
    with _driver(browser, web_url, artifacts, **PHONE) as driver:
        yield driver


@pytest.fixture
def quickstart_web(browser, quickstart_web_url: str, artifacts: Path) -> Iterator[Web]:
    with _driver(browser, quickstart_web_url, artifacts) as driver:
        yield driver


@pytest.fixture
def demo_web(browser, demo_web_url: str, artifacts: Path) -> Iterator[Web]:
    with _driver(browser, demo_web_url, artifacts) as driver:
        yield driver
