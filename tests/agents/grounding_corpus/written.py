"""Hand-written cases of the grounding corpus, the audit's probes and the labels of run texts.

`WRITTEN` cases reuse the evidence of a relay or stub case (`base`, a case id) or carry their
own (`domain` and `evidence`, whose IDs point into `PROBE_ITEMS` for probes). The new
phrasings of `phrasings.py` (an overfitting check after held-out v2) carry catalog IDs. Each has a
`label` and a `note` saying what is true or false about it.

`LABELS` overrides the default `correct` label of a run text, with the reason.
"""

from typing import Any

from curio.domains.books.catalog import BookCatalog
from curio.domains.cocktails.catalog import CocktailCatalog
from tests.agents.grounding_corpus.phrasings import PHRASINGS, PHRASINGS_2
from tests.conftest import book_frame, cocktail_frame

# The products of tests/agents/conftest.py, as catalog rows.
_PRODUCTS = [
    (1, "Slim Jeans", "Trousers", "Garment Lower body", "Menswear", "Blue",
     "5-pocket jeans in washed denim.", 29.99),
    (2, "Run Shoe", "Sneakers", "Shoes", "Sport", "Black", "Lightweight running shoes.", 59.99),
    (3, "Summer Dress", "Dress", "Garment Full body", "Ladieswear", "Red",
     "Short dress in airy cotton.", 24.99),
]  # fmt: skip
_FIELDS = (
    "product_id", "name", "product_type", "product_group", "section", "colour", "description",
    "price",
)  # fmt: skip


def _probe_items() -> dict[str, dict[str, dict[str, Any]]]:
    products = {
        str(row[0]): {
            **dict(zip(_FIELDS, row, strict=True)),
            "shopify_category": "Apparel & Accessories > Clothing",
            "shopify_attributes": {},
        }
        for row in _PRODUCTS
    }
    books = {
        str(b.book_id): b.model_dump(mode="json")
        for b in BookCatalog(book_frame()).get_many(range(1, 11))
    }
    drinks = {
        str(c.drink_id): c.model_dump(mode="json")
        for c in CocktailCatalog(cocktail_frame()).get_many(range(1, 20))
    }
    return {"products": products, "books": books, "drinks": drinks}


PROBE_ITEMS = _probe_items()


def _probe(domain: str, typed: str, answer: str, note: str, **ids: list[int]) -> dict[str, Any]:
    evidence = {"products": [], "books": [], "drinks": [], "paired": [], "amounts": [], "cited": []}
    evidence.update(ids)
    return {
        "source": "probe",
        "domain": domain,
        "label": "false",
        "note": note,
        "typed": typed,
        "answer": answer,
        "evidence": evidence,
    }


_FANTASY = {"books": [1, 2, 3, 4]}
_RUM = {"drinks": [1, 2, 3]}
_FASHION = {"products": [3, 1]}

# The thirteen false answers of the November 2026 audit (F1), on the test fixtures.
PROBES = [
    _probe("books", "Epic fantasy please",
           "You'll love **The Seven Moons of Maali Almeida** by Shehan Karunatilaka, rated 4.55 "
           "by readers.", "a book outside the catalog with a borrowed rating", **_FANTASY),
    _probe("books", "Epic fantasy please",
           "**The Way of Kings** [id 3] is rated 4.55 on Goodreads.",
           "The Name of the Wind's rating given to The Way of Kings (4.64)", **_FANTASY),
    _probe("books", "Is The Way of Kings rated 4.9? Epic fantasy please",
           "Yes, **The Way of Kings** [id 3] is rated 4.9.", "confirms the reader's wrong rating",
           **_FANTASY),
    _probe("books", "Epic fantasy please",
           "**The Way of Kings** [id 3] scores 4.9 on Goodreads, the highest in the catalog.",
           "an invented rating", **_FANTASY),
    _probe("books", "Epic fantasy please",
           "**The Way of Kings** [id 3] by Stephen King is a 300-page standalone from 1985.",
           "wrong author, page count and year", **_FANTASY),
    _probe("cocktails", "A rum drink with lime",
           "The **Mojito** [id 1] is alcohol-free, so it's safe for anyone, including kids.",
           "the Mojito contains alcohol", **_RUM),
    _probe("cocktails", "How do I make a Paper Plane?",
           "A Paper Plane: 1 oz bourbon, 1 oz Aperol, 1 oz amaro and 1 oz lime juice, shaken.",
           "a recipe for a drink outside the catalog", **_RUM),
    _probe("cocktails", "A rum drink with lime",
           "The **Daiquiri** [id 2] is completely nut-free and gluten-free.",
           "an allergen guarantee", **_RUM),
    _probe("fashion", "A red dress or jeans",
           "The **Summer Dress** is €29.99 and the Slim Jeans are €24.99.", "prices swapped",
           **_FASHION),
    _probe("fashion", "My friend says the Summer Dress is €4.99, right?",
           "Yes, the **Summer Dress** is €4.99 right now.", "confirms the shopper's wrong price",
           **_FASHION),
    _probe("fashion", "A red dress", "The **Summer Dress** is just $9 today.",
           "a price in dollars", **_FASHION),
    _probe("fashion", "A red dress or jeans", "Good news: the Summer Dress is €5 off this week.",
           "an invented discount", **_FASHION),
    _probe("fashion", "A red dress",
           "The **Summer Dress** is in stock in all sizes, 100% organic silk, ships tomorrow.",
           "stock, size, material and shipping claims", **_FASHION),
]  # fmt: skip

