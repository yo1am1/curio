"""Cite mode: fact references the model writes and the server renders from the catalog.

The grammar and the generic machinery (`curio.engine.facts`), each domain's resolver, the
engine turn (answers, repair, fallback, questions, streaming) and the HTTP endpoints. The
property tests draw random references over every catalog row the fixtures (and, when it is
prepared, the real data) hold, and check that what is rendered is always the catalog's value.
"""

import functools
import random
from dataclasses import dataclass
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from curio.app.assistants import ShoppingAssistant
from curio.domains.books.agent.facts import book_facts
from curio.domains.cocktails.agent.facts import alcohol_label, drink_facts, recipe
from curio.domains.fashion.agent.facts import product_facts
from curio.domains.fashion.agent.grounding import check_grounding
from curio.domains.fashion.models import ScoredProduct
from curio.engine.facts import (
    MASK,
    REFERENCE,
    Attribute,
    FactSheet,
    ItemKind,
    Rendered,
    checked_report,
    references,
    render,
)
from curio.engine.schemas import GroundingReport, ReplyEvent

DATA = Path(__file__).resolve().parents[2] / "data"


# --- The grammar, on a generic sheet ---------------------------------------------------------


@dataclass(frozen=True)
class Widget:
    id: int
    name: str
    weight: float | None
    parts: tuple[tuple[str, str], ...] = ()


def widget_sheet(*widgets: Widget) -> FactSheet:
    def part(w: Widget, name: str | None) -> str | None:
        return dict((k.casefold(), v) for k, v in w.parts).get((name or "").casefold())

    return FactSheet(
        {"widget": ItemKind(lambda w: w.name, {w.id: w for w in widgets})},
        {
            "weight": Attribute(
                "weight", "widget", lambda w, _: w.weight, lambda v: f"{v:g} kg", total=True,
                difference=True,
            ),
            "part": Attribute("part", "widget", part, str, argument=True),
        },
    )  # fmt: skip


SPROCKET = Widget(1, "Sprocket", 2.5, (("Bolt", "4 mm"),))
GEAR = Widget(2, "Gear", 1.25)
HOLLOW = Widget(3, "Hollow", None)
SHEET = widget_sheet(SPROCKET, GEAR, HOLLOW)


def test_item_references_render_the_name_in_bold():
    rendered = render("Try the {widget:1}.", SHEET)
    assert rendered.text == "Try the **Sprocket**."
    assert rendered.counts == {"name": 1} and not rendered.errors
    # Already marked: not bolded twice.
    assert render("Try the **{widget:1}**.", SHEET).text == "Try the **Sprocket**."
    assert render("Try the _{widget:2}_.", SHEET).text == "Try the _Gear_."


def test_attribute_references_with_and_without_the_kind():
    rendered = render("{widget:1} weighs {weight:1}, the {widget:2} {weight:widget:2}.", SHEET)
    assert rendered.text == "**Sprocket** weighs 2.5 kg, the **Gear** 1.25 kg."
    assert rendered.counts == {"name": 2, "weight": 2}


def test_spaces_case_and_a_hash_are_tolerated():
    assert render("{ Weight : widget : #1 }", SHEET).text == "2.5 kg"


def test_an_argument_follows_the_id():
    assert render("{part:widget:1:bolt}", SHEET).text == "4 mm"
    assert render("{part:1:Bolt}", SHEET).text == "4 mm"


def test_totals_and_differences_are_computed_by_the_server():
    rendered = render("{total:weight:1,2} in all, {diff:weight:2,1} apart.", SHEET)
    assert rendered.text == "3.75 kg in all, 1.25 kg apart."
    assert rendered.counts == {"total": 1, "diff": 1}
    assert render("{total:weight:widget:1, 2}", SHEET).text == "3.75 kg"
    assert render("{difference:weight:1,2}", SHEET).text == "1.25 kg"


