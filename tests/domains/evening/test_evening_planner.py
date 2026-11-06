"""Plan my evening: theme mapping, the three parts, budget, alcohol-free, swaps, degradation."""

import pytest

from curio.domains.books.models import BookPreferences
from curio.domains.cocktails.models import CocktailPreferences
from curio.domains.evening.models import EveningRequest, EveningState, EveningTastes
from curio.domains.evening.planner import (
    AUDIENCE_NOTE,
    BOOK_NEEDS_BOOKS,
    DRINK_NEEDS_BOOK,
    DRINK_NEEDS_COCKTAILS,
    OUTFIT_NEEDS_FASHION,
    EveningError,
    alcohol_free_in,
    budget_in,
    resolve_alcohol_free,
    wants_alcohol_free,
)
from curio.domains.evening.themes import THEMES, detect_theme
from curio.domains.pairings.models import PairingRequest
from tests.domains.evening.conftest import make_planner

OCCASIONS = [
    "rainy Sunday in",
    "date night under €60",
    "book club, alcohol-free",
    "summer garden party",
    "murder mystery night",
    "game night with friends",
    "weekend camping trip",
    "New Year's Eve party",
]


def plan(planner, occasion: str, **fields):
    return planner.plan(EveningRequest(occasion=occasion, **fields))


def paired(pairer, plan_, **fields) -> set[int]:
    """Every drink the pairer pairs with the plan's book, over all its sets."""
    request = PairingRequest(book_id=plan_.book.book.book_id, top_k=5, **fields)
    first = pairer.pair(request)
    found = set()
    for seed in range(first.sets):
        result = pairer.pair(request.model_copy(update={"seed": seed}))
        found |= {p.cocktail.drink_id for p in result.pairings}
    return found


# --- Theme -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("occasion", "theme", "words"),
    [
        ("rainy Sunday in", "cosy", ("rainy", "Sunday")),
        ("date night", "romantic", ("date night",)),
        ("first date dinner", "romantic", ("first date",)),
        ("summer garden party", "summery", ("summer", "garden")),
        ("New Year's Eve party", "glamorous", ("New Year's Eve", "party")),
        ("book club", "thoughtful", ("book club",)),
        ("murder mystery night", "mysterious", ("murder mystery",)),
        ("weekend camping trip", "adventurous", ("camping", "trip")),
        ("game night with friends", "playful", ("game night", "friends")),
    ],
)
def test_occasions_map_to_themes_by_their_words(occasion, theme, words):
    match = detect_theme(occasion)
    assert (match.theme.id, match.words, match.source) == (theme, words, "occasion")


def test_a_mood_wins_and_an_occasion_without_theme_words_is_cosy():
    assert detect_theme("dinner", "spooky").theme.id == "mysterious"
    assert detect_theme("dinner", "Glamorous").source == "mood"
    fallback = detect_theme("Tuesday")
    assert (fallback.theme.id, fallback.source, fallback.words) == ("cosy", "default", ())
    assert "names no theme" in fallback.evidence("Tuesday")
    assert len({t.id for t in THEMES}) == len(THEMES) == 8


def test_the_theme_drives_the_outfit_book_and_drink(planner):
    cosy = plan(planner, "rainy Sunday in")
    assert cosy.theme.id == "cosy"
    assert cosy.theme.evidence == "you mentioned rainy and Sunday"
    assert [s.slot for s in cosy.outfit.outfit.slots] == ["top", "bottom", "outerwear"]
    assert cosy.outfit.outfit.slots[0].product.name == "Chunky Knit Sweater"
    assert cosy.book.book.title == "The Name of the Wind"
    assert cosy.drink.cocktail.name == "Honey Bee"
    assert cosy.drink.evening_fit == ["Honey"]
    party = plan(planner, "New Year's Eve party")
    assert [s.slot for s in party.outfit.outfit.slots] == ["dress", "shoes", "bag", "accessory"]
    assert party.book.book.title == "The Great Gatsby"


