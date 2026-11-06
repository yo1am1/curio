"""Evening grounding: every price, ID, title, rating and drink name must come from the plan.

It reuses each domain's checks: euro amounts and product IDs as fashion checks them (with
the outfit's total, budget and what is left), titles, book IDs and Goodreads ratings as books
check them, and drink names, drink IDs and recipe amounts as a book answer's paired drinks
are checked (`PairedDrinkCheck`). The parts of one evening share an answer, so each check
accepts the other parts' names: a product called "Paloma" is not the Paloma cocktail.
"""

import re
from collections.abc import Iterable, Sequence

from curio.domains.books.agent.grounding import BookGroundingCheck
from curio.domains.books.models import Book
from curio.domains.cocktails.models import Cocktail
from curio.domains.fashion.models import Product
from curio.domains.pairings.agent.grounding import PairedDrinkCheck
from curio.engine.grounding import (
    amounts_in,
    check_ids,
    check_prices,
    grounding_report,
    merge_reports,
)
from curio.engine.schemas import GroundingReport

# An evening answer cites pieces as "product #N" ("[id N]" and "book #N" are books, "drink #N"
# drinks), and fashion's long H&M ids may stand alone ("#108775015").
_PRODUCT_ID = re.compile(r"\bproduct #(\d+)\b|\bproduct (\d{4,})\b|(?<!\w)#(\d{6,})\b", re.I)


class EveningGroundingCheck:
    def __init__(
        self,
        *,
        titles: Iterable[str],
        other_names: Iterable[str],
        drink_names: Iterable[str],
        ingredients: Iterable[str],
        glass_names: Iterable[str] = (),
    ) -> None:
        self._books = BookGroundingCheck(titles, other_names=other_names)
        self._drinks = PairedDrinkCheck(drink_names, ingredients, glass_names=glass_names)

    def check(
        self,
        text: str,
        *,
        products: Sequence[Product],
        books: Sequence[Book],
        drinks: Sequence[Cocktail],
        amounts: Iterable[float] = (),
        typed: str = "",
        cited: Sequence[str] = (),
    ) -> GroundingReport:
        """`amounts` are euro figures besides prices (outfit totals, budgets, what is left);
        `typed` is what the shopper wrote; `cited` is tool text the answer may repeat (the
        pairer's reasons name The Long Goodbye when Chandler's gimlet chose a drink)."""
        priced, price_issues = check_prices(
            text, (p.price for p in products), [*amounts, *amounts_in(typed)]
        )
        ids, id_issues = check_ids(text, _PRODUCT_ID, (p.product_id for p in products), "product")
        fashion = grounding_report(priced + ids, price_issues + id_issues)
        product_names = [p.name for p in products]
        drink_names = [c.name for c in drinks]
        book_names = [b.title for b in books] + [b.series for b in books if b.series]
        book_names += [author for b in books for author in b.authors]
        reading = self._books.check(
            text, books, typed=typed, also_allowed=[*drink_names, *product_names, *cited]
        )
        pouring = self._drinks.check(
            text, drinks, typed=typed, also_allowed=[*book_names, *product_names]
        )
        return merge_reports(fashion, reading, pouring)
