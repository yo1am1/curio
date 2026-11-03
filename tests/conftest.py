"""Shared fixtures: a fake encoder, a tiny catalog and an in-memory vector store."""

import hashlib
from collections.abc import Iterator, Sequence

import numpy as np
import pandas as pd
import pytest
from PIL.Image import Image
from qdrant_client import QdrantClient

from clothes_recommender.catalog import ProductCatalog
from clothes_recommender.domain import Product
from clothes_recommender.vector_store import ProductVectorStore


class FakeEncoder:
    """Deterministic `Encoder`: equal inputs map to the same random unit vector.

    Texts and images live in unrelated regions of the space, so a search only finds a
    product when queried with exactly the text or image its vector was built from.
    """

    dimension = 8

    def encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        return self._unit_vectors([text.encode() for text in texts])

    def encode_images(self, images: Sequence[Image]) -> np.ndarray:
        return self._unit_vectors([image.tobytes() for image in images])

    def _unit_vectors(self, payloads: list[bytes]) -> np.ndarray:
        vectors = np.empty((len(payloads), self.dimension), dtype=np.float32)
        for row, payload in enumerate(payloads):
            seed = int.from_bytes(hashlib.sha256(payload).digest()[:8])
            vector = np.random.default_rng(seed).standard_normal(self.dimension)
            vectors[row] = vector / np.linalg.norm(vector)
        return vectors


PRODUCT_GROUPS = {
    "Trousers": "Garment Lower body",
    "Dress": "Garment Full body",
    "Polo shirt": "Garment Upper body",
    "Sneakers": "Shoes",
    "Boots": "Shoes",
}


def _product(
    product_id: int, name: str, product_type: str, section: str, colour: str, price: float
) -> Product:
    return Product(
        product_id=product_id,
        name=name,
        product_type=product_type,
        product_group=PRODUCT_GROUPS[product_type],
        section=section,
        colour=colour,
        description=f"{name} in {colour.lower()}.",
        price=price,
        shopify_category=f"Apparel & Accessories > {PRODUCT_GROUPS[product_type]}",
        shopify_attributes={"color": colour},
    )


@pytest.fixture
def fake_encoder() -> FakeEncoder:
    return FakeEncoder()


@pytest.fixture
def sample_products() -> list[Product]:
    """Six H&M-style products over five product types and three sections."""
    return [
        _product(1, "Slim Jeans", "Trousers", "Menswear", "Blue", 29.99),
        _product(2, "Wide Leg Trousers", "Trousers", "Ladieswear", "Black", 24.99),
        _product(3, "Floral Summer Dress", "Dress", "Ladieswear", "Red", 34.99),
        _product(4, "Cotton Polo Shirt", "Polo shirt", "Menswear", "White", 17.99),
        _product(5, "Running Sneakers", "Sneakers", "Sport", "Black", 89.99),
        _product(6, "Leather Ankle Boots", "Boots", "Ladieswear", "Brown", 119.99),
    ]


@pytest.fixture
def sample_catalog(sample_products: list[Product]) -> ProductCatalog:
    return ProductCatalog(pd.DataFrame([product.model_dump() for product in sample_products]))


@pytest.fixture
def vector_store(
    fake_encoder: FakeEncoder, sample_products: list[Product]
) -> Iterator[ProductVectorStore]:
    """In-memory store holding every sample product, embedded from its name as text."""
    client = QdrantClient(":memory:")
    store = ProductVectorStore(client, "test-products")
    store.ensure_collection(fake_encoder.dimension)
    store.upsert(sample_products, fake_encoder.encode_texts([p.name for p in sample_products]))
    yield store
    client.close()


COCKTAIL_ROWS = [
    # drink_id, name, alcoholic, category, glass, ingredients (name, measure)
    (
        1,
        "Mojito",
        "Alcoholic",
        "Cocktail",
        "Highball glass",
        [
            ("Light rum", "2 oz"),
            ("Lime juice", "1 oz"),
            ("Mint", "6 leaves"),
            ("Sugar", "2 tsp"),
            ("Soda water", None),
        ],
    ),
    (
        2,
        "Daiquiri",
        "Alcoholic",
        "Ordinary Drink",
        "Cocktail glass",
        [("Light rum", "1 1/2 oz"), ("Lime juice", "1 oz"), ("Sugar", "1/2 tsp")],
    ),
    (
        3,
        "Virgin Mojito",
        "Non alcoholic",
        "Cocktail",
        "Highball glass",
        [("Lime juice", "1 oz"), ("Mint", "8 leaves"), ("Sugar", "2 tsp"), ("Soda water", "4 oz")],
    ),
    (
        4,
        "Gin and Tonic",
        "Alcoholic",
        "Ordinary Drink",
        "Highball glass",
        [("Gin", "2 oz"), ("Tonic water", "5 oz"), ("Lime", "1 wedge")],
    ),
    (
        5,
        "Margarita",
        "Alcoholic",
        "Ordinary Drink",
        "Cocktail glass",
        [("Tequila", "1 1/2 oz"), ("Triple sec", "1/2 oz"), ("Lime juice", "1 oz"), ("Salt", None)],
    ),
    (
        6,
        "Lemonade",
        "Non alcoholic",
        "Soft Drink",
        "Collins glass",
        [("Lemon juice", "2 oz"), ("Sugar", "2 tsp"), ("Water", "6 oz")],
    ),
    (
        7,
        "Espresso Martini",
        "Alcoholic",
        "Cocktail",
        "Cocktail glass",
        [("Vodka", "2 oz"), ("Kahlua", "1/2 oz"), ("Espresso", "1 oz")],
    ),
    (
        8,
        "Shirley Temple",
        "Optional alcohol",
        "Soft Drink",
        "Highball glass",
        [("Ginger ale", "6 oz"), ("Grenadine", "1/2 oz"), ("Maraschino cherry", "1")],
    ),
]


def cocktail_frame():
    """The prepared cocktails.parquet layout for a tiny, varied recipe set."""
    import json as _json

    return pd.DataFrame(
        [
            {
                "drink_id": drink_id,
                "name": name,
                "alcoholic": alcoholic,
                "category": category,
                "glass": glass,
                "instructions": f"Build the {name} in a {glass.lower()}.",
                "image_url": f"https://www.thecocktaildb.com/images/media/drink/{drink_id}.jpg",
                "ingredients": _json.dumps([{"name": n, "measure": m} for n, m in ingredients]),
            }
            for drink_id, name, alcoholic, category, glass, ingredients in COCKTAIL_ROWS
        ]
    )


@pytest.fixture
def cocktail_catalog():
    from clothes_recommender.cocktails.catalog import CocktailCatalog

    return CocktailCatalog(cocktail_frame())