def test_the_explanation_cites_evidence_from_every_part(planner):
    cosy = plan(planner, "rainy Sunday in")
    assert cosy.why == (
        "Rainy Sunday in → cosy: Chunky Knit Sweater (€24.99) + 2 more, €69.97 in all · "
        "The Name of the Wind (fantasy, ★4.55) · Honey Bee (Honey; pairs with fantasy and magic)"
    )
    assert cosy.status == "complete" and not cosy.missing
    assert cosy.outfit.reason.startswith("Cosy: a warm night in calls for chunky knit sweater")
    assert cosy.book.reason == (
        "Cosy pick, a fantasy read. Fantasy · ★4.55 from 400,000 Goodreads ratings."
    )
    assert cosy.drink.reason.startswith("The Name of the Wind (Fantasy")
    assert cosy.drink.reasons[-1] == "Suits a warm night in: warming (Honey)"


@pytest.mark.parametrize("occasion", OCCASIONS)
def test_the_drink_always_comes_from_the_pairer_on_the_chosen_book(planner, pairer, occasion):
    evening = plan(planner, occasion)
    assert evening.book is not None and evening.drink is not None
    free = evening.state.alcohol_free
    assert evening.drink.cocktail.drink_id in paired(pairer, evening, alcohol_free=free)
    assert evening.state.book_id == evening.book.book.book_id
    assert evening.state.drink_id == evening.drink.cocktail.drink_id


@pytest.mark.parametrize("occasion", OCCASIONS)
def test_plans_are_deterministic(occasion):
    first = plan(make_planner(), occasion).model_dump()
    assert plan(make_planner(), occasion).model_dump() == first


# --- Budget, audience, alcohol ---------------------------------------------------------------


def test_the_budget_caps_the_outfit_and_can_be_written_in_the_occasion(planner):
    assert budget_in("date night under €100") == 100
    assert budget_in("date night under 80 euros") == 80
    assert budget_in("party for 4 friends") is None
    evening = plan(planner, "date night under €60")
    assert evening.state.budget == 60
    assert evening.outfit.outfit.budget == 60
    assert evening.outfit.outfit.total <= 60
    roomy = plan(planner, "date night", budget=200)
    assert roomy.outfit.outfit.total <= 200
    assert len(roomy.outfit.outfit.products) > len(evening.outfit.outfit.products)


def test_a_budget_nothing_fits_says_so_and_plans_the_rest(planner):
    evening = plan(planner, "rainy Sunday in", budget=1)
    assert evening.outfit is None
    gap = next(m for m in evening.missing if m.part == "outfit")
    assert gap.reason.startswith("No outfit fits this evening's budget.")
    assert evening.book is not None and evening.drink is not None
    assert evening.status == "partial"
    assert evening.why.endswith("· no outfit yet")


def test_a_remembered_piece_limit_caps_every_piece(planner):
    evening = plan(planner, "rainy Sunday in", max_item_price=20)
    assert all(p.price <= 20 for p in evening.outfit.outfit.products)


def test_audience_defaults_to_womenswear_and_men_get_menswear(planner):
    women = plan(planner, "rainy Sunday in")
    assert women.outfit.outfit.audience == "women"
    assert AUDIENCE_NOTE in women.outfit.outfit.notes
    men = plan(planner, "rainy Sunday in", audience="men")
    assert {p.section for p in men.outfit.outfit.products} == {"Menswear"}
    assert AUDIENCE_NOTE not in men.outfit.outfit.notes
    assert plan(planner, "date night for him").state.audience == "men"


def test_alcohol_free_keeps_the_drink_alcohol_free_however_it_is_asked(planner):
    assert wants_alcohol_free("book club, alcohol-free")
    assert wants_alcohol_free("a sober night") and not wants_alcohol_free("a night out")
    for evening in (
        plan(planner, "book club, alcohol-free"),
        plan(planner, "murder mystery night", alcohol_free=True),
        plan(
            planner,
            "murder mystery night",
            cocktail_preferences=CocktailPreferences(alcohol_free=True),
        ),
    ):
        assert evening.drink.cocktail.alcohol_free
        assert evening.drink.alcohol_free_reason
    assert not plan(planner, "murder mystery night").drink.cocktail.alcohol_free


