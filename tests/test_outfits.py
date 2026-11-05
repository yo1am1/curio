"""Outfit planner: slot plans, cheapest-feasible budget allocation, coherence and swaps."""

import numpy as np
import pytest

from clothes_recommender.outfits import (
    SLOT_TYPES,
    FilledSlot,
    OutfitError,
    OutfitPiece,
    OutfitPlanner,
    OutfitRequest,
    OutfitState,
    PlannedSlots,
    audience_in,
    named_types,
    occasion_season,
    occasion_style,
    plan_slots,
)


class Links:
    def __init__(self, edges):
        self._edges = {}
        for (a, b), value in edges.items():
            self._edges.setdefault(a, {})[b] = value
            self._edges.setdefault(b, {})[a] = value

    def bought_together(self, product_id):
        return self._edges.get(product_id, {})


class Vectors:
    def __init__(self, vectors):
        self._vectors = {pid: np.asarray(v, dtype=np.float32) for pid, v in vectors.items()}

    def get_vectors(self, product_ids):
        return {pid: self._vectors[pid] for pid in product_ids if pid in self._vectors}


@pytest.fixture
def planner(outfit_search, outfit_catalog):
    return OutfitPlanner(outfit_search, outfit_catalog)


def build(planner, *args, **kwargs):
    return planner.outfit(OutfitRequest(*args, **kwargs))


def picked(outfit):
    return {slot.slot: slot.product.product_id if slot.product else None for slot in outfit.slots}


# --- Plans ---------------------------------------------------------------------------------


def test_occasion_words_choose_style_season_and_audience():
    assert occasion_style("Summer wedding in July") == "wedding"
    assert occasion_style("job interview") == "office"
    assert occasion_style("a workout") == "sport"  # "work" is not "workout"
    assert occasion_style("something nice") == "casual"
    assert occasion_season("summer wedding") == "summer"
    assert occasion_season("beach holiday") == "summer"
    assert occasion_season("office") == ""
    assert audience_in("a wedding outfit for him") == "men"
    assert audience_in("for my girlfriend") == "women"
    assert audience_in("a party") is None
    assert audience_in("wedding outfit for my husband") == "men"
    assert audience_in("Party look for my son") is None  # no audience word
    assert audience_in("first date dinner with my boyfriend") is None  # a companion
    assert audience_in("dinner with my wife, for him") == "men"


def test_each_occasion_plans_three_or_four_distinct_slots_per_audience():
    for occasion in ("wedding", "office", "party", "beach", "gym", "weekend"):
        for audience in ("women", "men", "kids"):
            slots = plan_slots(occasion, audience)
            assert 3 <= len(slots) <= 4
            assert len({s.slot for s in slots}) == len(slots)
            for spec in slots:
                assert set(spec.types) <= set(SLOT_TYPES[spec.slot])


def test_wedding_plans_differ_by_audience_and_season():
    assert [s.slot for s in plan_slots("summer wedding", "women")] == [
        "dress",
        "shoes",
        "bag",
        "accessory",
    ]
    assert plan_slots("summer wedding", "women")[0].query == "elegant summer wedding guest dress"
    assert [s.slot for s in plan_slots("wedding", "men")] == [
        "top",
        "bottom",
        "outerwear",
        "shoes",
    ]
    winter = plan_slots("winter weekend", "women")
    assert [s.slot for s in winter] == ["top", "bottom", "shoes", "outerwear"]
    assert winter[2].types == ("Boots",) and winter[3].types == ("Coat", "Jacket")


def test_named_slots_are_filled_in_priority_order_with_generic_specs():
    specs = plan_slots("wedding", "women", ["accessory", "top", "shoes"])
    assert [s.slot for s in specs] == ["top", "shoes", "accessory"]
    # The wedding template has no top: every top that suits a wedding.
    assert specs[0].types == tuple(t for t in SLOT_TYPES["top"] if t not in ("Vest top", "Hoodie"))
    assert specs[1].query == "elegant heeled sandals"  # the template's shoes


# --- Build ---------------------------------------------------------------------------------


