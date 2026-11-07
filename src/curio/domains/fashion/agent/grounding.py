"""Fashion grounding: prices, product IDs and product names in an answer, bound to products."""

import re
from collections.abc import Iterable, Sequence

from curio.domains.fashion.models import Product
from curio.engine.grounding import (
    Binder,
    Citation,
    Claim,
    FactCheck,
    Item,
    Tally,
    check_currency,
    check_ids,
    check_marked_names,
    check_unsupported,
    euro_claims,
    parse_number,
    same_number,
    same_price,
)
from curio.engine.schemas import GroundingReport

_PRODUCT_ID = re.compile(r"\[id (\d+)\]|\bproduct #?(\d{4,})\b|#(\d{6,})\b", re.IGNORECASE)
PRODUCT_CITATIONS = (Citation(_PRODUCT_ID, "product"),)
_PERCENT = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d+)?)\s?%")
# Stock, sizes, delivery and discounts: nothing in the catalog supports them.
UNSUPPORTED_SHOP_CLAIMS = re.compile(
    r"\b(?:in|out of|back in) stock\b|\bsold out\b|\bavailable in (?:all|every|your) sizes?\b"
    r"|\bin (?:all|every) sizes?\b|\bships?(?: today| tomorrow| free| fast| within)\b"
    r"|\b(?:free|fast|next[- ]day|express) (?:shipping|delivery|returns)\b"
    r"|\bdelivered (?:today|tomorrow|by)\b|\bon sale\b|\b\d{1,2}\s?% off\b"
    r"|(?:€\s?)?\b\d+(?:[.,]\d+)? off (?:this|today|now)\b|\b(?:discounted|clearance)\b",
    re.IGNORECASE,
)
SHOP_REASON = "stock, sizes, delivery and discounts are not in the catalog"


# Occasion, season and outfit-slot words: headings in outfit answers, never product names.
OCCASION_WORDS = frozenset(
    {
    "summer", "winter", "spring", "autumn", "fall", "wedding", "guest", "office", "work",
    "party", "beach", "holiday", "vacation", "gym", "sport", "sporty", "casual", "weekend",
    "interview", "dinner", "date", "night", "out", "evening", "birthday", "garden", "brunch",
    "business", "formal", "smart", "everyday", "day", "look", "looks", "outfit", "dress", "top",
    "bottom", "bottoms", "shoes", "bag", "accessory", "accessories", "outerwear", "jacket",
    "layer", "layers", "basics", "staples", "essentials", "budget-friendly", "affordable",
    "elegant", "chic", "romantic", "cosy", "cozy",
    }
)  # fmt: skip


def product_item(product: Product) -> Item:
    percents = tuple(parse_number(m.group(1)) for m in _PERCENT.finditer(product.description))
    names = tuple(dict.fromkeys(n for n in (product.display_name, product.name) if n.strip()))
    return Item(
        "product",
        product.product_id,
        names,
        {"price": (product.price,), "percent": percents},
        text=product.description,
    )


def percent_claims(text: str) -> list[Claim]:
    """Percentages ("100% cotton"): they must be in the product's description."""
    return [
        Claim("percent", m.start(), m.end(), m.group(0), parse_number(m.group(1)))
        for m in _PERCENT.finditer(text)
        if not re.match(r"\s*off\b", text[m.end() :], re.IGNORECASE)
    ]


def _percent_compare(claimed: object, actual: object, mode: str) -> bool:
    return same_number(float(claimed), float(actual), mode)  # type: ignore[arg-type]


PRODUCT_COMPARE = {"price": same_price, "percent": _percent_compare}


class FashionGroundingCheck:
    """Checks the products an answer names and the prices it states.

    - Product IDs must have been returned.
    - A price binds to the product it is about (the nearest product named or cited in the
      sentence, else the last one named before) and must be that product's price; a whole
      euro may round it, and "under €30" or "about €35" compare. Totals, budgets and what
      is left (`amounts`) may stand anywhere. A total, difference or multiple of the named
      products' prices ("together that's €74.98", "€5 cheaper", "4 pairs for €23.96") must
      add up. Anything else is unattributed.
    - A price the shopper typed counts only as their budget or limit, or where the answer
      contradicts it: "Yes, it's €4.99" for a €24.99 dress is reported.
    - Prices in dollars or pounds, percentages not in the product's description, and stock,
      size, delivery or discount promises are reported.
    - A bold, italic or quoted span naming a catalog product (by its display or raw name),
      or a capitalised brand-like name ("**Nike Air Max 90**"), must name a returned one.
    """

    def __init__(self, catalog_names: Iterable[str] = (), vocabulary: Iterable[str] = ()) -> None:
        """`catalog_names` are every product's display and raw names; `vocabulary` (types,
        colours, sections, occasions) never reads as a product name."""
        self._catalog = {name.casefold() for name in catalog_names if name.strip()}
        self._vocabulary = {word.casefold() for word in vocabulary if word.strip()}

    def vocabulary(self, words: str) -> bool:
        return words.casefold() in self._vocabulary

    def catalog_name(self, name: str) -> bool:
        return name.casefold() in self._catalog

    def check(
        self,
        text: str,
        evidence: Sequence[Product],
        amounts: Iterable[float] = (),
        typed: str = "",
    ) -> GroundingReport:
        tally = Tally()
        self.check_into(text, evidence, amounts, typed, tally)
        return tally.report()

    def check_into(
        self,
        text: str,
        evidence: Sequence[Product],
        amounts: Iterable[float],
        typed: str,
        tally: Tally,
        *,
        citations: Sequence[Citation] = PRODUCT_CITATIONS,
        names: bool = True,
    ) -> None:
        """Add this answer's product facts to `tally`. With `names` False the caller checks
        bold and quoted names itself (an evening answer names books and drinks too)."""
        id_pattern = citations[0].pattern
        checked, issues = check_ids(text, id_pattern, (p.product_id for p in evidence), "product")
        tally.counts["id"] += checked
        tally.issues += [i for i in issues if i not in tally.issues]
        binder = Binder(text, [product_item(p) for p in evidence], citations)
        FactCheck(
            binder,
            PRODUCT_COMPARE,
            free={"price": list(amounts)},
            typed=typed,
            noun={"price": "price", "percent": "percentage"},
            describe={"percent": lambda value: f"{value:g}%"},
            arithmetic=frozenset({"price"}),
        ).check([*euro_claims(text), *percent_claims(text)], tally)
        check_currency(text, tally)
        check_unsupported(text, UNSUPPORTED_SHOP_CLAIMS, tally, reason=SHOP_REASON)
        if names:
            known = [n for p in evidence for n in (p.display_name, p.name)]
            check_marked_names(
                text,
                [*known, typed],
                tally,
                kind="name",
                vocabulary=self.vocabulary,
                also_name_like=self.catalog_name,
            )


def check_grounding(
    text: str, evidence: Sequence[Product], extra_amounts: Iterable[float] = (), typed: str = ""
) -> GroundingReport:
    """Check `text` against `evidence` without catalog vocabulary: prices, IDs and the rest
    of `FashionGroundingCheck`; `extra_amounts` are totals, budgets and filters."""
    return FashionGroundingCheck().check(text, evidence, extra_amounts, typed)
