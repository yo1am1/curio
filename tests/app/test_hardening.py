"""HTTP hardening: the cap on concurrent agent turns, and what stays responsive under it."""

import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from curio.app import api as api_module
from curio.app.api import create_app
from curio.app.config import Settings
from curio.app.schemas import AssistantReply, ReplyEvent
from curio.app.services import CocktailServices
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.domains.fashion.agent.intent import SearchIntent
from tests.conftest import serve


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


class SlowAssistant:
    """A fake agent whose turns block until `release` is set, like a model that hangs."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.running = 0
        self._lock = threading.Lock()

    def _turn(self, message: str) -> AssistantReply:
        with self._lock:
            self.running += 1
        try:
            self.release.wait(30)
            return AssistantReply(text=f"done: {message}", intent=SearchIntent(query=message))
        finally:
            with self._lock:
                self.running -= 1

    def reply(self, message, history, context=None) -> AssistantReply:
        return self._turn(message)

    def resume(self, decision, history, context=None) -> AssistantReply:
        return self._turn("resume")

    def stream(self, message, history, context=None, *, decision=None) -> Iterator[ReplyEvent]:
        yield ReplyEvent(reply=self._turn(message))


@pytest.fixture
def slow(cocktail_catalog) -> SlowAssistant:
    return SlowAssistant()


def _app(cocktail_catalog, assistant, **overrides):
    cocktails = CocktailServices(
        cocktail_catalog, CocktailRecommender(cocktail_catalog), assistant=assistant
    )
    return create_app(serve(cocktails=cocktails), settings=settings(**overrides))


def _wait_until(predicate, timeout=10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        time.sleep(0.01)


CHAT = {"domain": "cocktails", "message": "a mojito please"}


def test_turns_over_the_cap_get_503_with_retry_after(cocktail_catalog, slow) -> None:
    app = _app(cocktail_catalog, slow, max_concurrent_turns=2)
    with TestClient(app) as client:
        results: list[int] = []

        def chat() -> None:
            results.append(client.post("/chat", json=CHAT).status_code)

        threads = [threading.Thread(target=chat) for _ in range(2)]
        for thread in threads:
            thread.start()
        _wait_until(lambda: slow.running == 2)
        for path in ("/chat", "/chat/stream"):
            busy = client.post(path, json=CHAT)
            assert busy.status_code == 503
            assert busy.headers["retry-after"] == "5"
            assert "busy" in busy.json()["detail"]
        slow.release.set()
        for thread in threads:
            thread.join(10)
        assert results == [200, 200]
        # The slots are free again, for both routes.
        assert client.post("/chat", json=CHAT).status_code == 200
        assert client.post("/chat/stream", json=CHAT).status_code == 200


def test_health_and_search_stay_responsive_while_many_turns_hang(cocktail_catalog, slow) -> None:
    """The auditor's flood: 45 chats against a model that never answers. Before the cap they
    held all 40 worker threads and /health timed out; now 4 hold slots and 41 get 503."""
    app = _app(cocktail_catalog, slow, max_concurrent_turns=4)
    with TestClient(app) as client:
        statuses: list[int] = []

        def chat() -> None:
            statuses.append(client.post("/chat", json=CHAT).status_code)

        threads = [threading.Thread(target=chat, daemon=True) for _ in range(45)]
        try:
            for thread in threads:
                thread.start()
            _wait_until(lambda: slow.running == 4 and len(statuses) == 41)
            assert statuses.count(503) == 41

            answers: dict[str, int] = {}

            def probe() -> None:
                answers["health"] = client.get("/health").status_code
                search = client.post("/cocktails/search", json={"query": "mojito"})
                answers["search"] = search.status_code

            started = time.monotonic()
            prober = threading.Thread(target=probe, daemon=True)
            prober.start()
            prober.join(5)
            assert answers == {"health": 200, "search": 200}
            assert time.monotonic() - started < 5
        finally:
            slow.release.set()
            for thread in threads:
                thread.join(10)
        assert statuses.count(200) == 4


def test_a_streamed_turn_gives_its_slot_back(cocktail_catalog, slow) -> None:
    slow.release.set()
    app = _app(cocktail_catalog, slow, max_concurrent_turns=1)
    with TestClient(app) as client:
        for _ in range(3):
            response = client.post("/chat/stream", json=CHAT)
            assert response.status_code == 200
            assert '"type":"reply"' in response.text
        assert client.post("/chat", json=CHAT).status_code == 200


class FailingAssistant:
    def reply(self, message, history, context=None):
        raise ConnectionError("gateway down")

    def stream(self, message, history, context=None, *, decision=None):
        raise ConnectionError("gateway down")
        yield


def test_failed_turns_give_their_slot_back(cocktail_catalog) -> None:
    app = _app(cocktail_catalog, FailingAssistant(), max_concurrent_turns=1)
    with TestClient(app) as client:
        for _ in range(3):
            assert client.post("/chat", json=CHAT).status_code == 502
            assert '"status": 502' in client.post("/chat/stream", json=CHAT).text


def test_the_cap_is_configurable_and_bounded(monkeypatch) -> None:
    assert Settings(_env_file=None).max_concurrent_turns == 8
    monkeypatch.setenv("MAX_CONCURRENT_TURNS", "3")
    assert Settings(_env_file=None).max_concurrent_turns == 3
    for value in ("0", "33"):
        monkeypatch.setenv("MAX_CONCURRENT_TURNS", value)
        with pytest.raises(ValueError):
            Settings(_env_file=None)


def test_production_app_reads_the_cap_from_the_environment(monkeypatch, cocktail_catalog) -> None:
    slow = SlowAssistant()
    monkeypatch.setattr(api_module, "get_settings", lambda: settings(max_concurrent_turns=1))
    cocktails = CocktailServices(
        cocktail_catalog, CocktailRecommender(cocktail_catalog), assistant=slow
    )
    with TestClient(create_app(serve(cocktails=cocktails))) as client:
        thread = threading.Thread(target=lambda: client.post("/chat", json=CHAT), daemon=True)
        thread.start()
        _wait_until(lambda: slow.running == 1)
        assert client.post("/chat", json=CHAT).status_code == 503
        slow.release.set()
        thread.join(10)