def test_build_streams_the_plan_then_each_slot(planner):
    stages = list(planner.build(OutfitRequest("wedding", 80, "women")))
    assert isinstance(stages[0], PlannedSlots)
    assert [s.slot for s in stages[0].slots] == ["dress", "shoes", "bag", "accessory"]
    assert all(isinstance(stage, FilledSlot) for stage in stages[1:])
    assert [stage.slot.slot for stage in stages[1:]] == ["dress", "shoes", "bag", "accessory"]


def test_outfit_fits_the_budget_with_one_piece_of_the_right_kind_per_slot(planner):
    outfit = build(planner, "summer wedding", 80, "women")
    assert outfit.status == "complete"
    assert picked(outfit) == {"dress": 1, "shoes": 3, "bag": 5, "accessory": 6}
    assert outfit.total == 75.96 and outfit.left == 4.04
    for slot in outfit.slots:
        assert slot.product.product_type in SLOT_TYPES[slot.slot]
        assert slot.product.section == "Ladieswear"
    assert outfit.state.pieces == [
        OutfitPiece(slot="dress", product_id=1),
        OutfitPiece(slot="shoes", product_id=3),
        OutfitPiece(slot="bag", product_id=5),
        OutfitPiece(slot="accessory", product_id=6),
    ]


def test_menswear_outfit_is_a_shirt_trousers_blazer_and_shoes(planner):
    outfit = build(planner, "wedding", 150, "men")
    assert picked(outfit) == {"top": 8, "bottom": 9, "outerwear": 10, "shoes": 11}
    assert outfit.total == 134.96


def test_planning_is_deterministic(planner):
    first = build(planner, "summer wedding", 80, "women")
    assert build(planner, "summer wedding", 80, "women") == first


def test_a_slot_spends_only_what_the_later_slots_do_not_need(planner, outfit_search):
    # The silk dress is the best match, but at €49.99 it would leave no shoes under €60.
    outfit_search.boost[2] = 0.05
    outfit = build(planner, "wedding", 60, "women", ("dress", "shoes"))
    assert picked(outfit) == {"dress": 1, "shoes": 3}
    assert outfit.total == 54.98
    assert "within what is left of the budget" in outfit.slots[0].reason
    # Without a budget the best match wins.
    assert picked(build(planner, "wedding", None, "women", ("dress", "shoes")))["dress"] == 2


def test_infeasible_budget_names_the_slot_that_cannot_fit(planner):
    outfit = build(planner, "wedding", 70, "women")
    assert outfit.status == "partial"
    assert picked(outfit) == {"dress": 1, "shoes": 3, "bag": 5, "accessory": None}
    accessory = outfit.slots[3]
    assert accessory.cheapest == 5.99
    assert accessory.issue == (
        "The budget cannot cover the accessory: the cheapest match costs €5.99, more than is "
        "left after the cheapest dress, shoes and bag."
    )
    assert outfit.total == 69.97 and outfit.minimum == 75.96
    assert "A complete outfit needs at least €75.96." in outfit.notes
    assert {69.97, 70.0, 0.03, 75.96, 5.96, 5.99} <= set(outfit.amounts())


def test_a_slot_without_any_match_is_reported(planner):
    outfit = build(planner, "wedding", None, "men", ("top", "bag"))
    assert picked(outfit) == {"top": 8, "bag": None}
    assert outfit.slots[1].issue == "No bag matched this look in the photo index."
    assert outfit.status == "partial" and outfit.minimum is None


def test_remembered_price_limit_applies_to_every_piece(planner):
    outfit = build(planner, "wedding", None, "women", max_item_price=30)
    assert picked(outfit)["dress"] is None
    assert (
        outfit.slots[0].issue == "No dress matched this look in the photo index at €30.00 or less."
    )
    assert all(s.product.price <= 30 for s in outfit.slots if s.product)


def test_pieces_that_are_not_worn_are_never_picked(planner, outfit_search):
    outfit_search.boost[7] = 1.0  # the laundry bag
    assert picked(build(planner, "weekend", None, "women"))["bag"] == 5


