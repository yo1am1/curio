"""Follow-ups from the v1.0 verification pass: agent filters, overrides and memory merges."""

from langchain_core.messages import AIMessage

from curio.app.assistants import ShoppingAssistant
from curio.app.schemas import ShopperPreferences, TurnContext
from curio.domains.cocktails.agent.toolkit import CocktailToolkit
from curio.domains.cocktails.models import CocktailPreferences
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.domains.fashion.models import ScoredProduct
from curio.engine.agent import _merged

NO_SEARCH = {"query": "", "wants_products": False}


def action(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def test_avoided_plurals_and_spelling_variants_filter_agent_tools(cocktail_catalog):
    toolkit = CocktailToolkit(cocktail_catalog, CocktailRecommender(cocktail_catalog))
    assert toolkit._avoided(
        cocktail_catalog.get(1), CocktailPreferences(avoided_ingredients=["limes"])
    )
    # Broad terms are kept as plain normalised words, never raw client text.
    assert toolkit._known(["Limes!", "Sodas"]) == ["lime", "soda"]


def test_just_approved_budget_replaces_the_remembered_one_in_the_same_turn(
    chat_model_replying, search, catalog
):
    model = chat_model_replying(
        {"query": "jeans"},
        action("RememberPreferences", {"max_price": 20}, "mem"),
        action("SearchCatalog", {"query": "jeans"}, "search"),
        "Here you go.",
    )
    context = TurnContext(
        preferences=ShopperPreferences(max_price=40), auto_approve=["RememberPreferences"]
    )
    ShoppingAssistant(model, search, catalog).reply("jeans, and keep my budget at €20", [], context)
    assert search.calls[0][2].max_price == 40
    assert search.calls[-1][2].max_price == 20


def test_full_memory_keeps_the_newest_values_like_the_ui():
    merged = _merged(
        ShopperPreferences(sections=["Menswear", "Divided", "Sport"]),
        ShopperPreferences(sections=["Ladieswear", "Divided"]),
    )
    assert merged.sections == ["Sport", "Ladieswear", "Divided"]


def test_empty_related_result_keeps_cards_and_hints_the_next_relation(
    chat_model_replying, search, catalog
):
    from langchain_core.messages import ToolMessage
    from langchain_core.runnables import RunnableLambda

    class NoComplements:
        def related(self, product_id, relation, top_k):
            return []

    search.results = [ScoredProduct(1, 0.9)]
    model = chat_model_replying(
        {"query": "jeans"},
        action("FindRelated", {"product_id": 1, "relation": "complement"}, "rel"),
        "No bought-together evidence; here are the jeans again.",
    )
    assistant = ShoppingAssistant(model, search, catalog, navigator=NoComplements())
    seen = []
    agent = assistant._agent
    assistant._agent = RunnableLambda(lambda m: seen.append(list(m)) or agent.invoke(m))
    reply = assistant.reply("What goes with these jeans?", [])
    tool_result = seen[-1][-1]
    assert isinstance(tool_result, ToolMessage)
    assert "Try relation 'similar'" in tool_result.content
    assert [p.product_id for p in reply.products] == [1]
    assert reply.steps[2].status == "skipped"