def test_a_young_adult_book_always_gets_an_alcohol_free_drink(planner):
    state = plan(planner, "murder mystery night").state.model_copy(
        update={"book_id": 6, "drink_id": 2}  # Harry Potter, with an alcoholic drink
    )
    evening = planner.restore(state)
    assert evening.book.book.title == "Harry Potter and the Sorcerer's Stone"
    assert evening.drink.cocktail.alcohol_free
    assert "children's or young-adult" in evening.drink.alcohol_free_reason


def test_remembered_tastes_shape_the_book_and_drink(planner):
    tastes = {
        "book_preferences": BookPreferences(avoided_genres=["Fantasy"]),
        "cocktail_preferences": CocktailPreferences(avoided_ingredients=["Honey"]),
    }
    evening = plan(planner, "rainy Sunday in", **tastes)
    assert "Fantasy" not in evening.book.book.genres
    assert all(i.name != "Honey" for i in evening.drink.cocktail.ingredients)


# --- Swaps -----------------------------------------------------------------------------------


def test_another_book_is_re_paired_and_never_comes_back(planner, pairer):
    first = plan(planner, "rainy Sunday in")
    second = planner.swap(first.state, "book")
    assert second.book.book.book_id != first.book.book.book_id
    assert second.state.skipped_books == [first.book.book.book_id]
    assert second.drink.cocktail.drink_id in paired(pairer, second)
    assert second.change.startswith(f"Another book: {first.book.book.title} → ")
    assert "the drink is re-paired with it" in second.change
    assert second.outfit.outfit.total == first.outfit.outfit.total  # the outfit stays
    third = planner.swap(second.state, "book")
    assert third.book.book.book_id not in {first.book.book.book_id, second.book.book.book_id}


def test_another_drink_and_an_alcohol_free_drink_stay_with_the_book(planner, pairer):
    first = plan(planner, "murder mystery night")
    other = planner.swap(first.state, "drink")
    assert other.book.book.book_id == first.book.book.book_id
    assert other.drink.cocktail.drink_id != first.drink.cocktail.drink_id
    assert other.state.skipped_drinks == [first.drink.cocktail.drink_id]
    assert other.drink.cocktail.drink_id in paired(pairer, other)
    free = planner.swap(other.state, "drink", mode="alcohol_free")
    assert free.drink.cocktail.alcohol_free and free.state.alcohol_free
    assert free.change.startswith("An alcohol-free drink: ")
    again = planner.swap(free.state, "drink", mode="alcohol_free")
    assert again.change.endswith("is already alcohol-free.")


def test_a_cheaper_piece_keeps_the_rest_of_the_evening(planner):
    first = plan(planner, "rainy Sunday in")
    top = first.outfit.outfit.slots[0].product
    cheaper = planner.swap(first.state, "outfit", slot="top", mode="cheaper")
    new_top = cheaper.outfit.outfit.slots[0].product
    assert new_top.price < top.price
    assert cheaper.book.book.book_id == first.book.book.book_id
    assert cheaper.drink.cocktail.drink_id == first.drink.cocktail.drink_id
    assert cheaper.change.startswith(f"A cheaper top: {top.name} → {new_top.name}")


def test_restore_rereads_every_part_and_re_pairs_a_forged_drink(planner, pairer):
    first = plan(planner, "rainy Sunday in")
    restored = planner.restore(first.state)
    assert (restored.why, restored.book, restored.drink, restored.state) == (
        first.why,
        first.book,
        first.drink,
        first.state,
    )
    # The pieces' reasons came back from the client: shown, never evidence.
    assert all(s.carried for s in restored.outfit.outfit.slots)
    forged = first.state.model_copy(update={"drink_id": 18})  # Zombie: never a pairing
    restored = planner.restore(forged)
    assert restored.drink.cocktail.drink_id in paired(pairer, restored)
    unknown = planner.restore(first.state.model_copy(update={"book_id": 9999}))
    assert unknown.book is not None  # an unknown book is planned again


