"""End-to-end fixtures: the real API, the real Streamlit UI, a browser and a scripted LLM.

The suite needs prepared data (H&M sample, product photos, the Qdrant image index and the
cocktail catalog) and runs only on request:

    uv run pytest -m e2e tests/e2e

Environment: E2E_DATA_DIR (default `data`), E2E_QDRANT_PATH (default `.qdrant`),
E2E_ARTIFACTS (screenshot folder, default `e2e-artifacts`), E2E_CHROMIUM (browser binary).
Embedded Qdrant allows one process, so stop any other API using the same index first.
"""

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

from tests.e2e.llm_stub import serve

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("E2E_DATA_DIR", ROOT / "data"))
QDRANT_PATH = Path(os.environ.get("E2E_QDRANT_PATH", ROOT / ".qdrant"))
ARTIFACTS = Path(os.environ.get("E2E_ARTIFACTS", ROOT / "e2e-artifacts"))
# A finished Streamlit rerun: no stale elements and no running indicator.
SETTLED = """() =>
    !document.querySelector('[data-stale="true"]') &&
    !document.querySelector('[data-testid="stStatusWidgetRunningIcon"]')"""


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/e2e/" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(pytest.mark.e2e)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait(url: str, timeout: float, process: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{url} exited early:\n{log.read_text()[-3000:]}")
        try:
            if httpx.get(url, timeout=2).is_success:
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
    env = {**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"} | env
    with log.open("w") as out:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "clothes_recommender.api:create_app",
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


@contextmanager
def _streamlit(api_url: str, log: Path) -> Iterator[str]:
    """The real Streamlit UI on a free port, talking to `api_url`."""
    port = _free_port()
    env = {**os.environ, "API_URL": api_url, "NO_PROXY": "127.0.0.1,localhost"}
    with log.open("w") as out:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "src/clothes_recommender/ui.py",
                "--server.headless",
                "true",
                "--server.port",
                str(port),
                "--browser.gatherUsageStats",
                "false",
            ],
            cwd=ROOT,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait(f"{url}/_stcore/health", 120, process, log)
        yield url
    finally:
        process.terminate()
        process.wait(30)


@pytest.fixture(scope="session")
def api_url(llm_stub: str, artifacts: Path) -> Iterator[str]:
    required = [DATA_DIR / "products.parquet", DATA_DIR / "cocktails.parquet", QDRANT_PATH]
    if missing := [str(path) for path in required if not path.exists()]:
        pytest.skip(f"E2E data not prepared: {', '.join(missing)}")
    env = {
        "DATA_DIR": str(DATA_DIR),
        "QDRANT_PATH": str(QDRANT_PATH),
        "QDRANT_URL": "",
        "ENABLED_DOMAINS": "",
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "curio-e2e-stub",
        "LLM_BASE_URL": llm_stub,
        "LLM_API_KEY": "e2e-not-a-secret",
        "AGENT_SIGNING_KEY": "e2e-signing-key-0123456789abcdef",
    }
    with _api(env, artifacts / "api.log") as url:
        yield url


@pytest.fixture(scope="session")
def ui_url(api_url: str, artifacts: Path) -> Iterator[str]:
    with _streamlit(api_url, artifacts / "ui.log") as url:
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
def quickstart_ui_url(quickstart_api_url: str, artifacts: Path) -> Iterator[str]:
    with _streamlit(quickstart_api_url, artifacts / "quickstart-ui.log") as url:
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


class Driver:
    """A browser page with Streamlit-aware waits, chat helpers and named screenshots."""

    def __init__(self, page, base: str, artifacts: Path) -> None:
        self.page = page
        self.base = base
        self.artifacts = artifacts
        self.errors: list[str] = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))

    def open(self, path: str = "") -> "Driver":
        self.page.goto(f"{self.base}/{path}")
        self.settle()
        return self

    def settle(self, timeout: float = 120_000) -> None:
        self.page.wait_for_timeout(600)  # reruns start asynchronously after an interaction
        self.page.wait_for_function(SETTLED, timeout=timeout)
        self.page.wait_for_timeout(400)

    def key(self, key: str):
        """The element Streamlit renders for widget/container `key`."""
        return self.page.locator(f".st-key-{key}")

    def click(self, locator) -> None:
        locator.first.click()
        self.settle()

    def send(self, text: str) -> None:
        box = self.page.get_by_test_id("stChatInputTextArea").first
        box.fill(text)
        box.press("Enter")
        self.settle()

    def shot(self, name: str, *, full_page: bool = False) -> Path:
        path = self.artifacts / f"{name}.png"
        self.page.screenshot(path=path, full_page=full_page)
        return path

    def no_horizontal_scroll(self) -> bool:
        return self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


@pytest.fixture
def ui(browser, ui_url: str, artifacts: Path) -> Iterator[Driver]:
    context = browser.new_context(viewport={"width": 1440, "height": 1100})
    driver = Driver(context.new_page(), ui_url, artifacts)
    yield driver
    context.close()
    assert not driver.errors, f"Browser errors: {driver.errors}"


@pytest.fixture
def quickstart_ui(browser, quickstart_ui_url: str, artifacts: Path) -> Iterator[Driver]:
    context = browser.new_context(viewport={"width": 1440, "height": 1100})
    driver = Driver(context.new_page(), quickstart_ui_url, artifacts)
    yield driver
    context.close()
    assert not driver.errors, f"Browser errors: {driver.errors}"


@pytest.fixture
def phone(browser, ui_url: str, artifacts: Path) -> Iterator[Driver]:
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    driver = Driver(context.new_page(), ui_url, artifacts)
    yield driver
    context.close()
