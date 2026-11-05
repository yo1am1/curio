"""Outfit builder: plan the slots of an outfit for an occasion and fill each from the photo index.

A request ("summer wedding", €80, for women) becomes 3-4 slots from a per-occasion template
(dress, shoes, bag, accessory). Every slot is filled by a CLIP text search restricted to the
slot's product types and the audience's sections, so a slot can only hold the right kind of
garment. The planner is deterministic: equal inputs give the same outfit.

Budget allocation is cheapest-feasible. Each slot's cheapest relevant candidate is known
before anything is chosen; a slot may spend what is left of the budget after reserving those
minimums for the slots still to fill, so the outfit always fits when any outfit fits. Within
that cap a soft fair share (by slot weight) keeps the first slot from taking almost
everything. When even the cheapest pieces do not fit, the slots are kept in priority order
while their minimums fit and the rest are reported with the reason, never silently dropped.

Later slots prefer pieces real shoppers bought together with the pieces already chosen
(co-purchase graph) and pieces whose photo looks like them (image embeddings); each pick
says which reason applied.
"""

import math
import re
from collections.abc import Generator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol

import numpy as np
from pydantic import BaseModel, Field, PositiveInt

from clothes_recommender.catalog import ProductCatalog
from clothes_recommender.domain import Product, ProductFilter, ScoredProduct

SlotName = Literal["top", "bottom", "dress", "outerwear", "shoes", "bag", "accessory"]
Audience = Literal["women", "men", "kids"]
SwapMode = Literal["other", "cheaper"]

SLOT_LABELS: dict[str, str] = {
    "top": "Top",
    "bottom": "Bottom",
    "dress": "Dress",
    "outerwear": "Outerwear",
    "shoes": "Shoes",
    "bag": "Bag",
    "accessory": "Accessory",
}
# Every product type a slot may hold, for slots the shopper names and as a fallback when an
# occasion's narrower types find nothing.
SLOT_TYPES: dict[str, tuple[str, ...]] = {
    "top": (
        "T-shirt",
        "Shirt",
        "Blouse",
        "Top",
        "Vest top",
        "Polo shirt",
        "Sweater",
        "Hoodie",
        "Bodysuit",
    ),
    "bottom": ("Trousers", "Shorts", "Skirt", "Leggings/Tights"),
    "dress": ("Dress", "Jumpsuit/Playsuit"),
    "outerwear": ("Blazer", "Jacket", "Coat", "Cardigan"),
    "shoes": (
        "Sneakers",
        "Boots",
        "Sandals",
        "Wedge",
        "Ballerinas",
        "Flat shoe",
        "Flat shoes",
        "Pumps",
        "Heels",
        "Other shoe",
        "Flip flop",
    ),
    "bag": ("Bag", "Shoulder bag", "Tote bag", "Backpack", "Weekend/Gym bag"),
    "accessory": (
        "Belt",
        "Sunglasses",
        "Scarf",
        "Earring",
        "Earrings",
        "Necklace",
        "Bracelet",
        "Ring",
        "Watch",
        "Tie",
        "Hat/beanie",
        "Hat/brim",
        "Straw hat",
        "Felt hat",
        "Beanie",
        "Cap/peaked",
        "Bucket hat",
    ),
}
# The order slots are filled in when the shopper names them: the piece that sets the look
# first, small pieces last.
SLOT_PRIORITY: tuple[str, ...] = (
    "dress",
    "top",
    "bottom",
    "outerwear",
    "shoes",
    "bag",
    "accessory",
)
AUDIENCE_SECTIONS: dict[str, tuple[str, ...]] = {
    "women": ("Ladieswear", "Divided"),
    "men": ("Menswear",),
    "kids": ("Baby/Children",),
}
SECTION_AUDIENCE: dict[str, Audience] = {
    "Ladieswear": "women",
    "Divided": "women",
    "Menswear": "men",
    "Baby/Children": "kids",
}

MAX_SLOTS = 5
MAX_SKIPPED = 20
# Candidates per slot: the best matches under the whole budget, plus the best under the
# slot's fair share so a tight budget still has relevant, affordable options.
POOL_SIZE = 24
CHEAP_POOL_SIZE = 12
# Score = CLIP relevance + bonuses for fitting the pieces already chosen - a soft penalty for
# spending more than the slot's fair share. CLIP text-image cosines differ by a few
# hundredths between good and poor matches, so the bonuses are on that scale.
VISUAL_WEIGHT = 0.08
CO_PURCHASE_BONUS = 0.02
CO_PURCHASE_WEIGHT = 0.03
OVERSPEND_WEIGHT = 0.03
# Products of a wearable type that are not worn ("TC LAUNDRY MESH BAG 2-PACK").
_NOT_WORN = re.compile(r"\b(laundry|toiletry|wash bag|phone case|water ?bottle)\b", re.IGNORECASE)
# A photo this close to a chosen piece's photo is called out as matching its look.
VISUAL_REASON_THRESHOLD = 0.75


