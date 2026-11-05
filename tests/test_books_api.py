"""The book domain served: loading from prepared files, /books/* endpoints and book chat."""

import json
from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from clothes_recommender import api as api_module
from clothes_recommender import services as service_module
from clothes_recommender.api import CHAT_DISABLED, create_app
from clothes_recommender.books.recommend import BookRecommender
from clothes_recommender.chat.book_agent import BookAssistant
from clothes_recommender.config import Settings
from clothes_recommender.services import BOOKS_MISSING, BookServices, build_services
from tests.chat.conftest import FakeToolCallingChatModel
from tests.conftest import book_frame, book_neighbours_frame, serve


def settings_for(tmp_path, **overrides) -> Settings:
    values = {"data_dir": tmp_path, "qdrant_path": tmp_path / ".qdrant", "llm_model": None}
    return Settings(_env_file=None, **(values | overrides))


def write_books(folder) -> None:
    book_frame().to_parquet(folder / "books.parquet")
    book_neighbours_frame().to_parquet(folder / "book_neighbours.parquet")


@pytest.fixture
def book_services(book_catalog, book_recommender) -> BookServices:
    return BookServices(book_catalog, book_recommender)


@pytest.fixture
def client(book_services) -> Iterator[TestClient]:
    with TestClient(create_app(serve(books=book_services))) as client:
        yield client


# --- Loading -------------------------------------------------------------------------------


def test_prepared_files_enable_books_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "ClipEncoder", Mock(side_effect=AssertionError))
    write_books(tmp_path)
    services = build_services(settings_for(tmp_path))
    assert list(services.domains) == ["books"]
    assert services.books.item_count() == 10
    assert services.books.assistant is None
    services.close()


@pytest.mark.parametrize("missing", ["books.parquet", "book_neighbours.parquet"])
def test_either_missing_file_disables_books(tmp_path, caplog, missing):
    write_books(tmp_path)
    (tmp_path / missing).unlink()
    assert build_services(settings_for(tmp_path)).books is None
    assert "scripts.prepare_books" in caplog.text


def test_a_broken_book_file_disables_only_books(tmp_path, caplog):
    write_books(tmp_path)
    (tmp_path / "book_neighbours.parquet").write_bytes(b"not parquet")
    from tests.conftest import cocktail_frame

    cocktail_frame().to_parquet(tmp_path / "cocktails.parquet")
    services = build_services(settings_for(tmp_path))
    assert list(services.domains) == ["cocktails"]
    assert "the book domain is disabled" in caplog.text


def test_enabled_domains_can_leave_books_out(tmp_path):
    write_books(tmp_path)
    assert build_services(settings_for(tmp_path, enabled_domains=("cocktails",))).books is None


def test_an_llm_builds_the_book_assistant_with_the_shared_signer(tmp_path, monkeypatch):
    from tests.conftest import cocktail_frame

    write_books(tmp_path)
    cocktail_frame().to_parquet(tmp_path / "cocktails.parquet")
    model = FakeToolCallingChatModel(messages=iter([]))
    monkeypatch.setattr(service_module, "build_chat_model", Mock(return_value=model))
    services = build_services(settings_for(tmp_path, llm_model="fake"))
    assert isinstance(services.books.assistant, BookAssistant)
    assert services.books.assistant._signer is services.cocktails.assistant._signer


# --- Endpoints -----------------------------------------------------------------------------


def test_health_and_domains_report_books(client):
    health = client.get("/health").json()
    assert (health["books_enabled"], health["book_count"]) == (True, 10)
    assert health["domains"][2] == {
        "id": "books",
        "label": "Books",
        "enabled": True,
        "chat_enabled": False,
        "item_count": 10,
        "hint": CHAT_DISABLED,
    }


def test_catalog_endpoints(client):
    assert client.get("/books/genres").json()[0] == "Fantasy"
    assert client.get("/books/moods").json()[:2] == ["Cosy", "Dark"]
    index = client.get("/books/index").json()
    assert index[0] == {"book_id": 10, "title": "Twilight", "author": "Stephenie Meyer"}
    assert len(index) == 10
    book = client.get("/books/1").json()
    assert (book["title"], book["source_url"]) == (
        "The Name of the Wind",
        "https://www.goodreads.com/book/show/1001",
    )
    assert client.get("/books/404").status_code == 404