@pytest.mark.parametrize(
    ("text", "issue"),
    [
        ("{colour:1}", "is not a known reference"),
        ("{widget:9}", "widget 9 was not returned by any tool this turn"),
        ("{weight:9}", "widget 9 was not returned by any tool this turn"),
        ("{widget:abc}", "is not a widget ID"),
        ("{widget:1:2}", "needs exactly one widget ID"),
        ("{weight}", "needs a widget ID"),
        ("{weight:3}", "the catalog has no weight for Hollow"),
        ("{part:1}", "needs a value after the ID"),
        ("{weight:1:extra}", "takes only a widget ID"),
        ("{part:1:Nut}", "Sprocket has no part for “Nut”"),
        ("{total:weight:1}", "a total needs two or more widget IDs"),
        ("{diff:weight:1,2,3}", "a difference needs exactly two widget IDs"),
        ("{total:part:1,2}", "a total can be computed of weight"),
        ("{total:weight:1,3}", "the catalog has no weight for Hollow"),
        ("{total:weight:1,7}", "widget 7 was not returned"),
        ("{total:weight}", "needs an attribute and item IDs"),
    ],
)
def test_a_reference_that_cannot_be_rendered_fails_and_is_dropped(text, issue):
    rendered = render(f"Before {text} after.", SHEET)
    assert len(rendered.errors) == 1 and issue in rendered.errors[0], rendered.errors
    # Dropped from what the client sees, masked in the backstop's copy; no braces in issues.
    assert rendered.text == "Before  after." and "{" not in rendered.errors[0]
    assert rendered.check_text == f"Before {MASK} after."


# --- Malformed references (re-audit, November 2026) ---------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "{total:weight:1,2,1}",
        "{total:weight:widget:1, #1}",
        "{total:weight:1,01}",
        "{diff:weight:2,2}",
    ],
)
def test_a_total_or_difference_may_not_repeat_an_item(text):
    rendered = render(f"Before {text} after.", SHEET)
    assert rendered.text == "Before  after."
    assert len(rendered.errors) == 1 and "may be counted only once" in rendered.errors[0]


@pytest.mark.parametrize(
    "raw",
    ["\u00b2", "1\u00b2", "\u0661", "\uff11", "1" * 5000, "1" * 20],
    ids=["superscript", "digit-and-superscript", "arabic-indic", "fullwidth", "5000", "20"],
)
def test_an_id_that_is_not_plain_ascii_digits_fails_instead_of_crashing(raw):
    # str.isdigit() accepts these and int() raises on them (or on 4,300+ digits).
    for text in (f"{{widget:{raw}}}", f"{{weight:{raw}}}", f"{{total:weight:1,{raw}}}"):
        rendered = render(text, SHEET)
        assert rendered.text == "" and len(rendered.errors) == 1, rendered.errors
        assert "is not a widget ID" in rendered.errors[0]
        assert len(rendered.errors[0]) < 200  # a 5,000-digit ID is not echoed back


def test_a_nineteen_digit_id_is_still_an_id():
    big = Widget(10**18 + 7, "Big", 1.0)
    assert render(f"{{weight:{big.id}}}", widget_sheet(big)).text == "1 kg"


def test_a_domain_value_that_raises_becomes_a_reference_error():
    def broken(widget, _argument):
        raise KeyError("weight")

    sheet = FactSheet(
        {"widget": ItemKind(lambda w: w.name, {1: SPROCKET})},
        {"weight": Attribute("weight", "widget", broken, str)},
    )
    rendered = render("It weighs {weight:1}.", sheet)
    assert rendered.text == "It weighs ." and rendered.errors == [
        "fact reference “{weight:1}” could not be rendered"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "is rated ★ {weight:1}9 today",  # a digit after
        "has 1{weight:1} in it",  # a digit before
        "weighs {weight:1},000 in all",  # a thousands separator and digits after
        "weighs {weight:1}.5 more",  # a decimal point and digits after
        "weighs 4.{weight:1} now",  # digits and a point before
        "a total of 1{total:weight:1,2}",
        "a {part:widget:1:Bolt}0 bolt",
    ],
)
def test_a_value_reference_glued_to_typed_digits_fails(text):
    rendered = render(text, SHEET)
    assert len(rendered.errors) == 1 and "touches a typed number" in rendered.errors[0]
    assert "kg" not in rendered.text and "mm" not in rendered.text


def test_punctuation_and_names_next_to_a_reference_are_fine():
    rendered = render(
        "1. {widget:1} weighs {weight:1}, the {widget:2} ({weight:2}). "
        "Total: {total:weight:1,2}; {weight:1}/{weight:2}, item {widget:1}2.",
        SHEET,
    )
    assert not rendered.errors, rendered.errors
    assert rendered.text == (
        "1. **Sprocket** weighs 2.5 kg, the **Gear** (1.25 kg). "
        "Total: 3.75 kg; 2.5 kg/1.25 kg, item **Sprocket**2."
    )


