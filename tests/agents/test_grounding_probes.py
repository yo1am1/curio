"""The thirteen false answers of the November 2026 agent audit (F1) and its AskShopper probe
(F6), run through the real assistants with a scripted model.

Each false answer is written twice (the draft and the self-correction), so a caught answer
ends as `fallback`: catalog facts replace it. Before grounding v2 every one of them was
labelled `verified` or `not_applicable`.
"""

import pytest
from langchain_core.messages import AIMessage

from curio.app.assistants import BookAssistant, CocktailAssistant, ShoppingAssistant
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.domains.fashion.models import ScoredProduct
from tests.agents.test_agent import FakeNavigator


def act(name, args, call_id="t1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


BOOK_PROBES = [
    (
        "invented out-of-catalog title with a borrowed rating",
        "Epic fantasy please",
        "You'll love **The Seven Moons of Maali Almeida** by Shehan Karunatilaka, rated 4.55 "
        "by readers.",
    ),
    (
        "rating misattributed between two evidence books",
        "Epic fantasy please",
        "**The Way of Kings** [id 3] is rated 4.55 on Goodreads.",
    ),
    (
        "a rating the reader typed, confirmed",
        "Is The Way of Kings rated 4.9? Epic fantasy please",
        "Yes, **The Way of Kings** [id 3] is rated 4.9.",
    ),
    (
        "a rating phrased outside the old pattern",
        "Epic fantasy please",
        "**The Way of Kings** [id 3] scores 4.9 on Goodreads, the highest in the catalog.",
    ),
    (
        "wrong author",
        "Epic fantasy please",
        "**The Way of Kings** [id 3] by Stephen King is a 300-page standalone from 1985.",
    ),
]

COCKTAIL_PROBES = [
    (
        "alcohol status flipped",
        "A rum drink with lime",
        "The **Mojito** [id 1] is alcohol-free, so it's safe for anyone, including kids.",
    ),
    (
        "an invented recipe for a drink outside the catalog, amounts borrowed",
        "How do I make a Paper Plane?",
        "A Paper Plane: 1 oz bourbon, 1 oz Aperol, 1 oz amaro and 1 oz lime juice, shaken.",
    ),
    (
        "allergen guarantee",
        "A rum drink with lime",
        "The **Daiquiri** [id 2] is completely nut-free and gluten-free.",
    ),
]

FASHION_PROBES = [
    (
        "prices swapped between two evidence items",
        "A red dress or jeans",
        "The **Summer Dress** is €29.99 and the Slim Jeans are €24.99.",
    ),
    (
        "a wrong price the shopper typed, confirmed",
        "My friend says the Summer Dress is €4.99, right?",
        "Yes, the **Summer Dress** is €4.99 right now.",
    ),
    ("a price in dollars", "A red dress", "The **Summer Dress** is just $9 today."),
    (
        "an invented discount equal to a price difference",
        "A red dress or jeans",
        "Good news: the Summer Dress is €5 off this week.",
    ),
    (
        "stock, size, material and shipping claims",
        "A red dress",
        "The **Summer Dress** is in stock in all sizes, 100% organic silk, ships tomorrow.",
    ),
]


@pytest.mark.parametrize(("label", "user", "answer"), BOOK_PROBES, ids=[p[0] for p in BOOK_PROBES])
def test_book_probes_are_caught(
    chat_model_replying, book_catalog, book_recommender, label, user, answer
):
    model = chat_model_replying(act("SearchBooks", {"query": "epic fantasy"}), answer, answer)
    reply = BookAssistant(model, book_catalog, book_recommender).reply(user, [])
    assert reply.grounding.status == "fallback", reply.grounding
    assert reply.text != answer


@pytest.mark.parametrize(
    ("label", "user", "answer"), COCKTAIL_PROBES, ids=[p[0] for p in COCKTAIL_PROBES]
)
def test_cocktail_probes_are_caught(chat_model_replying, cocktail_catalog, label, user, answer):
    model = chat_model_replying(act("SearchCocktails", {"query": "rum lime"}), answer, answer)
    assistant = CocktailAssistant(model, cocktail_catalog, CocktailRecommender(cocktail_catalog))
    reply = assistant.reply(user, [])
    assert reply.grounding.status == "fallback", reply.grounding
    assert reply.text != answer


@pytest.mark.parametrize(
    ("label", "user", "answer"), FASHION_PROBES, ids=[p[0] for p in FASHION_PROBES]
)
def test_fashion_probes_are_caught(chat_model_replying, search, catalog, label, user, answer):
    search.results = [ScoredProduct(3, 0.9), ScoredProduct(1, 0.8)]
    model = chat_model_replying({"query": "red dress"}, answer, answer)
    reply = ShoppingAssistant(model, search, catalog, navigator=FakeNavigator()).reply(user, [])
    assert reply.grounding.status == "fallback", reply.grounding
    assert reply.text != answer


def test_the_probes_name_what_is_wrong(chat_model_replying, search, catalog):
    search.results = [ScoredProduct(3, 0.9), ScoredProduct(1, 0.8)]
    answer = FASHION_PROBES[0][2]
    model = chat_model_replying({"query": "red dress"}, answer, answer)
    reply = ShoppingAssistant(model, search, catalog, navigator=FakeNavigator()).reply("x", [])
    assert reply.grounding.issues == [
        "€29.99 does not match the price of Summer Dress (€24.99)",
        "€24.99 does not match the price of Slim Jeans (€29.99)",
    ]


def test_an_unverified_question_is_asked_again_then_replaced(chat_model_replying, cocktail_catalog):
    question = {"question": "The Mojito is alcohol-free and €2; want it?", "options": ["Yes", "No"]}
    model = chat_model_replying(act("AskShopper", question), act("AskShopper", question, "t2"))
    assistant = CocktailAssistant(model, cocktail_catalog, CocktailRecommender(cocktail_catalog))
    reply = assistant.reply("I want a drink", [])
    assert reply.status == "needs_input"
    assert reply.text == "Could you tell me a little more about what you're looking for?"
    assert reply.clarification.options == []
    assert reply.grounding.status == "fallback"
    assert [s.name for s in reply.steps][-2:] == ["Question grounding", "Question grounding"]


def test_a_rewritten_question_is_repaired(chat_model_replying, cocktail_catalog):
    wrong = {"question": "The Mojito is alcohol-free; want it?", "options": ["Yes", "No"]}
    right = {"question": "Would you like the Mojito?", "options": ["Yes", "No"]}
    model = chat_model_replying(
        act("SearchCocktails", {"query": "mojito"}),
        act("AskShopper", wrong, "t2"),
        act("AskShopper", right, "t3"),
    )
    assistant = CocktailAssistant(model, cocktail_catalog, CocktailRecommender(cocktail_catalog))
    reply = assistant.reply("I want a mojito", [])
    assert reply.status == "needs_input" and reply.text == "Would you like the Mojito?"
    assert reply.grounding.status == "repaired"
    assert reply.grounding.issues == ["Mojito is not alcohol-free"]


def test_a_grounded_question_carries_its_report(chat_model_replying, cocktail_catalog):
    question = {"question": "Shall I find drinks like the Mojito?", "options": ["Yes", "No"]}
    model = chat_model_replying(
        act("SearchCocktails", {"query": "mojito"}), act("AskShopper", question, "t2")
    )
    assistant = CocktailAssistant(model, cocktail_catalog, CocktailRecommender(cocktail_catalog))
    reply = assistant.reply("I want a mojito", [])
    assert reply.status == "needs_input"
    assert reply.grounding.status == "verified"
    assert reply.grounding.counts == {"name": 1}