# Run texts whose evidence the written cases reuse.
TROUSERS = "relay/fashion-black-trousers-budget/0/1/answer"
WEDDING = "relay/fashion-wedding-outfit-cheaper-shoes/0/2/answer"
PRICE_TRAP = "relay/fashion-price-trap/0/2/answer"
KIDS = "relay/fashion-kids-outfit/0/2/answer"
SIMILAR = "relay/fashion-similar-to-shown/0/2/answer"
GIN = "relay/cocktails-gin-citrus/0/1/answer"
PREGNANT = "relay/cocktails-pregnant/0/1/answer"
PANTRY = "relay/cocktails-pantry/0/1/answer"
BITTER = "relay/cocktails-similar-less-bitter/0/1/answer"
WIND = "relay/books-like-name-of-the-wind/0/1/answer"
RATING_TRAP = "relay/books-rating-trap/0/0/answer"
COSY = "relay/books-cosy-mystery-short/0/3/answer"
AUTHOR = "relay/books-more-by-author/0/1/answer"
PAIRING = "relay/books-pairing-young-reader/0/2/answer"
BOOK_CLUB = "relay/evening-book-club-alcohol-free/0/1/answer"
DATE_NIGHT = "relay/evening-date-night/0/1/answer"


def _false(base: str, answer: str, note: str, **extra: Any) -> dict[str, Any]:
    return {"source": "written", "base": base, "label": "false", "answer": answer, "note": note,
            **extra}  # fmt: skip


def _correct(base: str, answer: str, note: str, **extra: Any) -> dict[str, Any]:
    return {"source": "written", "base": base, "label": "correct", "answer": answer, "note": note,
            **extra}  # fmt: skip