@pytest.mark.parametrize(
    "text", ["Try {widget:{widget:1}} now.", "Try {{widget:1}} now.", "Try {weight:{weight:1} now."]
)
def test_a_reference_nested_in_braces_fails(text):
    rendered = render(text, SHEET)
    assert rendered.errors, rendered.text
    assert "Sprocket" not in rendered.text and "kg" not in rendered.text


def test_a_leftover_reference_opening_fails():
    # "{widget:" with no closing brace before the next reference.
    rendered = render("Try {widget: and the {widget:1}", SHEET)
    assert any("malformed fact reference" in e for e in rendered.errors), rendered.errors


@pytest.mark.parametrize(
    ("answer", "shown"),
    [
        # The rows of the re-audit (regressions-backend-security.md, section 1).
        ("{book:1} is rated ★ {rating:book:1}9 on Goodreads.", "4.559"),
        ("{book:1} has 1{pages:book:1} pages.", "1662"),
        ("{book:1} was first published in 1{year:book:1}.", "12007"),
        ("{book:1} runs to {pages:book:1},000 pages.", "662,000"),
        ("{book:1} has a rating of 4{rating:book:1} out of 5", "44.55"),
    ],
)
def test_glued_book_references_are_not_verified(answer, shown, book_catalog):
    sheet = book_facts(book_catalog.get_many([1]))
    rendered = render(answer, sheet)
    assert shown not in rendered.text
    report = checked_report(rendered, lambda text: GroundingReport(status="not_applicable"))
    assert report.status == "fallback"
    assert "touches a typed number" in report.issues[0]


def test_glued_drink_amounts_and_nested_drinks_are_not_verified(cocktail_catalog):
    sheet = drink_facts([cocktail_catalog.get(1)])
    glued = render("{drink:1} takes 1{amount:drink:1:Light rum} of light rum.", sheet)
    assert "12 oz" not in glued.text and glued.errors
    for nested in ("Try {drink:{drink:1}} now.", "Try {{drink:1}} now."):
        rendered = render(nested, sheet)
        assert "Mojito" not in rendered.text and rendered.errors, rendered.text
    assert render("Try {drink:\u00b2} or {drink:1}.", sheet).errors


def test_a_malformed_id_in_a_turn_is_repaired_not_a_crash(chat_model_replying, search, catalog):
    search.results = [ScoredProduct(3, 0.9)]
    model = chat_model_replying(
        {"query": "dress"},
        "Try the {product:\u00b2} or the {product:3}.",
        "The {product:3}: {price:3}.",
    )
    reply = _assistant(model, search, catalog).reply("a red dress", [])
    assert reply.grounding.status == "repaired"
    assert "is not a product ID" in reply.grounding.issues[0]
    assert reply.text == "The **Summer Dress**: €24.99."


def test_braces_that_are_not_references_are_left_alone():
    text = "A set {} and {curly} words, {:x}."
    assert references(text, SHEET) == []
    assert render(text, SHEET).text == text


def test_an_empty_sheet_refuses_every_reference():
    rendered = render("{product:1} at {price:1}", FactSheet())
    assert rendered.text == " at " and len(rendered.errors) == 2


def test_the_backstop_copy_keeps_names_and_masks_values():
    rendered = render("The {widget:1} weighs {weight:1}.", SHEET)
    assert rendered.check_text == f"The **Sprocket** weighs {MASK}."
    assert rendered.names == ["**Sprocket**"]


def test_merged_sheets_keep_every_kind_and_join_items():
    other = widget_sheet(Widget(5, "Cog", 0.5))
    merged = SHEET.merged(other)
    assert render("{widget:1} {widget:5}", merged).text == "**Sprocket** **Cog**"


def test_the_report_counts_rendered_facts_apart_from_typed_ones():
    def check(text: str) -> GroundingReport:
        # A fake backstop: every bold name and every "kg" is a checked fact; "9 kg" is wrong.
        names = text.count("**") // 2
        kilos = text.count(" kg")
        issues = ["9 kg is wrong"] if "9 kg" in text else []
        counts = {"name": names, "weight": kilos}
        return GroundingReport(
            status="verified", checked=names + kilos, counts=counts, issues=issues
        )

    report = checked_report(render("{widget:1} weighs {weight:1}, about 3 kg.", SHEET), check)
    # The rendered name is not counted by the backstop; the typed "3 kg" is.
    assert report.status == "verified"
    assert report.rendered == {"name": 1, "weight": 1}
    assert report.counts == {"weight": 1} and report.checked == 1
    bad = checked_report(render("{widget:1} weighs 9 kg.", SHEET), check)
    assert bad.status == "fallback" and bad.issues == ["9 kg is wrong"]
    failed = checked_report(render("{widget:8}", SHEET), check)
    assert failed.status == "fallback" and "widget 8" in failed.issues[0]
    empty = checked_report(render("Hello.", SHEET), check)
    assert empty.status == "not_applicable" and not empty.rendered


