"""Grounding v2: facts bound to items, typed numbers, names, labels and unsupported claims."""

import re

import pytest

from curio.domains.books.agent.grounding import BookGroundingCheck
from curio.domains.cocktails.agent.grounding import CocktailGroundingCheck
from curio.domains.fashion.agent.grounding import check_grounding
from curio.engine.grounding import (
    Binder,
    Citation,
    FactCheck,
    Item,
    Tally,
    describe_counts,
    euro_claims,
    grounding_report,
    merge_reports,
    qualifier,
    same_price,
    sentences,
)

DRESS = Item("product", 3, ("Summer Dress",), {"price": (24.99,)})
JEANS = Item("product", 1, ("Slim Jeans",), {"price": (29.99,)})
CITATIONS = (Citation(re.compile(r"\[id (\d+)\]"), "product"),)


def prices(text, items=(DRESS, JEANS), free=(), typed=""):
    tally = Tally()
    binder = Binder(text, list(items), CITATIONS)
    FactCheck(binder, {"price": same_price}, free={"price": list(free)}, typed=typed).check(
        euro_claims(text), tally
    )
    return tally.report()


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("The Summer Dress is €24.99 and the Slim Jeans are €29.99.", "verified"),
        ("The Summer Dress is €29.99 and the Slim Jeans are €24.99.", "fallback"),  # swapped
        ("At €24.99, the Summer Dress is the cheaper one.", "verified"),  # the item after it
        ("[id 1] costs €29.99.", "verified"),  # a citation is a reference
        ("The Summer Dress is a lovely pick. It costs €24.99.", "verified"),  # last item named
        ("The Summer Dress is a lovely pick. It costs €29.99.", "fallback"),
        ("The jeans cost €29.99 and the dress €24.99.", "verified"),  # a distinctive name word
        ("Only €12.50!", "fallback"),  # unattributed
        ("The Summer Dress is about €25.", "verified"),
        ("The Summer Dress is under €30.", "verified"),
        ("The Summer Dress is under €20.", "fallback"),
        ("Both are under €35.", "verified"),  # holds for every item
        ("Both are under €25.", "fallback"),
        ("Shall I look for something under €15?", "verified"),  # a question suggests a limit
        ("The Summer Dress is €5 off.", "fallback"),
    ],
)
def test_prices_bind_to_the_item_they_describe(text, status):
    assert prices(text).status == status


def test_amounts_the_tools_stated_without_an_item_may_stand_anywhere():
    text = "With the Summer Dress (€24.99) the total is €54.98, leaving €5.02."
    assert prices(text, free=[54.98, 5.02, 60.0]).status == "verified"
    assert prices(text, free=[60.0]).status == "fallback"
    assert prices("Your €60 budget covers it.", free=[60.0]).status == "verified"
    assert prices("Your €70 budget covers it.", free=[60.0]).status == "fallback"


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("Yes, the Summer Dress is €4.99 right now.", "fallback"),  # confirms a wrong figure
        ("The Summer Dress is €24.99, not €4.99.", "verified"),
        ("The listed price is €24.99, not €4.99.", "verified"),
        ("I can't confirm the €4.99 your friend saw: the Summer Dress is €24.99.", "verified"),
        ("Under your €4.99 budget there is nothing, sorry.", "verified"),
        ("The Summer Dress isn't €24.99.", "fallback"),  # denies its real price
    ],
)
def test_a_typed_number_is_only_the_shoppers_own_figure(text, status):
    typed = "My friend says the Summer Dress is €4.99?"
    assert prices(text, items=(DRESS,), typed=typed).status == status


def test_the_framing_of_a_number():
    text = "It is under €30, about €25, from €20, not €5 and in your €40 budget."

    def framing(raw):
        start = text.index(raw)
        return qualifier(text, start, start + len(raw), (0, len(text)))

    assert framing("€30").mode == "less"
    assert framing("€25").mode == "about"
    assert framing("€20").mode == "more"
    assert framing("€5").contradicted
    assert framing("€40").budget