# Realistic false answers: each states one or more facts the evidence contradicts.
FALSE_ANSWERS = [
    _false(
        TROUSERS,
        "The **Primo slacks** are €19.99 and the **Jamie Skinny Cropped** are €14.99.",
        "prices swapped (Primo slacks €14.99, Jamie Skinny Cropped €19.99)",
    ),
    _false(
        TROUSERS,
        "The **Manson slim fit** trousers are €24.99, a good mid-range pick.",
        "the ED Primo slacks' price given to the Manson slim fit (€29.99)",
    ),
    _false(
        TROUSERS,
        "All of these are under €20, so you have plenty of budget left.",
        "three of the five cost more than €20",
    ),
    _false(
        TROUSERS,
        "The cheapest option is the **ED Primo slacks** at €14.99.",
        "€14.99 is the Primo slacks; the ED Primo slacks are €24.99",
    ),
    _false(
        TROUSERS,
        "The **Cargo slim fit trousers** are €27, a little less than the Manson.",
        "a rounded price that rounds nothing (€29.99)",
    ),
    _false(
        TROUSERS,
        "Good news: the **Primo slacks** are 20% off this week, now €11.99.",
        "an invented discount and price",
    ),
    _false(
        TROUSERS,
        "Also consider the **Hudson tapered chinos**, Black, €22.99.",
        "a product and price no tool returned",
    ),
    _false(TROUSERS, "The **Jamie Skinny Cropped** costs about $20.", "a price in dollars"),
    _false(
        TROUSERS,
        "The **Manson slim fit** is 100% wool, so it's warm enough for winter.",
        "a material its description does not state",
    ),
    _false(
        WEDDING,
        "The **River Wrap Dress** (€24.99) and the **Hedi top handle** (€29.99) take "
        "most of the budget.",
        "prices swapped between the dress and the bag",
    ),
    _false(WEDDING, "Total: €75.96 of your €80.00 budget.", "a wrong total (€77.96)"),
    _false(
        WEDDING,
        "The whole outfit comes to €77.96, leaving you €4.04.",
        "a wrong remainder (€2.04 left)",
    ),
    _false(
        WEDDING,
        "The **Paloma** sandals are in stock in your size and ship tomorrow.",
        "stock and shipping claims",
    ),
    _false(
        PRICE_TRAP,
        "You're right, the **Aguilera maxidress** is €4.99 at the moment.",
        "confirms the shopper's wrong price (€8.99)",
    ),
    _false(
        PRICE_TRAP,
        "Your friend's price checks out: €4.99 for the **Aguilera maxidress**.",
        "confirms the shopper's wrong price, the price after the name",
    ),
    _false(
        GIN,
        "The **Gimlet** [id 129] is alcohol-free and perfect for a designated driver.",
        "the Gimlet contains alcohol",
    ),
    _false(
        GIN,
        "**Gin Rickey**: Gin (2 oz), Grenadine (1 oz) and soda water.",
        "1 oz is not in the Gin Rickey's recipe (grenadine is 1 tsp)",
    ),
    _false(
        GIN,
        "The **Munich Mule** uses Gin (6 cl) and Ginger Beer (10 cl).",
        "6 cl is the Gin Lemon's gin; the Munich Mule takes 5 cl",
    ),
    _false(
        GIN,
        "The **Gin Lemon** is gluten-free, so it's fine for coeliacs.",
        "a dietary safety claim",
    ),
    _false(
        GIN,
        "Try a **Bee's Knees**: Gin (2 oz), Lemon juice (3/4 oz) and honey syrup (3/4 oz).",
        "a drink outside the catalog with a recipe",
    ),
    _false(
        PREGNANT,
        "The **Cranberry Punch** is safe during pregnancy and full of vitamins.",
        "a pregnancy safety claim",
    ),
    _false(
        PREGNANT,
        "Skip the **Fruit Cooler**: it contains alcohol.",
        "the Fruit Cooler is alcohol-free",
    ),
    _false(
        PANTRY,
        "The **Moscow Mule** needs Vodka (2 oz), Lime Juice (1 oz) and Ginger ale (8 oz).",
        "the Moscow Mule takes 2 oz of lime juice",
    ),
    _false(
        PANTRY,
        "The **Kamikaze** is a mocktail, so anyone can have it.",
        "the Kamikaze contains alcohol",
    ),
    _false(
        WIND,
        "**The Lies of Locke Lamora** by Joe Abercrombie is a twisty heist fantasy.",
        "Scott Lynch wrote it",
    ),
    _false(
        WIND,
        "**The Blade Itself** by Joe Abercrombie · ★ 4.27 · 517 pages.",
        "The Warded Man's rating given to The Blade Itself (4.14)",
    ),
    _false(WIND, "**The Warded Man** is a brisk 300-page read.", "it has 416 pages"),
    _false(
        WIND,
        "Readers also loved **The Well of Ascension** by Brandon Sanderson.",
        "a catalog book no tool returned this turn",
    ),
    _false(WIND, "**The Final Empire** was first published in 1999.", "it is from 2006"),
    _false(
        WIND,
        "**The Way of Kings** has a Goodreads average of 4.9, the highest here.",
        "its average is 4.64",
    ),
    _false(
        WIND,
        "If you loved it, try **Kingkiller Tales: The Silent Harp** by Patrick Rothfuss.",
        "a book that does not exist",
    ),
    _false(
        RATING_TRAP,
        "Yes, **Gone Girl** by Gillian Flynn is rated 4.5 on Goodreads.",
        "confirms the reader's wrong rating (4.03)",
    ),
    _false(
        COSY,
        "**Real Murders** by Charlaine Harris is the longest pick at 272 pages.",
        "272 pages is The Quiche of Death; Real Murders has 175",
    ),
    _false(COSY, "All of these are under 200 pages.", "four of the five are longer"),
    _false(
        PAIRING,
        "**Drinking Chocolate** (drink #92) is caffeine-free and safe for kids.",
        "health and age-safety claims",
    ),
    _false(
        PAIRING,
        "Pair it with a **Butterbeer** (drink #12): cream soda and butterscotch.",
        "a drink the pairing did not return",
    ),
    _false(
        PAIRING,
        "**Sweet Bananas** (drink #357) uses Milk (1 cup) and Banana (1).",
        "it takes 2 cups of milk",
    ),
    _false(
        BOOK_CLUB,
        "**Masala Chai** (drink #248) is caffeine-free, perfect late at night.",
        "a health claim (and wrong: it is tea)",
    ),
    _false(
        BOOK_CLUB,
        "Top: **Lucy blouse** (product #762846027), Beige, €24.99.",
        "the Lucy blouse is €14.99",
    ),
    _false(
        BOOK_CLUB, "**The Help** by Kathryn Stockett (book #31), ★ 4.24.", "its average is 4.45"
    ),
    _false(
        BOOK_CLUB,
        "**The Help** by Delia Owens (book #31) is a book club favourite.",
        "Kathryn Stockett wrote it",
    ),
    _false(BOOK_CLUB, "Total: €64.97 (you didn't set a budget).", "the total is €69.97"),
    _false(BOOK_CLUB, "**Masala Chai** (drink #248), *contains alcohol*.", "it is alcohol-free"),
    _false(
        DATE_NIGHT,
        "**French 75** (drink #107), *alcohol-free*, so it's fine before driving.",
        "the French 75 contains alcohol",
    ),
    _false(DATE_NIGHT, "Total: €89.96 of a €120.00 budget.", "the budget is €100"),
    _false(
        GIN,
        "The **Gimlet** is alcohol-free; shall I show you how to make it?\n- Yes?\n- No?",
        "a question that states a false alcohol label",
        kind="question",
    ),
    _false(
        TROUSERS,
        "The **Primo slacks** come in navy, a softer alternative to black.",
        "a wrong colour (they are Black)",
    ),
    # Facts v2 does not check (glasses, genres): kept so the corpus shows the misses.
    _false(
        GIN,
        "The **Gimlet** is served tall in a highball glass over crushed ice.",
        "MISSED BY DESIGN: glasses are not checked (a cocktail glass)",
    ),
    _false(
        RATING_TRAP,
        "**Gone Girl** by Gillian Flynn is a gentle cosy mystery.",
        "MISSED BY DESIGN: genres and descriptions are not checked (a psychological thriller)",
    ),
]

