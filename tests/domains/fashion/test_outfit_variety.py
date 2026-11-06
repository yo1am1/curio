"""Occasion outfits: a soft mid-price preference and a deterministic variety per occasion."""

import pandas as pd

from curio.domains.fashion.catalog import ProductCatalog
from curio.domains.fashion.models import Product
from curio.domains.fashion.outfits import OutfitPlanner, OutfitRequest
from tests.conftest import OutfitSearch

# Twelve tops of near-equal relevance (the fake search steps 0.001 per id); the most
# relevant one is the cheapest.
PRICES = [3.99, 4.99, 12.99, 14.99, 17.99, 19.99, 22.99, 24.99, 27.99, 29.99, 34.99, 39.99]
TOPS = [
    Product(
        product_id=100 + i,
        name=f"Top {i}",
        product_type="Top",
        product_group="Garment Upper body",
        section="Ladieswear",
        colour="Black",
        description=f"Top {i}.",
        price=price,
    )
    for i, price in enumerate(PRICES)
]
OCCASIONS = ["date night", "anniversary dinner", "first date", "rooftop party", "gala"]


def planner() -> OutfitPlanner:
    catalog = ProductCatalog(pd.DataFrame([p.model_dump(exclude={"display_name"}) for p in TOPS]))
    return OutfitPlanner(OutfitSearch(TOPS), catalog)


def top(occasion: str, *, variety: bool = True, budget: float | None = None) -> Product:
    request = OutfitRequest(
        occasion=occasion,
        audience="women",
        slots=("top",),
        budget=budget,
        variety=occasion if variety else None,
    )
    return planner().outfit(request).products[0]


def test_without_variety_the_most_relevant_piece_wins_even_at_399():
    assert top("date night", variety=False).price == 3.99


def test_an_occasion_outfit_avoids_the_cheapest_tenth_unless_the_budget_forces_it():
    picks = [top(occasion) for occasion in OCCASIONS]
    assert all(p.price > 4.99 for p in picks)
    assert top("date night", budget=4.5).price == 3.99  # nothing else fits


def test_variety_is_deterministic_per_occasion_and_differs_between_occasions():
    assert top("date night").product_id == top("Date  night").product_id
    assert len({top(occasion).product_id for occasion in OCCASIONS}) >= 3