def test_issues_the_rendered_names_alone_raise_are_not_reported():
    def check(text: str) -> GroundingReport:
        flagged = "**Sprocket**" in text
        return GroundingReport(
            status="fallback" if flagged else "not_applicable",
            checked=int(flagged),
            counts={"claim": int(flagged)},
            issues=["Sprocket sounds unsafe"] if flagged else [],
        )

    report = checked_report(render("Try the {widget:1}.", SHEET), check)
    assert report.status == "verified" and report.issues == [] and report.counts == {}


# --- Domain resolvers: what is rendered is the catalog's value ----------------------------


def test_product_references_render_the_catalog(catalog):
    sheet = product_facts(catalog.get_many([1, 3]))
    rendered = render(
        "{product:1} ({colour:1}, {price:1}) or {product:3} ({color:3}, {price:product:3}): "
        "{total:price:1,3} together, {diff:price:1,3} apart.",
        sheet,
    )
    assert rendered.text == (
        "**Slim Jeans** (Blue, €29.99) or **Summer Dress** (Red, €24.99): "
        "€54.98 together, €5.00 apart."
    )
    assert not rendered.errors
    assert render("{price:2}", sheet).errors  # Run Shoe was not returned this turn


def test_book_references_render_the_catalog(book_catalog):
    sheet = book_facts(book_catalog.get_many([1, 3, 6, 10]))
    rendered = render(
        "{book:1} by {author:book:1} (★ {rating:book:1}, {year:book:1}, {pages:book:1} pages); "
        "{book:3} is {diff:rating:3,1} higher and {diff:pages:3,1} pages longer; "
        "{total:pages:1,3} pages together. {author:6}.",
        sheet,
    )
    assert rendered.text == (
        "**The Name of the Wind** by Patrick Rothfuss (★ 4.55, 2007, 662 pages); "
        "**The Way of Kings** is 0.09 higher and 345 pages longer; 1,669 pages together. "
        "George Orwell."
    )
    assert "the catalog has no pages for Twilight" in render("{pages:10}", sheet).errors[0]
    assert render("{year:book:5}", sheet).errors  # not returned this turn


def test_drink_references_render_the_catalog(cocktail_catalog):
    mojito, virgin = cocktail_catalog.get(1), cocktail_catalog.get(3)
    sheet = drink_facts([mojito, virgin])
    rendered = render(
        "{drink:1} ({alcohol:drink:1}): {amount:drink:1:Light rum} light rum, "
        "{amount:1:lime juice} lime. {drink:3} is {alcohol:3}: {recipe:drink:3}.",
        sheet,
    )
    assert rendered.text == (
        "**Mojito** (contains alcohol): 2 oz light rum, 1 oz lime. **Virgin Mojito** is "
        "alcohol-free: Lime juice (1 oz), Mint (8 leaves), Sugar (2 tsp), Soda water (4 oz)."
    )
    # Soda water has no measure in the Mojito; gin is not in it; prices are not drinks'.
    assert "has no amount for “Soda water”" in render("{amount:1:Soda water}", sheet).errors[0]
    assert render("{amount:1:Gin}", sheet).errors
    assert render("{total:price:1,3}", sheet).errors


# --- Properties: random references over every catalog row ---------------------------------


@functools.cache
def _real(name: str) -> list | None:
    """Every row of a prepared catalog in `data/` (None when it is not prepared)."""
    path = DATA / f"{name}.parquet"
    if not path.exists():
        return None
    import pandas as pd

    frame = pd.read_parquet(path)
    if name == "products":
        from curio.domains.fashion.catalog import ProductCatalog

        ids = random.Random(7).sample(list(frame["product_id"]), 400)
        return ProductCatalog(frame).get_many(ids)
    if name == "books":
        from curio.domains.books.catalog import BookCatalog

        return list(BookCatalog(frame))
    from curio.domains.cocktails.catalog import CocktailCatalog

    return list(CocktailCatalog(frame))


