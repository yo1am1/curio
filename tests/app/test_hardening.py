"""HTTP hardening: body limits, uploads, and the cap on concurrent agent turns."""

import io
import threading
import time
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

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


# --- Request-body limits ------------------------------------------------------------------


@pytest.fixture
def cocktail_client(cocktail_catalog) -> Iterator[TestClient]:
    with TestClient(_app(cocktail_catalog, None, max_body_bytes=64 * 1024)) as client:
        yield client


def test_json_bodies_over_the_limit_get_413_before_parsing(cocktail_client) -> None:
    """The auditor sent 200 MB of unknown fields to /cocktails/search and got 200."""
    padded = b'{"query":"mojito","junk":"' + b"x" * (65 * 1024) + b'"}'
    response = cocktail_client.post(
        "/cocktails/search", content=padded, headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body is too large: the limit is 65,536 bytes."}
    small = cocktail_client.post("/cocktails/search", json={"query": "mojito"})
    assert small.status_code == 200


def test_a_streamed_body_without_length_is_cut_off_at_the_limit(cocktail_client) -> None:
    sent = 0

    def chunks() -> Iterator[bytes]:
        nonlocal sent
        yield b'{"query":"mojito","junk":"'
        for _ in range(64):  # 4 MiB in 64 KiB chunks, no Content-Length
            sent += 1
            yield b"x" * (64 * 1024)
        yield b'"}'

    response = cocktail_client.post(
        "/cocktails/search", content=chunks(), headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    assert "too large" in response.json()["detail"]


def test_the_largest_valid_chat_request_fits_the_default_limit() -> None:
    history = [{"role": "user", "content": "é" * 4000} for _ in range(50)]
    body = httpx.Request("POST", "/", json={"message": "m" * 2000, "history": history}).content
    assert len(body) < Settings(_env_file=None).max_body_bytes


def _png(size=(4, 4)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "red").save(out, format="PNG")
    return out.getvalue()


def test_an_oversized_upload_is_refused_before_it_is_spooled(services) -> None:
    """The upload limit applies while the body streams in: a 30 MB part is cut off just past
    10 MB instead of being written to a temporary file in full."""
    read = 0

    def body() -> Iterator[bytes]:
        nonlocal read
        yield (
            b'--B\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\n'
            b"Content-Type: image/png\r\n\r\n"
        )
        for _ in range(30):
            read += 1
            yield b"\0" * (1024 * 1024)
        yield b"\r\n--B--\r\n"

    with TestClient(create_app(services, settings=settings())) as client:
        response = client.post(
            "/search/image",
            content=body(),
            headers={"content-type": "multipart/form-data; boundary=B"},
        )
        assert response.status_code == 413
        assert response.json() == {"detail": "Image must be at most 10 MB."}
        # A declared length over the limit is refused without reading anything.
        declared = client.post(
            "/search/image",
            content=b"x",
            headers={
                "content-type": "multipart/form-data; boundary=B",
                "content-length": "99999999",
            },
        )
        assert declared.status_code == 413
        assert client.post("/search/image", files={"file": ("a.png", _png())}).status_code == 200


@pytest.mark.parametrize("fmt", ["TIFF", "BMP", "GIF", "ICO"])
def test_uploads_are_decoded_only_as_png_or_jpeg(services, fmt) -> None:
    raw = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(raw, format=fmt)
    with TestClient(create_app(services, settings=settings())) as client:
        response = client.post("/search/image", files={"file": ("a.img", raw.getvalue())})
        assert response.status_code == 400
        assert response.json() == {"detail": "Upload a valid PNG or JPEG image."}
        jpeg = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(jpeg, format="JPEG")
        ok = client.post("/search/image", files={"file": ("a.jpg", jpeg.getvalue())})
        assert ok.status_code == 200


# --- Response headers, Host check and the docs UI ---------------------------------------------


def test_every_response_carries_the_security_headers(cocktail_catalog, tmp_path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Curio</title>")
    cocktails = CocktailServices(cocktail_catalog, CocktailRecommender(cocktail_catalog))
    app = create_app(serve(cocktails=cocktails), web_dist=dist, settings=settings())
    with TestClient(app) as client:
        responses = [
            client.get("/health"),
            client.get("/ask/cocktails", headers={"accept": "text/html"}),  # the SPA
            client.get("/cocktails/999999"),  # an error
            client.post("/cocktails/search", content=b"x" * (2 * 1024 * 1024)),  # a 413
        ]
    assert [r.status_code for r in responses] == [200, 200, 404, 413]
    for response in responses:
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert response.headers["x-frame-options"] == "DENY"
        policy = dict(
            part.strip().split(" ", 1)
            for part in response.headers["content-security-policy"].split(";")
        )
        assert policy["default-src"] == "'self'"
        assert policy["script-src"] == "'self'"
        assert policy["frame-ancestors"] == "'none'"
        assert policy["connect-src"] == "'self'"
        assert policy["object-src"] == "'none'"
        images = policy["img-src"].split()
        assert "https://www.thecocktaildb.com" in images
        assert "https://images.gr-assets.com" in images
        assert "https://covers.openlibrary.org" in images
        assert "data:" not in images and "*" not in images and "https:" not in images


def test_requests_under_a_foreign_host_name_are_refused(cocktail_catalog) -> None:
    """DNS rebinding: a page on attacker.example resolving to 127.0.0.1 sends its own Host."""
    cocktails = CocktailServices(cocktail_catalog, CocktailRecommender(cocktail_catalog))
    app = create_app(
        serve(cocktails=cocktails), settings=settings(allowed_hosts="localhost,127.0.0.1")
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health", headers={"host": "localhost:8000"}).status_code == 200
        rebound = client.get("/health", headers={"host": "attacker.example"})
        assert rebound.status_code == 400
        chat = client.post("/chat", json=CHAT, headers={"host": "attacker.example:8000"})
        assert chat.status_code == 400


def test_allowed_hosts_default_to_loopback_names_and_are_configurable(monkeypatch) -> None:
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    assert Settings(_env_file=None).allowed_hosts == ("localhost", "127.0.0.1")
    monkeypatch.setenv("ALLOWED_HOSTS", " curio.lan, LOCALHOST ")
    assert Settings(_env_file=None).allowed_hosts == ("curio.lan", "localhost")


def test_the_docs_ui_is_off_by_default(cocktail_catalog) -> None:
    cocktails = CocktailServices(cocktail_catalog, CocktailRecommender(cocktail_catalog))
    with TestClient(create_app(serve(cocktails=cocktails), settings=settings())) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 200  # the schema stays available


def test_the_opt_in_docs_ui_loads_pinned_assets_with_sri(cocktail_catalog) -> None:
    cocktails = CocktailServices(cocktail_catalog, CocktailRecommender(cocktail_catalog))
    app = create_app(serve(cocktails=cocktails), settings=settings(api_docs=True))
    with TestClient(app) as client:
        page = client.get("/docs")
        init = client.get("/docs/init.js")
    assert page.status_code == 200 and init.status_code == 200
    assert "swagger-ui-dist@5.33.3/swagger-ui-bundle.js" in page.text
    assert page.text.count('integrity="sha384-') == 2
    assert "swagger-ui-dist@5/" not in page.text  # no floating major version
    assert "<script>" not in page.text  # nothing inline: the app's script-src has no hashes
    assert "SwaggerUIBundle(" in init.text
    policy = page.headers["content-security-policy"]
    assert "script-src 'self' https://cdn.jsdelivr.net" in policy
    assert "frame-ancestors 'none'" in policy


# --- Single-use, session-bound approvals over HTTP -------------------------------------------


def _replay_app(cocktail_catalog, *replies):
    from langchain_core.messages import AIMessage

    from curio.app.assistants import CocktailAssistant
    from curio.engine import ActionSigner
    from tests.agents.conftest import FakeToolCallingChatModel

    model = FakeToolCallingChatModel(messages=iter([AIMessage(**reply) for reply in replies]))
    assistant = CocktailAssistant(
        model,
        cocktail_catalog,
        CocktailRecommender(cocktail_catalog),
        signer=ActionSigner(b"k" * 32),
    )
    return _app(cocktail_catalog, assistant)


SAVE = {
    "content": "",
    "tool_calls": [{"name": "SaveCocktails", "args": {"drink_ids": [1]}, "id": "t"}],
}
SESSION_ID = "s" * 32


def test_the_audit_replay_probe_now_gets_409(cocktail_catalog) -> None:
    """`work/security/replay.py`: the same signed proposal resumed three times. It used to be
    applied each time; now the first answer applies and every later one is a 409."""
    app = _replay_app(cocktail_catalog, SAVE, {"content": "Saved."}, {"content": "Saved again."})
    base = {"domain": "cocktails", "session_id": SESSION_ID}
    with TestClient(app) as client:
        reply = client.post("/chat", json={**base, "message": "save it"}).json()
        decision = {"action": reply["pending_action"], "approved": True}
        first = client.post("/chat", json={**base, "decision": decision})
        assert first.status_code == 200
        assert first.json()["applied"][0]["drink_ids"] == [1]
        for path in ("/chat", "/chat/stream"):
            again = client.post(path, json={**base, "decision": decision})
            if path == "/chat":
                assert again.status_code == 409
                assert again.json()["detail"] == (
                    "This proposal was already answered. Please ask again."
                )
            else:
                assert '"status": 409' in again.text and "already answered" in again.text


def test_a_proposal_cannot_be_answered_from_another_session(cocktail_catalog) -> None:
    app = _replay_app(cocktail_catalog, SAVE, {"content": "Saved."})
    with TestClient(app) as client:
        reply = client.post(
            "/chat", json={"domain": "cocktails", "message": "save it", "session_id": SESSION_ID}
        ).json()
        decision = {"action": reply["pending_action"], "approved": True}
        for other in ({"session_id": "t" * 32}, {}):
            stolen = client.post(
                "/chat", json={"domain": "cocktails", "decision": decision, **other}
            )
            assert stolen.status_code == 409
        own = {"domain": "cocktails", "decision": decision, "session_id": SESSION_ID}
        assert client.post("/chat", json=own).status_code == 200


@pytest.mark.parametrize("session_id", ["short", "x" * 129, "has space" * 4, "ü" * 20])
def test_session_ids_are_validated(cocktail_catalog, session_id) -> None:
    with TestClient(_app(cocktail_catalog, SlowAssistant())) as client:
        body = {**CHAT, "session_id": session_id}
        assert client.post("/chat", json=body).status_code == 422


def test_a_short_signing_key_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SIGNING_KEY", "too-short")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        Settings(_env_file=None)
    monkeypatch.setenv("AGENT_SIGNING_KEY", "k" * 32)
    assert Settings(_env_file=None).agent_signing_key is not None


def test_several_workers_without_a_signing_key_refuse_to_start(tmp_path, caplog) -> None:
    from curio.app.services import build_services

    chat = {"data_dir": tmp_path, "llm_model": "m", "llm_api_key": "x", "web_concurrency": 2}
    with pytest.raises(RuntimeError, match="AGENT_SIGNING_KEY is not set"):
        build_services(settings(**chat))
    with caplog.at_level("WARNING"):
        build_services(settings(**chat, agent_signing_key="k" * 32)).close()
    assert "once per worker" in caplog.text
    build_services(settings(**{**chat, "web_concurrency": 1})).close()  # one worker: fine


# --- Shopper privacy -----------------------------------------------------------------------


def test_shopper_profiles_leave_out_the_age(services) -> None:
    """The H&M sample has each shopper's age; no page uses it, so the API does not serve it.
    Purchases (product and date) stay: the For you page lists them."""
    with TestClient(create_app(services, settings=settings())) as client:
        profile = client.get("/users/2").json()
        assert profile["user"] == {"user_id": 2}
        dates = [p["purchase_date"] for p in profile["purchases"]]
        assert dates == ["2020-09-04", "2020-09-03"]
        assert client.get("/users/1").json()["user"] == {"user_id": 1}


def test_health_discloses_no_configuration(cocktail_catalog) -> None:
    """/health carries what the web app shows (catalog sizes, which features are on); never
    the model, provider, URLs, paths or keys."""
    cocktails = CocktailServices(cocktail_catalog, CocktailRecommender(cocktail_catalog))
    configured = settings(
        llm_model="secret-model-name",
        llm_base_url="http://10.0.0.5:4000/v1",
        llm_api_key="sk-not-for-clients",
        agent_signing_key="k" * 40,
    )
    app = create_app(serve(cocktails=cocktails, llm_configured=True), settings=configured)
    with TestClient(app) as client:
        body = client.get("/health").text
    for secret in ("secret-model-name", "10.0.0.5", "sk-not", "kkkk", "/home/", "/tmp/"):
        assert secret not in body
