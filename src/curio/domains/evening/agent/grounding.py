"""Evening grounding: every price, ID, title, rating and drink name must come from the plan.

It reuses each domain's checks: euro amounts and product IDs as fashion checks them (with
the outfit's total, budget and what is left), titles, book IDs and Goodreads ratings as books
check them, and drink names, drink IDs and recipe amounts as a book answer's paired drinks
are checked (`PairedDrinkCheck`). The parts of one evening share an answer, so each check
accepts the other parts' names: a product called "Paloma" is not the Paloma cocktail.

Three checks are the evening's own:
- Unmarked drink names. `PairedDrinkCheck` reads a name only where the answer marks it; the
  evening also reads catalog drink names in any case, as whole words ("pour a mojito"). A
  name that is also ordinary English (it appears in book titles or descriptions: "Thriller",
  "Manhattan", "Dark and Stormy") counts only in a drink context ("pour a manhattan", "a
  manhattan cocktail"). Names inside the plan's product, book or author names, or its own
  text ("a dark and stormy night"), are allowed.
- "by <author>" after a book title must name one of that book's authors.
- Prices are euros: an amount in dollars or pounds is never in the evidence.
"""

import re
from collections.abc import Iterable, Sequence

from curio.domains.books.agent.grounding import BookGroundingCheck
from curio.domains.books.catalog import name_key
from curio.domains.books.models import Book
from curio.domains.cocktails.models import Cocktail
from curio.domains.fashion.models import Product
from curio.domains.pairings.agent.grounding import PairedDrinkCheck
from curio.engine.grounding import (
    amounts_in,
    any_of,
    check_ids,
    check_prices,
    grounding_report,
    merge_reports,
    name_allowed,
)
from curio.engine.schemas import GroundingReport

# An evening answer cites pieces as "product #N" ("[id N]" and "book #N" are books, "drink #N"
# drinks), and fashion's long H&M ids may stand alone ("#108775015").
_PRODUCT_ID = re.compile(r"\bproduct #(\d+)\b|\bproduct (\d{4,})\b|(?<!\w)#(\d{6,})\b", re.I)
# Words before a drink name that make it a drink ("pour a classic manhattan"), or after it.
_DRINK_BEFORE = re.compile(
    r"\b(?:pour|pours|poured|pouring|sip|sips|sipping|drink|drinks|drinking|mix|mixes|mixing|"
    r"shake|shaken|stir|stirred|order|serve|serves|served|toast|try|make|makes|enjoy|"
    r"glass of|cocktail|mocktail)\b(?:\s+(?:a|an|the|some|your|one|another|classic|cold|"
    r"chilled|frosty|fresh|tall|perfect|signature|warm|hot|iced)){0,3}\s*$",
    re.IGNORECASE,
)
_DRINK_AFTER = re.compile(r"^\s*(?:cocktail|mocktail|drink|recipe)s?\b", re.IGNORECASE)
DRINK_CONTEXT = 48
_WORDS = re.compile(r"[a-z0-9']+")
# "by Patrick Rothfuss": up to four capitalised words after "by".
_NAME_WORD = r"[A-Z][\w.'\u2019-]*"
_BY = re.compile(rf"\bby\s+({_NAME_WORD}(?:\s+(?:{_NAME_WORD}|de|van|von|le|la)){{0,3}})")
BY_WINDOW = 80
# "$24.99", "£ 30", "24 USD", "30 pounds": prices in another currency than the catalog's.
_OTHER_CURRENCY = re.compile(
    r"[$£¥]\s?\d+(?:[.,]\d+)*|\b\d+(?:[.,]\d+)*\s?(?:USD|GBP|dollars?|pounds?(?:\s+sterling)?)\b",
    re.IGNORECASE,
)


# Drink names that read as everyday words in an answer even though no book uses them.
EVERYDAY_DRINK_NAMES = frozenset(
    {
        "afterglow", "bluebird", "bubble gum", "chocolate milk", "downshift", "happy skipper",
        "jam donut", "jelly bean", "kiss me quick", "lunch box", "oatmeal cookie",
        "orange crush", "pink moon", "scooter", "snowday", "sweet bananas",
    }
)  # fmt: skip


def ordinary_names(drink_names: Iterable[str], texts: Iterable[str]) -> set[str]:
    """The drink names that are also ordinary words or phrases: they appear in `texts` (the
    book catalog's titles and descriptions), so they are drinks only in a drink context."""
    wanted = {tuple(_WORDS.findall(name.casefold())): name for name in drink_names if name.strip()}
    sizes: dict[str, set[int]] = {}
    for words in wanted:
        sizes.setdefault(words[0], set()).add(len(words))
    found: set[str] = set()
    for text in texts:
        words = _WORDS.findall(text.casefold())
        for start, word in enumerate(words):
            for size in sizes.get(word, ()):
                if (gram := tuple(words[start : start + size])) in wanted:
                    found.add(wanted[gram])
    return found


