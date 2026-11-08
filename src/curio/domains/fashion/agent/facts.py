"""Fashion facts an answer cites by reference: a product's name, price and colour.

{product:843614008}                 **Basquiat blazer**
{price:843614008}                   €39.99
{colour:843614008}                  Black (the catalog's colour; "color" works too)
{total:price:843614008,906741002}   €79.98
{diff:price:843614008,906741002}    €10.00 (the larger minus the smaller)
"""

from collections.abc import Iterable

from curio.domains.fashion.models import Product
from curio.engine.facts import Attribute, FactSheet, ItemKind


def euro(amount: float) -> str:
    return f"€{amount:.2f}"


_COLOUR = Attribute("colour", "product", lambda p, _: p.colour.strip() or None, str)
PRODUCT_ATTRIBUTES = {
    "price": Attribute("price", "product", lambda p, _: p.price, euro, total=True, difference=True),
    "colour": _COLOUR,
    "color": _COLOUR,
}


def product_facts(products: Iterable[Product]) -> FactSheet:
    """The fact sheet for `products` (the turn's evidence)."""
    items = {p.product_id: p for p in products}
    kind = ItemKind(lambda p: p.display_name, items)
    return FactSheet({"product": kind}, PRODUCT_ATTRIBUTES)


# How the answer prompts explain references to the model.
PRODUCT_REFERENCES = (
    "{product:ID} for a product's name (rendered in bold, so do not add ** around it), "
    "{price:ID} for its price, {colour:ID} for its colour, {total:price:ID,ID,...} for the "
    "sum of several products' prices and {diff:price:ID,ID} for the difference of two"
)
