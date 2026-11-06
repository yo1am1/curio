"""Plan my evening served: the registry entry, POST /evening/plan, /evening/swap and chat."""

from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from curio.app import services as service_module
from curio.app.api import CHAT_DISABLED, create_app
from curio.app.assistants import CocktailAssistant, EveningAssistant
from curio.app.config import Settings
from curio.app.schemas import ChatRequest, TurnContext
from curio.app.services import (
    BOOKS_MISSING,
    COCKTAILS_MISSING,
    EVENING_MISSING,
    BookServices,
    CocktailServices,
    EveningServices,
    Services,
    build_services,
    compose_evening,
)
from curio.domains.books.recommend import BookRecommender
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.domains.evening.models import EveningRequest
from curio.domains.evening.planner import OUTFIT_NEEDS_FASHION
from curio.domains.pairings.pairer import BookCocktailPairer
from tests.agents.conftest import FakeToolCallingChatModel
from tests.conftest import book_frame, book_neighbours_frame, cocktail_frame
from tests.domains.evening.conftest import make_planner
from tests.domains.pairings.conftest import pairing_books, pairing_cocktails


def settings_for(tmp_path, **overrides) -> Settings:
    values = {"data_dir": tmp_path, "qdrant_path": tmp_path / ".qdrant", "llm_model": None}
    return Settings(_env_file=None, **(values | overrides))


def prepare(tmp_path, *, books=True, cocktails=True) -> None:
    if books:
        book_frame().to_parquet(tmp_path / "books.parquet")
        book_neighbours_frame().to_parquet(tmp_path / "book_neighbours.parquet")
    if cocktails:
        cocktail_frame().to_parquet(tmp_path / "cocktails.parquet")


def runtimes():
    books, drinks = pairing_books(), pairing_cocktails()
    recommender = CocktailRecommender(drinks)
    pairer = BookCocktailPairer(books, drinks, recommender)
    book_runtime = BookServices(
        books, BookRecommender(books, book_neighbours_frame()), pairing=pairer
    )
    return book_runtime, CocktailServices(drinks, recommender)


def evening_services(*, fashion: bool = True, assistant=None) -> Services:
    books, cocktails = runtimes()
    evening = EveningServices(planner=make_planner(fashion=fashion), assistant=assistant)
    return Services(domains={"cocktails": cocktails, "books": books, "evening": evening})


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(evening_services())) as client:
        yield client


def evening_info(client) -> dict:
    return next(d for d in client.get("/domains").json() if d["id"] == "evening")


# --- Registry --------------------------------------------------------------------------------


def test_books_and_cocktails_compose_the_evening_even_when_enabled_domains_omit_it(
    tmp_path, monkeypatch
):
    prepare(tmp_path)
    model = FakeToolCallingChatModel(messages=iter([]))
    monkeypatch.setattr(service_module, "build_chat_model", Mock(return_value=model))
    settings = settings_for(tmp_path, llm_model="fake", enabled_domains="cocktails,books")
    services = build_services(settings)
    evening = services.evening
    assert isinstance(evening, EveningServices)
    assert evening.planner.parts == ["book", "drink"]
    assert evening.assistant is not None
    assert list(services.domains) == ["cocktails", "books", "evening"]
    with TestClient(create_app(services)) as client:
        assert evening_info(client) == {
            "id": "evening",
            "label": "Evening",
            "enabled": True,
            "chat_enabled": True,
            "item_count": 2,
            "hint": OUTFIT_NEEDS_FASHION,
        }


def test_without_an_llm_the_hint_names_chat_and_the_missing_outfit(tmp_path):
    prepare(tmp_path)
    with TestClient(create_app(build_services(settings_for(tmp_path)))) as client:
        info = evening_info(client)
        assert (info["enabled"], info["chat_enabled"]) == (True, False)
        assert info["hint"] == f"{CHAT_DISABLED} {OUTFIT_NEEDS_FASHION}"


@pytest.mark.parametrize(
    ("books", "cocktails", "missing"),
    [(True, False, COCKTAILS_MISSING), (False, True, BOOKS_MISSING)],
)
def test_with_one_part_missing_the_evening_is_503_and_says_which(
    tmp_path, books, cocktails, missing
):
    prepare(tmp_path, books=books, cocktails=cocktails)
    services = build_services(settings_for(tmp_path))
    assert services.evening is None
    hint = EVENING_MISSING.format(missing=missing)
    with TestClient(create_app(services)) as client:
        assert evening_info(client)["hint"] == hint
        response = client.post("/evening/plan", json={"occasion": "date night"})
        assert (response.status_code, response.json()) == (503, {"detail": hint})
        chat = client.post("/chat", json={"message": "plan it", "domain": "evening"})
        assert (chat.status_code, chat.json()) == (503, {"detail": hint})