def test_sentences_do_not_end_inside_a_name_or_after_an_abbreviation():
    text = "Read All-Star Superman, Vol. 1 tonight. Then sleep.\n- A bullet"
    keep = [(5, 30)]
    assert [text[a:b] for a, b in sentences(text, keep)] == [
        "Read All-Star Superman, Vol. 1 tonight. ",
        "Then sleep.\n",
        "- A bullet",
    ]


def test_reports_count_by_kind_and_verify_only_when_something_was_checked():
    assert grounding_report(0, []).status == "not_applicable"
    assert grounding_report(2, []).counts == {"fact": 2}
    merged = merge_reports(
        grounding_report(0, [], {"price": 2}), grounding_report(0, ["x"], {"name": 1})
    )
    assert merged.status == "fallback" and merged.counts == {"price": 2, "name": 1}
    assert merged.checked == 3
    assert describe_counts({"price": 3, "name": 1, "id": 2}) == "3 prices, 1 name and 2 IDs"


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("The **Summer Dress** is just $9 today.", "fallback"),
        ("The **Summer Dress** is in stock in all sizes.", "fallback"),
        ("I can't check stock or sizes; the **Summer Dress** is €24.99.", "verified"),
        ("The **Summer Dress** is 100% silk.", "fallback"),
        ("The **Summer Dress** ships tomorrow.", "fallback"),
    ],
)
def test_fashion_reports_currency_stock_and_material_claims(text, status, catalog):
    assert check_grounding(text, catalog.get_many([3, 1])).status == status


def test_fashion_reports_even_the_shoppers_own_dollar_figure(catalog):
    typed = "Something under $40"
    text = "Prices are in euros, so I used your $40 budget as €40: the **Slim Jeans** are €29.99."
    report = check_grounding(text, catalog.get_many([1]), [40.0], typed)
    assert report.issues == ["$40 is not a price in euros, the catalog's currency"]
    text = "Prices are in euros, so I read your budget as €40: the **Slim Jeans** are €29.99."
    assert check_grounding(text, catalog.get_many([1]), [40.0], typed).status == "verified"


@pytest.fixture
def cocktails(cocktail_catalog):
    return CocktailGroundingCheck(
        (c.name for c in cocktail_catalog), cocktail_catalog.ingredients()
    )


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("The **Mojito** [id 1] contains alcohol.", "verified"),
        ("The **Mojito** [id 1] is alcohol-free.", "fallback"),
        ("The **Mojito** [id 1] is not alcohol-free.", "verified"),
        ("An alcohol-free **Mojito** [id 1]? Not in this catalog.", "fallback"),
        ("The **Mojito** [id 1] has an alcohol-free version elsewhere.", "verified"),
        ("The **Daiquiri** [id 2] is nut-free.", "fallback"),
        ("I can't guarantee the **Daiquiri** [id 2] is nut-free.", "verified"),
        ("Check the labels to see whether it's gluten-free.", "not_applicable"),
        ("It's safe for kids.", "fallback"),
        ("A Paper Plane: 1 oz bourbon and 1 oz lime juice.", "fallback"),  # unattributed
    ],
)
def test_cocktails_check_alcohol_labels_safety_and_amounts(
    text, status, cocktails, cocktail_catalog
):
    evidence = cocktail_catalog.get_many([1, 2, 3])
    assert cocktails.check(text, evidence).status == status


def test_a_plural_label_covers_the_listed_drinks(cocktails, cocktail_catalog):
    evidence = cocktail_catalog.get_many([1, 2, 3])
    names = {c.drink_id: c.name for c in evidence}
    text = f"All of these are alcohol-free:\n- **{names[1]}** [id 1]\n- **{names[2]}** [id 2]"
    report = cocktails.check(text, evidence)
    assert report.status == "fallback"
    assert f"{names[1]} is not alcohol-free" in report.issues


