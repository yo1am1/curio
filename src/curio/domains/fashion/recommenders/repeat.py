"""Strong H&M baseline: unique recent purchases, followed by the data's last-week best-sellers."""

from curio.domains.fashion.catalog import CustomerData
from curio.domains.fashion.models import ScoredProduct
from curio.domains.fashion.recommenders.base import UnknownUserError
from curio.domains.fashion.recommenders.popularity import PopularityRecommender


class RepeatPurchaseRecommender:
    def __init__(self, customers: CustomerData, popular: PopularityRecommender) -> None:
        self._customers = customers
        self._popular = popular

    def recommend(self, user_id: int, top_k: int = 10) -> list[ScoredProduct]:
        if self._customers.get_user(user_id) is None:
            raise UnknownUserError(user_id)
        if top_k <= 0:
            return []
        purchases = sorted(
            self._customers.purchases_for(user_id),
            key=lambda item: (-item.purchase_date.toordinal(), item.product_id),
        )
        ids = list(dict.fromkeys(item.product_id for item in purchases))[:top_k]
        ids.extend(item.product_id for item in self._popular.top(top_k - len(ids), ids))
        return [ScoredProduct(product_id, 1 / rank) for rank, product_id in enumerate(ids, 1)]

    def reasons(self, user_id: int, product_id: int) -> list[str]:
        last = next(
            (
                item
                for item in self._customers.purchases_for(user_id)
                if item.product_id == product_id
            ),
            None,
        )
        if last is not None:
            return [f"Buy again: last purchased on {last.purchase_date.isoformat()}."]
        return [f"{self._popular.label()}; fills the list after recent purchases."]
