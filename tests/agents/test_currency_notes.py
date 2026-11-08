"""A budget in dollars or pounds is used as euros, never converted, and the evening and
fashion tools carry a note saying so into their tool results (2.1.1: "$150" was silently
read as €150)."""

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from curio.app.assistants import EveningAssistant, ShoppingAssistant
from curio.domains.fashion.models import ScoredProduct
from tests.domains.evening.conftest import evening_catalog, make_planner
from tests.domains.pairings.conftest import pairing_books, pairing_cocktails

NO_SEARCH = {"query": "", "wants_products": False}
DOLLAR_NOTE = (
    "Prices are in euros; I used 150 as a € budget. You gave it in US dollars, and Curio does "
    "not convert currencies."
)


def action(name, args, call_id="tool_1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def record(assistant):
    seen = []
    model = assistant._agent
    assistant._agent = RunnableLambda(lambda m: seen.append(list(m)) or model.invoke(m))
    return seen


def tool_results(seen):
    return [m.content for m in seen[-1] if isinstance(m, ToolMessage)]


def system_prompt(seen):
    return next(m.content for m in seen[0] if isinstance(m, SystemMessage))


# --- Evening: the dollar budget ---------------------------------------------------------------


def test_plan_evening_notes_a_dollar_budget_in_the_tool_result_and_the_plan(chat_model_replying):
    """The model passes the number; the tool says it was used as euros, unconverted."""
    model = chat_model_replying(
        action("PlanEvening", {"occasion": "business dinner", "budget": 150, "audience": "men"}),
        "Here is your evening, planned with a €150 budget.",
    )
    assistant = EveningAssistant(
        model,
        make_planner(),
        books=pairing_books(),
        cocktails=pairing_cocktails(),
        products=evening_catalog(),
    )
    seen = record(assistant)
    reply = assistant.reply(
        "Plan a business dinner evening for men, budget $150 for the outfit.", []
    )
    assert reply.evening.state.budget == 150
    assert reply.evening.notes[0] == DOLLAR_NOTE
    assert f"Note: {DOLLAR_NOTE}" in tool_results(seen)[0]
    # The €150 is the shopper's budget, not a catalog price: nothing was checked.
    assert reply.grounding.status == "not_applicable"


def test_plan_evening_adds_no_note_for_a_euro_budget(chat_model_replying):
    model = chat_model_replying(
        action("PlanEvening", {"occasion": "date night", "budget": 100}), "Here it is."
    )
    assistant = EveningAssistant(
        model, make_planner(), books=pairing_books(), cocktails=pairing_cocktails()
    )
    reply = assistant.reply("Date night, outfit under €100", [])
    assert not any("convert" in note for note in reply.evening.notes)


# --- Fashion: the dollar budget ---------------------------------------------------------------


def test_a_dollar_budget_becomes_the_price_limit_and_a_noted_step(
    chat_model_replying, search, catalog
):
    search.results = [ScoredProduct(1, 0.9)]
    # The intent extractor left the dollar budget out; the shopper wrote it, so it applies.
    model = chat_model_replying(
        {"query": "jeans"},
        action("SearchCatalog", {"query": "blue jeans"}),
        "The **Slim Jeans** cost €29.99, within your 30 budget in euros.",
    )
    assistant = ShoppingAssistant(model, search, catalog)
    seen = record(assistant)
    reply = assistant.reply("Jeans under $30, please", [])

    assert reply.intent.max_price == 30
    assert all(f.max_price == 30 for _, _, f in search.calls)
    note = (
        "Prices are in euros; I used 30 as a € budget. You gave it in US dollars, and Curio "
        "does not convert currencies."
    )
    currency = [s for s in reply.steps if s.name == "Currency"]
    assert [s.summary for s in currency] == [note]
    assert note.rstrip(".") in system_prompt(seen)
    assert tool_results(seen)[0].endswith(f"Note: {note}")


def test_an_explicit_price_limit_is_kept(chat_model_replying, search, catalog):
    model = chat_model_replying({"query": "jeans", "max_price": 25}, "Nothing matched.")
    reply = ShoppingAssistant(model, search, catalog).reply("Jeans under $30", [])
    assert reply.intent.max_price == 25
    assert any(s.name == "Currency" and "you gave 30" in s.summary for s in reply.steps)


def test_euro_requests_have_no_currency_step(chat_model_replying, search, catalog):
    model = chat_model_replying({"query": "jeans", "max_price": 30}, "Nothing matched.")
    reply = ShoppingAssistant(model, search, catalog).reply("Jeans under €30", [])
    assert not any(s.name == "Currency" for s in reply.steps)


def test_build_outfit_carries_the_note_for_a_pound_budget(
    chat_model_replying, outfit_search, outfit_catalog
):
    model = chat_model_replying(
        NO_SEARCH,
        action("BuildOutfit", {"occasion": "summer wedding", "budget": 80, "audience": "women"}),
        "Here is the outfit.",
    )
    assistant = ShoppingAssistant(model, outfit_search, outfit_catalog)
    seen = record(assistant)
    reply = assistant.reply("An outfit for a summer wedding, under £80", [])
    assert reply.outfit is not None and reply.outfit.budget == 80
    assert tool_results(seen)[0].endswith(
        "Note: Prices are in euros; I used 80 as a € budget. You gave it in pounds, and Curio "
        "does not convert currencies."
    )
