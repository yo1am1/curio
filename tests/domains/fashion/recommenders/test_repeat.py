from datetime import date

import pytest

from curio.domains.fashion.models import ScoredProduct
from curio.domains.fashion.recommenders.base import UnknownUserError
from curio.domains.fashion.recommenders.popularity import PopularityRecommender
from curio.domains.fashion.recommenders.repeat import RepeatPurchaseRecommender


def test_recent_unique_purchases_then_popularity_with_deterministic_ties(make_customers):
    customers = make_customers(
        [1, 2],
        [
            (1, 2, date(2020, 1, 2)),
            (1, 1, date(2020, 1, 2)),
            (1, 1, date(2020, 1, 1)),
            (2, 3, date(2020, 1, 2)),
        ],
    )
    recommender = RepeatPurchaseRecommender(customers, PopularityRecommender(customers.purchases))
    assert recommender.recommend(1, 3) == [
        ScoredProduct(1, 1),
        ScoredProduct(2, 0.5),
        ScoredProduct(3, 1 / 3),
    ]
    assert len(recommender.recommend(1, 1)) == 1
    assert recommender.recommend(1, 0) == []
    assert recommender.reasons(1, 1) == ["Buy again: last purchased on 2020-01-02."]
    # The data ends on its own date: not "last week" from today.
    assert recommender.reasons(1, 3) == [
        "Popular as of 2 Jan 2020 (the data's last week); fills the list after recent purchases."
    ]
    with pytest.raises(UnknownUserError):
        recommender.recommend(99)


def test_known_customer_without_history_gets_best_sellers(make_customers):
    customers = make_customers([1, 2], [(1, 3, date(2020, 1, 2))])
    recommender = RepeatPurchaseRecommender(customers, PopularityRecommender(customers.purchases))
    assert recommender.recommend(2, 12) == [ScoredProduct(3, 1)]
