"""Regression tests for the v1.0 pre-release audit findings in the chat agent."""

import json

import pandas as pd
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from curio.app.assistants import CocktailAssistant, ShoppingAssistant
from curio.app.schemas import ActionDecision, ShopperPreferences, TurnContext
from curio.domains.cocktails.agent.facts import drink_facts
from curio.domains.cocktails.agent.grounding import CocktailGroundingCheck, measures_in
from curio.domains.cocktails.agent.prompts import COCKTAIL_ANSWER_PROMPT, alcohol_label
from curio.domains.cocktails.agent.toolkit import CocktailToolkit
from curio.domains.cocktails.catalog import CocktailCatalog
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.domains.fashion.models import ScoredProduct
from curio.engine import schemas as engine
from curio.engine.facts import render
from curio.engine.signing import ActionSigner, InvalidActionError, narrow_args

NO_SEARCH = {"query": "", "wants_products": False}


def action(name, args, call_id="tool_1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def drinks(model, catalog, **kwargs):
    return CocktailAssistant(model, catalog, CocktailRecommender(catalog), **kwargs)


def ids(reply):
    return [c.drink_id for c in reply.cocktails]


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


# --- F1: a resumed turn keeps the original filters and remembered preferences ------------


def test_resumed_search_keeps_remembered_budget(chat_model_replying, search, catalog):
    search.results = [ScoredProduct(1, 0.9)]
    model = chat_model_replying(
        {"query": "jeans"},
        action("SaveToShortlist", {"product_ids": [1]}),
        action("SearchCatalog", {"query": "dark jeans"}, "tool_2"),
        "Here is another option.",
    )
    assistant = ShoppingAssistant(model, search, catalog)
    context = TurnContext(preferences=ShopperPreferences(max_price=30))
    pending = assistant.reply("Save the jeans", [], context).pending_action
    assert search.calls[-1][2].max_price == 30

    reply = assistant.resume(ActionDecision(action=pending, approved=False), [], context)
    query, _, product_filter = search.calls[-1]
    assert query == "dark jeans"
    assert product_filter.max_price == 30
    assert reply.intent.max_price == 30


def test_resumed_search_keeps_filters_stated_in_the_original_request(
    chat_model_replying, search, catalog
):
    search.results = [ScoredProduct(3, 0.9)]
    model = chat_model_replying(
        {"query": "dress", "colours": ["Red"], "max_price": 25},
        action("SaveToShortlist", {"product_ids": [3]}),
        action("SearchCatalog", {"query": "summer dress"}, "tool_2"),
        "Here is another option.",
    )
    assistant = ShoppingAssistant(model, search, catalog)
    pending = assistant.reply("Save a red dress under 25 euros", []).pending_action

    assistant.resume(ActionDecision(action=pending, approved=False), [])
    product_filter = search.calls[-1][2]
    assert product_filter.colours == ("Red",) and product_filter.max_price == 25


def test_signed_intent_cannot_be_modified(chat_model_replying, search, catalog):
    model = chat_model_replying(
        {"query": "dress", "max_price": 25}, action("SaveToShortlist", {"product_ids": [3]})
    )
    assistant = ShoppingAssistant(model, search, catalog)
    pending = assistant.reply("Save a dress under 25 euros", []).pending_action
    assert pending.intent is not None and pending.intent.max_price == 25

    widened = pending.model_copy(
        update={"intent": pending.intent.model_copy(update={"max_price": 999})}
    )
    with pytest.raises(InvalidActionError):
        assistant.resume(ActionDecision(action=widened, approved=False), [])


# --- F3: auto-approved memory keeps the items already shown ------------------------------


def test_auto_approved_preferences_keep_the_shown_products(chat_model_replying, search, catalog):
    search.results = [ScoredProduct(1, 0.9)]
    model = chat_model_replying(
        {"query": "jeans", "sections": ["Menswear"]},
        action("RememberPreferences", {"sections": ["Menswear"]}),
        "Try the Slim Jeans [id 1] at €29.99.",
    )
    context = TurnContext(auto_approve=["RememberPreferences"])
    reply = ShoppingAssistant(model, search, catalog).reply(
        "I always shop menswear. Show me jeans", [], context
    )
    assert [p.product_id for p in reply.products] == [1]
    assert reply.grounding.status == "verified"
    assert reply.suggestions[0] == "What goes with the Slim Jeans?"


def test_auto_approved_tastes_keep_the_shown_drinks(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("SearchCocktails", {"query": "mojito"}),
        action("RememberTastes", {"liked_ingredients": ["mint"]}, "tool_2"),
        "The Mojito [id 1] and Virgin Mojito [id 3] both use mint.",
    )
    context = TurnContext(auto_approve=["RememberTastes"])
    reply = drinks(model, cocktail_catalog).reply("I love mint. A mojito?", [], context)
    assert ids(reply) == [1, 3]


# --- F5: preferences approved mid-turn apply to later searches in that turn -------------


def test_auto_approved_alcohol_free_applies_to_the_next_search(
    chat_model_replying, cocktail_catalog
):
    model = chat_model_replying(
        action("RememberTastes", {"alcohol_free": True}),
        action("SearchCocktails", {"query": "mojito"}, "tool_2"),
        "Try the Virgin Mojito [id 3].",
    )
    context = TurnContext(auto_approve=["RememberTastes"])
    reply = drinks(model, cocktail_catalog).reply("I don't drink alcohol. A mojito?", [], context)
    assert ids(reply) == [3]
    assert reply.steps[-3].detail["alcohol"] == "non_alcoholic"


def test_approved_tastes_apply_to_the_resumed_search(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("RememberTastes", {"alcohol_free": True}),
        action("SearchCocktails", {"query": "mojito"}, "tool_2"),
        "Try the Virgin Mojito [id 3].",
    )
    assistant = drinks(model, cocktail_catalog)
    pending = assistant.reply("I don't drink alcohol", []).pending_action
    reply = assistant.resume(ActionDecision(action=pending, approved=True), [])
    assert ids(reply) == [3]


def test_auto_approved_budget_applies_to_the_next_search(chat_model_replying, search, catalog):
    model = chat_model_replying(
        NO_SEARCH,
        action("RememberPreferences", {"max_price": 30}),
        action("SearchCatalog", {"query": "jeans"}, "tool_2"),
        "Here you go.",
    )
    context = TurnContext(auto_approve=["RememberPreferences"])
    ShoppingAssistant(model, search, catalog).reply("Keep it under €30. Jeans?", [], context)
    assert search.calls[-1][2].max_price == 30


# --- F7: the repair call respects the turn deadline --------------------------------------


def test_repair_is_skipped_when_the_time_budget_is_spent(chat_model_replying, search, catalog):
    clock = Clock()
    search.results = [ScoredProduct(1, 0.9)]
    model = chat_model_replying({"query": "jeans"}, "Only €12.50!", "never reached")
    assistant = ShoppingAssistant(model, search, catalog, clock=clock)
    calls = []
    agent = assistant._agent

    def slow(messages):
        calls.append(clock.now)
        clock.now += 95
        return agent.invoke(messages)

    assistant._agent = RunnableLambda(slow)
    reply = assistant.reply("jeans", [])
    assert len(calls) == 1
    assert reply.steps[-1].name == "Time budget"
    assert reply.grounding.status == "fallback"
    assert "Slim Jeans" in reply.text and "12.50" not in reply.text


# --- F4: glass and ingredient names are not drink mentions --------------------------------


def _mini_catalog():
    rows = [
        (1, "Martini", "Alcoholic", "Cocktail", "Martini Glass", [("Gin", "2 oz")]),
        (
            2,
            "Gimlet",
            "Alcoholic",
            "Ordinary Drink",
            "Martini Glass",
            [("Gin", "2 oz"), ("Lime juice", "1/2 oz")],
        ),
        (
            3,
            "Frozen Daiquiri",
            "Alcoholic",
            "Cocktail",
            "Margarita glass",
            [("Daiquiri mix", "2 oz")],
        ),
        (4, "Daiquiri", "Alcoholic", "Cocktail", "Cocktail glass", [("Light rum", "2 oz")]),
        (5, "Margarita", "Alcoholic", "Cocktail", "Margarita glass", [("Tequila", "2 oz")]),
        (6, "Gin Tonic", "Alcoholic", "Cocktail", "Highball glass", [("Gin", "2 oz")]),
    ]
    frame = pd.DataFrame(
        [
            {
                "drink_id": drink_id,
                "name": name,
                "alcoholic": alcoholic,
                "category": category,
                "glass": glass,
                "instructions": "Stir.",
                "image_url": None,
                "ingredients": json.dumps([{"name": n, "measure": m} for n, m in ingredients]),
            }
            for drink_id, name, alcoholic, category, glass, ingredients in rows
        ]
    )
    return CocktailCatalog(frame)


@pytest.mark.parametrize(
    ("text", "evidence", "status"),
    [
        ("The **Gimlet** [id 2] is served in a Martini Glass.", [2], "verified"),
        ("Serve the Gimlet in a martini glass.", [2], "verified"),
        ("The Frozen Daiquiri uses Daiquiri mix in a Margarita glass.", [3], "verified"),
        ("Or try a Martini.", [2], "fallback"),
        ("Or try a Gin Tonic.", [2], "fallback"),  # an ingredient never hides a drink
    ],
)
def test_glass_and_ingredient_names_are_not_drink_mentions(text, evidence, status):
    catalog = _mini_catalog()
    check = CocktailGroundingCheck(
        (c.name for c in catalog),
        catalog.ingredients(),
        glass_names=(c.glass for c in catalog),
    )
    assert check.check(text, catalog.get_many(evidence)).status == status


def test_cocktail_agent_accepts_an_answer_that_names_the_glass(chat_model_replying):
    catalog = _mini_catalog()
    model = chat_model_replying(
        action("InspectCocktails", {"drink_ids": [2]}),
        "The **Gimlet** [id 2] (contains alcohol): 2 oz gin, 1/2 oz lime juice, "
        "served in a Martini Glass.",
    )
    reply = drinks(model, catalog).reply("How do I make a gimlet?", [])
    assert reply.grounding.status == "verified"


# --- VAL-2: a zero denominator never crashes the grounding check --------------------------


@pytest.mark.parametrize("text", ["1/00 oz", "1 1/000 oz", "0/0 ml"])
def test_zero_denominators_are_skipped(text):
    assert measures_in(text) == []


def test_typed_zero_denominator_does_not_fail_the_turn(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("SearchCocktails", {"query": "mojito"}), "Try the Mojito [id 1]."
    )
    reply = drinks(model, cocktail_catalog).reply("I have 1/00 oz of rum", [])
    assert reply.grounding.status == "verified"


# --- F8 / VAL-4: malformed decisions are invalid actions, never crashes -------------------


@pytest.mark.parametrize(
    "edits",
    [
        {"drink_ids": [{"x": 1}]},
        {"drink_ids": [[1]]},
        {"drink_ids": "1"},
        {"note": {"x": 1}},
    ],
)
def test_malformed_edits_are_invalid_actions(edits):
    signer = ActionSigner(b"k" * 32)
    pending = signer.issue("SaveCocktails", {"drink_ids": [1, 3], "note": "x"}, "save")
    with pytest.raises(InvalidActionError):
        narrow_args(engine.ActionDecision(action=pending, approved=True, edited_args=edits))


@pytest.mark.parametrize("signature", ["é", "☃" * 64, ""])
def test_non_ascii_signatures_are_rejected(signature):
    signer = ActionSigner(b"k" * 32)
    pending = signer.issue("SaveCocktails", {"drink_ids": [1]}, "save")
    with pytest.raises(InvalidActionError):
        signer.verify(pending.model_copy(update={"signature": signature}))


# --- C6: pantry words are mapped to catalog ingredients -----------------------------------


def test_pantry_words_are_mapped_to_catalog_ingredients(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("WhatCanIMake", {"ingredients": ["gin", "tonic"], "max_missing": 1}),
        "You can make a Gin and Tonic [id 4] if you add a lime wedge.",
    )
    reply = drinks(model, cocktail_catalog).reply("What can I make with gin and tonic?", [])
    assert ids(reply)[0] == 4
    step = reply.steps[1]
    assert step.detail["ingredients"] == ["Gin", "Tonic water"]
    toolkit = CocktailToolkit(cocktail_catalog, CocktailRecommender(cocktail_catalog))
    # Unknown or ambiguous words, and alias keys, are passed through unchanged.
    assert toolkit._pantry_terms(["Soda", "unicorn", "lime"]) == ["Soda", "unicorn", "Lime"]


# --- Alcohol-free semantics: "Optional alcohol" is not alcohol-free -----------------------


def test_alcohol_optional_drinks_are_labelled_and_get_the_responsibility_note(cocktail_catalog):
    shirley = cocktail_catalog.get(8)
    assert alcohol_label(shirley) == "alcohol optional"
    toolkit = CocktailToolkit(cocktail_catalog, CocktailRecommender(cocktail_catalog))
    # The fallback states facts through references the engine renders from the catalog.
    raw = toolkit.fallback([shirley])
    assert "{drink:8} · {alcohol:drink:8}" in raw
    text = render(raw, drink_facts([shirley])).text
    assert "**Shirley Temple** · alcohol optional" in text
    assert text.endswith("Please drink responsibly.")


def test_prompt_says_alcohol_free_excludes_alcohol_optional(cocktail_catalog):
    system = COCKTAIL_ANSWER_PROMPT.messages[0].prompt.template
    assert "alcohol-optional drinks are not alcohol-free" in system
