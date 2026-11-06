"""Book → cocktail pairing served: registry wiring and POST /pairings/book-cocktail."""

from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from curio.app import services as service_module
from curio.app.api import create_app
from curio.app.config import Settings
from curio.app.services import (
    BOOKS_MISSING,
    COCKTAILS_MISSING,
    BookServices,
    CocktailServices,
    build_services,
)
from curio.domains.books.recommend import BookRecommender
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.domains.pairings.pairer import BookCocktailPairer
from tests.agents.conftest import FakeToolCallingChatModel
from tests.conftest import book_frame, book_neighbours_frame, cocktail_frame, serve
from tests.domains.pairings.conftest import pairing_books, pairing_cocktails

PATH = "/pairings/book-cocktail"


def settings_for(tmp_path, **overrides) -> Settings:
    values = {"data_dir": tmp_path, "qdrant_path": tmp_path / ".qdrant", "llm_model": None}
    return Settings(_env_file=None, **(values | overrides))


def runtimes() -> tuple[BookServices, CocktailServices]:
    books, cocktails = pairing_books(), pairing_cocktails()
    recommender = CocktailRecommender(cocktails)
    pairer = BookCocktailPairer(books, cocktails, recommender)
    book_runtime = BookServices(
        books, BookRecommender(books, book_neighbours_frame()), pairing=pairer
    )
    return book_runtime, CocktailServices(cocktails, recommender)


@pytest.fixture
def client() -> Iterator[TestClient]:
    books, cocktails = runtimes()
    with TestClient(create_app(serve(books=books, cocktails=cocktails))) as client:
        yield client


# --- Wiring ------------------------------------------------------------------------------------


def test_books_and_cocktails_together_enable_pairing(tmp_path, monkeypatch):
    book_frame().to_parquet(tmp_path / "books.parquet")
    book_neighbours_frame().to_parquet(tmp_path / "book_neighbours.parquet")
    cocktail_frame().to_parquet(tmp_path / "cocktails.parquet")
    model = FakeToolCallingChatModel(messages=iter([]))
    monkeypatch.setattr(service_module, "build_chat_model", Mock(return_value=model))
    services = build_services(settings_for(tmp_path, llm_model="fake"))
    assert isinstance(services.pairing, BookCocktailPairer)
    assert services.books.pairing is services.pairing
    # The book agent gets PairCocktail with the shared recipes.
    assert services.books.assistant.toolkit._pairing._pairer is services.pairing
    # Books alone serve no pairing (and the book agent's PairCocktail says why).
    alone = build_services(settings_for(tmp_path, enabled_domains=("books",)))
    assert alone.pairing is None and alone.books.pairing is None


def test_a_pairer_that_fails_to_build_leaves_both_domains_serving(tmp_path, monkeypatch, caplog):
    book_frame().to_parquet(tmp_path / "books.parquet")
    book_neighbours_frame().to_parquet(tmp_path / "book_neighbours.parquet")
    cocktail_frame().to_parquet(tmp_path / "cocktails.parquet")
    monkeypatch.setattr(service_module, "BookCocktailPairer", Mock(side_effect=ValueError("x")))
    services = build_services(settings_for(tmp_path))
    assert services.books is not None and services.cocktails is not None
    assert services.pairing is None
    assert "pairing" in caplog.text


# --- Endpoint ----------------------------------------------------------------------------------


def test_pairs_one_book_with_reasons(client):
    response = client.post(PATH, json={"book_id": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["source"] == "The Big Sleep"
    assert body["profile"]["themes"][0]["id"] == "noir"
    assert len(body["pairings"]) == 3
    first = body["pairings"][0]
    assert first["cocktail"]["name"] == "Manhattan"
    assert first["reasons"][0].startswith("The Big Sleep (Mystery · shelved as noir")
    assert body["note"].startswith("Please drink responsibly.")
    assert body["sets"] >= 2 and body["seed"] == 0
    another = client.post(PATH, json={"book_id": 1, "seed": 1}).json()
    assert {p["cocktail"]["drink_id"] for p in another["pairings"]}.isdisjoint(
        p["cocktail"]["drink_id"] for p in body["pairings"]
    )


def test_pairs_a_reader_taste_and_honours_alcohol_free(client):
    body = {
        "book_ids": [1, 2, 8],
        "liked_genres": ["Fantasy"],
        "avoided_genres": ["Horror"],
        "cocktail_preferences": {"alcohol_free": True, "avoided_ingredients": ["coffee"]},
        "top_k": 2,
    }
    response = client.post(PATH, json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["profile"]["source"] == "Your reading list (3 books)"
    assert "horror" not in {t["id"] for t in result["profile"]["themes"]}
    assert result["profile"]["alcohol_free_reason"] == (
        "your remembered cocktail taste is alcohol-free"
    )
    assert result["pairings"]
    for item in result["pairings"]:
        assert item["cocktail"]["alcoholic"] == "Non alcoholic"
        assert "Coffee" not in {i["name"] for i in item["cocktail"]["ingredients"]}
    # When no allowed recipe fits, the answer is empty rather than a drink without a reason.
    body["cocktail_preferences"]["avoided_ingredients"] = ["milk"]
    assert client.post(PATH, json=body).json()["pairings"] == []


def test_a_young_adult_book_is_alcohol_free_even_when_not_asked(client):
    result = client.post(PATH, json={"book_id": 6, "alcohol_free": False}).json()
    assert result["profile"]["alcohol_free"]
    assert all(p["cocktail"]["alcoholic"] == "Non alcoholic" for p in result["pairings"])


def test_unknown_book_is_404_and_nothing_known_is_422(client):
    assert client.post(PATH, json={"book_id": 999}).status_code == 404
    response = client.post(PATH, json={"book_ids": [998]})
    assert response.status_code == 422
    assert "None of those books" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"book_id": 1, "seed": 10_001},
        {"book_id": 1, "top_k": 9},
        {"book_ids": list(range(1, 30))},
        {"book_id": 1, "liked_genres": ["Fantasy"] * 11},
        {"book_id": 1, "cocktail_preferences": {"avoided_ingredients": ["x" * 61]}},
        {"book_id": 1, "extra": "field"},
        {"book_id": "one"},
    ],
)
def test_invalid_pairing_requests_are_422(client, body):
    assert client.post(PATH, json=body).status_code == 422


@pytest.mark.parametrize(
    ("served", "hint"), [("books", COCKTAILS_MISSING), ("cocktails", BOOKS_MISSING)]
)
def test_pairing_is_503_with_the_missing_domains_hint(served, hint):
    books, cocktails = runtimes()
    services = serve(books=books) if served == "books" else serve(cocktails=cocktails)
    with TestClient(create_app(services)) as client:
        response = client.post(PATH, json={"book_id": 1})
    assert (response.status_code, response.json()) == (503, {"detail": hint})