def test_audience_comes_from_the_best_match_when_not_given(planner, outfit_search):
    outfit = build(planner, "summer wedding", 80)
    assert outfit.audience == "women" and picked(outfit)["dress"] == 1
    assert outfit.notes == ["Planned for women from the best match; ask for another audience."]
    outfit_search.boost[8] = 0.1  # the Oxford Shirt becomes the best match
    assert build(planner, "summer wedding", 200).audience == "men"


def test_the_audience_named_in_the_occasion_beats_the_best_match(planner, outfit_search):
    # The women's dress is the best garment match, but the occasion names the wearer.
    outfit = build(planner, "wedding outfit for my husband", 150)
    assert outfit.audience == "men"
    assert {slot.slot for slot in outfit.slots} == {"top", "bottom", "outerwear", "shoes"}
    assert not any("best match" in note for note in outfit.notes)
    # A companion is not the wearer: the best match still decides.
    assert build(planner, "summer wedding with my boyfriend", 80).audience == "women"


def test_bought_together_pieces_are_preferred_and_explained(outfit_search, outfit_catalog):
    planner = OutfitPlanner(outfit_search, outfit_catalog, links=Links({(1, 4): (3, 2.0)}))
    outfit = build(planner, "wedding", None, "women")
    shoes = outfit.slots[1]
    assert shoes.product.product_id == 4  # less relevant than the sandals on its own
    assert shoes.reason == "Bought together with the Linen Wrap Dress in 3 baskets."
    assert outfit.slots[0].reason == "Best match for “elegant wedding guest dress”."


def test_visually_coherent_pieces_are_preferred_and_explained(outfit_search, outfit_catalog):
    vectors = Vectors({1: [1, 0], 3: [0, 1], 4: [1, 0]})
    planner = OutfitPlanner(outfit_search, outfit_catalog, vectors=vectors)
    shoes = build(planner, "wedding", None, "women").slots[1]
    assert shoes.product.product_id == 4
    assert shoes.reason == "Its photo matches the look of the Linen Wrap Dress."


# --- Swap ----------------------------------------------------------------------------------


def state(budget=90.0, **pieces):
    return OutfitState(
        occasion="wedding",
        budget=budget,
        audience="women",
        pieces=[OutfitPiece(slot=slot, product_id=pid) for slot, pid in pieces.items()],
    )


def test_swap_other_replaces_one_piece_within_the_budget(planner):
    swap = planner.swap(state(dress=1, shoes=3, bag=5, accessory=6), "shoes")
    assert swap.old.product_id == 3 and swap.new.product_id == 4
    assert picked(swap.outfit) == {"dress": 1, "shoes": 4, "bag": 5, "accessory": 6}
    assert swap.outfit.total == 80.96
    assert swap.outfit.state.skipped == [3]
    # Another swap does not bring the sandals back: nothing else is left.
    again = planner.swap(swap.outfit.state, "shoes")
    assert again.new is None and picked(again.outfit)["shoes"] == 4


def test_swap_cheaper_finds_a_lower_price_and_never_exceeds_the_budget(planner):
    swap = planner.swap(state(100, dress=2, shoes=3), "dress", "cheaper")
    assert swap.new.product_id == 1 and swap.outfit.total == 54.98
    # The heels are more expensive, and €80 leaves no room for other shoes either.
    assert planner.swap(state(80, dress=1, shoes=3), "shoes", "cheaper").new is None
    assert planner.swap(state(55, dress=1, shoes=3), "shoes").new is None


def test_swap_refines_the_query(planner, outfit_search):
    swap = planner.swap(state(dress=1, accessory=6), "accessory", refine="pearl necklace")
    assert swap.new.product_id == 13
    assert outfit_search.queries[-1][0] == "elegant earrings pearl necklace"


def test_swap_needs_an_outfit_with_that_slot(planner):
    with pytest.raises(OutfitError, match="has no top; its slots are dress, shoes"):
        planner.swap(state(dress=1, shoes=3), "top")
    with pytest.raises(OutfitError, match="no outfit to change"):
        planner.swap(state(dress=999), "dress")


