"""Drink facts an answer cites by reference: a drink's name, alcohol label and recipe.

    {drink:12}              **Mojito**
    {alcohol:drink:12}      contains alcohol (or "alcohol-free", "alcohol optional")
    {recipe:drink:12}       Light rum (2-3 oz), Lime (Juice of 1), ... (every ingredient)
    {amount:drink:12:Mint}  2-4 (the recipe's measure for that ingredient)

The kind may be left out ({alcohol:12}): alcohol labels, recipes and amounts are drinks'.
An amount names the ingredient as the recipe does (case does not matter); an ingredient
the recipe lacks, or one listed without a measure, cannot be rendered.
"""

from collections.abc import Iterable

from curio.domains.cocktails.models import OPTIONAL_ALCOHOL, Cocktail
from curio.engine.facts import Attribute, FactSheet, ItemKind


def alcohol_label(cocktail: Cocktail) -> str:
    """The catalog's alcohol label: "contains alcohol", "alcohol-free" or "alcohol optional"."""
    if cocktail.alcoholic == OPTIONAL_ALCOHOL:
        return "alcohol optional"
    return "alcohol-free" if cocktail.alcohol_free else "contains alcohol"


def recipe(cocktail: Cocktail) -> str:
    """Every ingredient with its measure, as the tool results list them."""
    return ", ".join(
        f"{i.name} ({i.measure.strip()})" if i.measure and i.measure.strip() else i.name
        for i in cocktail.ingredients
    )


def amount(cocktail: Cocktail, ingredient: str | None) -> str | None:
    """The recipe's measure for `ingredient`, or None when it has none."""
    wanted = (ingredient or "").strip().casefold()
    for item in cocktail.ingredients:
        if item.name.strip().casefold() == wanted:
            return (item.measure or "").strip() or None
    return None


DRINK_ATTRIBUTES = {
    "alcohol": Attribute("alcohol", "drink", lambda c, _: alcohol_label(c), str),
    "recipe": Attribute("recipe", "drink", lambda c, _: recipe(c) or None, str),
    "amount": Attribute("amount", "drink", amount, str, argument=True),
}


def drink_facts(drinks: Iterable[Cocktail]) -> FactSheet:
    """The fact sheet for `drinks` (the turn's evidence)."""
    kind = ItemKind(lambda c: c.name, {c.drink_id: c for c in drinks})
    return FactSheet({"drink": kind}, DRINK_ATTRIBUTES)


# How the answer prompts explain references to the model.
DRINK_REFERENCES = (
    "{drink:ID} for a drink's name (rendered in bold, so do not add ** around it), "
    "{alcohol:drink:ID} for its alcohol label, {recipe:drink:ID} for its ingredients with "
    "their amounts and {amount:drink:ID:Ingredient} for one ingredient's amount, the "
    "ingredient spelled as the recipe lists it"
)