# --- Client-held state and the reply ---------------------------------------------------------


class OutfitPiece(BaseModel):
    slot: SlotName
    product_id: PositiveInt


class OutfitState(BaseModel):
    """What the client sends back to swap or save pieces of the current outfit.

    The server re-reads every product (and so every price) from the catalog; the client only
    says which piece fills which slot.
    """

    occasion: str = Field(min_length=1, max_length=200)
    budget: float | None = Field(default=None, gt=0, le=10_000)
    audience: Audience | None = None
    pieces: list[OutfitPiece] = Field(default_factory=list, max_length=MAX_SLOTS)
    # Pieces swapped out, so "another top" does not bring an earlier top back.
    skipped: list[PositiveInt] = Field(default_factory=list, max_length=MAX_SKIPPED)


class OutfitSlot(BaseModel):
    slot: SlotName
    label: str
    product: Product | None = None
    reason: str = ""
    # Why the slot is empty (no match, or the budget cannot cover it).
    issue: str = ""
    # The cheapest matching piece of an empty slot, when one exists.
    cheapest: float | None = None


class Outfit(BaseModel):
    occasion: str
    audience: Audience | None = None
    budget: float | None = None
    total: float
    status: Literal["complete", "partial", "empty"]
    slots: list[OutfitSlot]
    notes: list[str] = Field(default_factory=list)
    # The cheapest complete outfit, reported when the budget cannot cover one.
    minimum: float | None = None
    state: OutfitState

    @property
    def products(self) -> list[Product]:
        return [slot.product for slot in self.slots if slot.product is not None]

    @property
    def left(self) -> float | None:
        return None if self.budget is None else round(self.budget - self.total, 2)

    def amounts(self) -> list[float]:
        """Euro figures an answer may state besides piece prices: the total, the budget, what
        is left or missing, and the cheapest option of a slot that did not fit."""
        values = [self.total]
        if self.budget is not None:
            values += [self.budget, abs(self.budget - self.total)]
        if self.minimum is not None:
            values.append(self.minimum)
            if self.budget is not None:
                values.append(abs(self.minimum - self.budget))
        values += [slot.cheapest for slot in self.slots if slot.cheapest is not None]
        return [round(value, 2) for value in values]


@dataclass(frozen=True)
class OutfitRequest:
    occasion: str
    budget: float | None = None
    audience: Audience | None = None
    slots: tuple[SlotName, ...] = ()
    # A remembered per-piece price limit, applied to every slot.
    max_item_price: float | None = None


# --- Progress, streamed while an outfit is built ----------------------------------------------


@dataclass(frozen=True)
class PlannedSlots:
    slots: tuple["SlotSpec", ...]
    audience: Audience | None
    audience_source: Literal["request", "best match", "none"]
    style: str
    season: str


@dataclass(frozen=True)
class FilledSlot:
    slot: OutfitSlot


type OutfitProgress = PlannedSlots | FilledSlot


@dataclass(frozen=True)
class Swap:
    """The result of swapping one slot: the new outfit and what changed."""

    outfit: Outfit
    slot: SlotName
    old: Product
    new: Product | None


class OutfitError(ValueError):
    """A request the planner cannot serve; the message says what to change."""


# --- Templates ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class SlotSpec:
    slot: SlotName
    query: str
    types: tuple[str, ...]
    # Relative share of the budget the slot may spend without a penalty.
    weight: float

    @property
    def label(self) -> str:
        return SLOT_LABELS[self.slot]