@pytest.mark.parametrize(
    ("part", "fields", "message"),
    [
        ("book", {"mode": "cheaper"}, "Only an outfit piece can be made cheaper."),
        ("outfit", {"slot": "top", "mode": "alcohol_free"}, "Only the drink can be made"),
        ("outfit", {}, "Name the outfit piece to change"),
        ("book", {"slot": "top"}, "A slot only applies to the outfit."),
        ("outfit", {"slot": "dress"}, "This outfit has no dress"),
    ],
)
def test_invalid_swaps_say_what_to_change(planner, part, fields, message):
    state = plan(planner, "rainy Sunday in").state
    with pytest.raises(EveningError, match=message):
        planner.swap(state, part, **fields)


def test_swapping_a_part_the_plan_lacks_is_an_error():
    no_fashion = make_planner(fashion=False)
    state = no_fashion.plan(EveningRequest(occasion="rainy Sunday in")).state
    with pytest.raises(EveningError, match="fashion catalogue"):
        no_fashion.swap(state, "outfit", slot="top")
    empty = EveningState(occasion="rainy Sunday in", theme="cosy")
    with pytest.raises(EveningError, match="Plan the evening first"):
        make_planner(books=False).swap(empty, "outfit", slot="top")


# --- Degradation -----------------------------------------------------------------------------


def test_without_fashion_it_plans_the_book_and_drink_and_says_why():
    planner = make_planner(fashion=False)
    assert planner.parts == ["book", "drink"]
    evening = planner.plan(EveningRequest(occasion="rainy Sunday in"))
    assert evening.outfit is None and evening.state.outfit is None
    assert [(m.part, m.reason) for m in evening.missing] == [("outfit", OUTFIT_NEEDS_FASHION)]
    assert evening.book.book.title == "The Name of the Wind"
    assert evening.drink.cocktail.name == "Honey Bee"
    assert evening.status == "partial"
    assert evening.why == (
        "Rainy Sunday in → cosy: The Name of the Wind (fantasy, ★4.55) · Honey Bee (Honey; "
        "pairs with fantasy and magic) · no outfit yet"
    )
    assert planner.swap(evening.state, "book").book is not None


def test_with_only_one_of_books_or_cocktails_it_says_what_is_missing():
    books_only = make_planner(cocktails=False).plan(EveningRequest(occasion="date night"))
    assert books_only.book is not None and books_only.drink is None
    assert ("drink", DRINK_NEEDS_COCKTAILS) in [(m.part, m.reason) for m in books_only.missing]
    drinks_only = make_planner(books=False).plan(EveningRequest(occasion="date night"))
    assert drinks_only.outfit is not None and drinks_only.book is None
    assert [(m.part, m.reason) for m in drinks_only.missing] == [
        ("book", BOOK_NEEDS_BOOKS),
        ("drink", DRINK_NEEDS_BOOK),
    ]


def test_with_nothing_served_the_plan_is_empty_not_an_error():
    evening = make_planner(fashion=False, books=False, cocktails=False).plan(
        EveningRequest(occasion="date night")
    )
    assert evening.status == "empty"
    assert [m.part for m in evening.missing] == ["outfit", "book", "drink"]
    assert evening.why == "Date night → romantic: no outfit, book and drink yet"


