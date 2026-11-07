"""Serving the built React app beside the API (`curio.app.web`) and the OpenAPI export."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from curio.app import api as api_module
from curio.app.api import create_app
from curio.app.config import Settings
from scripts import export_openapi
from tests.conftest import serve

HTML = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
INDEX = "<!doctype html><title>Curio</title><div id=root></div>"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    folder = tmp_path / "dist"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text(INDEX)
    (folder / "assets" / "index-abc123.js").write_text("console.log('curio')")
    (folder / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside dist")
    return folder


@pytest.fixture
def client(dist: Path) -> Iterator[TestClient]:
    with TestClient(create_app(serve(), web_dist=dist)) as client:
        yield client


def test_page_navigations_get_index_html(client: TestClient) -> None:
    for path in ("/", "/evening", "/ask/books", "/ask/fashion"):
        response = client.get(path, headers=HTML)
        assert response.status_code == 200, path
        assert response.text == INDEX
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-cache"


def test_assets_and_top_level_files_are_served(client: TestClient) -> None:
    script = client.get("/assets/index-abc123.js")
    assert script.status_code == 200
    assert script.text == "console.log('curio')"
    assert "immutable" in script.headers["cache-control"]
    assert client.get("/favicon.svg").text == "<svg/>"
    assert client.head("/assets/index-abc123.js").status_code == 200


def test_api_routes_and_errors_are_unchanged(client: TestClient) -> None:
    # API routes answer as before, even for a browser.
    domains = client.get("/domains", headers=HTML)
    assert domains.status_code == 200 and isinstance(domains.json(), list)
    # A domain the server does not serve still answers 503 with its hint.
    assert client.get("/books/1", headers=HTML).status_code == 503
    # A path matching an API route with another method stays 405.
    assert client.get("/chat", headers=HTML).status_code == 405
    # Unknown paths without text/html (API clients) keep the JSON 404.
    missing = client.get("/no/such/path")
    assert missing.status_code == 404 and missing.json() == {"detail": "Not Found"}
    assert client.post("/evening", headers=HTML).status_code == 404
    assert client.get("/assets/missing.js", headers=HTML).status_code == 404


def test_files_outside_dist_are_not_served(client: TestClient) -> None:
    for path in ("/assets/../../secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt", "/.env"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert "outside dist" not in response.text


def test_without_a_build_the_api_is_unchanged(tmp_path: Path) -> None:
    with TestClient(create_app(serve(), web_dist=tmp_path / "missing")) as client:
        assert client.get("/evening", headers=HTML).status_code == 404
        assert client.get("/assets/index.js").status_code == 404


def test_a_build_made_after_startup_is_served(tmp_path: Path) -> None:
    folder = tmp_path / "later"
    with TestClient(create_app(serve(), web_dist=folder)) as client:
        assert client.get("/", headers=HTML).status_code == 404
        folder.mkdir()
        (folder / "index.html").write_text(INDEX)
        assert client.get("/", headers=HTML).text == INDEX


def test_production_reads_the_web_settings(monkeypatch, dist: Path, tmp_path: Path) -> None:
    # No data: no domain loads, so this needs no catalog or image index.
    empty = {"data_dir": tmp_path / "data", "qdrant_path": tmp_path / "qdrant", "llm_model": None}
    on = Settings(web_dist=dist, **empty)
    monkeypatch.setattr(api_module, "get_settings", lambda: on)
    app = create_app()
    assert any(r.path == "/domains" for r in app.routes)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/assets/index-abc123.js").status_code == 200
    off = Settings(web_dist=dist, serve_web=False, **empty)
    monkeypatch.setattr(api_module, "get_settings", lambda: off)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        assert client.get("/assets/index-abc123.js").status_code == 404


def test_openapi_export_writes_the_schema(tmp_path: Path) -> None:
    out = export_openapi.main(["--out", str(tmp_path / "openapi.json")])
    schema = json.loads(out.read_text())
    assert schema["info"]["title"] == "Curio Recommendation API"
    assert "/chat/stream" in schema["paths"]
    assert "AssistantReply" in schema["components"]["schemas"]


def test_committed_openapi_matches_the_api() -> None:
    """web/openapi.json is what the React app's types are generated from: regenerate it with
    `npm run openapi:export && npm run gen:api` (in web/) after changing the API."""
    committed = Path(__file__).resolve().parents[2] / "web" / "openapi.json"
    current = export_openapi.openapi_schema()
    saved = json.loads(committed.read_text())
    # The version changes with every release; the shapes are what the types depend on.
    saved["info"].pop("version")
    current["info"].pop("version")
    assert saved == current