_OCCASIONS: dict[str, tuple[str, ...]] = {
    "wedding": (
        "wedding",
        "ceremony",
        "formal",
        "gala",
        "christening",
        "engagement",
        "black tie",
        "reception",
        "graduation",
    ),
    "office": ("office", "work", "interview", "meeting", "business", "conference", "job"),
    "party": (
        "party",
        "night out",
        "club",
        "birthday",
        "cocktail",
        "dinner",
        "date",
        "evening",
        "celebration",
        "new year",
        "concert",
    ),
    "beach": ("beach", "holiday", "vacation", "pool", "seaside", "resort", "island"),
    "sport": ("gym", "sport", "workout", "running", "yoga", "training", "fitness", "jogging"),
}
_SEASONS: dict[str, tuple[str, ...]] = {
    "summer": ("summer", "hot", "sunny", "heat", "warm weather", "july", "august"),
    "winter": ("winter", "cold", "snow", "christmas", "chilly", "freezing", "december"),
    "autumn": ("autumn", "fall", "rain", "rainy", "october"),
    "spring": ("spring", "april"),
}
_AUDIENCE_WORDS: dict[Audience, tuple[str, ...]] = {
    "men": ("men", "man", "menswear", "him", "male", "boyfriend", "husband", "guy"),
    "women": ("women", "woman", "ladies", "her", "female", "girlfriend", "wife"),
    "kids": ("kid", "kids", "child", "children", "boy", "girl", "baby", "toddler"),
}

# (slot, query, product types, weight); "{season}" becomes the season word or nothing.
type _Row = tuple[SlotName, str, tuple[str, ...], float]

