"""Cocktail agent: tools, remembered tastes, approvals, grounding and the shared engine."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from curio.chat.assistant import ShoppingAssistant
from curio.chat.cocktail_agent import EXAMPLE_PANTRY, CocktailAssistant
from curio.chat.engine import MAX_TOOL_CALLS
from curio.chat.guardrails import (
    ActionSigner,
    CocktailGroundingCheck,
    InvalidActionError,
    measures_in,
    narrow_args,
)
from curio.chat.schemas import (
    ActionDecision,
    AppliedEffect,
    ChatTurn,
    ReplyEvent,
    StepEvent,
    TurnContext,
)
from curio.cocktails.domain import CocktailPreferences
from curio.cocktails.recommend import CocktailRecommender

NO_SEARCH = {"query": "", "wants_products": False}


def action(name, args, call_id="tool_1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def build(model, cocktail_catalog, **kwargs):
    return CocktailAssistant(
        model, cocktail_catalog, CocktailRecommender(cocktail_catalog), **kwargs
    )


def record(assistant):
    """Capture every message list the agent model sees."""
    seen = []
    model = assistant._agent

    def invoke(messages):
        seen.append(list(messages))
        return model.invoke(messages)

    assistant._agent = RunnableLambda(invoke)
    return seen


def tool_results(seen):
    return [m.content for m in seen[-1] if isinstance(m, ToolMessage)]


def ids(reply):
    return [c.drink_id for c in reply.cocktails]


def tastes(**fields):
    return TurnContext(cocktail_preferences=CocktailPreferences(**fields))


# --- Read tools and the trace ------------------------------------------------------------


def test_stream_searches_then_verifies_a_grounded_answer(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("SearchCocktails", {"query": "mojito"}),
        "Try the **Mojito** [id 1] (contains alcohol): 2 oz light rum and 1 oz lime juice.",
    )
    events = list(build(model, cocktail_catalog).stream("A mojito please", []))

    assert all(isinstance(event, StepEvent) for event in events[:-1])
    assert isinstance(events[-1], ReplyEvent)
    names = [event.step.name for event in events[:-1]]
    assert names == ["Understand request", "SearchCocktails", "Draft answer", "Grounding check"]
    reply = events[-1].reply
    assert reply.domain == "cocktails" and reply.products == []
    assert ids(reply) == [1, 3]
    assert reply.intent.query == "A mojito please"
    assert reply.grounding.status == "verified" and reply.grounding.checked == 4
    assert reply.steps[0].summary == "No remembered filters to apply."
    assert reply.steps[-1].summary == "Verified 4 drinks, IDs and amounts against the catalog."
    assert reply.suggestions == [
        "Something similar to Mojito",
        "Make it alcohol-free",
        EXAMPLE_PANTRY,
        "Save Mojito",
    ]


def test_remembered_tastes_fill_alcohol_and_exclude_avoided_ingredients(
    chat_model_replying, cocktail_catalog
):
    model = chat_model_replying(
        action("SearchCocktails", {"ingredients": ["Sugar"]}), "Here are some drinks."
    )
    reply = build(model, cocktail_catalog).reply(
        "Something sweet", [], tastes(alcohol_free=True, avoided_ingredients=["mint"])
    )

    # Virgin Mojito is alcohol-free but contains mint; Daiquiri and Mojito contain alcohol.
    assert ids(reply) == [6]
    plan, search = reply.steps[0], reply.steps[1]
    assert plan.kind == "plan"
    assert plan.summary == (
        "Applying remembered tastes to every search: alcohol-free drinks only; avoiding Mint."
    )
    assert plan.detail["from_memory"] == ["alcohol", "exclude_ingredients"]
    assert search.detail["alcohol"] == "non_alcoholic"
    assert search.detail["exclude_ingredients"] == ["Mint"]
    assert search.detail["from_memory"] == ["alcohol", "exclude_ingredients"]
    assert "Make it alcohol-free" not in reply.suggestions


def test_explicit_alcohol_filter_is_not_overridden_by_memory(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("SearchCocktails", {"query": "mojito", "alcohol": "alcoholic"}), "Here you go."
    )
    reply = build(model, cocktail_catalog).reply("A real mojito", [], tastes(alcohol_free=True))
    assert ids(reply) == [1]
    assert reply.steps[1].detail["from_memory"] == []


def test_unknown_remembered_ingredients_are_dropped_from_context(
    chat_model_replying, cocktail_catalog
):
    model = chat_model_replying(action("SearchCocktails", {"query": "mojito"}), "Here you go.")
    reply = build(model, cocktail_catalog).reply(
        "Mojito", [], tastes(avoided_ingredients=["unicorn tears"], liked_ingredients=["lime"])
    )
    assert ids(reply) == [1, 3]
    # Values are mapped to the catalog spelling; ones matching no recipe are dropped.
    assert reply.steps[0].summary == "No remembered filters to apply. Likes Lime."


def test_search_category_is_matched_case_insensitively_or_rejected(
    chat_model_replying, cocktail_catalog
):
    model = chat_model_replying(
        action("SearchCocktails", {"category": "soft drink"}),
        action("SearchCocktails", {"category": "Smoothie"}, "tool_2"),
        "Here you go.",
    )
    assistant = build(model, cocktail_catalog)
    seen = record(assistant)
    reply = assistant.reply("Soft drinks", [])

    found, unknown = tool_results(seen)
    assert "[id 6] Lemonade" in found and "[id 8] Shirley Temple" in found
    assert unknown.startswith("Unknown category 'Smoothie'. Known categories: Cocktail")
    assert [step.status for step in reply.steps[1:3]] == ["ok", "error"]


def test_inspect_reads_full_recipes_with_method(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("InspectCocktails", {"drink_ids": [5, 5, 999]}),
        "The Margarita uses 1 1/2 oz tequila, 1/2 oz triple sec and 1 oz lime juice.",
    )
    assistant = build(model, cocktail_catalog)
    seen = record(assistant)
    reply = assistant.reply("How do I make a margarita?", [])

    (result,) = tool_results(seen)
    assert "Tequila (1 1/2 oz)" in result and "Salt" in result
    assert "method: Build the Margarita in a cocktail glass." in result
    assert ids(reply) == [5]
    assert reply.grounding.status == "verified"


def test_similar_cocktails_respect_avoided_ingredients(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("SimilarCocktails", {"drink_id": 1}),
        action("SimilarCocktails", {"drink_id": 1}, "tool_2"),
        "Here are similar drinks.",
    )
    plain = build(model, cocktail_catalog).reply("Like a mojito", [])
    assert 2 in ids(plain)  # Daiquiri shares light rum, lime juice and sugar

    model = chat_model_replying(action("SimilarCocktails", {"drink_id": 1}), "Here you go.")
    avoiding = build(model, cocktail_catalog).reply(
        "Like a mojito", [], tastes(avoided_ingredients=["rum"])
    )
    assert ids(avoiding) and 2 not in ids(avoiding)
    assert avoiding.steps[1].summary.endswith("similar to Mojito.")
    assert avoiding.steps[1].detail["from_memory"] == ["exclude_ingredients"]


def test_similar_to_an_unknown_drink_is_a_tool_error(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(action("SimilarCocktails", {"drink_id": 999}), "Sorry.")
    reply = build(model, cocktail_catalog).reply("Like drink 999", [])
    assert reply.steps[1].status == "error"
    assert reply.steps[1].summary == "Drink 999 is not in the catalog."


def test_what_can_i_make_lists_have_and_missing(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action(
            "WhatCanIMake",
            {"ingredients": ["lime", "mint", "sugar", "soda"], "max_missing": 0},
        ),
        "You can make a Virgin Mojito (alcohol-free).",
    )
    assistant = build(model, cocktail_catalog)
    seen = record(assistant)
    reply = assistant.reply(
        "What can I make with lime, mint, sugar and soda?", [], tastes(alcohol_free=True)
    )

    assert ids(reply) == [3]
    (result,) = tool_results(seen)
    assert "You have Lime juice, Mint, Sugar, Soda water" in result
    assert "Nothing missing" in result
    step = reply.steps[1]
    assert step.name == "WhatCanIMake"
    assert step.detail["alcohol"] == "non_alcoholic" and step.detail["max_missing"] == 0
    assert reply.grounding.status == "verified"


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("SearchCocktails", {}),
        ("SearchCocktails", {"query": "x", "exclude_ingredients": ["gin"]}),
        ("SearchCocktails", {"query": "x", "alcohol": "some"}),
        ("InspectCocktails", {"drink_ids": []}),
        ("InspectCocktails", {"drink_ids": list(range(1, 7))}),
        ("WhatCanIMake", {"ingredients": []}),
        ("WhatCanIMake", {"ingredients": ["lime"], "max_missing": 4}),
        ("WhatCanIMake", {"ingredients": [f"i{n}" for n in range(16)]}),
        ("SaveCocktails", {"drink_ids": [0]}),
        ("RememberTastes", {}),
        ("SaveToShortlist", {"product_ids": [1]}),  # a fashion tool
    ],
)
def test_invalid_cocktail_actions_return_a_tool_error(
    name, args, chat_model_replying, cocktail_catalog
):
    model = chat_model_replying(action(name, args), "Please clarify.")
    assistant = build(model, cocktail_catalog)
    seen = record(assistant)
    reply = assistant.reply("Help", [])
    assert reply.status == "complete" and reply.pending_action is None
    (result,) = tool_results(seen)
    assert result.startswith("Invalid action. Use SearchCocktails")


def test_tool_budget_is_shared_with_the_fashion_agent(chat_model_replying, cocktail_catalog):
    batch = AIMessage(
        content="",
        tool_calls=[
            {"name": "SearchCocktails", "args": {"query": "mojito"}, "id": f"call_{i}"}
            for i in range(MAX_TOOL_CALLS + 1)
        ],
    )
    reply = build(chat_model_replying(batch), cocktail_catalog).reply("Mojitos", [])
    assert reply.steps[-1].name == "Tool budget"
    assert reply.text.startswith("Here are the drinks I could confirm:\n- Mojito")
    assert reply.text.endswith("Please drink responsibly.")


def test_ask_shopper_pauses_the_cocktail_turn(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("AskShopper", {"question": "With or without alcohol?", "options": ["With", "No"]})
    )
    reply = build(model, cocktail_catalog).reply("A drink for tonight", [])
    assert reply.status == "needs_input"
    assert reply.clarification.options == ["With", "No"]
    assert reply.suggestions == []


# --- Approvals ---------------------------------------------------------------------------


def test_save_cocktails_requires_approval_then_resumes(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("SaveCocktails", {"drink_ids": [1, 3, 999]}), "Saved the Mojito for you."
    )
    assistant = build(model, cocktail_catalog)
    proposal = assistant.reply("Keep both mojitos", [])

    assert proposal.status == "needs_approval"
    pending = proposal.pending_action
    assert pending.tool == "SaveCocktails" and pending.args == {"drink_ids": [1, 3, 999]}
    assert [c.drink_id for c in pending.cocktails] == [1, 3] and pending.products == []
    assert proposal.text.startswith("I'd like to save these drinks")
    assert not proposal.applied

    history = [ChatTurn(role="assistant", content=proposal.text, drink_ids=[1, 3])]
    decision = ActionDecision(action=pending, approved=True, edited_args={"drink_ids": [1]})
    done = assistant.resume(decision, history)

    assert done.status == "complete"
    assert [e.model_dump(exclude_none=True) for e in done.applied] == [
        {"kind": "cocktail_shortlist", "drink_ids": [1]}
    ]
    assert done.steps[0].summary == "Approved: Saved 1 to the cocktail shortlist: Mojito."
    assert done.domain == "cocktails" and ids(done) == [1]
    assert done.text == "Saved the Mojito for you."
    assert "Save Mojito" not in done.suggestions


def test_declined_cocktail_proposal_changes_nothing(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(action("SaveCocktails", {"drink_ids": [4]}), "No problem.")
    assistant = build(model, cocktail_catalog)
    pending = assistant.reply("Save the G&T", []).pending_action

    reply = assistant.resume(ActionDecision(action=pending, approved=False), [])
    assert not reply.applied
    assert reply.steps[0].status == "skipped"
    assert ids(reply) == [4]


def test_remember_tastes_maps_values_and_can_be_narrowed(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action(
            "RememberTastes",
            {
                "alcohol_free": True,
                "liked_ingredients": ["lime juice", "dragon fruit"],
                "avoided_ingredients": ["rum", "Tonic Water"],
            },
        ),
        "Noted.",
    )
    assistant = build(model, cocktail_catalog)
    pending = assistant.reply("I don't drink, I love lime and hate rum and tonic", [])
    pending = pending.pending_action
    assert pending.tool == "RememberTastes"
    assert pending.summary == (
        "Proposed: Would remember alcohol-free drinks only; avoiding rum, Tonic water; "
        "likes Lime juice."
    )

    decision = ActionDecision(
        action=pending,
        approved=True,
        edited_args={"alcohol_free": False, "avoided_ingredients": ["rum"]},
    )
    done = assistant.resume(decision, [])
    (effect,) = done.applied
    assert effect.kind == "cocktail_preferences"
    assert effect.cocktail_preferences == CocktailPreferences(
        liked_ingredients=["Lime juice"], avoided_ingredients=["rum"]
    )
    assert effect.model_dump() == {
        "kind": "cocktail_preferences",
        "drink_ids": [],
        "cocktail_preferences": {
            "alcohol_free": False,
            "liked_ingredients": ["Lime juice"],
            "avoided_ingredients": ["rum"],
        },
    }


def test_unknown_tastes_are_a_tool_error_not_a_proposal(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("RememberTastes", {"avoided_ingredients": ["peanuts"]}),
        "I can't filter on that; please check labels.",
    )
    reply = build(model, cocktail_catalog).reply("I'm allergic to peanuts", [])
    assert reply.status == "complete" and reply.pending_action is None
    assert reply.steps[1].summary == "None of those ingredients appear in any recipe."


def test_narrowing_away_every_taste_is_rejected(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(action("RememberTastes", {"alcohol_free": True}))
    assistant = build(model, cocktail_catalog)
    pending = assistant.reply("No alcohol for me", []).pending_action
    decision = ActionDecision(action=pending, approved=True, edited_args={"alcohol_free": False})
    with pytest.raises(InvalidActionError, match="decline it instead"):
        assistant.resume(decision, [])


def test_auto_approved_save_runs_without_pausing(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(action("SaveCocktails", {"drink_ids": [6]}), "Saved Lemonade.")
    context = TurnContext(auto_approve=["SaveCocktails"])
    reply = build(model, cocktail_catalog).reply("Save the lemonade", [], context)
    assert reply.status == "complete"
    assert [e.drink_ids for e in reply.applied] == [[6]]
    assert reply.steps[1].summary.startswith("Auto-approved: Saved 1 to the cocktail shortlist")


def test_resumed_cocktail_turn_cannot_pause_again(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("SaveCocktails", {"drink_ids": [1]}),
        action("RememberTastes", {"alcohol_free": True}, "tool_2"),
        "Done.",
    )
    assistant = build(model, cocktail_catalog)
    pending = assistant.reply("Save it", []).pending_action
    reply = assistant.resume(ActionDecision(action=pending, approved=True), [])
    assert reply.status == "complete"
    assert reply.steps[1].status == "blocked"


def test_proposals_cannot_be_replayed_across_domains(
    chat_model_replying, cocktail_catalog, search, catalog
):
    signer = ActionSigner(b"k" * 32)
    cocktails = build(
        chat_model_replying(action("SaveCocktails", {"drink_ids": [1]})),
        cocktail_catalog,
        signer=signer,
    )
    fashion = ShoppingAssistant(
        chat_model_replying(NO_SEARCH, action("SaveToShortlist", {"product_ids": [1]})),
        search,
        catalog,
        signer=signer,
    )
    drink_proposal = cocktails.reply("Save the mojito", []).pending_action
    product_proposal = fashion.reply("Save the jeans", []).pending_action

    with pytest.raises(InvalidActionError, match="different assistant"):
        fashion.resume(ActionDecision(action=drink_proposal, approved=True), [])
    with pytest.raises(InvalidActionError, match="different assistant"):
        cocktails.resume(ActionDecision(action=product_proposal, approved=True), [])
    # The signature covers the tool name, so relabelling a proposal breaks it.
    relabelled = drink_proposal.model_copy(update={"tool": "SaveToShortlist"})
    with pytest.raises(InvalidActionError, match="not issued by Curio"):
        fashion.resume(ActionDecision(action=relabelled, approved=True), [])


# --- Grounding ---------------------------------------------------------------------------


def test_invented_drink_is_repaired_once(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("SearchCocktails", {"query": "mojito"}),
        "Try the Mojito, or a Daiquiri [id 2].",
        "Try the Mojito or the Virgin Mojito.",
    )
    reply = build(model, cocktail_catalog).reply("Mojito ideas", [])
    assert reply.grounding.status == "repaired"
    assert reply.grounding.issues == [
        "drink 2 was not returned by any tool",
        "Daiquiri was not returned by any tool",
    ]
    assert reply.text == "Try the Mojito or the Virgin Mojito."


def test_invented_measure_falls_back_to_recipe_facts(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        action("SearchCocktails", {"query": "mojito"}),
        "Use 3 oz lime juice in the Mojito.",
        "Use 3 oz of lime juice.",
    )
    reply = build(model, cocktail_catalog).reply("Mojito", [])
    assert reply.grounding.status == "fallback"
    assert reply.grounding.issues == ["3 oz does not match any amount in the evidence recipes"]
    assert reply.text == (
        "Here are the drinks I could confirm:\n"
        "- Mojito · contains alcohol · Highball glass\n"
        "- Virgin Mojito · alcohol-free · Highball glass\n\n"
        "Please drink responsibly."
    )


def test_history_and_shortlist_drinks_count_as_evidence(chat_model_replying, cocktail_catalog):
    model = chat_model_replying(
        "The Margarita takes 1 oz lime juice; the Lemonade 2 oz lemon juice."
    )
    history = [
        ChatTurn(role="user", content="Show me a margarita"),
        ChatTurn(role="assistant", content="Here it is.", drink_ids=[5]),
    ]
    reply = build(model, cocktail_catalog).reply(
        "How much lime and lemon?", history, TurnContext(cocktail_shortlist=[6])
    )
    assert reply.grounding.status == "verified"
    assert reply.grounding.checked == 4


def test_cocktail_system_prompt_carries_safety_rules(chat_model_replying, cocktail_catalog):
    assistant = build(chat_model_replying("Hello!"), cocktail_catalog)
    seen = record(assistant)
    assistant.reply("hi", [], tastes(alcohol_free=True))
    system = seen[0][0].content
    for rule in (
        "responsible drinking",
        "under the legal drinking age",
        "Never claim a drink is safe for an allergy",
        "never claim health or medical effects",
        "alcohol status",
        "drinking and driving",
    ):
        assert rule in system
    assert "remembered tastes (already applied to every search): alcohol-free drinks only" in (
        system
    )


@pytest.fixture
def grounding(cocktail_catalog):
    return CocktailGroundingCheck(
        (c.name for c in cocktail_catalog), [*cocktail_catalog.ingredients(), "Lemonade"]
    )


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("The Virgin Mojito is a lighter Mojito.", "verified"),  # part of an evidence name
        ("A perfect drink for a margarita fan.", "not_applicable"),  # lowercase: no mention
        ("Lemonade works as a mixer.", "not_applicable"),  # also an ingredient name
        ("Like the Daiquiri you mentioned.", "verified"),  # the shopper typed it
        ("Try the Margarita.", "fallback"),
        ("Use ½ oz grenadine or 1/2 oz.", "fallback"),
        ("8 leaves of mint and 1 oz lime juice.", "verified"),
        ("See drink #7.", "fallback"),
    ],
)
def test_cocktail_grounding_check(text, status, grounding, cocktail_catalog):
    evidence = cocktail_catalog.get_many([3])
    assert grounding.check(text, evidence, typed="something like a daiquiri").status == status


def test_measures_are_parsed_to_quantity_and_unit():
    found = measures_in("1 1/2 oz, 1.5 ounces, 1½ oz, 2 Tablespoons, 3 dashes, 1/0 oz, in 2024")
    assert [measure for _, measure in found] == [
        (1.5, "oz"),
        (1.5, "oz"),
        (1.5, "oz"),
        (2.0, "tbsp"),
        (3.0, "dash"),
    ]


# --- Schemas ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("edits", "ok"),
    [
        ({"alcohol_free": False}, True),
        ({"alcohol_free": True}, True),
        ({"alcohol_free": None}, False),
        ({"alcohol_free": "no"}, False),
    ],
)
def test_proposed_flags_can_only_be_switched_off(edits, ok):
    signer = ActionSigner(b"k" * 32)
    pending = signer.issue("RememberTastes", {"alcohol_free": True}, "no alcohol")
    decision = ActionDecision(action=pending, approved=True, edited_args=edits)
    if ok:
        assert narrow_args(decision) == edits
    else:
        with pytest.raises(InvalidActionError):
            narrow_args(decision)


def test_applied_effects_serialize_only_their_domain_fields():
    fashion = AppliedEffect(kind="shortlist", product_ids=[1])
    drinks = AppliedEffect(kind="cocktail_shortlist", drink_ids=[2])
    assert fashion.model_dump() == {"kind": "shortlist", "product_ids": [1], "preferences": None}
    assert drinks.model_dump() == {
        "kind": "cocktail_shortlist",
        "drink_ids": [2],
        "cocktail_preferences": None,
    }


def test_turn_context_accepts_every_approval_tool():
    context = TurnContext(
        auto_approve=["SaveToShortlist", "RememberPreferences", "SaveCocktails", "RememberTastes"],
        cocktail_shortlist=list(range(1, 21)),
    )
    assert len(context.auto_approve) == 4
    with pytest.raises(ValueError):
        TurnContext(cocktail_shortlist=list(range(1, 22)))
