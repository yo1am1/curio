"""Follow-ups from the v1.0 verification pass: agent filters, overrides and memory merges."""

from langchain_core.messages import AIMessage

from clothes_recommender.chat.assistant import ShoppingAssistant
from clothes_recommender.chat.cocktail_agent import CocktailToolkit
from clothes_recommender.chat.engine import _merged
from clothes_recommender.chat.schemas import CocktailPreferences, ShopperPreferences, TurnContext
from clothes_recommender.cocktails.recommend import CocktailRecommender

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
