"""Fashion grounding: prices, product IDs and product names in an answer, bound to products."""

import re
from collections.abc import Iterable, Sequence

from curio.domains.fashion.models import Product
from curio.engine.grounding import (
    SHOP_CLAIMS,
    SHOP_REASON,
    Binder,
    Citation,
    Claim,
    FactCheck,
    Item,
    Ref,
    Tally,
    check_currency,
    check_ids,
    check_marked_names,
    check_safety,
    check_unsupported,
    euro_claims,
    hedged,
    parse_number,
    same_number,
    same_price,
)
from curio.engine.schemas import GroundingReport

_PRODUCT_ID = re.compile(r"\[id (\d+)\]|\bproduct #?(\d{4,})\b|#(\d{6,})\b", re.IGNORECASE)
PRODUCT_CITATIONS = (Citation(_PRODUCT_ID, "product"),)
_PERCENT = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d+)?)\s?%")
# Colour words an answer binds to a product, and the catalog colours they allow. A base
# colour may carry a shade ("light", "dark", "off") and a tint ("greenish"); "navy" is dark
# blue and "gray" grey. Other words (cream, charcoal, olive) are not read as colours.
_BASE_COLOURS = (
    "black|white|blue|navy|beige|pink|khaki|grey|gray|gold|golden|green|brown|orange|yellow|"
    "red|purple|silver|turquoise|bronze|copper"
)
_COLOUR = re.compile(
    r"(?<![\w-])(?:(light|dark|pale|deep|bright|off)[\s-]+)?"
    r"(?:(?:greenish|yellowish|greyish|grayish|reddish|bluish|pinkish)[\s-]+)?"
    rf"({_BASE_COLOURS})(?![\w-])"
    # Food, drink and fixed phrases are not a garment's colour: "orange juice", "red wine".
    r"(?!\s+(?:juice|wine|rum|tea|pepper|peel|zest|sugar|chocolate|beans?|olives?|onions?|"
    r"cherr(?:y|ies)|grapes?|currants?|vermouth|label|friday)\b)",
    re.IGNORECASE,
)
_COLOUR_SYNONYMS = {"gray": "grey", "golden": "gold"}
_LIGHT, _DARK = frozenset({"pale"}), frozenset({"deep"})
_TINTS = {"greenish": "green", "yellowish": "yellow", "greyish": "grey", "reddish": "red"}
# A colour farther than this from the product it describes is not read as its colour.
_COLOUR_REACH = 40
# Between a colour and its product: no turn to another subject ("you asked for black, but
# the **Waverly hoodie**...").
_COLOUR_GAP_BREAK = re.compile(
    r"[;.!?]|\b(?:but|while|whereas|though|although|instead|unlike|than|rather|or)\b", re.I
)
# A line that labels a colour: "- Colour: navy", "**Color:** black".
_COLOUR_LABEL = re.compile(r"\bcolou?r\W{0,4}$", re.I)
# A colour in a clause about the shopper ("your black jeans", "you wanted black").
_ABOUT_SHOPPER = re.compile(r"\b(?:you|your|you're|you've|you'd)\b", re.I)
# Words a product's colour may stand before: nothing, a joining word, or a word for the
# piece itself. Any other noun ("a gold buckle", "a white shirt") is another thing's colour.
_COLOUR_NEXT = re.compile(r"[\s-]*([^\W\d_][\w'-]*)?")
_COLOUR_HEADS = frozenset(
    {
    "and", "with", "at", "for", "is", "in", "on", "one", "ones", "version", "option",
    "colour", "color", "colourway", "colorway", "shade", "tone", "finish", "knit", "weave",
    "fabric", "pair", "piece", "style", "model", "variant", "edition", "costs", "cost", "comes",
    "it", "that", "which", "so", "from", "to", "by", "a", "an", "the", "would", "will",
    }
)  # fmt: skip


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
        flags={"colour": product.colour, "type": product.product_type},
        text=product.description,
    )


def colour_allows(catalog: str, shade: str | None, base: str) -> bool | None:
    """Whether a product of `catalog` colour ("Light Beige", "Greenish Khaki", "Bronze/
    Copper") may be called `shade` `base` ("beige", "light beige", "navy"); None when its
    colour says nothing ("Other", "Transparent"). The base must be the catalog's (or its
    tint: "green" for greenish khaki), and a shade the catalog's ("light grey" is not
    grey). "Navy" is dark blue."""
    words = catalog.casefold().replace("/", " ").split()
    bases = {_TINTS.get(w, w) for w in words if w not in ("light", "dark", "off", "other")}
    if not bases or bases <= {"transparent"}:
        return None
    base = _COLOUR_SYNONYMS.get(base.casefold(), base.casefold())
    shade = (shade or "").casefold() or None
    if base == "navy":
        base, shade = "blue", shade or "dark"
    if base not in bases:
        return False
    # The catalog's own shades: "light beige" is not plain beige, "off white" not white.
    # A plain word covers every shade ("beige" for light beige); "pale" and "deep" are
    # loose and only rule out the opposite shade.
    have = next((w for w in words if w in ("light", "dark", "off")), None)
    if shade in ("light", "dark", "off"):
        return shade == have
    if shade in _LIGHT:
        return have != "dark"
    return not (shade in _DARK and have == "light")