@pytest.fixture
def books(book_catalog):
    return BookGroundingCheck(
        book_catalog.titles(), other_names=[*book_catalog.authors(), *book_catalog.genres()]
    )


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("**The Way of Kings** [id 3] by Brandon Sanderson, 1007 pages (2010).", "verified"),
        ("**The Way of Kings** [id 3] by Stephen King.", "fallback"),
        ("**The Way of Kings** [id 3] is a 300-page read.", "fallback"),
        ("**The Way of Kings** [id 3] was first published in 1985.", "fallback"),
        ("**The Way of Kings** [id 3] scores 4.9 on Goodreads.", "fallback"),
        (
            "**The Way of Kings** [id 3] (4.64) beats **The Final Empire** [id 4] (4.43).",
            "verified",
        ),
        ("**The Seven Moons of Maali Almeida** by Shehan Karunatilaka.", "fallback"),
        ("Try *Gideon the Ninth*, a gothic space fantasy.", "fallback"),  # not returned
        ("**Fantasy** picks for you:", "not_applicable"),  # a heading
        ("**Why it works**: shared readers.", "not_applicable"),
    ],
)
def test_books_check_authors_pages_years_ratings_and_titles(text, status, books, book_catalog):
    assert books.check(text, book_catalog.get_many([3, 4])).status == status


def test_a_title_the_reader_typed_may_be_named(books, book_catalog):
    typed = "Do you have The Seven Moons of Maali Almeida?"
    text = "Sorry, *The Seven Moons of Maali Almeida* isn't in the catalog."
    report = books.check(text, book_catalog.get_many([3]), typed)
    assert report.status == "verified" and report.counts == {"title": 1}


def test_a_bare_rating_before_the_next_bullet_is_read(books, book_catalog):
    """The next bullet's dash does not make "4.43" a range: another book's rating is caught."""
    evidence = book_catalog.get_many([3, 4])
    wrong = "- **The Way of Kings** - 4.43\n- **The Final Empire** - 4.43"
    assert books.check(wrong, evidence).status == "fallback"
    right = "- **The Way of Kings** - 4.64\n- **The Final Empire** - 4.43"
    assert books.check(right, evidence).counts["rating"] == 2


def test_a_names_final_period_ends_no_sentence():
    hw = Item("product", 9, ("Curvy Jegging HW.",), {"price": (19.99,)})
    text = "The **Curvy Jegging HW.** (€19.99) costs less than the **Summer Dress**."
    tally = Tally()
    FactCheck(Binder(text, [DRESS, hw], CITATIONS), {"price": same_price}).check(
        euro_claims(text), tally
    )
    assert tally.report().status == "verified"


# --- Grounding v2.1: the categories the blind held-out set (v1) exposed --------------------