_WOMEN: dict[str, list[_Row]] = {
    "wedding": [
        (
            "dress",
            "elegant {season} wedding guest dress",
            ("Dress", "Jumpsuit/Playsuit"),
            4,
        ),
        (
            "shoes",
            "elegant heeled sandals",
            ("Sandals", "Heels", "Pumps", "Wedge", "Ballerinas", "Flat shoe"),
            2,
        ),
        ("bag", "small elegant handbag", ("Bag", "Shoulder bag"), 1.5),
        ("accessory", "elegant earrings", ("Earring", "Earrings", "Necklace", "Bracelet"), 1),
    ],
    "office": [
        ("top", "elegant {season} blouse for the office", ("Blouse", "Shirt", "Top"), 2),
        ("bottom", "tailored trousers for the office", ("Trousers", "Skirt"), 2.5),
        ("outerwear", "tailored blazer", ("Blazer", "Cardigan"), 3),
        ("shoes", "classic pumps", ("Pumps", "Ballerinas", "Flat shoe", "Heels", "Boots"), 2),
    ],
    "party": [
        ("dress", "{season} party dress", ("Dress", "Jumpsuit/Playsuit"), 4),
        ("shoes", "party heels", ("Heels", "Pumps", "Sandals", "Wedge", "Boots"), 2),
        ("bag", "small evening bag", ("Bag", "Shoulder bag"), 1.5),
        ("accessory", "statement earrings", ("Earring", "Earrings", "Necklace"), 1),
    ],
    "beach": [
        ("top", "{season} beach vest top", ("Vest top", "Top", "T-shirt", "Blouse"), 2),
        ("bottom", "beach shorts", ("Shorts", "Skirt"), 2),
        ("shoes", "beach sandals", ("Sandals", "Flip flop"), 1.5),
        ("accessory", "sunglasses", ("Sunglasses", "Straw hat", "Hat/brim"), 1),
    ],
    "sport": [
        ("top", "sports top for the gym", ("Vest top", "T-shirt", "Top"), 2),
        ("bottom", "sports leggings", ("Leggings/Tights", "Shorts"), 2),
        ("shoes", "running sneakers", ("Sneakers",), 3),
        ("accessory", "sports cap", ("Cap/peaked", "Hat/beanie", "Sunglasses"), 1),
    ],
    "casual": [
        (
            "top",
            "casual {season} top",
            ("T-shirt", "Top", "Shirt", "Blouse", "Sweater", "Vest top", "Hoodie"),
            2,
        ),
        ("bottom", "casual jeans", ("Trousers", "Shorts", "Skirt"), 2.5),
        ("shoes", "casual sneakers", ("Sneakers", "Ballerinas", "Sandals", "Boots"), 2),
        ("bag", "everyday shoulder bag", ("Bag", "Shoulder bag", "Tote bag"), 1.5),
    ],
}
_MEN: dict[str, list[_Row]] = {
    "wedding": [
        ("top", "smart formal shirt", ("Shirt",), 2),
        ("bottom", "smart suit trousers", ("Trousers",), 2.5),
        ("outerwear", "tailored suit blazer", ("Blazer",), 4),
        ("shoes", "smart leather shoes", ("Other shoe", "Flat shoes", "Boots", "Sneakers"), 2.5),
    ],
    "office": [
        ("top", "smart shirt for the office", ("Shirt", "Polo shirt"), 2),
        ("bottom", "smart chinos", ("Trousers",), 2.5),
        ("shoes", "smart leather shoes", ("Other shoe", "Flat shoes", "Boots", "Sneakers"), 2.5),
        ("accessory", "leather belt", ("Belt", "Tie", "Watch"), 1),
    ],
    "party": [
        ("top", "{season} party shirt", ("Shirt", "T-shirt", "Polo shirt"), 2),
        ("bottom", "slim trousers", ("Trousers",), 2.5),
        ("shoes", "white sneakers", ("Sneakers", "Boots", "Other shoe"), 2.5),
        ("accessory", "leather belt", ("Belt", "Bracelet", "Watch"), 1),
    ],
    "beach": [
        (
            "top",
            "{season} short-sleeved shirt",
            ("Shirt", "T-shirt", "Vest top", "Polo shirt"),
            2,
        ),
        ("bottom", "swim shorts", ("Shorts",), 2),
        ("shoes", "sandals", ("Sandals", "Flip flop"), 1.5),
        ("accessory", "sunglasses", ("Sunglasses", "Straw hat", "Hat/brim"), 1),
    ],
    "sport": [
        ("top", "sports t-shirt", ("T-shirt", "Vest top", "Top"), 2),
        ("bottom", "sports shorts", ("Shorts", "Trousers"), 2),
        ("shoes", "running sneakers", ("Sneakers",), 3),
        ("bag", "gym bag", ("Weekend/Gym bag", "Backpack"), 1.5),
    ],
    "casual": [
        ("top", "casual {season} t-shirt", ("T-shirt", "Shirt", "Sweater", "Hoodie"), 2),
        ("bottom", "casual jeans", ("Trousers", "Shorts"), 2.5),
        ("shoes", "casual sneakers", ("Sneakers", "Boots", "Sandals"), 2.5),
        ("accessory", "cap", ("Cap/peaked", "Belt", "Sunglasses", "Hat/beanie"), 1),
    ],
}
_KIDS: list[_Row] = [
    ("top", "kids {season} {style} top", SLOT_TYPES["top"], 2),
    ("bottom", "kids {style} trousers", SLOT_TYPES["bottom"], 2),
    ("shoes", "kids shoes", SLOT_TYPES["shoes"], 2),
    ("accessory", "kids hat", ("Cap/peaked", "Hat/beanie", "Beanie", "Hat/brim", "Bucket hat"), 1),
]
_STYLE_WORDS = {
    "wedding": "smart",
    "office": "smart",
    "party": "party",
    "beach": "summer",
    "sport": "sports",
    "casual": "casual",
}
# Generic queries for slots the shopper names that the occasion template does not have.
_GENERIC_QUERIES = {
    "top": "{style} {season} top",
    "bottom": "{style} trousers",
    "dress": "{style} {season} dress",
    "outerwear": "{season} jacket",
    "shoes": "{style} shoes",
    "bag": "{style} bag",
    "accessory": "{style} accessory",
}
_GENERIC_WEIGHTS = {
    "dress": 4,
    "top": 2,
    "bottom": 2.5,
    "outerwear": 3,
    "shoes": 2.5,
    "bag": 1.5,
    "accessory": 1,
}
_WARM_LAYER: _Row = ("outerwear", "warm {season} coat", ("Coat", "Jacket"), 3)
_LIGHT_LAYER: _Row = ("outerwear", "light {season} jacket", ("Jacket", "Blazer", "Cardigan"), 3)


def detect(words: Mapping[str, tuple[str, ...]], text: str) -> str | None:
    """The first key whose words occur in `text` as whole words."""
    low = text.lower()
    for key, options in words.items():
        if any(re.search(rf"\b{re.escape(option)}\b", low) for option in options):
            return key
    return None


def occasion_style(text: str) -> str:
    return detect(_OCCASIONS, text) or "casual"


def occasion_season(text: str) -> str:
    style = occasion_style(text)
    return detect(_SEASONS, text) or ("summer" if style == "beach" else "")


def audience_in(text: str) -> Audience | None:
    found = detect(_AUDIENCE_WORDS, text)
    return found if found in AUDIENCE_SECTIONS else None  # type: ignore[return-value]


