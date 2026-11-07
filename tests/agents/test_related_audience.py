"""FindRelated keeps related pieces in the product's own audience, unless the shopper asks
for another one (2.1.1: look-alikes of a men's rollneck included Ladieswear)."""

from langchain_core.messages import AIMessage

from curio.app.assistants import ShoppingAssistant
from curio.domains.fashion.agent.toolkit import related_sections
from curio.domains.fashion.models import ScoredProduct
from curio.domains.fashion.navigator import Evidence, RecommenderNavigator

NO_SEARCH = {"query": "", "wants_products": False}


def action(name, args, call_id="tool_1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


# --- Fashion: related pieces stay in the shopper's audience ----------------------------------


class RecordingNavigator:
    def __init__(self):
        self.sections = None

    def related(self, product_id, relation, top_k, sections=()):
        self.sections = tuple(sections)
        return [Evidence(1, ("Closest product photo in the image index.",))]

    def personal(self, shopper_id, top_k):  # pragma: no cover - not used here
        return []


def find_related(chat_model_replying, search, catalog, args):
    navigator = RecordingNavigator()
    model = chat_model_replying(NO_SEARCH, action("FindRelated", args), "Here they are.")
    reply = ShoppingAssistant(model, search, catalog, navigator=navigator).reply("Similar?", [])
    return navigator.sections, reply


def test_find_related_keeps_the_products_audience(chat_model_replying, search, catalog):
    # Product 1 is Menswear: look-alikes come from Menswear only.
    sections, reply = find_related(
        chat_model_replying, search, catalog, {"product_id": 1, "relation": "similar"}
    )
    assert sections == ("Menswear",)
    step = next(s for s in reply.steps if s.name == "FindRelated")
    assert step.summary == "Found 1 look-alikes of Slim Jeans in Menswear."
    assert step.detail["sections"] == ["Menswear"]
    # Product 3 is Ladieswear: womenswear is Ladieswear and Divided.
    sections, _ = find_related(
        chat_model_replying, search, catalog, {"product_id": 3, "relation": "complement"}
    )
    assert sections == ("Ladieswear", "Divided")


def test_find_related_follows_an_audience_the_shopper_asks_for(
    chat_model_replying, search, catalog
):
    args = {"product_id": 1, "relation": "similar", "audience": "women"}
    sections, _ = find_related(chat_model_replying, search, catalog, args)
    assert sections == ("Ladieswear", "Divided")


def test_a_section_with_no_audience_is_not_restricted(chat_model_replying, search, catalog):
    # Product 2 is Sport, which the catalog does not split by audience.
    sections, _ = find_related(
        chat_model_replying, search, catalog, {"product_id": 2, "relation": "similar"}
    )
    assert sections == ()
    assert related_sections(catalog.get(2), "men") == ("Menswear",)


# --- The navigator applies the sections to every relation ------------------------------------


class FakeGraph:
    def related(self, product_id, kind, top_k):
        hits = [ScoredProduct(3, 0.9), ScoredProduct(2, 0.8), ScoredProduct(1, 0.7)]
        return hits[:top_k]


class FakeSimilar:
    def __init__(self):
        self.calls = []

    def similar_to(self, product_id, top_k, sections=()):
        self.calls.append((product_id, top_k, tuple(sections)))
        return [ScoredProduct(1, 0.9)]


def test_the_navigator_filters_graph_relations_and_the_photo_search(catalog):
    similar = FakeSimilar()
    navigator = RecommenderNavigator(FakeGraph(), similar, None, catalog=catalog)
    for relation, reason in (
        ("complement", "Bought on the same day by the same shoppers; different product group."),
        ("alternative", "Same specific category, type and section, at a higher approximate price."),
    ):
        # Unrestricted: the graph's order, cut to top_k.
        assert [e.product_id for e in navigator.related(1, relation, 2)] == [3, 2]
        kept = navigator.related(1, relation, 2, sections=("Menswear",))
        assert kept == [Evidence(1, (reason,))]
    navigator.related(1, "similar", 5, sections=("Menswear",))
    assert similar.calls == [(1, 5, ("Menswear",))]