def test_a_failed_pairer_leaves_no_evening(tmp_path, monkeypatch):
    prepare(tmp_path)
    monkeypatch.setattr(service_module, "BookCocktailPairer", Mock(side_effect=ValueError("x")))
    services = build_services(settings_for(tmp_path))
    assert services.books is not None and services.evening is None
    assert "pairing could not be built" in service_module.evening_missing(services)


def test_compose_needs_books_and_cocktails():
    books, cocktails = runtimes()
    assert compose_evening({"books": books}, None) is None
    assert compose_evening({"cocktails": cocktails}, None) is None
    assert compose_evening({"books": books, "cocktails": cocktails}, None) is not None


# --- POST /evening/plan ----------------------------------------------------------------------


def test_plan_returns_three_parts_with_reasons_and_state(client):
    body = client.post("/evening/plan", json={"occasion": "rainy Sunday in"}).json()
    assert body["theme"]["id"] == "cosy" and body["status"] == "complete"
    assert [s["slot"] for s in body["outfit"]["outfit"]["slots"]] == ["top", "bottom", "outerwear"]
    assert body["book"]["book"]["title"] == "The Name of the Wind"
    assert body["drink"]["cocktail"]["name"] == "Honey Bee"
    assert body["why"].startswith("Rainy Sunday in → cosy: Chunky Knit Sweater (€24.99)")
    assert body["state"]["book_id"] == 4 and body["state"]["drink_id"] == 9
    assert body["change"] is None


def test_plan_honours_budget_alcohol_free_and_tastes(client):
    body = client.post(
        "/evening/plan",
        json={
            "occasion": "murder mystery night",
            "budget": 70,
            "alcohol_free": True,
            "book_preferences": {"avoided_genres": ["Thriller"]},
        },
    ).json()
    assert body["outfit"]["outfit"]["total"] <= 70
    assert body["drink"]["cocktail"]["alcoholic"] == "Non alcoholic"
    assert "Thriller" not in body["book"]["book"]["genres"]


def test_plan_without_fashion_is_partial_not_an_error():
    with TestClient(create_app(evening_services(fashion=False))) as client:
        response = client.post("/evening/plan", json={"occasion": "rainy Sunday in"})
        assert response.status_code == 200
        body = response.json()
        assert body["outfit"] is None and body["status"] == "partial"
        assert body["missing"] == [{"part": "outfit", "reason": OUTFIT_NEEDS_FASHION}]
        assert body["book"] and body["drink"]


@pytest.mark.parametrize(
    "body",
    [
        {"occasion": "x"},
        {"occasion": "   "},
        {"occasion": "a" * 201},
        {"occasion": "date night", "budget": 0},
        {"occasion": "date night", "budget": 5001},
        {"occasion": "date night", "audience": "kids"},
        {"occasion": "date night", "mood": "m" * 41},
        {"occasion": "date night", "surprise": True},
        {"occasion": "date night", "cocktail_preferences": {"avoided_ingredients": ["x"] * 11}},
    ],
)
def test_plan_requests_are_tightly_bounded(client, body):
    assert client.post("/evening/plan", json=body).status_code == 422


# --- POST /evening/swap ----------------------------------------------------------------------


def plan_state(client, occasion="rainy Sunday in") -> dict:
    return client.post("/evening/plan", json={"occasion": occasion}).json()["state"]


def test_swap_another_book_re_pairs_the_drink(client):
    state = plan_state(client)
    body = client.post("/evening/swap", json={"evening": state, "part": "book"}).json()
    assert body["book"]["book"]["book_id"] != 4
    assert body["state"]["skipped_books"] == [4]
    assert body["change"].startswith("Another book: The Name of the Wind → ")


