import pytest
from PIL import Image

from curio.domains.fashion.models import Encoder, Product, ProductFilter
from curio.domains.fashion.search import ProductSearch
from curio.domains.fashion.vector_store import ProductVectorStore


@pytest.fixture
def search(fake_encoder: Encoder, vector_store: ProductVectorStore) -> ProductSearch:
    return ProductSearch(fake_encoder, vector_store)


def test_by_text_returns_best_match_first(search: ProductSearch):
    hits = search.by_text("Cotton Polo Shirt", top_k=2)

    assert len(hits) == 2
    assert hits[0].product_id == 4


def test_by_text_applies_filter(search: ProductSearch):
    hits = search.by_text(
        "Cotton Polo Shirt",
        top_k=10,
        product_filter=ProductFilter(product_types=("Boots", "Sneakers")),
    )

    assert {hit.product_id for hit in hits} == {5, 6}


def test_by_image_finds_product_indexed_from_same_photo(
    search: ProductSearch,
    fake_encoder: Encoder,
    vector_store: ProductVectorStore,
    sample_products: list[Product],
):
    photo = Image.new("RGB", (4, 4), color=(200, 30, 30))
    boots = sample_products[5]
    vector_store.upsert([boots], fake_encoder.encode_images([photo]))

    hits = search.by_image(photo, top_k=1)

    assert hits[0].product_id == boots.product_id
    assert hits[0].score == pytest.approx(1.0, abs=1e-5)


def test_similar_to_stays_in_the_given_sections(search: ProductSearch):
    everything = search.similar_to(1, top_k=10)
    assert {hit.product_id for hit in everything} == {2, 3, 4, 5, 6}
    menswear = search.similar_to(1, top_k=10, sections=("Menswear",))
    assert [hit.product_id for hit in menswear] == [4]
    assert search.similar_to(99, top_k=10, sections=("Menswear",)) is None
