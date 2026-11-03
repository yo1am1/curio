"""Read-only bridge from agent tools to the recommendation system.

The agent never touches recommender internals: it gets ranked product IDs plus the same
human-readable evidence the browsing pages show, so its explanations stay grounded.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from clothes_recommender.domain import ScoredProduct
from clothes_recommender.recommenders.base import UnknownUserError

Relation = Literal["complement", "similar", "alternative"]


@dataclass(frozen=True)
class Evidence:
    product_id: int
    reasons: tuple[str, ...] = ()


class CatalogNavigator(Protocol):
    def related(self, product_id: int, relation: Relation, top_k: int) -> list[Evidence]: ...

    def personal(self, shopper_id: int, top_k: int) -> list[Evidence]: ...


class _Graph(Protocol):
    def related(self, product_id: int, kind: str, top_k: int) -> list[ScoredProduct]: ...


class _Similar(Protocol):
    def similar_to(self, product_id: int, top_k: int) -> list[ScoredProduct] | None: ...


class _Explaining(Protocol):
    def recommend(self, user_id: int, top_k: int) -> list[ScoredProduct]: ...

    def reasons(self, user_id: int, product_id: int) -> list[str]: ...


class NoPersonalHistoryError(LookupError):
    pass


class RecommenderNavigator:
    """Serves FindRelated and PersonalPicks from the graph, CLIP index and ranker."""

    def __init__(self, graph: _Graph, search: _Similar, personal: _Explaining) -> None:
        self._graph = graph
        self._search = search
        self._personal = personal

    def related(self, product_id: int, relation: Relation, top_k: int) -> list[Evidence]:
        if relation == "similar":
            hits = self._search.similar_to(product_id, top_k) or []
            reason = "Closest product photo in the image index."
            return [Evidence(h.product_id, (reason,)) for h in hits]
        reason = (
            "Bought on the same day by the same shoppers; different product group."
            if relation == "complement"
            else "Same specific category, type and section, at a higher approximate price."
        )
        hits = self._graph.related(product_id, relation, top_k)
        return [Evidence(h.product_id, (reason,)) for h in hits]

    def personal(self, shopper_id: int, top_k: int) -> list[Evidence]:
        try:
            ranked = self._personal.recommend(shopper_id, top_k)
        except UnknownUserError as error:
            raise NoPersonalHistoryError(str(error)) from error
        explain = self._personal.reasons
        return [
            Evidence(item.product_id, tuple(explain(shopper_id, item.product_id)[:2]))
            for item in ranked
        ]