def plan_slots(
    occasion: str, audience: Audience | None, slots: Sequence[SlotName] = ()
) -> list[SlotSpec]:
    """The slots of an outfit for `occasion`, in the order they are filled.

    Without an audience the women's template is used to find the look's first piece; the
    planner then re-plans for the audience of the best match.
    """
    style = occasion_style(occasion)
    season = occasion_season(occasion)
    if audience == "kids":
        rows = list(_KIDS)
    else:
        rows = list((_MEN if audience == "men" else _WOMEN)[style])
    if season in ("winter", "autumn") and style not in ("beach", "sport"):
        if not any(row[0] == "outerwear" for row in rows):
            rows[-1] = _WARM_LAYER if season == "winter" else _LIGHT_LAYER
        if season == "winter":
            rows = [
                ("shoes", "warm winter boots", ("Boots",), row[3]) if row[0] == "shoes" else row
                for row in rows
            ]
    if slots:
        by_slot = {row[0]: row for row in rows}
        rows = [
            by_slot.get(slot)
            or (slot, _GENERIC_QUERIES[slot], SLOT_TYPES[slot], _GENERIC_WEIGHTS[slot])
            for slot in sorted(dict.fromkeys(slots), key=SLOT_PRIORITY.index)
        ]
    words = {"season": season, "style": _STYLE_WORDS[style]}
    return [
        SlotSpec(slot, _clean(query.format(**words)), types, weight)
        for slot, query, types, weight in rows
    ]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# --- Collaborators -----------------------------------------------------------------------------


class TextSearch(Protocol):
    def by_text(
        self, query: str, top_k: int = 10, product_filter: ProductFilter | None = None
    ) -> list[ScoredProduct]: ...


class VectorLookup(Protocol):
    def get_vectors(self, product_ids: Sequence[int]) -> dict[int, np.ndarray]: ...


class CoPurchases(Protocol):
    def bought_together(self, product_id: int) -> Mapping[int, tuple[int, float]]:
        """Products bought with `product_id`: id -> (baskets, lift)."""
        ...


@dataclass(frozen=True)
class _Candidate:
    product: Product
    relevance: float

    @property
    def cents(self) -> int:
        return _cents(self.product.price)


@dataclass
class _Pool:
    spec: SlotSpec
    candidates: list[_Candidate] = field(default_factory=list)

    @property
    def cheapest(self) -> int | None:
        return min((c.cents for c in self.candidates), default=None)


def _cents(euros: float) -> int:
    return round(euros * 100)


def _euros(cents: int) -> float:
    return round(cents / 100, 2)


def _money(cents: int) -> str:
    return f"€{cents / 100:.2f}"


def _co_purchase_weight(count: int, lift: float) -> float:
    """The graph recommender's bounded association strength."""
    return (lift / (1 + lift)) * (count / (count + 2))