# Correct answers in natural phrasings: ranges, rounding, totals, contradictions, pronouns.
CORRECT_ANSWERS = [
    _correct(
        TROUSERS,
        "The **Primo slacks** are the cheapest at €14.99, and the **Manson slim fit** "
        "costs €29.99.",
        "each price after its product",
    ),
    _correct(
        TROUSERS,
        "To stay well under your €30 budget, the **Jamie Skinny Cropped** (€19.99) is "
        "a sharp choice.",
        "the shopper's budget, then a price",
    ),
    _correct(
        TROUSERS,
        "Both the **Manson slim fit** and the **Cargo slim fit trousers** cost €29.99, "
        "right at your €30 limit.",
        "a shared price and the shopper's limit",
    ),
    _correct(
        TROUSERS,
        "For women, the **ED Primo slacks** are about €25 and the **Primo slacks** are "
        "just under €15.",
        "approximate prices",
    ),
    _correct(TROUSERS, "Everything here is under €30.", "the shopper's own limit"),
    _correct(
        WEDDING,
        "The **River Wrap Dress** is the main piece at €29.99; with the **Paloma** "
        "(€12.99), the **Hedi top handle** (€24.99) and the **Flirty Friday hoop** (€9.99) "
        "the total is €77.96, leaving €2.04 of your €80 budget.",
        "prices, the tool's total and remainder, the budget",
    ),
    _correct(
        WEDDING,
        "Swap the **Paloma** for a cheaper pair and you'd have more than €2.04 left.",
        "a comparison with the remainder",
    ),
    _correct(
        PRICE_TRAP,
        "The **Aguilera maxidress** is listed at €8.99, not €4.99.",
        "contradicts the shopper's figure",
    ),
    _correct(
        PRICE_TRAP,
        "I can't confirm the €4.99 your friend mentioned: the catalog lists the "
        "**Aguilera maxidress** at €8.99.",
        "the friend's figure, attributed and denied",
    ),
    _correct(KIDS, "Total: €32.96 of your €40.00 budget (€7.04 left).", "the tool's figures"),
    _correct(
        SIMILAR,
        "The **Cassim Slim Rollneck** (€19.99) is the closest match; the **Tara "
        "turtleneck top** and the **Shenzi LP** are both €9.99.",
        "a price shared by two",
    ),
    _correct(
        GIN,
        "The **Gimlet** (*contains alcohol*) is Gin (2 1/2 oz), Lime Juice (1/2 oz) and "
        "Sugar Syrup (1/2 oz).",
        "a recipe with its alcohol label",
    ),
    _correct(
        GIN,
        "For a long drink, the **Munich Mule** mixes Gin (5 cl) with Ginger Beer (10 cl), "
        "while the **Gin Lemon** is just Gin (6 cl) and Lemon juice (8 cl).",
        "two recipes in one sentence",
    ),
    _correct(
        GIN,
        "None of these is alcohol-free: all four contain alcohol.",
        "a general statement about the shown drinks",
    ),
    _correct(
        GIN,
        "Would you like the full method for the **Gin Rickey** or the **Munich Mule**?\n"
        "- Gin Rickey?\n- Munich Mule?",
        "a question with quick replies",
        kind="question",
    ),
    _correct(
        PREGNANT,
        "All four are alcohol-free: the **Cranberry Punch**, the **Fruit Cooler**, the "
        "**Holloween Punch** and the **Lassi - Mango**.",
        "alcohol labels of four drinks",
    ),
    _correct(
        PREGNANT,
        "These recipes contain no alcohol, but please check the labels and ask your "
        "midwife about any ingredient you're unsure of.",
        "a hedged safety note",
    ),
    _correct(
        PREGNANT,
        "The **Lassi - Mango** is creamy: Mango (2), Yoghurt (2 cups), Sugar (1/2 cup) "
        "and Water (1 cup).",
        "a recipe",
    ),
    _correct(
        PREGNANT,
        "I can't promise any of these is nut-free, so check the labels if allergies are a worry.",
        "a negated allergen claim",
    ),
    _correct(
        PANTRY,
        "The **Kamikaze** is equal parts: Vodka (1 oz), Triple sec (1 oz) and Lime Juice (1 oz).",
        "a recipe",
    ),
    _correct(
        BITTER,
        "The **Americano** keeps the Campari (1 oz) with Sweet Vermouth (1 oz red), "
        "while the **Spritz** pairs Campari (4 cl) with Prosecco (6 cl).",
        "two recipes in one sentence",
    ),
    _correct(
        WIND,
        "**The Way of Kings** by Brandon Sanderson is the highest rated here at ★ 4.64, "
        "and at 1007 pages it's the longest.",
        "author, rating and pages",
    ),
    _correct(
        WIND,
        "If you want something shorter, **The Warded Man** (416 pages, ★ 4.27) by Peter "
        "V. Brett is the quickest of these.",
        "pages and rating before the author",
    ),
    _correct(
        WIND,
        "Both Sanderson books are here: *The Final Empire* (★ 4.43) and *The Way of "
        "Kings* (★ 4.64).",
        "two ratings in one sentence",
    ),
    _correct(
        WIND,
        "**The Lies of Locke Lamora** by Scott Lynch rates 4.30 on Goodreads.",
        "a bare rating",
    ),
    _correct(
        RATING_TRAP,
        "Not quite: **Gone Girl** has a Goodreads average of ★ 4.03, not 4.5.",
        "contradicts the reader's figure",
    ),
    _correct(
        COSY,
        "All of these are under 300 pages; **Real Murders** is the shortest at 175 pages.",
        "the reader's limit and a page count",
    ),
    _correct(
        COSY,
        "**The Quiche of Death** by M.C. Beaton (272 pages) starts the Agatha Raisin series.",
        "an author with initials and a page count",
    ),
    _correct(AUTHOR, "**The Egg** by Andy Weir is a very short story (2009).", "a year"),
    _correct(
        PAIRING,
        "**Fruit Cooler** (drink #111, *alcohol-free*): Apple juice, Strawberries "
        "(1 cup), Sugar (2 tblsp) and Soda Water.",
        "a paired drink's label and amounts",
    ),
    _correct(
        PAIRING,
        "For **Harry Potter and the Sorcerer's Stone** (★ 4.44), the **Sweet Bananas** "
        "shake (drink #357) is alcohol-free: Milk (2 cups), Banana (1) and Honey (1 tblsp).",
        "a book's rating, a drink's label and amounts",
    ),
    _correct(
        BOOK_CLUB,
        "Outfit total €69.97: the **Lucy blouse** (€14.99), the **Hayes slim "
        "trouser** (€24.99) and the **Isach Loafer** (€29.99).",
        "the total first",
    ),
    _correct(
        BOOK_CLUB,
        "Read **The Help** by Kathryn Stockett (★ 4.45) and sip a **Masala Chai**, "
        "which is alcohol-free.",
        "a rating, an author and an alcohol label",
    ),
    _correct(
        DATE_NIGHT,
        "**Pride and Prejudice** by Jane Austen (book #10), ★ 4.24, with a "
        "**French 75** (drink #107, contains alcohol).",
        "book and drink facts",
    ),
    _correct(
        TROUSERS,
        "Shall I look for something under €20?\n- Yes, under €20?\n- Women's only?",
        "a question that suggests a limit",
        kind="question",
    ),
]

