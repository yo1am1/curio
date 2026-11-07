"""Fixtures shared by the API tests."""

from datetime import date

import pandas as pd
import pytest
from PIL import Image

from curio.app.services import FashionServices, Services
from curio.domains.fashion.catalog import CustomerData
from curio.domains.fashion.recommenders.collaborative import CollaborativeRecommender
from curio.domains.fashion.recommenders.content_based import ContentBasedRecommender
from curio.domains.fashion.recommenders.graph import GraphRecommender
from curio.domains.fashion.recommenders.popularity import PopularityRecommender
from curio.domains.fashion.recommenders.repeat import RepeatPurchaseRecommender
from curio.domains.fashion.search import ProductSearch
from tests.conftest import serve


@pytest.fixture
def services(sample_catalog, vector_store, fake_encoder, tmp_path) -> Services:
    users = pd.DataFrame({"user_id": [1, 2], "age": [34, 27]})
    purchases = pd.DataFrame(
        [(1, 1, date(2020, 9, 2)), (2, 1, date(2020, 9, 3)), (2, 3, date(2020, 9, 4))],
        columns=["user_id", "product_id", "purchase_date"],
    )
    Image.new("RGB", (4, 6), "red").save(tmp_path / "3.jpg")
    customers = CustomerData(users, purchases)
    popularity = PopularityRecommender(customers.purchases)
    content_based = ContentBasedRecommender(customers, sample_catalog, vector_store, popularity)
    # The sampled customers double as the whole crowd in this tiny fixture.
    crowd = purchases.rename(columns={"user_id": "shopper_id"})
    collaborative = CollaborativeRecommender(customers, sample_catalog, crowd, popularity)
    fashion = FashionServices(
        catalog=sample_catalog,
        customers=customers,
        store=vector_store,
        search=ProductSearch(fake_encoder, vector_store),
        content_based=content_based,
        collaborative=collaborative,
        graph=GraphRecommender(
            customers,
            sample_catalog,
            popularity,
            pd.DataFrame([(1, 3, 2, 1.5)], columns=["product_id", "other_id", "count", "lift"]),
        ),
        repeat=RepeatPurchaseRecommender(customers, popularity),
        images_dir=tmp_path,
        assistant=None,
    )
    return serve(fashion)