@pytest.mark.parametrize("seed", range(20))
def test_rendered_product_facts_always_equal_the_catalog(seed, catalog):
    rng = random.Random(seed)
    products = _real("products") or list(catalog)
    evidence = rng.sample(products, min(len(products), rng.randint(1, 6)))
    sheet = product_facts(evidence)
    for product in evidence:
        pid = product.product_id
        assert render(f"{{price:{pid}}}", sheet).text == f"€{product.price:.2f}"
        assert render(f"{{colour:product:{pid}}}", sheet).text == product.colour.strip()
        assert render(f"{{product:{pid}}}", sheet).text == f"**{product.display_name}**"
    if len(evidence) >= 2:
        chosen = rng.sample(evidence, rng.randint(2, len(evidence)))
        ids = ",".join(str(p.product_id) for p in chosen)
        total = render(f"{{total:price:{ids}}}", sheet).text
        assert total == f"€{round(sum(p.price for p in chosen), 2):.2f}"
        a, b = chosen[:2]
        diff = render(f"{{diff:price:{a.product_id},{b.product_id}}}", sheet).text
        assert diff == f"€{round(abs(a.price - b.price), 2):.2f}"
    returned = {p.product_id for p in evidence}
    stranger = next(p for p in [*products, None] if p is None or p.product_id not in returned)
    if stranger is not None:
        assert render(f"{{price:{stranger.product_id}}}", sheet).errors


@pytest.mark.parametrize("seed", range(20))
def test_rendered_book_facts_always_equal_the_catalog(seed, book_catalog):
    rng = random.Random(seed)
    books = _real("books") or list(book_catalog)
    evidence = rng.sample(books, rng.randint(1, 6))
    sheet = book_facts(evidence)
    for book in evidence:
        ref = f"book:{book.book_id}"
        assert render(f"{{{ref}}}", sheet).text == f"**{book.title}**"
        assert render(f"{{author:{ref}}}", sheet).text == book.authors[0]
        assert render(f"{{rating:{ref}}}", sheet).text == f"{book.average_rating:.2f}"
        year = render(f"{{year:{ref}}}", sheet)
        if book.year is None:
            assert year.errors
        else:
            assert year.text == (str(book.year) if book.year > 0 else f"{-book.year} BC")
        pages = render(f"{{pages:{ref}}}", sheet)
        assert pages.errors if not book.pages else pages.text == f"{book.pages:,}"
    paged = [b for b in evidence if b.pages]
    if len(paged) >= 2:
        ids = ",".join(str(b.book_id) for b in paged)
        assert render(f"{{total:pages:{ids}}}", sheet).text == f"{sum(b.pages for b in paged):,}"
    if len(evidence) >= 2:
        a, b = evidence[:2]
        diff = render(f"{{diff:rating:{a.book_id},{b.book_id}}}", sheet).text
        assert diff == f"{round(abs(a.average_rating - b.average_rating), 2):.2f}"


@pytest.mark.parametrize("seed", range(20))
def test_rendered_drink_facts_always_equal_the_catalog(seed, cocktail_catalog):
    rng = random.Random(seed)
    drinks = _real("cocktails") or list(cocktail_catalog)
    evidence = rng.sample(drinks, rng.randint(1, 5))
    sheet = drink_facts(evidence)
    for drink in evidence:
        ref = f"drink:{drink.drink_id}"
        assert render(f"{{{ref}}}", sheet).text == f"**{drink.name}**"
        assert render(f"{{alcohol:{ref}}}", sheet).text == alcohol_label(drink)
        assert render(f"{{recipe:{ref}}}", sheet).text == recipe(drink)
        for ingredient in drink.ingredients:
            amount = render(f"{{amount:{ref}:{ingredient.name}}}", sheet)
            first = next(
                i for i in drink.ingredients if i.name.casefold() == ingredient.name.casefold()
            )
            if (first.measure or "").strip():
                assert amount.text == first.measure.strip()
            else:
                assert amount.errors


# --- The engine turn --------------------------------------------------------------------------


NO_SEARCH = {"query": "", "wants_products": False}


