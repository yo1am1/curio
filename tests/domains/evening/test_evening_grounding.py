"""The evening's own grounding checks, on their own: unmarked drink names in context."""

from curio.domains.books.models import Book
from curio.domains.cocktails.models import Cocktail, Ingredient
from curio.domains.evening.agent.grounding import EveningGroundingCheck, ordinary_names

NAMES = ["Dark and Stormy", "Manhattan", "Mojito", "Paloma", "Thriller"]


def check(text, *, cited=(), products=()):
    grounding = EveningGroundingCheck(
        titles=["Gone Girl"],
        other_names=["Gillian Flynn"],
        drink_names=NAMES,
        ingredients=["Light rum", "Lime"],
        glass_names=["Highball glass"],
        ordinary=ordinary_names(NAMES, ["A thriller set in Manhattan on a dark and stormy night."]),
    )
    book = Book(
        book_id=1, title="Gone Girl", authors=["Gillian Flynn"], average_rating=4.03,
        ratings_count=1_600_000, source_url="https://www.goodreads.com/book/show/1",
    )  # fmt: skip
    mojito = Cocktail(
        drink_id=1, name="Mojito", alcoholic="Alcoholic", category="Cocktail",
        glass="Highball glass", instructions="Muddle.", source_url="https://example.test/1",
        ingredients=[Ingredient(name="Light rum", measure="2 oz")],
    )  # fmt: skip
    return grounding.check(text, products=products, books=[book], drinks=[mojito], cited=cited)


def test_ordinary_words_count_as_drinks_only_in_a_drink_context():
    assert ordinary_names(NAMES, ["A thriller set in Manhattan."]) == {"Thriller", "Manhattan"}
    assert check("Gone Girl is a thriller set in Manhattan.").status != "fallback"
    assert check("Pour a manhattan with it.").issues == [
        "manhattan was not returned by PairCocktail"
    ]
    assert check("A classic Manhattan cocktail.").status == "fallback"


def test_distinctive_names_count_anywhere_and_plan_words_are_allowed():
    assert check("And a paloma later.").issues == ["paloma was not returned by PairCocktail"]
    assert check("A mojito, of course.").status == "verified"
    assert check("Sip something on a dark and stormy night.").status != "fallback"
    assert check("Pour a dark and stormy.", cited=["a dark and stormy night"]).status != "fallback"


def test_a_product_may_be_named_by_its_raw_or_its_display_name():
    from curio.domains.fashion.models import Product

    top = Product(
        product_id=108775015, name="PQ PALOMA TOP", product_type="Top",
        product_group="Garment Upper body", section="Ladieswear", colour="Black",
        description="A top.", price=19.99,
    )  # fmt: skip
    assert top.display_name == "Paloma Top"
    for said in ("Paloma Top", "PQ PALOMA TOP"):
        report = check(f"Wear the **{said}** (product #108775015, €19.99).", products=[top])
        assert report.status == "verified", (said, report.issues)