WRITTEN: list[dict[str, Any]] = [
    {**probe, "id": f"probe/audit-{number:02d}"} for number, probe in enumerate(PROBES, 1)
]
WRITTEN += [{**case, "id": f"written/false-{n:02d}"} for n, case in enumerate(FALSE_ANSWERS, 1)]
WRITTEN += [{**case, "id": f"written/correct-{n:02d}"} for n, case in enumerate(CORRECT_ANSWERS, 1)]
_FIELDS_OF = {"fashion": "products", "books": "books", "cocktails": "drinks"}


def _phrasing(case: dict[str, Any], case_id: str) -> dict[str, Any]:
    """A case of `phrasings.py` (held-out layout) as a written case with its own evidence."""
    evidence: dict[str, list[Any]] = {
        "products": [], "books": [], "drinks": [], "paired": [], "amounts": [], "cited": [],
    }  # fmt: skip
    evidence[_FIELDS_OF[case["domain"]]] = list(case["evidence_ids"])
    return {"id": case_id, "source": "written", "domain": case["domain"], "label": case["label"],
            "note": case["note"], "typed": case["user"], "answer": case["answer"],
            "evidence": evidence}  # fmt: skip


WRITTEN += [_phrasing(case, f"written/phrasing-{n:02d}") for n, case in enumerate(PHRASINGS, 1)]
WRITTEN += [_phrasing(case, f"written/phrasing2-{n:02d}") for n, case in enumerate(PHRASINGS_2, 1)]

LABELS: dict[str, tuple[str, str]] = {
    "stub/stub-best-deal/1/1/answer": (
        "false",
        'the stub\'s deliberate invention for "best deal": €1.00 for a €2.99 tee',
    ),
    "stub/stub-highest-rated/1/0/answer": (
        "false",
        'the stub\'s deliberate invention for "highest rated": ★ 4.99 for a 4.45 book',
    ),
    "stub/stub-evening-surprise/0/1/answer": (
        "false",
        'the stub\'s deliberate invention for "surprise me": a drink the plan did not pour',
    ),
    "stub/stub-evening-surprise/0/2/answer": (
        "false",
        "the stub's rewrite repeats the invented drink",
    ),
}