def _action(name, args, call_id="tool_1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _assistant(model, search, catalog):
    return ShoppingAssistant(model, search, catalog)


def _no_reference_left(events) -> None:
    """No raw reference anywhere in what the engine sends the client."""
    for event in events:
        payload = event.model_dump_json()
        for match in REFERENCE.finditer(payload):
            assert match.group(2) is None, match.group(0)


def test_an_answer_with_references_is_rendered_and_verified(chat_model_replying, search, catalog):
    search.results = [ScoredProduct(1, 0.9), ScoredProduct(3, 0.8)]
    answer = (
        "Next to the {product:1}, the {product:3} costs {price:3} and the jeans {price:1}; "
        "both come to {total:price:1,3}."
    )
    model = chat_model_replying({"query": "jeans"}, answer)
    events = list(_assistant(model, search, catalog).stream("jeans or a dress", []))
    reply = events[-1].reply
    assert reply.text == (
        "Next to the **Slim Jeans**, the **Summer Dress** costs €24.99 and the jeans €29.99; "
        "both come to €54.98."
    )
    grounding = reply.grounding
    assert grounding.status == "verified"
    assert grounding.rendered == {"name": 2, "price": 2, "total": 1}
    assert grounding.checked == 0 and grounding.counts == {}
    step = reply.steps[-1]
    assert step.name == "Grounding check"
    assert step.summary == "Rendered 2 names, 2 prices and 1 total from the catalog."
    _no_reference_left(events)


def test_rendered_facts_avoid_the_backstops_misbinding(catalog):
    # Typed raw, this true sentence binds €29.99 to the dress and is a false alarm; cited,
    # it cannot be wrong and the backstop never reads the rendered values.
    raw = "Next to the **Run Shoe**, the **Summer Dress** costs €24.99 and the jeans €29.99."
    evidence = catalog.get_many([1, 2, 3])
    assert check_grounding(raw, evidence).status == "fallback"
    rendered = render(
        "Next to the {product:2}, the {product:3} costs {price:3} and the jeans {price:1}.",
        product_facts(evidence),
    )
    assert rendered.text == raw
    report = checked_report(rendered, lambda text: check_grounding(text, evidence))
    assert report.status == "verified" and report.issues == []


def test_a_typed_value_is_still_checked_by_the_backstop(chat_model_replying, search, catalog):
    search.results = [ScoredProduct(3, 0.9)]
    model = chat_model_replying(
        {"query": "dress"},
        "The {product:3} ({colour:3}) is just €9.99 today.",
        "The {product:3} ({colour:3}) is {price:3}.",
    )
    reply = _assistant(model, search, catalog).reply("a red dress", [])
    assert reply.grounding.status == "repaired"
    assert reply.grounding.issues == ["€9.99 does not match the price of Summer Dress (€24.99)"]
    assert reply.grounding.rendered == {"name": 1, "colour": 1, "price": 1}
    assert reply.text == "The **Summer Dress** (Red) is €24.99."


def test_a_typed_value_that_matches_is_counted_as_checked(chat_model_replying, search, catalog):
    search.results = [ScoredProduct(3, 0.9)]
    model = chat_model_replying({"query": "dress"}, "The {product:3} is €24.99.")
    reply = _assistant(model, search, catalog).reply("a red dress", [])
    assert reply.grounding.status == "verified"
    assert reply.grounding.rendered == {"name": 1}
    # The rendered name is not counted again by the backstop; the typed price is.
    assert reply.grounding.counts == {"price": 1}
    assert reply.steps[-1].summary == (
        "Rendered 1 name from the catalog; checked 1 price against the catalog."
    )


def test_an_unknown_reference_is_repaired_once(chat_model_replying, search, catalog):
    search.results = [ScoredProduct(3, 0.9)]
    model = chat_model_replying(
        {"query": "dress"}, "The {product:3} weighs {weight:3}.", "The {product:3}: {price:3}."
    )
    reply = _assistant(model, search, catalog).reply("a red dress", [])
    assert reply.grounding.status == "repaired"
    assert "is not a known reference" in reply.grounding.issues[0]
    assert reply.text == "The **Summer Dress**: €24.99."


def test_the_trace_counts_unverified_claims_in_the_singular(chat_model_replying, search, catalog):
    search.results = [ScoredProduct(3, 0.9)]
    model = chat_model_replying(
        {"query": "dress"}, "The {product:3} weighs {weight:3}.", "The {product:3}: {price:3}."
    )
    reply = _assistant(model, search, catalog).reply("a red dress", [])
    summaries = [step.summary for step in reply.steps]
    assert "Found 1 unverified claim; asking for a rewrite." in summaries
    bad = {"question": "Like the {product:2} or the {product:4}?", "options": ["Yes", "No"]}
    model = chat_model_replying(
        NO_SEARCH, _action("AskShopper", bad), _action("AskShopper", bad, "tool_2")
    )
    reply = _assistant(model, search, catalog).reply("something", [])
    summaries = [step.summary for step in reply.steps]
    assert "Found 2 unverified claims in a question; asking for a rewrite." in summaries


def test_a_reference_to_an_item_no_tool_returned_falls_back(chat_model_replying, search, catalog):
    # The Run Shoe (2) is in the catalog but not in this turn's evidence.
    search.results = [ScoredProduct(3, 0.9)]
    model = chat_model_replying(
        {"query": "dress"}, "Get the {product:2} at {price:2}.", "Still {product:2}."
    )
    events = list(_assistant(model, search, catalog).stream("a red dress", []))
    reply = events[-1].reply
    assert reply.grounding.status == "fallback"
    assert any("product 2 was not returned" in issue for issue in reply.grounding.issues)
    # The fallback states its facts through references too, rendered from the catalog.
    assert reply.text == "Here are the pieces I could confirm:\n- **Summer Dress** · Red · €24.99"
    assert reply.grounding.rendered == {"name": 1, "colour": 1, "price": 1}
    assert "Run Shoe" not in reply.text
    _no_reference_left(events)


def test_carried_shortlist_cards_are_evidence(chat_model_replying, search, catalog):
    from curio.app.schemas import TurnContext

    model = chat_model_replying(NO_SEARCH, "Your {product:2} is {price:2}.")
    context = TurnContext(shortlist=[2])
    reply = _assistant(model, search, catalog).reply("what did I save?", [], context)
    assert reply.text == "Your **Run Shoe** is €59.99."
    assert reply.grounding.status == "verified"


def test_questions_and_quick_replies_are_rendered(chat_model_replying, search, catalog):
    search.results = [ScoredProduct(1, 0.9), ScoredProduct(3, 0.8)]
    question = {
        "question": "The {product:1} or the {product:3}?",
        "options": ["{product:1} ({price:1})", "{product:3}"],
    }
    model = chat_model_replying({"query": "jeans"}, _action("AskShopper", question))
    events = list(_assistant(model, search, catalog).stream("jeans or dress", []))
    reply = events[-1].reply
    assert reply.status == "needs_input"
    assert reply.text == "The **Slim Jeans** or the **Summer Dress**?"
    assert reply.clarification.options == ["**Slim Jeans** (€29.99)", "**Summer Dress**"]
    assert reply.steps[-1].detail == {
        "question": reply.text,
        "options": reply.clarification.options,
    }
    assert reply.grounding.rendered == {"name": 4, "price": 1}
    _no_reference_left(events)


def test_a_question_with_a_bad_reference_is_asked_again_then_replaced(
    chat_model_replying, search, catalog
):
    bad = {"question": "Like the {product:2}?", "options": ["Yes", "No"]}
    model = chat_model_replying(
        NO_SEARCH, _action("AskShopper", bad), _action("AskShopper", bad, "tool_2")
    )
    events = list(_assistant(model, search, catalog).stream("something", []))
    reply = events[-1].reply
    assert reply.text == "Could you tell me a little more about what you're looking for?"
    assert reply.grounding.status == "fallback" and reply.grounding.rendered == {}
    _no_reference_left(events)


def test_a_reply_never_carries_a_reference_even_when_rendering_fails():
    rendered = render("{product:1} {price:1} {total:price:1,2} {nonsense:1}", FactSheet())
    assert isinstance(rendered, Rendered)
    assert not any(m.group(2) for m in REFERENCE.finditer(rendered.text))


def test_reply_events_are_the_only_text_the_engine_sends(chat_model_replying, search, catalog):
    # Steps carry summaries the server wrote, never the model's draft.
    search.results = [ScoredProduct(3, 0.9)]
    model = chat_model_replying({"query": "dress"}, "The {product:3}: {price:3}.")
    events = list(_assistant(model, search, catalog).stream("a dress", []))
    assert isinstance(events[-1], ReplyEvent)
    drafted = [e for e in events[:-1] if e.step.name == "Draft answer"]
    assert drafted and not any(
        match.group(2) for match in REFERENCE.finditer(drafted[0].step.model_dump_json())
    )