@pytest.mark.parametrize(
    ("text", "status"),
    [
        # A total, difference or multiple of the named items' prices is verified...
        ("The Summer Dress (€24.99) and the Slim Jeans (€29.99) come to €54.98.", "verified"),
        ("- Summer Dress: €24.99\n- Slim Jeans: €29.99\n\nTogether that's €54.98.", "verified"),
        ("| Summer Dress | €24.99 |\n| Slim Jeans | €29.99 |\n| **Total** | €54.98 |", "verified"),
        ("The Summer Dress is €24.99, so it's €5 cheaper than the Slim Jeans.", "verified"),
        ("The Slim Jeans are €29.99. The dress is €5.00 less.", "verified"),
        ("The Summer Dress saves you €5 compared with the Slim Jeans.", "verified"),
        ("Two Summer Dresses would be €49.98.", "verified"),
        ("Both together cost about €55.", "verified"),
        # ...and a wrong one is reported.
        ("The Summer Dress (€24.99) and the Slim Jeans (€29.99) come to €56.98.", "fallback"),
        ("The Summer Dress is €24.99, so it's €7 cheaper than the Slim Jeans.", "fallback"),
        ("Two Summer Dresses would be €52.98.", "fallback"),
        # A discount is never arithmetic.
        ("The Slim Jeans are €29.99, but the Summer Dress is €5 off.", "fallback"),
        ("The Summer Dress is on sale, €5 less than the Slim Jeans.", "fallback"),
        # A price stated for one item stays that item's price, "cheaper" or not.
        ("The Summer Dress is the cheaper one at €29.99.", "fallback"),
    ],
)
def test_totals_differences_and_multiples_are_verified(text, status):
    tally = Tally()
    binder = Binder(text, [DRESS, JEANS], CITATIONS)
    FactCheck(binder, {"price": same_price}, arithmetic=frozenset({"price"})).check(
        euro_claims(text), tally
    )
    assert tally.report().status == status, tally.report().issues


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("The Summer Dress (€24.99) fits your €30 budget.", "verified"),
        ("The Summer Dress is €24.99; the Slim Jeans are over €25.", "verified"),
        ("The last one is over €25, so the Summer Dress wins.", "verified"),
        ("Both are within €30.", "verified"),
        ("You mentioned €25, but the Summer Dress is €24.99.", "verified"),
        ("Yes, the Summer Dress is under €20.", "fallback"),  # confirms a wrong figure
        ("The Slim Jeans are lovely. Yes, it's under €25.", "fallback"),
    ],
)
def test_the_shoppers_own_figures_are_theirs_never_an_items(text, status):
    typed = "Anything under €25? Is the Summer Dress under €20? My budget is €30."
    assert prices(text, typed=typed).status == status


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("The **Summer Dress** is red, €24.99.", "verified"),
        ("The **Summer Dress** (dark red) is €24.99.", "fallback"),  # the catalog says red
        ("The **Slim Jeans** come in navy.", "fallback"),  # blue, not dark blue
        ("The **Slim Jeans** are blue denim.", "verified"),
        ("The **Run Shoe** - black, €59.99.", "verified"),
        ("The **Run Shoe** is white.", "fallback"),
        ("If you'd prefer black, the **Summer Dress** is the one to skip.", "verified"),
        ("Pair the **Slim Jeans** with a white shirt.", "verified"),  # another thing's colour
        ("You wanted black, but the **Summer Dress** is red.", "verified"),
    ],
)
def test_fashion_checks_colours_bound_to_a_product(text, status, catalog):
    assert check_grounding(text, catalog.get_many([1, 2, 3])).status == status


@pytest.mark.parametrize(
    ("catalog_colour", "said", "ok"),
    [
        ("Light Beige", "beige", True),
        ("Light Beige", "light beige", True),
        ("Beige", "light beige", False),
        ("Dark Blue", "navy", True),
        ("Blue", "navy", False),
        ("Grey", "navy", False),
        ("Off White", "white", True),
        ("Greenish Khaki", "khaki", True),
        ("Bronze/Copper", "copper", True),
        ("Dark Grey", "light grey", False),
        ("Grey", "gray", True),
    ],
)
def test_colour_words_and_catalog_colours(catalog_colour, said, ok):
    from curio.domains.fashion.agent.grounding import colour_allows

    *shade, base = said.split()
    assert colour_allows(catalog_colour, shade[0] if shade else None, base) is ok
    assert colour_allows("Other", None, "black") is None


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("The **Summer Dress** is hypoallergenic.", "fallback"),
        ("The **Summer Dress** is safe for kids.", "fallback"),
        ("The **Summer Dress** is gentle on sensitive skin.", "fallback"),
        ("I can't confirm the **Summer Dress** is hypoallergenic.", "verified"),
        ("The **Summer Dress** is cotton, but check the label for allergens.", "verified"),
    ],
)
def test_fashion_reports_safety_promises(text, status, catalog):
    report = check_grounding(text, catalog.get_many([3]))
    assert (report.status == "fallback") is (status == "fallback"), report.issues


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("**The Way of Kings** [id 3] is suitable for kids aged 8 and up.", "fallback"),
        ("**The Way of Kings** [id 3] is fine for 10-year-olds.", "fallback"),
        ("**The Way of Kings** [id 3] costs €9.99 in paperback.", "fallback"),
        ("**The Way of Kings** [id 3] is in stock and ships within 24 hours.", "fallback"),
        ("**The Way of Kings** [id 3] is available in hardcover.", "fallback"),
        ("**The Way of Kings** [id 3] (★ 4.64); I can't check prices.", "verified"),
        ("I can't say whether **The Way of Kings** [id 3] suits a 10-year-old.", "verified"),
    ],
)
def test_books_report_age_safety_prices_and_store_claims(text, status, books, book_catalog):
    assert books.check(text, book_catalog.get_many([3, 4])).status == status


