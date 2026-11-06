"""An evening over small catalogs: the pairing books and recipes plus an outfit catalog with
a cosy look (sweater, trousers, cardigan) beside the party pieces."""

import pandas as pd
import pytest

from curio.domains.books.recommend import BookRecommender
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.domains.evening.planner import EveningPlanner
from curio.domains.fashion.catalog import ProductCatalog
from curio.domains.fashion.models import Product
from curio.domains.fashion.outfits import OutfitPlanner
from curio.domains.pairings.pairer import BookCocktailPairer
from tests.conftest import OutfitSearch, book_neighbours_frame, outfit_products
from tests.domains.pairings.conftest import pairing_books, pairing_cocktails

COSY_ROWS = [
    # product_id, name, product_type, product_group, section, colour, price
    (21, "Chunky Knit Sweater", "Sweater", "Garment Upper body", "Ladieswear", "Beige", 24.99),
    (22, "Soft Knit Cardigan", "Cardigan", "Garment Upper body", "Ladieswear", "Grey", 19.99),
    (23, "Relaxed Joggers", "Trousers", "Garment Lower body", "Ladieswear", "Grey", 14.99),
    (24, "Silk Blouse", "Blouse", "Garment Upper body", "Ladieswear", "White", 17.99),
    (25, "Velvet Ballerinas", "Ballerinas", "Shoes", "Ladieswear", "Black", 12.99),
    (26, "Wool Sweater", "Sweater", "Garment Upper body", "Menswear", "Navy", 29.99),
    (27, "Cotton Chinos", "Trousers", "Garment Lower body", "Menswear", "Beige", 24.99),
    (28, "Shawl Cardigan", "Cardigan", "Garment Upper body", "Menswear", "Brown", 34.99),
]


def evening_products() -> list[Product]:
    extra = [
        Product(
            product_id=pid,
            name=name,
            product_type=kind,
            product_group=group,
            section=section,
            colour=colour,
            description=f"{name} in {colour.lower()}.",
            price=price,
        )
        for pid, name, kind, group, section, colour, price in COSY_ROWS
    ]
    return [*outfit_products(), *extra]


def evening_catalog() -> ProductCatalog:
    return ProductCatalog(pd.DataFrame([p.model_dump() for p in evening_products()]))


def make_planner(*, fashion: bool = True, books: bool = True, cocktails: bool = True):
    book_catalog = pairing_books()
    drinks = pairing_cocktails()
    pairer = BookCocktailPairer(book_catalog, drinks, CocktailRecommender(drinks))
    catalog = evening_catalog()
    return EveningPlanner(
        outfits=OutfitPlanner(OutfitSearch(evening_products()), catalog) if fashion else None,
        books=BookRecommender(book_catalog, book_neighbours_frame()) if books else None,
        book_catalog=book_catalog if books else None,
        pairer=pairer if books and cocktails else None,
        cocktails=drinks if cocktails else None,
    )


@pytest.fixture
def planner() -> EveningPlanner:
    return make_planner()


@pytest.fixture
def pairer() -> BookCocktailPairer:
    drinks = pairing_cocktails()
    return BookCocktailPairer(pairing_books(), drinks, CocktailRecommender(drinks))