def test_swap_cheaper_piece_and_alcohol_free_drink(client):
    state = plan_state(client)
    cheaper = client.post(
        "/evening/swap",
        json={"evening": state, "part": "outfit", "slot": "top", "mode": "cheaper"},
    ).json()
    assert cheaper["outfit"]["outfit"]["slots"][0]["product"]["price"] < 24.99
    free = client.post(
        "/evening/swap", json={"evening": state, "part": "drink", "mode": "alcohol_free"}
    ).json()
    assert free["drink"]["cocktail"]["alcoholic"] == "Non alcoholic"
    assert free["state"]["alcohol_free"] is True


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"part": "book", "mode": "cheaper"}, 422),
        ({"part": "outfit"}, 422),
        ({"part": "outfit", "slot": "dress"}, 422),
        ({"part": "hat"}, 422),
        ({"part": "book", "refine": "r" * 101}, 422),
    ],
)
def test_invalid_swaps_are_422(client, change, status):
    state = plan_state(client)
    assert client.post("/evening/swap", json={"evening": state, **change}).status_code == status


def test_a_tampered_state_is_bounded_or_re_read(client):
    state = plan_state(client)
    bad = {**state, "skipped_books": list(range(1, 30))}
    assert client.post("/evening/swap", json={"evening": bad, "part": "book"}).status_code == 422
    forged = {**state, "drink_id": 18}  # Zombie, which the pairer never offers
    body = client.post(
        "/evening/swap", json={"evening": forged, "part": "outfit", "slot": "top"}
    ).json()
    assert body["drink"]["cocktail"]["drink_id"] != 18


# --- Chat --------------------------------------------------------------------------------------


def test_evening_chat_replies_with_the_plan_and_other_domains_keep_their_format():
    plan_call = AIMessage(
        content="",
        tool_calls=[{"name": "PlanEvening", "args": {"occasion": "rainy Sunday in"}, "id": "c1"}],
    )
    model = FakeToolCallingChatModel(
        messages=iter([plan_call, "A cosy evening.", "Here it is again."])
    )
    books, cocktails = runtimes()
    assistant = EveningAssistant(
        model, make_planner(), books=books.catalog, cocktails=cocktails.catalog
    )
    services = evening_services(assistant=assistant)
    with TestClient(create_app(services)) as client:
        response = client.post("/chat", json={"message": "rainy Sunday in", "domain": "evening"})
        assert response.status_code == 200
        body = response.json()
        assert body["domain"] == "evening"
        assert body["evening"]["theme"]["id"] == "cosy"
        assert list(body)[-1] == "evening"
        assert [b["book_id"] for b in body["books"]] == [4]
        lines = client.post(
            "/chat/stream",
            json={"message": "x", "domain": "evening", "evening": body["evening"]["state"]},
        )
        assert lines.status_code == 200
        assert '"type":"reply"' in lines.text and '"domain":"evening"' in lines.text
    drinks = FakeToolCallingChatModel(messages=iter(["Cheers."]))
    cocktail_services = Services(
        domains={
            "cocktails": CocktailServices(
                cocktails.catalog,
                cocktails.recommender,
                CocktailAssistant(drinks, cocktails.catalog, cocktails.recommender),
            )
        }
    )
    with TestClient(create_app(cocktail_services)) as client:
        body = client.post("/chat", json={"message": "mojito", "domain": "cocktails"}).json()
        assert "evening" not in body
        assert list(body)[-2:] == ["outfit", "pairing"]


def test_evening_chat_without_an_llm_says_how_to_enable_it(client):
    response = client.post("/chat", json={"message": "plan it", "domain": "evening"})
    assert (response.status_code, response.json()) == (503, {"detail": CHAT_DISABLED})


def test_the_evening_state_travels_in_the_chat_context():
    state = make_planner().plan(EveningRequest(occasion="rainy Sunday in")).state
    request = ChatRequest.model_validate(
        {"message": "another book", "domain": "evening", "evening": state.model_dump(mode="json")}
    )
    assert request.evening == state
    assert TurnContext(auto_approve=["SaveEvening"]).auto_approve == ["SaveEvening"]


@pytest.mark.parametrize(
    ("occasion", "budget"),
    [
        ("date night under €0", None),
        ("gala under €6000", None),
        ("dinner under €10000", None),
        ("date night under €99.50", 99.5),
        ("date night under €5,000", 5000),
    ],
)
def test_a_budget_in_the_occasion_never_crashes_the_plan(client, occasion, budget):
    response = client.post("/evening/plan", json={"occasion": occasion})
    assert response.status_code == 200
    body = response.json()
    assert body["state"]["budget"] == budget
    if budget is None:
        assert body["notes"][0].startswith("Planned without a budget: €")
