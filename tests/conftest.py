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


# (book_id, title, authors, series, position, year, rating, ratings, pages, genres, tags, cover)
BOOK_ROWS = [
    (1, "The Name of the Wind", ["Patrick Rothfuss"], "The Kingkiller Chronicle", "1", 2007,
     4.55, 400_000, 662, ["Fantasy"], ["epic fantasy", "magic", "high fantasy"], True),
    (2, "The Wise Man's Fear", ["Patrick Rothfuss"], "The Kingkiller Chronicle", "2", 2011,
     4.57, 245_000, 994, ["Fantasy"], ["epic fantasy", "magic"], True),
    (3, "The Way of Kings", ["Brandon Sanderson"], "The Stormlight Archive", "1", 2010,
     4.64, 145_000, 1007, ["Fantasy"], ["epic fantasy", "high fantasy"], True),
    (4, "The Final Empire", ["Brandon Sanderson"], "Mistborn", "1", 2006,
     4.43, 209_000, 541, ["Fantasy"], ["epic fantasy", "magic", "heist"], True),
    (5, "Of Mice and Men", ["John Steinbeck"], None, None, 1937,
     3.84, 1_500_000, 112, ["Classics"], ["classic", "american literature"], True),
    (6, "Animal Farm", ["George Orwell", "Russell Baker"], None, None, 1945,
     3.87, 1_880_000, 122, ["Classics", "Science fiction"], ["classic", "dystopia", "politics"],
     True),
    (7, "It", ["Stephen King"], None, None, 1986,
     4.18, 600_000, 1093, ["Horror"], ["horror", "dark", "clowns"], True),
    (8, "Gone Girl", ["Gillian Flynn"], None, None, 2012,
     4.03, 1_600_000, 399, ["Mystery", "Thriller"], ["psychological thriller", "dark"], True),
    (9, "Chocolate Chip Cookie Murder", ["Joanne Fluke"], "Hannah Swensen", "1", 2000,
     3.72, 40_000, 436, ["Mystery"], ["cozy mystery", "baking"], False),
    (10, "Twilight", ["Stephenie Meyer"], "Twilight", "1", 2005,
     3.57, 3_800_000, None, ["Young adult", "Romance"], ["vampires", "paranormal romance"], True),
]  # fmt: skip
# (book_id, neighbour_id, score, co_readers): the readers-also-liked links prepare_books writes.
BOOK_NEIGHBOURS = [
    (1, 2, 0.8, 2400),
    (1, 3, 0.44, 960),
    (1, 4, 0.42, 1140),
    (1, 7, 0.1, 20),
    (2, 1, 0.75, 2400),
    (3, 4, 0.5, 800),
    (3, 1, 0.4, 960),
    (4, 3, 0.45, 800),
    (5, 6, 0.4, 3000),
    (6, 5, 0.38, 3000),
    (8, 7, 0.2, 300),
    (8, 9, 0.1, 30),
]


def book_frame():
    """The prepared books.parquet layout for a tiny, varied catalog."""
    import json as _json

    return pd.DataFrame(
        [
            {
                "book_id": book_id,
                "goodreads_book_id": 1000 + book_id,
                "title": title,
                "authors": _json.dumps(authors),
                "series": series,
                "series_position": position,
                "year": year,
                "language": "eng",
                "average_rating": rating,
                "ratings_count": ratings,
                "pages": pages,
                "description": f"{title} is a book by {authors[0]}.",
                "genres": _json.dumps(genres),
                "tags": _json.dumps(tags),
                "image_url": (
                    f"https://images.gr-assets.com/books/1{book_id}m/{book_id}.jpg"
                    if cover
                    else None
                ),
                "readers": ratings // 100,
            }
            for (
                book_id, title, authors, series, position, year, rating, ratings, pages, genres,
                tags, cover,
            ) in BOOK_ROWS
        ]
    ).astype({"year": "Int64", "pages": "Int64"})  # fmt: skip


def book_neighbours_frame():
    return pd.DataFrame(BOOK_NEIGHBOURS, columns=["book_id", "neighbour_id", "score", "co_readers"])


@pytest.fixture
def book_catalog():
    from clothes_recommender.books.catalog import BookCatalog

    return BookCatalog(book_frame())


@pytest.fixture
def book_recommender(book_catalog):
    from clothes_recommender.books.recommend import BookRecommender

    return BookRecommender(book_catalog, book_neighbours_frame())


def serve(fashion=None, cocktails=None, books=None, *, llm_configured: bool = False):
    """`Services` holding the given domain runtimes; a None domain is not served."""
    from clothes_recommender.services import Services

    domains = {"fashion": fashion, "cocktails": cocktails, "books": books}
    return Services(
        domains={name: runtime for name, runtime in domains.items() if runtime is not None},
        llm_configured=llm_configured,
    )


def with_fashion(services, **changes):
    """`services` with fields of its fashion runtime replaced."""
    import dataclasses

    fashion = dataclasses.replace(services.fashion, **changes)
    return serve(
        fashion, services.cocktails, services.books, llm_configured=services.llm_configured
    )


def with_cocktails(services, **changes):
    """`services` with fields of its cocktail runtime replaced."""
    import dataclasses

    cocktails = dataclasses.replace(services.cocktails, **changes)
    return serve(
        services.fashion, cocktails, services.books, llm_configured=services.llm_configured
    )