def test_restore_rereads_prices_and_drops_unknown_pieces(planner):
    outfit = planner.restore(state(dress=1, shoes=999))
    assert picked(outfit) == {"dress": 1} and outfit.total == 34.99
    assert planner.restore(state(dress=999)) is None


# --- Off-occasion pieces and honest reasons ------------------------------------------------


def test_fallback_types_leave_out_what_does_not_suit_the_occasion():
    for occasion in ("summer wedding", "office", "party", "first date dinner"):
        for audience in ("women", "men", "kids"):
            for spec in plan_slots(occasion, audience):
                assert "Flip flop" not in spec.types + spec.fallback, (occasion, spec)
    wedding = {s.slot: s for s in plan_slots("summer wedding", "women")}
    assert "Sneakers" not in wedding["shoes"].fallback
    assert {"Boots", "Flat shoes", "Other shoe"} <= set(wedding["shoes"].fallback)
    beach = {s.slot: s for s in plan_slots("beach holiday", "women")}
    assert "Flip flop" in beach["shoes"].types


def test_queries_name_the_types_they_ask_for():
    assert named_types("leather belt", ("Belt", "Bracelet", "Watch")) == ("Belt",)
    shoes = ("Sandals", "Heels", "Pumps", "Wedge", "Ballerinas", "Flat shoe")
    assert named_types("elegant heeled sandals", shoes) == ("Sandals", "Heels")
    assert named_types("elegant earrings", ("Earring", "Earrings", "Necklace")) == (
        "Earring",
        "Earrings",
    )
    assert named_types("small elegant handbag", ("Bag", "Shoulder bag")) == ("Bag", "Shoulder bag")
    # A query that names the whole slot, or no type, accepts every type.
    assert named_types("smart leather shoes", ("Boots", "Other shoe")) == ()
    assert named_types("tailored blazer", ("Cardigan",)) == ()


def _with(*rows):
    import pandas as pd

    from clothes_recommender.catalog import ProductCatalog
    from clothes_recommender.domain import Product
    from tests.conftest import OutfitSearch, outfit_products

    extra = [
        Product(
            product_id=pid,
            name=name,
            product_type=kind,
            product_group=group,
            section=section,
            colour="Black",
            description=f"{name}.",
            price=price,
        )
        for pid, name, kind, group, section, price in rows
    ]
    products = [*outfit_products(), *extra]
    catalog = ProductCatalog(pd.DataFrame([p.model_dump() for p in products]))
    return OutfitPlanner(OutfitSearch(products), catalog)


def test_cheaper_wedding_shoes_never_end_in_flip_flops_and_say_what_they_are():
    planner = _with(
        (20, "Yaz flip flop", "Flip flop", "Shoes", "Ladieswear", 1.99),
        (21, "Canvas sneaker", "Sneakers", "Shoes", "Ladieswear", 2.99),
        (22, "Soft loafer", "Other shoe", "Shoes", "Ladieswear", 3.49),
    )
    swap = planner.swap(state(80, dress=1, shoes=3), "shoes", "cheaper")
    assert swap.new.product_id == 22  # no wedding-type shoe is cheaper than €19.99
    reason = next(s.reason for s in swap.outfit.slots if s.slot == "shoes")
    assert reason == (
        "Not the kind “elegant heeled sandals” asks for (sandals or heels): none is "
        "available within the budget, so this is the closest shoe instead."
    )
    # Nothing cheaper suits a wedding: the flip flops and sneakers stay out.
    assert planner.swap(swap.outfit.state, "shoes", "cheaper").new is None


def test_a_piece_of_another_type_than_the_query_names_says_so():
    planner = _with((23, "Bracelet Lincoln", "Bracelet", "Accessories", "Menswear", 2.93))
    outfit = build(planner, "first date dinner", audience="men")
    accessory = next(s for s in outfit.slots if s.slot == "accessory")
    assert accessory.product.product_id == 23
    assert accessory.reason == "Closest accessory to “leather belt”: a bracelet, not a belt."
    # A piece of a named type keeps the plain reason.
    shoes = next(
        s for s in build(planner, "summer wedding", 80, "women").slots if s.slot == "shoes"
    )
    assert shoes.reason == "Best match for “elegant heeled sandals”."