def test_search_because_similar_and_by_author(client):
    hits = client.post("/books/search", json={"query": "fantasy like the name of the wind"})
    assert [h["book"]["book_id"] for h in hits.json()] == [3, 4]
    because = client.post("/books/because", json={"liked": [1, 3], "exclude_genres": ["Horror"]})
    assert [h["book"]["book_id"] for h in because.json()] == [4]
    similar = client.get("/books/1/similar", params={"top_k": 2, "other_authors_only": True})
    assert [h["book"]["book_id"] for h in similar.json()] == [3, 4]
    assert client.get("/books/404/similar").status_code == 404
    more = client.get("/books/1/by-author").json()
    assert [h["book"]["book_id"] for h in more] == [2]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/books/search", {"query": "x" * 201}),
        ("/books/search", {"genres": ["x" * 61]}),
        ("/books/search", {"top_k": 0}),
        ("/books/search", {"min_rating": 6}),
        ("/books/because", {"liked": []}),
        ("/books/because", {"liked": [0]}),
    ],
)
def test_invalid_book_requests_are_422(client, path, body):
    assert client.post(path, json=body).status_code == 422


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/books/genres", None),
        ("GET", "/books/moods", None),
        ("GET", "/books/index", None),
        ("GET", "/books/1", None),
        ("GET", "/books/1/similar", None),
        ("GET", "/books/1/by-author", None),
        ("POST", "/books/search", {"query": "magic"}),
        ("POST", "/books/because", {"liked": [1]}),
        ("POST", "/chat", {"domain": "books", "message": "magic"}),
    ],
)
def test_book_endpoints_are_503_until_prepared(method, path, body):
    with TestClient(create_app(serve())) as client:
        response = client.request(method, path, json=body)
    assert response.status_code == 503
    assert response.json() == {"detail": BOOKS_MISSING}


def test_book_chat_without_llm_says_how_to_enable_it(client):
    response = client.post("/chat", json={"domain": "books", "message": "magic"})
    assert (response.status_code, response.json()) == (503, {"detail": CHAT_DISABLED})


def test_book_chat_streams_and_returns_book_cards(book_catalog, book_recommender):
    model = FakeToolCallingChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "SearchBooks", "args": {"query": "magic"}, "id": "t"}],
                ),
                AIMessage(content="Try **The Final Empire** [id 4]."),
            ]
        )
    )
    assistant = BookAssistant(
        model, book_catalog, BookRecommender(book_catalog, book_neighbours_frame())
    )
    services = serve(books=BookServices(book_catalog, book_recommender, assistant))
    with TestClient(create_app(services)) as client:
        body = {
            "domain": "books",
            "message": "magic",
            "history": [{"role": "assistant", "content": "Hi", "book_ids": [1]}],
            "book_preferences": {"avoided_authors": ["Patrick Rothfuss"]},
            "book_shortlist": [2],
            "auto_approve": ["SaveBooks", "RememberReadingTastes"],
        }
        with client.stream("POST", "/chat/stream", json=body) as response:
            events = [json.loads(line) for line in response.iter_lines() if line.strip()]
    reply = events[-1]["reply"]
    assert [e["step"]["name"] for e in events[:-1]][:2] == ["Understand request", "SearchBooks"]
    assert reply["domain"] == "books"
    assert [b["book_id"] for b in reply["books"]] == [4]
    assert reply["grounding"]["status"] == "verified"


def test_the_production_factory_serves_books(tmp_path, monkeypatch):
    write_books(tmp_path)
    monkeypatch.setattr(api_module, "get_settings", lambda: settings_for(tmp_path))
    with TestClient(create_app()) as client:
        assert client.get("/health").json()["book_count"] == 10
        assert client.get("/products/1").status_code == 503