def check_colours(text: str, binder: Binder, tally: Tally) -> None:
    """A colour word bound to a product must fit that product's catalog colour. It binds
    to the nearest product named in its sentence, within a few words ("**Tambov** - navy",
    "the **Paris Trench** (beige)", "the navy **Tambov**"). Colours inside a name, in a
    hedged or negated clause ("if you'd like black", "not navy"), about the shopper ("your
    white sneakers"), across a turn ("black, but the **Waverly hoodie**"), of a part or
    another thing ("a gold buckle", "a white shirt") or of food and drink are not read."""
    for match in _COLOUR.finditer(text):
        start, end = match.span()
        if any(r.start <= start < r.end for r in binder.refs) or hedged(text, start):
            continue
        ref = _nearest_ref(binder, start, end)
        if ref is None:
            labelled = _COLOUR_LABEL.search(text[text.rfind("\n", 0, start) + 1 : start])
            binding = binder.bind(start, end) if labelled else None
            if binding is not None:  # "- Colour: navy" under the product's heading
                ref = Ref(start, start, binding.items)
                _check_colour(match, ref, tally)
            continue
        gap = text[ref.end : start] if ref.end <= start else text[end : ref.start]
        clause_start = max(text.rfind(c, 0, start) for c in ".!?;\n,:(")
        if _COLOUR_GAP_BREAK.search(gap) or _ABOUT_SHOPPER.search(text[clause_start + 1 : start]):
            continue
        if _heads_the_piece(text, end, ref):
            _check_colour(match, ref, tally)


def _check_colour(match: re.Match[str], ref: Ref, tally: Tally) -> None:
    verdicts = [
        colour_allows(item.flags.get("colour", ""), match.group(1), match.group(2))
        for item in ref.items
    ]
    if all(v is None for v in verdicts):
        return
    colours = ", ".join(dict.fromkeys(i.flags.get("colour", "") for i in ref.items))
    tally.add(
        "colour",
        any(verdicts),
        f"{ref.items[0].label} is not {match.group(0)} (its colour is {colours})",
        key=f"{ref.items[0].id}|{match.start()}",
    )


def _heads_the_piece(text: str, end: int, ref: Ref) -> bool:
    """Whether the colour ending at `end` describes the piece itself: the word after it is
    none, a joining word, a word for the piece, or a word of the product's name or type
    ("a navy **blazer**" for the Basquiat blazer)."""
    if text[end : end + 1] in ("*", "_"):
        return True  # the product's bold name follows
    word = (_COLOUR_NEXT.match(text, end).group(1) or "").casefold()
    if not word or word in _COLOUR_HEADS:
        return True
    own = {
        w.casefold().rstrip("s")
        for item in ref.items
        for name in (*item.names, item.flags.get("type", ""))
        for w in re.findall(r"[^\W\d_]+", name)
    }
    return word.rstrip("s") in own


def _nearest_ref(binder: Binder, start: int, end: int) -> Ref | None:
    """The product reference nearest to text[start:end] in its sentence, if it is close."""
    first, last = binder.sentence(start)
    best: tuple[int, Ref] | None = None
    for ref in binder.strong:
        if not (first <= ref.start and ref.end <= last):
            continue
        gap = start - ref.end if ref.end <= start else ref.start - end
        if 0 <= gap <= _COLOUR_REACH and (best is None or gap < best[0]):
            best = (gap, ref)
    return best[1] if best else None


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
      size, delivery or discount promises are reported, and so are allergen, skin-health and
      age-safety promises ("hypoallergenic", "safe for kids").
    - A colour word next to a product must fit its catalog colour ("navy" for a grey one is
      reported; "beige" for light beige is not).
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
        safety: bool = True,
    ) -> None:
        """Add this answer's product facts to `tally`. With `names` False the caller checks
        bold and quoted names itself (an evening answer names books and drinks too); with
        `safety` False it checks safety promises itself."""
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
        check_unsupported(text, SHOP_CLAIMS, tally, reason=SHOP_REASON)
        check_colours(text, binder, tally)
        if safety:
            check_safety(text, tally, supported=[p.description for p in evidence])
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