def test_a_failing_photo_search_plans_the_rest(planner, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("index offline")

    monkeypatch.setattr(planner._outfits, "outfit", broken)
    evening = plan(planner, "rainy Sunday in")
    assert evening.outfit is None and evening.book is not None
    assert "photo search failed" in evening.missing[0].reason


def test_tastes_and_state_are_bounded():
    with pytest.raises(ValueError):
        EveningRequest(occasion="x")
    with pytest.raises(ValueError):
        EveningRequest(occasion="date night", budget=50_000)
    with pytest.raises(ValueError):
        EveningState(occasion="date", theme="sleepy")
    with pytest.raises(ValueError):
        EveningTastes(unknown=True)


# --- Who the evening is for: never romantic with family, kids or at work ---------------------


@pytest.mark.parametrize(
    ("occasion", "theme"),
    [
        ("dinner with my parents", "thoughtful"),
        ("business dinner", "thoughtful"),
        ("client dinner party", "thoughtful"),
        ("kids birthday party", "playful"),
        ("children's birthday party", "playful"),
        ("family movie night", "cosy"),
        ("baby shower", "playful"),
        ("anniversary dinner with my parents", "thoughtful"),
        ("a romantic night out with the kids", "playful"),
    ],
)
def test_family_kids_and_work_occasions_are_never_romantic_or_glamorous(occasion, theme):
    match = detect_theme(occasion)
    assert match.theme.id == theme
    assert detect_theme(occasion, "romantic").theme.id not in ("romantic", "glamorous")


def test_dinner_and_love_alone_are_not_romantic():
    assert detect_theme("dinner").theme.id == "cosy"
    assert detect_theme("I love board games").theme.id == "playful"
    assert detect_theme("dinner for two").theme.id == "romantic"


def test_negated_mentions_do_not_count():
    assert detect_theme("not a date, just dinner with friends").theme.id == "playful"
    assert detect_theme("date night, no kids").theme.id == "romantic"
    assert not wants_alcohol_free("alcohol-free is boring, a party")
    assert not wants_alcohol_free("I'm not sober-curious")
    assert not wants_alcohol_free("no mocktails please")
    assert wants_alcohol_free("a sober night")


def test_the_theme_evidence_reads_naturally():
    assert detect_theme("Date night").evidence("Date night") == "you mentioned Date night"
    assert detect_theme("business dinner").evidence("business dinner") == (
        "a work occasion (you mentioned business)"
    )
    assert detect_theme("dinner", "spooky").evidence("dinner") == "you asked for a spooky mood"
    assert detect_theme("Tuesday").evidence("Tuesday") == "the occasion names no theme"


@pytest.mark.parametrize(
    "occasion",
    [
        "kids birthday party",
        "family movie night",
        "dinner with my parents",
        "baby shower",
        "girls' night in, I'm pregnant",
        "pregnancy announcement dinner",
        "we're expecting! date night",
        "date night, I'm the designated driver",
        "dinner party, I'm driving",
    ],
)
def test_kids_family_pregnancy_and_driving_make_the_evening_alcohol_free(planner, occasion):
    evening = plan(planner, occasion)
    assert evening.state.alcohol_free
    assert evening.drink is None or evening.drink.cocktail.alcohol_free


def test_an_explicit_alcohol_free_false_overrides_words_but_never_safety(planner):
    assert resolve_alcohol_free("book club, alcohol-free", False) == (False, None)
    assert resolve_alcohol_free("dinner with my parents", False) == (False, None)
    assert resolve_alcohol_free("dinner with my parents", None) == (True, None)
    assert resolve_alcohol_free("murder mystery night", None) == (False, None)
    assert resolve_alcohol_free("murder mystery night", True) == (True, None)
    for occasion, said in (
        ("kids birthday party", "kids"),
        ("date night, I'm pregnant", "pregnant"),
        ("date night, I'm driving", "driving"),
    ):
        free, note = resolve_alcohol_free(occasion, False)
        assert free and note == f"The drink stays alcohol-free: you mentioned {said}."
    evening = plan(planner, "kids birthday party", alcohol_free=False)
    assert evening.state.alcohol_free
    assert evening.notes[0] == "The drink stays alcohol-free: you mentioned kids."
    assert not plan(planner, "book club, alcohol-free", alcohol_free=False).state.alcohol_free
    assert not alcohol_free_in("expecting guests for a summer party")


def test_a_partial_outfit_keeps_the_evening_partial_after_a_book_or_drink_swap(planner):
    evening = plan(planner, "New Year's Eve party under €40")
    assert evening.outfit.outfit.status == "partial" and evening.status == "partial"
    empty = [s.slot for s in evening.outfit.outfit.slots if s.product is None]
    assert empty
    for part in ("book", "drink"):
        swapped = planner.swap(evening.state, part)
        outfit = swapped.outfit.outfit
        assert outfit.status == "partial" and swapped.status == "partial", part
        assert [s.slot for s in outfit.slots if s.product is None] == empty
        assert outfit.minimum == evening.outfit.outfit.minimum
    restored = planner.restore(evening.state)
    assert restored.status == "partial"
