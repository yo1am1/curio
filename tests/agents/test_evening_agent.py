"""The evening agent on the engine: plan, swap, ask, save with approval, and grounding."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from curio.app.assistants import EveningAssistant
from curio.app.schemas import ActionDecision, AssistantReply, ChatTurn, TurnContext
from curio.domains.cocktails.models import CocktailPreferences
from curio.domains.evening.agent.toolkit import NO_PLAN
from curio.domains.evening.models import EveningRequest
from curio.domains.evening.planner import OUTFIT_NEEDS_FASHION
from curio.domains.fashion.agent.schemas import ShopperPreferences
from curio.engine import ActionSigner, InvalidActionError
from tests.domains.evening.conftest import evening_catalog, make_planner
from tests.domains.pairings.conftest import pairing_books, pairing_cocktails

SIGNER = ActionSigner(b"e" * 32)


def action(name, args, call_id="tool_1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def record(assistant):
    seen = []
    model = assistant._agent

    def invoke(messages):
        seen.append(list(messages))
        return model.invoke(messages)

    assistant._agent = RunnableLambda(invoke)
    return seen


def tool_results(seen):
    return [m.content for m in seen[-1] if isinstance(m, ToolMessage)]


@pytest.fixture
def agent(chat_model_replying):
    def make(*replies, fashion=True):
        return EveningAssistant(
            chat_model_replying(*replies),
            make_planner(fashion=fashion),
            books=pairing_books(),
            cocktails=pairing_cocktails(),
            products=evening_catalog() if fashion else None,
            signer=SIGNER,
        )

    return make


def cosy_plan(**fields):
    return make_planner().plan(EveningRequest(occasion="rainy Sunday in", **fields))


def grounded_answer(plan) -> str:
    """An answer that states only what the plan says."""
    outfit = plan.outfit.outfit
    pieces = "; ".join(
        f"{s.label}: **{s.product.name}** (product #{s.product.product_id}) €{s.product.price:.2f}"
        for s in outfit.slots
        if s.product
    )
    book, drink = plan.book.book, plan.drink.cocktail
    return (
        f"A {plan.theme.label.lower()} evening. {pieces}; total €{outfit.total:.2f}. "
        f"Read **{book.title}** (book #{book.book_id}) by {book.author}, ★ "
        f"{book.average_rating:.2f}, and pour a **{drink.name}** (drink #{drink.drink_id}, "
        f"contains alcohol). Why it works: {plan.why}"
    )


# --- PlanEvening ------------------------------------------------------------------------------


def test_plan_evening_streams_a_step_per_part_and_a_verified_answer(agent):
    plan = cosy_plan()
    assistant = agent(action("PlanEvening", {"occasion": "rainy Sunday in"}), grounded_answer(plan))
    seen = record(assistant)
    events = list(assistant.stream("Plan a rainy Sunday in", []))
    steps = [e.step for e in events[:-1]]
    assert [s.name for s in steps] == [
        "Understand request",
        "Evening theme",
        "Evening outfit",
        "Evening book",
        "Evening drink",
        "PlanEvening",
        "Draft answer",
        "Grounding check",
    ]
    assert steps[1].summary == "Cosy: you mentioned rainy and Sunday, so a warm night in."
    assert steps[5].summary == "Planned a cosy evening: outfit, book and drink."
    result = tool_results(seen)[0]
    assert result.startswith("Evening plan for “rainy Sunday in” · theme Cosy")
    assert "- Top: product #21 Chunky Knit Sweater | Sweater | Beige | €24.99" in result
    assert "Book: book #4 The Name of the Wind | Patrick Rothfuss | ★ 4.55" in result
    assert "Drink: drink #9 Honey Bee | contains alcohol | ingredients: White Rum (2 oz)" in result
    assert result.endswith(f"Why it works: {plan.why}")
    reply = events[-1].reply
    assert reply.grounding.status == "verified"
    assert reply.domain == "evening"
    assert reply.evening.why == plan.why and reply.evening.state == plan.state
    assert [p.product_id for p in reply.products] == [21, 12, 22]
    assert [b.book_id for b in reply.books] == [4]
    assert [c.drink_id for c in reply.cocktails] == [9]
    assert reply.suggestions == [
        "Another book",
        "Cheaper top",
        "Make the drink alcohol-free",
        "Save the evening",
    ]
    wire = AssistantReply.model_validate(reply.model_dump()).model_dump(mode="json")
    assert wire["evening"]["theme"]["id"] == "cosy"


def test_remembered_tastes_from_every_domain_apply(agent):
    assistant = agent(
        action("PlanEvening", {"occasion": "murder mystery night"}), "A dark evening awaits."
    )
    context = TurnContext(
        cocktail_preferences=CocktailPreferences(alcohol_free=True),
        preferences=ShopperPreferences(sections=["Menswear"], max_price=30),
    )
    reply = assistant.reply("Plan a murder mystery night", [], context)
    assert reply.steps[0].summary == (
        "Applying remembered tastes to every part: alcohol-free drinks; pieces up to €30."
    )
    assert reply.evening.drink.cocktail.alcohol_free
    assert reply.evening.outfit.outfit.audience == "men"  # from the remembered section
    assert all(p.price <= 30 for p in reply.products)


def test_without_fashion_the_agent_plans_the_book_and_drink_and_says_why(agent):
    assistant = agent(
        action("PlanEvening", {"occasion": "rainy Sunday in"}),
        "Read **The Name of the Wind** with a **Honey Bee** (drink #9). The outfit needs the "
        "fashion catalogue.",
        fashion=False,
    )
    reply = assistant.reply("Plan a rainy Sunday in", [])
    assert reply.steps[0].summary == (
        "No remembered tastes to apply. This server cannot plan the outfit."
    )
    assert [s.name for s in reply.steps[1:5]] == [
        "Evening theme",
        "Evening book",
        "Evening drink",
        "Evening outfit",
    ]
    assert reply.steps[4].summary == OUTFIT_NEEDS_FASHION and reply.steps[4].status == "skipped"
    assert reply.evening.outfit is None and reply.products == []
    assert reply.grounding.status == "verified"


def test_a_vague_request_gets_a_question(agent):
    question = {"question": "What's the occasion?", "options": ["Date night", "Rainy Sunday in"]}
    reply = agent(action("AskShopper", question)).reply("Plan my evening", [])
    assert reply.status == "needs_input"
    assert reply.clarification.options == ["Date night", "Rainy Sunday in"]


# --- Grounding --------------------------------------------------------------------------------


def test_an_invented_price_rating_and_drink_fall_back_to_the_plan(agent):
    invented = (
        "Wear the **Chunky Knit Sweater** at just €9.99, read **The Name of the Wind** "
        "(★ 4.9) and pour a **Manhattan** (drink #2)."
    )
    assistant = agent(
        action("PlanEvening", {"occasion": "rainy Sunday in"}), invented, invented + " Trust me."
    )
    reply = assistant.reply("Plan a rainy Sunday in", [])
    assert reply.grounding.status == "fallback"
    issues = reply.grounding.issues
    assert "€9.99 does not match any catalog price in the evidence" in issues
    assert "a rating of 4.9 does not match any book in the evidence" in issues
    assert "drink 2 was not returned by PairCocktail" in issues
    assert "Manhattan was not returned by PairCocktail" in issues
    assert [s.name for s in reply.steps[-2:]] == ["Grounding check", "Self-correction"]
    plan = cosy_plan()
    assert reply.text.startswith("A cosy evening, from the catalogs:\n- Top: Chunky Knit Sweater")
    assert "- Book: The Name of the Wind by Patrick Rothfuss · ★ 4.55" in reply.text
    assert "- Drink: Honey Bee · contains alcohol" in reply.text
    assert f"Why it works: {plan.why}" in reply.text
    assert reply.evening is not None  # the plan's cards still show


def test_a_wrong_total_is_rewritten_once(agent):
    plan = cosy_plan()
    assistant = agent(
        action("PlanEvening", {"occasion": "rainy Sunday in"}),
        "Three pieces for €65.00.",
        grounded_answer(plan),
    )
    reply = assistant.reply("Plan a rainy Sunday in", [])
    assert reply.grounding.status == "repaired"
    assert reply.text == grounded_answer(plan)


def test_a_product_called_like_a_cocktail_is_not_a_drink(agent):
    plan = cosy_plan()
    answer = (
        f"Pour a **Honey Bee** (drink #9) and wear the **{plan.outfit.outfit.products[0].name}**."
    )
    reply = agent(action("PlanEvening", {"occasion": "rainy Sunday in"}), answer).reply("x y", [])
    assert reply.grounding.status == "verified"


# --- Swaps and the client-held plan -------------------------------------------------------------


def test_swap_uses_the_client_held_plan_and_the_old_book_stays_citable(agent):
    plan = cosy_plan()
    assistant = agent(
        action("SwapEveningPart", {"part": "book"}),
        "Instead of **The Name of the Wind**, try the new book with a fresh pairing.",
    )
    seen = record(assistant)
    reply = assistant.reply("Another book", [], TurnContext(evening=plan.state))
    system = seen[0][0].content
    assert "current evening plan (SwapEveningPart and SaveEvening use it): theme Cosy" in system
    assert "book #4 The Name of the Wind ★ 4.55" in system
    assert reply.evening.book.book.book_id != 4
    assert reply.evening.change.startswith("Another book: The Name of the Wind → ")
    assert reply.steps[1].name == "SwapEveningPart" and reply.steps[1].status == "ok"
    assert reply.grounding.status == "verified"
    assert reply.evening.state.skipped_books == [4]


def test_answering_from_the_current_plan_without_a_tool_is_grounded(agent):
    plan = cosy_plan()
    reply = agent(grounded_answer(plan)).reply(
        "Remind me of my evening", [], TurnContext(evening=plan.state)
    )
    assert reply.grounding.status == "verified"
    assert reply.evening is None  # nothing changed this turn


def test_a_swap_without_a_plan_tells_the_model_to_plan_first(agent):
    assistant = agent(action("SwapEveningPart", {"part": "drink"}), "Let me plan first.")
    seen = record(assistant)
    assistant.reply("Another drink", [])
    assert tool_results(seen) == [NO_PLAN]


def test_an_invalid_swap_is_rejected_before_anything_runs(agent):
    assistant = agent(action("SwapEveningPart", {"part": "book", "mode": "cheaper"}), "Which part?")
    reply = assistant.reply("cheaper book", [], TurnContext(evening=cosy_plan().state))
    assert reply.steps[1].status == "blocked"


def test_earlier_drinks_are_context_not_evidence(agent):
    history = [
        ChatTurn(role="user", content="Plan a rainy Sunday in"),
        ChatTurn(role="assistant", content="Here you go.", drink_ids=[2], book_ids=[4]),
    ]
    answer = "You had a **Manhattan** (drink #2) with **The Name of the Wind**."
    assistant = agent(answer, answer)
    seen = record(assistant)
    reply = assistant.reply("What did I drink?", history)
    assert "drink #2 Manhattan (shown earlier)" in seen[0][2].content
    assert reply.grounding.status == "fallback"


# --- Approval -------------------------------------------------------------------------------------


def test_save_evening_is_proposed_narrowed_and_applied_to_each_list(agent):
    plan = cosy_plan()
    ids = {
        "product_ids": [p.product_id for p in plan.outfit.outfit.products],
        "book_ids": [plan.book.book.book_id],
        "drink_ids": [plan.drink.cocktail.drink_id],
    }
    context = TurnContext(evening=plan.state, shortlist=[1])
    reply = agent(action("SaveEvening", ids)).reply("Save the evening", [], context)
    assert reply.status == "needs_approval"
    pending = reply.pending_action
    assert pending.tool == "SaveEvening"
    assert pending.summary == (
        "Proposed: Would save 3 pieces to the shortlist, The Name of the Wind to the reading "
        "list and Honey Bee to the saved cocktails."
    )
    assert [p.product_id for p in pending.products] == ids["product_ids"]
    assert [b.book_id for b in pending.books] == [4]
    assert [c.drink_id for c in pending.cocktails] == [9]
    assert reply.text.startswith("Shall I save this evening")

    # The shopper unticks the drink and one piece.
    edits = {"drink_ids": [], "product_ids": ids["product_ids"][:2]}
    decision = ActionDecision(action=pending, approved=True, edited_args=edits)
    resuming = agent("Saved the outfit and the book.")
    seen = record(resuming)
    done = resuming.resume(decision, [], context)
    effect = done.applied[0]
    assert effect.kind == "evening"
    assert (effect.product_ids, effect.book_ids, effect.drink_ids) == (
        ids["product_ids"][:2],
        [4],
        [],
    )
    assert done.steps[0].summary == (
        "Approved: Saved 2 pieces to the shortlist and The Name of the Wind to the reading list."
    )
    assert "[Decision on your proposal] The shopper approved" in seen[0][-1].content
    wire = AssistantReply.model_validate(done.model_dump()).model_dump(mode="json")
    assert wire["applied"][0] == {
        "kind": "evening",
        "product_ids": ids["product_ids"][:2],
        "book_ids": [4],
        "drink_ids": [],
    }


def test_a_widened_or_forged_proposal_is_refused(agent):
    reply = agent(action("SaveEvening", {"book_ids": [4]})).reply("Save the book", [])
    pending = reply.pending_action
    widened = ActionDecision(action=pending, approved=True, edited_args={"book_ids": [4, 1]})
    with pytest.raises(InvalidActionError, match="subset"):
        agent("x").resume(widened, [])
    forged = pending.model_copy(update={"args": {"book_ids": [1]}})
    with pytest.raises(InvalidActionError, match="modified"):
        agent("x").resume(ActionDecision(action=forged, approved=True), [])


def test_a_declined_save_changes_nothing(agent):
    reply = agent(action("SaveEvening", {"book_ids": [4], "drink_ids": [9]})).reply("save", [])
    done = agent("No problem.").resume(
        ActionDecision(action=reply.pending_action, approved=False), []
    )
    assert done.applied == []
    assert done.steps[0].status == "skipped"


def test_saving_unknown_ids_is_a_tool_error(agent):
    assistant = agent(action("SaveEvening", {"book_ids": [999]}), "Nothing to save.")
    seen = record(assistant)
    reply = assistant.reply("save", [])
    assert tool_results(seen) == ["None of those pieces, books or drinks are in the catalogs."]
    assert reply.status == "complete"