class OutfitPlanner:
    def __init__(
        self,
        search: TextSearch,
        catalog: ProductCatalog,
        *,
        vectors: VectorLookup | None = None,
        links: CoPurchases | None = None,
    ) -> None:
        self._search = search
        self._catalog = catalog
        self._vectors = vectors
        self._links = links

    # --- Build ---------------------------------------------------------------------------------

    def build(self, request: OutfitRequest) -> Generator[OutfitProgress, None, Outfit]:
        """Plan and fill an outfit, yielding the plan (after each slot's candidates are
        searched) and then each filled or empty slot."""
        audience, source = request.audience, "request"
        if audience is None:
            audience = self._probe_audience(request)
            source = "best match" if audience else "none"
        specs = plan_slots(request.occasion, audience, request.slots)
        budget = None if request.budget is None else _cents(request.budget)
        item_cap = None if request.max_item_price is None else _cents(request.max_item_price)
        cap = _min_cap(budget, item_cap)
        weights = sum(spec.weight for spec in specs)
        sections = audience_sections(audience, occasion_style(request.occasion))
        pools = [
            self._pool(
                spec,
                sections,
                cap,
                None if budget is None else _min_cap(cap, budget * spec.weight / weights),
            )
            for spec in specs
        ]
        kept, unfit = self._feasible(pools, budget, cap)
        # The plan is announced once every slot's candidates and the budget check are known.
        yield PlannedSlots(
            tuple(specs),
            audience,
            source,  # type: ignore[arg-type]
            occasion_style(request.occasion),
            occasion_season(request.occasion),
        )
        minimum = None
        if unfit and all(pool.cheapest is not None for pool in pools):
            minimum = sum(pool.cheapest for pool in pools)  # type: ignore[misc]
        slots: dict[int, OutfitSlot] = {}
        chosen: list[Product] = []
        remaining = budget
        excluded = set()
        for position, index in enumerate(kept):
            pool = pools[index]
            reserve = sum(pools[later].cheapest or 0 for later in kept[position + 1 :])
            limit = None if remaining is None else remaining - reserve
            share = None
            if remaining is not None:
                later_weight = sum(pools[later].spec.weight for later in kept[position:])
                share = remaining * pool.spec.weight / later_weight
            pick = self._pick(pool, chosen, limit, share, excluded)
            if pick is None:  # pragma: no cover - the reserve guarantees a candidate
                slots[index] = _empty(pool.spec, "Nothing fits what is left of the budget.")
                continue
            product, reason = pick
            chosen.append(product)
            excluded.add(product.product_id)
            if remaining is not None:
                remaining -= _cents(product.price)
            slots[index] = OutfitSlot(
                slot=pool.spec.slot, label=pool.spec.label, product=product, reason=reason
            )
            yield FilledSlot(slots[index])
        for index, issue in unfit.items():
            pool = pools[index]
            cheapest = pool.cheapest
            slots[index] = _empty(pool.spec, issue, None if cheapest is None else _euros(cheapest))
            yield FilledSlot(slots[index])
        ordered = [slots[index] for index in range(len(specs))]
        notes = []
        if source == "best match" and audience:
            notes.append(f"Planned for {audience} from the best match; ask for another audience.")
        if minimum is not None and budget is not None and minimum > budget:
            notes.append(f"A complete outfit needs at least {_money(minimum)}.")
        return self._outfit(request, audience, ordered, notes, minimum, skipped=())

    def outfit(self, request: OutfitRequest) -> Outfit:
        """`build` without the progress: just the finished outfit."""
        stages = self.build(request)
        while True:
            try:
                next(stages)
            except StopIteration as done:
                return done.value

    def _probe_audience(self, request: OutfitRequest) -> Audience | None:
        """The audience of the best garment match for the occasion, when none was given."""
        garments = (*SLOT_TYPES["dress"], *SLOT_TYPES["top"], *SLOT_TYPES["bottom"])
        hits = self._search.by_text(
            _clean(f"{request.occasion} outfit"),
            top_k=5,
            product_filter=ProductFilter(product_types=garments),
        )
        for product in self._catalog.get_many(hit.product_id for hit in hits):
            if audience := SECTION_AUDIENCE.get(product.section):
                return audience
        return None

    def _pool(
        self, spec: SlotSpec, sections: Sequence[str], cap: int | None, share: float | None
    ) -> _Pool:
        pool = _Pool(spec)
        for types in dict.fromkeys((spec.types, SLOT_TYPES[spec.slot])):
            searches = [(POOL_SIZE, cap)]
            if share is not None:
                searches.append((CHEAP_POOL_SIZE, math.floor(share)))
            found: dict[int, _Candidate] = {}
            for top_k, limit in searches:
                for candidate in self._candidates(spec.query, types, sections, top_k, limit):
                    found.setdefault(candidate.product.product_id, candidate)
            if found:
                pool.candidates = sorted(found.values(), key=_by_relevance)
                return pool
        return pool

    def _candidates(
        self,
        query: str,
        types: Sequence[str],
        sections: Sequence[str],
        top_k: int,
        cap: int | None,
    ) -> list[_Candidate]:
        product_filter = ProductFilter(
            product_types=tuple(types),
            sections=tuple(sections),
            max_price=None if cap is None else _euros(cap),
        )
        hits = self._search.by_text(query, top_k=top_k, product_filter=product_filter)
        allowed_types, allowed_sections = set(types), set(sections)
        candidates = []
        for hit in hits:
            product = self._catalog.get(hit.product_id)
            # The index filters already; this guards against a stale payload or a fake search.
            if (
                product is None
                or product.product_type not in allowed_types
                or (allowed_sections and product.section not in allowed_sections)
                or (cap is not None and _cents(product.price) > cap)
                or not math.isfinite(product.price)
                or product.price <= 0
                or _NOT_WORN.search(f"{product.name} {product.description}")
            ):
                continue
            candidates.append(_Candidate(product, hit.score))
        return candidates

    @staticmethod
    def _feasible(
        pools: Sequence[_Pool], budget: int | None, cap: int | None
    ) -> tuple[list[int], dict[int, str]]:
        """Slots to fill, in order, and the reason each other slot stays empty."""
        priced = f" at {_money(cap)} or less" if cap is not None else ""
        unfit: dict[int, str] = {}
        kept: list[int] = []
        spent = 0
        for index, pool in enumerate(pools):
            label = pool.spec.label.lower()
            cheapest = pool.cheapest
            if cheapest is None:
                unfit[index] = f"No {label} matched this look in the photo index{priced}."
            elif budget is not None and spent + cheapest > budget:
                before = _and([pools[i].spec.label.lower() for i in kept])
                after = f" after the cheapest {before}" if before else ""
                unfit[index] = (
                    f"The budget cannot cover the {label}: the cheapest match costs "
                    f"{_money(cheapest)}, more than is left{after}."
                )
            else:
                kept.append(index)
                spent += cheapest
        return kept, unfit

    def _pick(
        self,
        pool: _Pool,
        chosen: Sequence[Product],
        limit: int | None,
        share: float | None,
        excluded: set[int],
    ) -> tuple[Product, str] | None:
        options = [
            c
            for c in pool.candidates
            if c.product.product_id not in excluded and (limit is None or c.cents <= limit)
        ]
        if not options:
            return None
        vectors = self._vectors_for([*chosen, *(c.product for c in options)])
        scored = []
        for candidate in options:
            score, reason = self._fit(candidate, pool.spec, chosen, vectors)
            if share is not None and share > 0 and candidate.cents > share:
                score -= OVERSPEND_WEIGHT * (candidate.cents - share) / share
            scored.append((-score, candidate.cents, candidate.product.product_id, reason))
        _, _, product_id, reason = min(scored)
        best = next(c for c in options if c.product.product_id == product_id)
        priced_out = any(
            c.relevance > best.relevance and c.product.product_id not in excluded
            for c in pool.candidates
            if limit is not None and c.cents > limit
        )
        if priced_out and reason.startswith("Best match"):
            reason = f"Best match for “{pool.spec.query}” within what is left of the budget."
        return best.product, reason

    def _fit(
        self,
        candidate: _Candidate,
        spec: SlotSpec,
        chosen: Sequence[Product],
        vectors: Mapping[int, np.ndarray],
    ) -> tuple[float, str]:
        """Relevance plus how well the candidate goes with the chosen pieces, and why."""
        score = candidate.relevance
        reason = f"Best match for “{spec.query}”."
        if not chosen:
            return score, reason
        pid = candidate.product.product_id
        links = self._links.bought_together(pid) if self._links is not None else {}
        together = [
            (_co_purchase_weight(*links[p.product_id]), links[p.product_id][0], p)
            for p in chosen
            if p.product_id in links
        ]
        similarities = [
            (float(np.dot(vectors[pid], vectors[p.product_id])), p)
            for p in chosen
            if pid in vectors and p.product_id in vectors
        ]
        if similarities:
            score += VISUAL_WEIGHT * sum(s for s, _ in similarities) / len(similarities)
        if together:
            weight, baskets, partner = max(together, key=lambda t: (t[0], -t[2].product_id))
            score += CO_PURCHASE_BONUS + CO_PURCHASE_WEIGHT * weight
            noun = "basket" if baskets == 1 else "baskets"
            reason = f"Bought together with the {partner.name} in {baskets} {noun}."
        elif similarities:
            similarity, partner = max(similarities, key=lambda s: (s[0], -s[1].product_id))
            if similarity >= VISUAL_REASON_THRESHOLD:
                reason = f"Its photo matches the look of the {partner.name}."
        return score, reason

    def _vectors_for(self, products: Iterable[Product]) -> dict[int, np.ndarray]:
        if self._vectors is None:
            return {}
        ids = list(dict.fromkeys(p.product_id for p in products))
        vectors = self._vectors.get_vectors(ids) if ids else {}
        return {
            pid: vector / norm
            for pid, vector in vectors.items()
            if (norm := float(np.linalg.norm(vector))) > 0
        }

    # --- Swap ----------------------------------------------------------------------------------

    def restore(self, state: OutfitState) -> Outfit | None:
        """The outfit a client holds, re-read from the catalog; None if no piece is known."""
        slots = []
        for piece in dict((p.slot, p) for p in state.pieces).values():
            product = self._catalog.get(piece.product_id)
            if product is not None:
                label = SLOT_LABELS[piece.slot]
                slots.append(OutfitSlot(slot=piece.slot, label=label, product=product))
        if not slots:
            return None
        request = OutfitRequest(state.occasion, state.budget, state.audience)
        return self._outfit(request, state.audience, slots, [], None, skipped=state.skipped)

    def swap(
        self,
        state: OutfitState,
        slot: SlotName,
        mode: SwapMode = "other",
        refine: str = "",
        *,
        max_item_price: float | None = None,
    ) -> Swap:
        """Replace the piece in one slot: another option, or a cheaper one, within the budget."""
        outfit = self.restore(state)
        if outfit is None:
            raise OutfitError("There is no outfit to change yet. Build one first.")
        current = next((s for s in outfit.slots if s.slot == slot and s.product), None)
        if current is None or current.product is None:
            names = ", ".join(s.slot for s in outfit.slots)
            raise OutfitError(f"This outfit has no {slot}; its slots are {names}.")
        old = current.product
        others = [s.product for s in outfit.slots if s.product and s.slot != slot]
        budget = None if state.budget is None else _cents(state.budget)
        limit = None if budget is None else budget - sum(_cents(p.price) for p in others)
        if mode == "cheaper":
            limit = _min_cap(limit, _cents(old.price) - 1)
        limit = _min_cap(limit, None if max_item_price is None else _cents(max_item_price))
        spec = self._spec_for(state, slot)
        query = _clean(f"{spec.query} {refine}") if refine.strip() else spec.query
        spec = replace(spec, query=query)
        excluded = {old.product_id, *state.skipped, *(p.product_id for p in others)}
        sections = audience_sections(state.audience, occasion_style(state.occasion))
        pool = self._pool(spec, sections, limit, None)
        pick = (
            None
            if limit is not None and limit <= 0
            else self._pick(pool, others, limit, None, excluded)
        )
        skipped = [*state.skipped, old.product_id] if pick else list(state.skipped)
        slots = []
        for item in outfit.slots:
            if item.slot == slot and pick is not None:
                item = item.model_copy(update={"product": pick[0], "reason": pick[1]})
            slots.append(item)
        request = OutfitRequest(state.occasion, state.budget, state.audience)
        swapped = self._outfit(request, state.audience, slots, [], None, skipped=skipped)
        return Swap(swapped, slot, old, pick[0] if pick else None)

    @staticmethod
    def _spec_for(state: OutfitState, slot: SlotName) -> SlotSpec:
        planned = plan_slots(state.occasion, state.audience, [p.slot for p in state.pieces])
        return next(spec for spec in planned if spec.slot == slot)

    # --- Helpers -------------------------------------------------------------------------------

    @staticmethod
    def _outfit(
        request: OutfitRequest,
        audience: Audience | None,
        slots: list[OutfitSlot],
        notes: list[str],
        minimum: int | None,
        *,
        skipped: Iterable[int],
    ) -> Outfit:
        filled = [s for s in slots if s.product is not None]
        total = sum(_cents(s.product.price) for s in filled if s.product is not None)
        status = "complete" if len(filled) == len(slots) else ("partial" if filled else "empty")
        state = OutfitState(
            occasion=request.occasion[:200],
            budget=request.budget,
            audience=audience,
            pieces=[
                OutfitPiece(slot=s.slot, product_id=s.product.product_id)
                for s in filled
                if s.product is not None
            ],
            skipped=list(dict.fromkeys(skipped))[-MAX_SKIPPED:],
        )
        return Outfit(
            occasion=request.occasion,
            audience=audience,
            budget=request.budget,
            total=_euros(total),
            status=status,
            slots=slots,
            notes=notes,
            minimum=None if minimum is None else _euros(minimum),
            state=state,
        )


def audience_sections(audience: Audience | None, style: str) -> tuple[str, ...]:
    """Catalog sections for an audience; sportswear also comes from the Sport section."""
    sections = AUDIENCE_SECTIONS.get(audience or "", ())
    return (*sections, "Sport") if sections and style == "sport" else sections


def _and(words: Sequence[str]) -> str:
    return " and ".join(filter(None, [", ".join(words[:-1]), *words[-1:]]))


def _min_cap(*caps: float | None) -> int | None:
    values = [math.floor(cap) for cap in caps if cap is not None]
    return min(values) if values else None


def _empty(spec: SlotSpec, issue: str, cheapest: float | None = None) -> OutfitSlot:
    return OutfitSlot(slot=spec.slot, label=spec.label, issue=issue, cheapest=cheapest)


def _by_relevance(candidate: _Candidate) -> tuple[float, int, int]:
    return (-candidate.relevance, candidate.cents, candidate.product.product_id)