def test_the_readers_own_price_figure_may_be_repeated_as_theirs(books, book_catalog):
    typed = "Fantasy under €15?"
    text = (
        "Books carry no prices, so I can't filter by your €15 budget: **The Way of Kings** [id 3]."
    )
    assert books.check(text, book_catalog.get_many([3]), typed).status == "verified"


@pytest.mark.parametrize(
    ("text", "status"),
    [
        # Each bullet is its own sentence: the rating before the next bullet's dash counts.
        ("- **The Way of Kings** - 4.64\n- **The Final Empire** - 4.43", "verified"),
        ("- **The Way of Kings** - 4.43\n- **The Final Empire** - 4.43", "fallback"),
        ("**The Way of Kings** and **The Final Empire** are rated 4.64 and 4.43.", "verified"),
        ("**The Way of Kings** and **The Final Empire** are rated 4.43 and 4.64.", "fallback"),
        ("Together they run to 1,548 pages.", "verified"),
        (
            "**The Way of Kings** (1007 pages) is 466 pages longer than **The Final Empire**.",
            "verified",
        ),
        (
            "**The Way of Kings** (1007 pages) is 400 pages longer than **The Final Empire**.",
            "fallback",
        ),
    ],
)
def test_book_ratings_in_lists_and_page_totals(text, status, books, book_catalog):
    assert books.check(text, book_catalog.get_many([3, 4])).status == status


@pytest.mark.parametrize(
    ("text", "status"),
    [
        (
            "The **Mojito** [id 1] uses light rum, lime juice, mint, sugar and soda water.",
            "verified",
        ),
        ("The **Mojito** [id 1] is made with white rum and mint.", "verified"),  # a synonym
        ("The **Mojito** [id 1] is made with gin and mint.", "fallback"),
        ("The **Daiquiri** [id 2] is a rum-based sour.", "verified"),
        ("The **Daiquiri** [id 2] is a tequila-based sour.", "fallback"),
        (
            "**Daiquiri** [id 2]\n- 1 1/2 oz light rum\n- 1 oz lime juice\n- 1/2 oz grenadine",
            "fallback",
        ),
        ("The **Mojito** [id 1]: 2 oz light rum, 1 oz lime juice and mint.", "verified"),
        ("If you want gin, the **Daiquiri** [id 2] is not for you.", "verified"),  # hedged
        ("The **Mojito** [id 1] uses rum, not gin.", "verified"),  # negated
        ("Swap in your gin and the **Mojito** [id 1] becomes a twist.", "verified"),
    ],
)
def test_cocktails_check_the_ingredients_said_to_be_in_a_drink(
    text, status, cocktails, cocktail_catalog
):
    evidence = cocktail_catalog.get_many([1, 2, 3])
    assert cocktails.check(text, evidence).status == status, cocktails.check(text, evidence)


@pytest.mark.parametrize(
    "text",
    [
        "The **Virgin Mojito** [id 3] is suitable for children.",
        "The **Virgin Mojito** [id 3] is fine for kids.",
        "The **Virgin Mojito** [id 3] is nut-free.",
    ],
)
def test_cocktails_report_age_and_allergen_claims(text, cocktails, cocktail_catalog):
    assert cocktails.check(text, cocktail_catalog.get_many([1, 2, 3])).status == "fallback"