class EveningGroundingCheck:
    def __init__(
        self,
        *,
        titles: Iterable[str],
        other_names: Iterable[str],
        drink_names: Iterable[str],
        ingredients: Iterable[str],
        glass_names: Iterable[str] = (),
        ordinary: Iterable[str] = (),
    ) -> None:
        drink_names, ingredients, glass_names = (
            list(drink_names),
            list(ingredients),
            list(glass_names),
        )
        self._books = BookGroundingCheck(titles, other_names=other_names)
        self._drinks = PairedDrinkCheck(drink_names, ingredients, glass_names=glass_names)
        skip = {name.casefold() for name in ingredients}
        names = sorted(
            {n for n in drink_names if n.strip() and n.casefold() not in skip},
            key=lambda name: (-len(name), name),
        )
        self._any_case = any_of(names, re.IGNORECASE) if names else None
        self._ordinary = {name.casefold() for name in ordinary} | EVERYDAY_DRINK_NAMES
        phrases = sorted(
            {p for p in [*ingredients, *glass_names] if p.strip()}, key=len, reverse=True
        )
        self._phrases = any_of(phrases, re.IGNORECASE) if phrases else None

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
        product_names = [n for p in products for n in (p.name, p.display_name)]
        drink_names = [c.name for c in drinks]
        book_names = [b.title for b in books] + [b.series for b in books if b.series]
        book_names += [author for b in books for author in b.authors]
        reading = self._books.check(
            text, books, typed=typed, also_allowed=[*drink_names, *product_names, *cited]
        )
        allowed = [*book_names, *product_names, *cited]
        pouring = self._drinks.check(text, drinks, typed=typed, also_allowed=allowed)
        unmarked = self._unmarked_drinks(text, drinks, typed, allowed, pouring.issues)
        bylines = self._bylines(text, books)
        currency = _other_currencies(text)
        return merge_reports(fashion, reading, pouring, unmarked, bylines, currency)

    def _unmarked_drinks(
        self,
        text: str,
        drinks: Sequence[Cocktail],
        typed: str,
        allowed: Sequence[str],
        reported: Sequence[str],
    ) -> GroundingReport:
        """Catalog drink names in any case, as whole words, that the plan did not pour."""
        if self._any_case is None:
            return grounding_report(0, [])
        phrases = [m.span() for m in self._phrases.finditer(text)] if self._phrases else []
        evidence = [*(c.name for c in drinks), *allowed]
        checked, issues = 0, []
        for match in self._any_case.finditer(text):
            start, end = match.span()
            if any(s <= start and end <= e and e - s > end - start for s, e in phrases):
                continue  # inside an ingredient or glass name ("martini glass")
            name = match.group(0)
            if name.casefold() in self._ordinary and not _drink_context(text, start, end):
                continue
            checked += 1
            issue = f"{name} was not returned by PairCocktail"
            if not name_allowed(name, evidence, typed) and issue not in reported:
                issues.append(issue)
        return grounding_report(checked, list(dict.fromkeys(issues)))

    def _bylines(self, text: str, books: Sequence[Book]) -> GroundingReport:
        """ "<title> … by <author>" must name one of that book's authors."""
        checked, issues = 0, []
        for book in books:
            pattern = re.compile(r"(?<!\w)" + re.escape(book.title) + r"(?!\w)", re.IGNORECASE)
            for found in pattern.finditer(text):
                after = text[found.end() : found.end() + BY_WINDOW]
                after = re.split(r"(?<=[.!?])\s|\n", after, maxsplit=1)[0]
                byline = _BY.search(after)
                if byline is None:
                    continue
                checked += 1
                named = byline.group(1).rstrip(".,;:")
                if not _names_author(named, book.authors):
                    issues.append(f"{book.title} is not by {named}")
        return grounding_report(checked, list(dict.fromkeys(issues)))


def _drink_context(text: str, start: int, end: int) -> bool:
    """Whether the words around text[start:end] make it a drink ("pour a manhattan")."""
    before = text[max(0, start - DRINK_CONTEXT) : start]
    return bool(_DRINK_BEFORE.search(before) or _DRINK_AFTER.match(text[end:]))


def _names_author(named: str, authors: Sequence[str]) -> bool:
    """Whether "by <named>" names one of `authors`: the full name, or its last name."""
    key = name_key(named)
    for author in authors:
        full = name_key(author)
        if key == full or full.endswith(f" {key}") or key.startswith(f"{full} "):
            return True
    return False


def _other_currencies(text: str) -> GroundingReport:
    found = [m.group(0) for m in _OTHER_CURRENCY.finditer(text)]
    issues = [f"{raw} is not a price in euros, the catalog's currency" for raw in found]
    return grounding_report(len(found), list(dict.fromkeys(issues)))
