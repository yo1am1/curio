"""Grounding: check an answer's claims against the evidence its tools returned.

A domain's check finds the claims it can verify in an answer and reports each one that no
tool supports. This module holds the domain-free parts:

- `Item`: a returned catalog row as grounding sees it (its citation kind and ID, the names
  an answer may use, its numeric facts, its people, flags and description).
- `Binder`: ties a claim to the item it is about. A claim binds to the nearest item
  reference in its sentence or bullet (the nearest one before it, else the nearest after),
  else to the last item referenced earlier in the answer, else to the only item the answer
  names. A reference is a citation ("[id 3]", "product #12") or an item's name.
- `FactCheck`: numbers. A number bound to an item must match that item's value (a whole
  euro may round a price, "under"/"over"/"about" compare instead of matching). A number the
  tools stated without an item (an outfit total, a budget, what is left) is allowed
  anywhere. Anything else is "unattributed". A number the shopper typed counts only as
  their own figure in a budget or limit ("under €30", "your €100 budget") or where the
  answer contradicts it ("€8.99, not €4.99"): confirming a wrong figure is caught.
  A number framed as a total, a difference or a multiple ("together that's €74.98", "€5
  cheaper", "4 pairs for €23.96", "2,786 pages in total") must equal that sum, difference
  or multiple of the values of the items its sentence (or, failing that, the answer) names.
  Whatever its wording, a number equal to the sum of any two or three items the answer
  names (or of all of them), the difference of two, or a budget minus one value or such a
  sum ("€20 left") is accepted, unless it is framed as a discount or as one item's own
  value ("the Summer Dress is €54.98"): a wrong total is caught only when it matches no
  such combination, a trade-off for few false alarms on correct arithmetic.
- Names: a bold, italic or quoted name-like span, or "<Title> by <Author>", must name an
  item this turn's tools returned (or something the shopper typed); "by <Author>" after an
  item ("**Scarlet** was written by", "It was written by"), and every co-author after it
  ("by A and B", "together with B", "co-written with B"), must name one of its people.
- Flags ("alcohol-free" must match the drink's flag) and claims no data can support
  (allergen, health benefit and age safety in every domain, stock and shipping), unless the
  answer negates or hedges them, or a domain finds a claim it attributes to the data
  ("dairy-free going by the ingredient list") borne out by that data.
- `Tally`: counts checked facts by kind and collects issues into a `GroundingReport`.

The engine asks for one rewrite of an unverified answer, then falls back to catalog facts.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations, pairwise
from typing import Any

from curio.engine.schemas import GroundingReport

# "€24.99", "€ 24", "24.99 €", "24 EUR", "24.99 euros"
# Signs are captured so "€-2.01" is checked (and rejected) rather than skipped.
PRICE = re.compile(
    r"€\s?([-\u2212]?\d{1,5}(?:[.,]\d{1,2})?)|([-\u2212]?\d{1,5}(?:[.,]\d{1,2})?)\s?(?:€|eur\b|euros?\b)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d{1,5}(?:[.,]\d{1,2})?")
# A whole-euro mention may round a real price ("about €35" for €34.99).
_ROUNDING_TOLERANCE = 1.0
_EXACT_TOLERANCE = 0.011
_CENT = 0.005
# "about", "around": within this share of the real value.
_APPROXIMATE_SHARE = 0.1
# "about" a total or difference: within its written rounding, or this share of it.
_ABOUT_DERIVED_SHARE = 0.02
# Markdown and quote marks that set a name apart from the prose around it.
_OPENERS = {
    "**": "**",
    "__": "__",
    "*": "*",
    "_": "_",
    '"': '"',
    "\u201c": "\u201d",  # curly double quotes
    "'": "'",
    "\u2018": "\u2019",  # curly single quotes
}
DASHES = "\u2014\u2013-"  # em dash, en dash, hyphen


# --- Reports ------------------------------------------------------------------------------


def grounding_report(
    checked: int, issues: list[str], counts: Mapping[str, int] | None = None
) -> GroundingReport:
    """The report for `checked` claims, of which `issues` are unverified. `counts` splits
    the checked claims by kind; without it they count as "fact"."""
    counts = {k: v for k, v in (counts or {"fact": checked}).items() if v}
    checked = sum(counts.values())
    if not checked:
        return GroundingReport(status="not_applicable", issues=issues)
    return GroundingReport(
        status="fallback" if issues else "verified",
        checked=checked,
        issues=issues,
        counts=counts,
    )


def merge_reports(*reports: GroundingReport) -> GroundingReport:
    """One report for several checks of the same answer: any issue means fallback."""
    counts: Counter[str] = Counter()
    issues: list[str] = []
    for report in reports:
        counts.update(report.counts or ({"fact": report.checked} if report.checked else {}))
        issues += [issue for issue in report.issues if issue not in issues]
    return grounding_report(0, issues, counts)


class Tally:
    """Checked facts by kind and the issues found, for one answer."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.issues: list[str] = []
        self._seen: set[tuple[str, str]] = set()

    def add(self, kind: str, ok: bool, issue: str = "", key: str | None = None) -> None:
        """Count one checked fact of `kind`; `issue` is reported when it is not `ok`. Facts
        with the same `key` (a name mentioned twice) count once."""
        if key is not None:
            if (kind, key) in self._seen:
                return
            self._seen.add((kind, key))
        self.counts[kind] += 1
        if not ok and issue and issue not in self.issues:
            self.issues.append(issue)

    def merge(self, report: GroundingReport) -> None:
        self.counts.update(report.counts or ({"fact": report.checked} if report.checked else {}))
        self.issues += [issue for issue in report.issues if issue not in self.issues]

    def report(self) -> GroundingReport:
        return grounding_report(0, list(self.issues), self.counts)


# How a count of each kind reads: "3 prices", "1 alcohol label".
KIND_NOUNS = {
    "price": ("price", "prices"),
    "id": ("ID", "IDs"),
    "rating": ("rating", "ratings"),
    "title": ("title", "titles"),
    "name": ("name", "names"),
    "author": ("author", "authors"),
    "amount": ("amount", "amounts"),
    "alcohol": ("alcohol label", "alcohol labels"),
    "pages": ("page count", "page counts"),
    "year": ("year", "years"),
    "percent": ("percentage", "percentages"),
    "colour": ("colour", "colours"),
    "ingredient": ("ingredient", "ingredients"),
    "claim": ("claim", "claims"),
    "fact": ("fact", "facts"),
    # Rendered by cite mode (`curio.engine.facts`) only.
    "recipe": ("recipe", "recipes"),
    "total": ("total", "totals"),
    "diff": ("difference", "differences"),
}


def describe_counts(counts: Mapping[str, int]) -> str:
    """ "3 prices, 2 names and 1 ID" for {"price": 3, "name": 2, "id": 1}."""
    parts = []
    for kind, number in counts.items():
        if number:
            singular, plural = KIND_NOUNS.get(kind, (kind, f"{kind}s"))
            parts.append(f"{number} {singular if number == 1 else plural}")
    if not parts:
        return "nothing"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + f" and {parts[-1]}"


# --- Numbers in prose ---------------------------------------------------------------------


def parse_number(text: str) -> float:
    """A number as written in prose, with a decimal comma or a minus sign."""
    return float(text.replace(",", ".").replace("\u2212", "-"))


def amounts_in(text: str) -> list[float]:
    """Every number in `text` (what the shopper typed: budgets, sizes, claimed prices)."""
    return [parse_number(raw) for raw in _NUMBER.findall(text)]


def check_ids(
    text: str, pattern: re.Pattern[str], known_ids: Iterable[int], noun: str
) -> tuple[int, list[str]]:
    """IDs `pattern` finds in `text` (in its first non-empty group): how many there are, and
    the ones no tool returned, described as e.g. "product 12"."""
    known = set(known_ids)
    checked = 0
    issues = []
    for match in pattern.finditer(text):
        item_id = int(next(group for group in match.groups() if group))
        checked += 1
        if item_id not in known:
            issues.append(f"{noun} {item_id} was not returned by any tool")
    return checked, issues


def any_of(names: Sequence[str], flags: int = 0) -> re.Pattern[str]:
    """Any of `names` as a whole word or phrase."""
    return re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, names)) + r")(?!\w)", flags)


def name_allowed(name: str, evidence_names: Sequence[str], typed: str) -> bool:
    """A name a tool returned, part of one ("Mojito" in "Virgin Mojito"), or typed."""
    pattern = re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)
    return any(pattern.search(other) for other in [*evidence_names, typed])


def marked_name(
    text: str, start: int, end: int, id_after: re.Pattern[str], id_before: re.Pattern[str]
) -> bool:
    """Whether text[start:end] is bold, italic or quoted, or next to its ID."""
    before, after = text[:start], text[end:]
    for opener, closer in _OPENERS.items():
        if before.endswith(opener) and after.startswith(closer):
            return True
    return bool(id_after.match(after) or id_before.search(before))


# --- Items and references -----------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Item:
    """A catalog row a tool returned, as grounding sees it.

    `kind` is what its citations call it ("product", "book", "drink"), `names` how an answer
    may name it, `values` its facts by kind ({"price": (24.99,)}), `people` who made it
    ("by <name>"), `flags` its labels ({"alcohol": "free"}) and `text` its description, for
    figures an answer quotes from it ("100% cotton").
    """

    kind: str
    id: int
    names: tuple[str, ...]
    values: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)
    people: tuple[str, ...] = ()
    flags: Mapping[str, str] = field(default_factory=dict)
    text: str = ""

    @property
    def label(self) -> str:
        return self.names[0] if self.names else f"{self.kind} {self.id}"


@dataclass(frozen=True)
class Citation:
    """How an answer cites an item by ID: `pattern`'s first non-empty group is the ID of an
    item of `kind`."""

    pattern: re.Pattern[str]
    kind: str


@dataclass(frozen=True)
class Ref:
    """A reference to item(s) at text[start:end]. A `weak` one is a single distinctive word
    of one item's name ("the jeans" for "Slim Jeans"), used only when its sentence has no
    full reference."""

    start: int
    end: int
    items: tuple[Item, ...]
    weak: bool = False


@dataclass(frozen=True)
class Binding:
    """The item(s) a claim is about; `explicit` when referenced in the claim's sentence."""

    items: tuple[Item, ...]
    explicit: bool


# A sentence ends at ".", "!" or "?" before a space (closing marks may come between), or at
# a line break, so each bullet is its own sentence.
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'\u201d\u2019)*_]*[ \t]+|\n+")
# Names this short and single-word ("It", "Rose") match only in their own spelling.
_MIN_CASELESS_NAME = 5


_ABBREVIATION = re.compile(
    r"(?:\b(?:vol|no|mr|mrs|ms|dr|st|jr|sr|vs|etc|e\.g|i\.e|approx|ca)|\b[A-Z])\.$", re.I
)


def sentences(text: str, keep: Sequence[tuple[int, int]] = ()) -> list[tuple[int, int]]:
    """The (start, end) of each sentence or line of `text`. No sentence ends inside a span
    of `keep` (an item's name: "All-Star Superman, Vol. 1") or after an abbreviation."""
    cuts = [0]
    for match in _SENTENCE_END.finditer(text):
        if "\n" not in match.group(0):
            # A name's own final period ("Curvy Jegging HW.") ends no sentence.
            if any(s < match.start() <= e for s, e in keep):
                continue
            if _ABBREVIATION.search(text[max(0, match.start() - 6) : match.start() + 1]):
                continue
        cuts.append(match.end())
    cuts.append(len(text))
    return [(a, b) for a, b in pairwise(cuts) if b > a]


def _name_pattern(names: Iterable[str], flags: int) -> re.Pattern[str] | None:
    names = sorted({n for n in names if n.strip()}, key=lambda n: (-len(n), n))
    if not names:
        return None
    # Letters and digits bound a name, but "_" does not: "_Beloved_" is italic.
    return re.compile(r"(?<![^\W_])(?:" + "|".join(map(re.escape, names)) + r")(?![^\W_])", flags)


def find_refs(text: str, items: Sequence[Item], citations: Sequence[Citation]) -> list[Ref]:
    """Every reference to an item in `text`: citations of a returned ID, and names."""
    by_id = {(item.kind, item.id): item for item in items}
    refs: list[Ref] = []
    taken: list[tuple[int, int]] = []
    for citation in citations:
        for match in citation.pattern.finditer(text):
            item_id = int(next(group for group in match.groups() if group))
            item = by_id.get((citation.kind, item_id))
            taken.append(match.span())
            if item is not None:
                refs.append(Ref(match.start(), match.end(), (item,)))
    owners: dict[str, list[Item]] = {}
    for item in items:
        for name in item.names:
            if len(name.strip()) >= 2:
                owners.setdefault(name.casefold(), []).append(item)
    long_names = [n for i in items for n in i.names if _caseless(n)]
    short_names = [n for i in items for n in i.names if not _caseless(n) and len(n.strip()) >= 2]
    for pattern in (_name_pattern(long_names, re.IGNORECASE), _name_pattern(short_names, 0)):
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(s < end and start < e for s, e in taken):
                continue
            taken.append((start, end))
            owned = tuple(dict.fromkeys(owners.get(match.group(0).casefold(), [])))
            if owned:
                refs.append(Ref(start, end, owned))
    refs += _word_refs(text, items, taken)
    return sorted(refs, key=lambda ref: ref.start)


_NAME_WORD = re.compile(r"[^\W\d_][\w'-]{3,}")


def _word_refs(text: str, items: Sequence[Item], taken: list[tuple[int, int]]) -> list[Ref]:
    """Weak references: words of at least four letters that occur in exactly one item's
    names (a plural "s" or "es" may follow), outside the spans already taken."""
    owners: dict[str, set[int]] = {}
    by_id = {id(item): item for item in items}
    for item in items:
        for name in item.names:
            for word in _NAME_WORD.findall(name):
                if word.casefold() not in HEADING_WORDS:
                    owners.setdefault(word.casefold(), set()).add(id(item))
    unique = {word: next(iter(ids)) for word, ids in owners.items() if len(ids) == 1}
    if not unique:
        return []
    words = sorted(unique, key=lambda w: (-len(w), w))
    pattern = re.compile(
        r"(?<![^\W_])(" + "|".join(map(re.escape, words)) + r")(?:e?s)?(?![^\W_])", re.IGNORECASE
    )
    refs = []
    for match in pattern.finditer(text):
        start, end = match.span()
        if any(s < end and start < e for s, e in taken):
            continue
        refs.append(Ref(start, end, (by_id[unique[match.group(1).casefold()]],), weak=True))
    return refs


def _caseless(name: str) -> bool:
    return len(name.split()) > 1 or len(name.strip()) >= _MIN_CASELESS_NAME


# "The former", "the latter", and "the first" or "the second" used as a pronoun ("the first
# uses vodka", "the second, gin").
_ORDINAL = re.compile(r"\bthe (former|latter|first|second)\b", re.IGNORECASE)
_ORDINAL_PRONOUN = re.compile(
    r"\s*(?:one\b|is\b|uses\b|has\b|needs\b|takes\b|adds\b|costs\b|runs\b|was\b|[,;:.])"
)


def _ordinal_refs(text: str, refs: Sequence[Ref]) -> list[Ref]:
    """References by order to the last two items named before, in the same paragraph."""
    found = []
    matches = list(_ORDINAL.finditer(text))
    words = {m.group(1).lower() for m in matches}
    # "The first ..., the second ..." pairs up even when a noun follows.
    paired = {"first", "second"} <= words or {"former", "latter"} <= words
    for match in matches:
        if not (paired or _ORDINAL_PRONOUN.match(text, match.end())):
            continue
        paragraph = text.rfind("\n\n", 0, match.start()) + 1
        named: list[Item] = []
        for ref in refs:
            if not ref.weak and paragraph <= ref.start and ref.end <= match.start():
                named += [item for item in ref.items if item not in named]
        if len(named) < 2:
            continue
        first, second = named[-2], named[-1]
        word = match.group(1).lower()
        item = first if word in ("former", "first") else second
        found.append(Ref(match.start(), match.end(), (item,)))
    return found


class Binder:
    """Ties claims in one answer to the items they are about (see the module docstring)."""

    def __init__(self, text: str, items: Sequence[Item], citations: Sequence[Citation]) -> None:
        self.text = text
        self.items = list(items)
        self.refs = find_refs(text, items, citations)
        self.refs = sorted([*self.refs, *_ordinal_refs(text, self.refs)], key=lambda r: r.start)
        self.strong = [r for r in self.refs if not r.weak]
        self.sentences = sentences(text, [(r.start, r.end) for r in self.strong])
        named = {id(item): item for ref in self.strong for item in ref.items}
        if len(named) == 1:
            self._only: tuple[Item, ...] = tuple(named.values())
        elif not named and len(self.items) == 1:
            self._only = (self.items[0],)
        else:
            self._only = ()

    def sentence(self, position: int) -> tuple[int, int]:
        for start, end in self.sentences:
            if start <= position < end:
                return start, end
        return position, position

    def bind(self, start: int, end: int, *, nearest: bool = False) -> Binding | None:
        """The item(s) the claim at text[start:end] is about. By default the nearest
        reference before it in its sentence wins (numbers follow what they describe:
        "the Summer Dress is €24.99"); with `nearest`, the closest one on either side does
        (adjectives come before or after: "an alcohol-free Shirley Temple"). Next come
        earlier sentences of the same line or bullet, then weak references in the sentence,
        then the last item named before, then the only item."""
        first, last = self.sentence(start)
        line = self.text.rfind("\n", 0, start) + 1
        weak = [r for r in self.refs if r.weak]
        for refs, explicit in ((self.strong, True), (self.strong, False), (weak, True)):
            if not explicit:  # an earlier sentence of the same line or bullet
                same_line = [r for r in refs if line <= r.start and r.end <= first]
                if same_line:
                    return Binding(same_line[-1].items, False)
                continue
            before = [r for r in refs if first <= r.start and r.end <= start]
            after = [r for r in refs if r.start >= end and r.end <= last]
            if nearest and before and after:
                gap_before = start - before[-1].end
                gap_after = after[0].start - end
                return Binding((after[0] if gap_after < gap_before else before[-1]).items, True)
            if before:
                return Binding(before[-1].items, True)
            if after:
                return Binding(after[0].items, True)
        earlier = [r for r in self.strong if r.end <= first]
        if earlier:
            return Binding(earlier[-1].items, False)
        if self._only:
            return Binding(self._only, False)
        return None

    def items_between(self, start: int, end: int, *, weak: bool = False) -> list[Item]:
        """The items referenced in text[start:end] (with `weak`, by a single name word too)."""
        refs = self.refs if weak else self.strong
        found = {id(i): i for r in refs if start <= r.start < end for i in r.items}
        return list(found.values())


# --- Claims about numbers -----------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """A number (or measure) the answer states: `value` is what `compare` receives."""

    kind: str
    start: int
    end: int
    raw: str
    value: Any


_STRIP_MARKS = re.compile(r"[*_`\u201c\u201d\"]")
_NEGATION = r"(?:\bnot|n['\u2019]t|\bnever|\bno longer|\brather than|\binstead of)"
_CONTRADICTED_BEFORE = re.compile(_NEGATION + r"\s+(?:[\w'\u2019-]+\s+){0,2}$")
_CONTRADICTED_AFTER = re.compile(
    r"^\s*(?:[\w'\u2019-]+\s+){0,2}(?:is|was|would be|seems)\s+(?:wrong|incorrect|outdated|"
    r"out of date|a mistake|not (?:right|correct|accurate|current))"
    r"|^\s*(?:[\w'\u2019-]+\s+){0,2}(?:isn['\u2019]t|wasn['\u2019]t) (?:right|correct|accurate)"
)
_REPORTED_BEFORE = re.compile(
    r"(?:\byou|\byour friend|\bthey|\bsomeone)\s+(?:said|mentioned|saw|heard|quoted|read|"
    r"suggested|thought)\s+(?:(?:that|it|it's|it['\u2019]s|was|is|the|a|costs?|rated)\s+){0,3}$"
    r"|\byour friend['\u2019]s\s+$"
)
_REPORTED_AFTER = re.compile(
    r"^\s*(?:that\s+|which\s+)?(?:you|your friend|they)\s+(?:said|mentioned|saw|heard|quoted|"
    r"read|suggested)"
)
_BUDGET_BEFORE = re.compile(
    r"\bbudget(?:\s+(?:of|is|was|at))?\s*:?\s*(?:(?:around|about|roughly|approximately)\s+)?$"
    r"|\bspend(?:ing)?\s+(?:up to\s+)?$"
    r"|\blimit(?:\s+(?:of|is))?\s*:?\s*$"
)
_BUDGET_AFTER = re.compile(r"^\s*(?:total\s+|outfit\s+|overall\s+)?(?:budget|limit|cap)\b")
_LESS = re.compile(
    r"(?:\bunder|\bbelow|\bless than|\bat most|\bno more than|\bwithin|\bmax(?:imum)?"
    r"(?:\s+of)?|\bcheaper than|\bnot over|\bbeneath"
    # "Adds up to 1,237 pages" states a sum, not a limit.
    r"|(?<!\badd )(?<!\badds )(?<!\badded )(?<!adding )\bup to)"
    r"\s+(?:(?:a|an|the|your|my|just)\s+)?$"
)
_MORE = re.compile(
    r"(?:\bover|\babove|\bmore than|\bat least|\bfrom|\bstarting at|\bmin(?:imum)?(?:\s+of)?"
    r"|\bpricier than)\s+(?:(?:a|an|the|your|my|just)\s+)?$"
)
_ABOUT = re.compile(
    r"(?:\babout|\baround|\broughly|\bapproximately|\bnearly|\balmost|\bclose to|\bjust under|"
    r"\bjust over|\bjust below|\bjust above|~)\s*$"
)
_LESS_AFTER = re.compile(r"^\s*(?:or less|or under|or below|max\b|at most)")
_MORE_AFTER = re.compile(r"^\s*(?:or more|or above|\+)")


@dataclass(frozen=True)
class Qualifier:
    """How a number is framed. `mode`: "exact", "less" (under), "more" (over) or "about".
    `contradicted`: the answer says it is wrong or someone else's figure. `budget`: it is a
    budget or limit, not an item's value."""

    mode: str = "exact"
    contradicted: bool = False
    budget: bool = False


def qualifier(text: str, start: int, end: int, sentence: tuple[int, int]) -> Qualifier:
    """How the number at text[start:end] is framed, from the words around it."""
    before = _STRIP_MARKS.sub("", text[max(sentence[0], start - 60) : start]).lower()
    after = _STRIP_MARKS.sub("", text[end : min(sentence[1], end + 60)]).lower()
    contradicted = bool(
        _CONTRADICTED_BEFORE.search(before)
        or _CONTRADICTED_AFTER.search(after)
        or _REPORTED_BEFORE.search(before)
        or _REPORTED_AFTER.search(after)
    )
    budget = bool(_BUDGET_BEFORE.search(before) or _BUDGET_AFTER.search(after))
    if _ABOUT.search(before):
        mode = "about"
    elif _LESS.search(before) or _LESS_AFTER.search(after):
        mode = "less"
    elif _MORE.search(before) or _MORE_AFTER.search(after):
        mode = "more"
    else:
        mode = "exact"
    return Qualifier(mode, contradicted, budget)


def same_number(
    claimed: float, actual: float, mode: str, tolerance: float = _EXACT_TOLERANCE
) -> bool:
    """Whether `actual` satisfies the claim "`mode` `claimed`"."""
    if mode == "less":
        return actual <= claimed + tolerance
    if mode == "more":
        return actual >= claimed - tolerance
    if mode == "about":
        return abs(actual - claimed) <= max(tolerance, abs(actual) * _APPROXIMATE_SHARE)
    return abs(actual - claimed) <= tolerance


def same_price(claimed: float, actual: float, mode: str) -> bool:
    """A price claim: exact to the cent, or a whole euro that rounds the price."""
    if same_number(claimed, actual, mode):
        return True
    return (
        mode == "exact"
        and float(claimed).is_integer()
        and abs(claimed - actual) <= _ROUNDING_TOLERANCE
    )


type Compare = Callable[[Any, Any, str], bool]

# --- Totals, differences and multiples -----------------------------------------------------

# Where a number is framed as derived from item values: a total anywhere in its sentence
# ("together that's €74.98", "| **Total** | €23.97 |"), a difference in its own clause ("so
# the Waverly is €5.00 cheaper"), so a price stated for one item in a sentence that also
# says "cheaper" is still that item's price.
_TOTAL = re.compile(
    r"\b(?:total(?:s|ling|ing)?|together|combined|in all|altogether|all together|sum|"
    r"adds? up|comes? to|come to|runs? to|"
    r"for (?:both|all (?:two|three|four|five|six)|the (?:two|three|four|pair|lot|set))|"
    r"buying (?:both|all|the two|the three|them|these|those)|"
    r"(?:both|all (?:three|four|five)) (?:would|will) (?:cost|be|come))\b"
)
_DIFFERENCE = re.compile(
    r"\b(?:cheaper|less|more|pricier|dearer|costlier|more expensive|difference|apart|"
    r"saves?|saving|savings|longer|shorter|over (?:your |the )?budget|under (?:your |the )?"
    r"budget|over by|short|left|leaving|leaves|remaining|to spare)\b"
)
# A discount is never arithmetic on the catalog: "€5 off", "on sale", "discounted".
# "Off" only after an amount or before a price: "Off White" is a colour.
_DISCOUNT = re.compile(
    r"\b(?:on sale|sale price|discount\w*|reduced|marked down|markdown|price drop|"
    r"promo\w*|coupon|clearance)\b|(?:\d|%)\s*off\b|\boff (?:the|its|their|regular|original|"
    r"retail|list) (?:price|tag)\b"
)
_BUDGETISH = re.compile(r"\b(?:budget|limit|left|leaving|leaves|remaining|spare|over|short)\b")
_COUNT_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "a pair of": 2, "a couple of": 2,
}  # fmt: skip
# "4 pairs for €23.96", "three of them come to €17.97", "two packs at €11.98".
_MULTIPLE_BEFORE = re.compile(
    r"(?:(?<![\d.,\u20ac])\b(\d{1,2})"
    r"|\b(two|three|four|five|six|seven|eight|nine|ten|a pair of|a couple of))"
    r"\s+(?:[\w'\u2019-]+\s+){0,6}?(?:for|at|costs?|would cost|would be|comes? to|come to|"
    r"=|is|are)\s+(?:(?:just|only|a total of|about|around)\s+)?$"
)
# "€5.99 x 4 = €23.96", "4x" (also with the multiplication sign).
_MULTIPLE_SIGN = re.compile(r"(?:[x\u00d7]\s?(\d{1,2})\b|\b(\d{1,2})\s?[x\u00d7])")
# A clause ends at punctuation, a spaced dash or a conjunction; a label's colon and table
# cells ("|") do not split it, so "Total: €54.98" and a "Total" row frame their amount.
_CLAUSE_EDGE = re.compile(
    r"[.!?;\n()]|,\s|\s[\u2014\u2013-]\s|\b(?:but|while|whereas|although|though|and)\b"
)
# What may stand between facts listed in the order of their items: "€24.99 and €29.99",
# "4.53, 4.46 and 4.37" (a unit or "stars" may follow each).
_LIST_JOIN = re.compile(
    r"\s*(?:pages?|stars?|\u2605)?\s*(?:,\s*(?:and\s+|or\s+)?|\s(?:and|or)\s+|\s*/\s*)"
    r"(?:\u20ac|\u2605\s*)?\s*"
)
# Subsets of more items than this are not tried: only their full sum.
_MAX_SUBSET_ITEMS = 8


def clause_span(text: str, start: int, end: int, sentence: tuple[int, int]) -> tuple[int, int]:
    """The (start, end) of the clause around text[start:end] within its sentence."""
    head = text[sentence[0] : start]
    cuts = [m.end() for m in _CLAUSE_EDGE.finditer(head)]
    first = sentence[0] + (cuts[-1] if cuts else 0)
    tail = text[end : sentence[1]]
    stop = _CLAUSE_EDGE.search(tail)
    return first, end + (stop.start() if stop else len(tail))


def _clause(text: str, start: int, end: int, sentence: tuple[int, int]) -> str:
    """The clause around text[start:end], lower case and without markdown marks."""
    first, last = clause_span(text, start, end, sentence)
    return _STRIP_MARKS.sub("", text[first:last]).lower()


# A clause whose subject is one item: "it's under €20", "this one is over €50".
_SINGULAR_SUBJECT = re.compile(
    r"^[\s*_\-]*(?:yes,?\s+|so\s+|and\s+)?(?:it|it['\u2019]s|this|that|this one|that one|he|she)\b",
    re.IGNORECASE,
)


def _rounding_unit(claimed: float, mode: str) -> float:
    """The rounding a written number implies: "€5.00" none, "€76" a euro, "about 1,200" a
    hundred. Only "about" allows more than a unit."""
    if not float(claimed).is_integer():
        return 0.0
    if mode != "about":
        return 1.0
    whole = int(abs(claimed))
    for unit in (1000, 100, 10, 5):
        if whole and whole % unit == 0:
            return float(unit)
    return 1.0


def same_derived(claimed: float, actual: float, mode: str) -> bool:
    """Whether a total, difference or multiple `claimed` states `actual`: to the cent, or
    rounded to the precision written ("€76" for €75.96, "about 1,200 pages" for 1,157)."""
    if mode in ("less", "more"):
        return same_number(claimed, actual, mode)
    tolerance = max(_EXACT_TOLERANCE, _rounding_unit(claimed, mode) / 2 + 1e-9)
    if mode == "about":
        tolerance = max(tolerance, abs(actual) * _ABOUT_DERIVED_SHARE)
    return abs(actual - claimed) <= tolerance


def _subset_sums(values: Sequence[float]) -> list[float]:
    """Sums of two or more of `values` (only the full sum when there are many)."""
    if len(values) > _MAX_SUBSET_ITEMS:
        return [sum(values)]
    sums = []
    for mask in range(1, 1 << len(values)):
        if mask & (mask - 1):  # at least two members
            sums.append(sum(v for i, v in enumerate(values) if mask >> i & 1))
    return sums


def _small_sums(values: Sequence[float]) -> list[float]:
    """Sums of any two or three of `values`, and of all of them."""
    sums = [a + b for a, b in combinations(values, 2)]
    sums += [a + b + c for a, b, c in combinations(values, 3)]
    if len(values) > 3:
        sums.append(sum(values))
    return sums


# Arithmetic written out: "€34.99 + €19.99 = €54.98", "1,000 \u2212 635".
_ARITHMETIC = re.compile(r"[+=\u00d7]|\s[-\u2212]\s|\b(?:minus|plus)\b")
# What may stand between an item's name and its own value: punctuation, a parenthesis with
# no figure in it ("(Off White)"; after "(176 pages)" the next figure is not the item's),
# table cells, a short descriptor (", Grey, ") and a copula ("is", "costs", "priced at").
_VALUE_GAP = re.compile(
    r"^[\s,:;(\[|\u2013\u2014-]*(?:\([^()\n\d]{0,40}\)[\s,:;|\u2013\u2014-]*)?"
    r"(?:[^|\n]{0,30}\|[\s|]*)*(?:(?:[\w'-]+ ){0,2}[\w'-]+,\s+)?"
    r"(?:(?:is|are|was|were|'s|\u2019s|costs?|costing|priced|at|for|now|currently|only|just|"
    r"a mere|listed|sells?|retails?|goes|comes? in|in|rated|has|with|of|about|around|roughly|"
    r"still|also|too|it's|it\u2019s)\s+)*(?:\(\s*)?$"
)
# Two names joined as one subject: "the A and the B cost ...".
_JOINED = re.compile(r"[\s,]*(?:and|&|\+|plus|with|or)\s+(?:the\s+)?")
# A singular pronoun and a copula before the value: "it's €54.98", "this one is just €9".
_PRONOUN_VALUE = re.compile(
    r"^[\s\-]*(?:yes,?\s+|so\s+|and\s+)?(?:it|this|this one|that one)"
    r"(?:['\u2019]s|\s+is|\s+costs?|\s+was|\s+sells for)\s+"
    r"(?:(?:only|just|now|currently|priced at|at|a mere|listed at)\s+)*$"
)
_FOR_ITEM = re.compile(r"\s*(?:for|on)\s+(?:the\s+)?[*_]*")
# Words that frame a figure the shopper typed as their own money: "with €15 for two", "a
# budget of around €20", "you have €40".
_OWN_MONEY = re.compile(
    r"\b(?:with|have|having|got|your|budget(?: of| is)?|spend(?:ing)?|limit(?: of)?)\s+"
    r"(?:(?:a|your|just|only|up to|around|about|roughly|approximately)\s+)*~?\s*$"
)


def euro_claims(text: str) -> list[Claim]:
    """Euro amounts in `text` as price claims."""
    claims = []
    for match in PRICE.finditer(text):
        raw = match.group(1) or match.group(2)
        claims.append(Claim("price", match.start(), match.end(), raw, parse_number(raw)))
    return claims


@dataclass
class FactCheck:
    """Checks number claims against the items they are bound to.

    `compare[kind](claimed, actual, mode)` decides whether an item's value supports a claim;
    `free[kind]` are values the tools stated without an item (totals, budgets, what is
    left); `typed` is what the shopper wrote; `describe[kind]` formats a value for issues
    ("€24.99"); `noun[kind]` names the fact ("price").
    """

    binder: Binder
    compare: Mapping[str, Compare]
    free: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    typed: str = ""
    describe: Mapping[str, Callable[[Any], str]] = field(default_factory=dict)
    noun: Mapping[str, str] = field(default_factory=dict)
    arithmetic: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        self._typed = amounts_in(self.typed)
        self._paired: dict[int, Binding] = {}

    def check(self, claims: Iterable[Claim], tally: Tally) -> None:
        claims = list(claims)
        self._paired = self._pair_in_order(claims)
        for claim in claims:
            ok, issue = self._judge(claim)
            tally.add(claim.kind, ok, issue)

    def _pair_in_order(self, claims: Sequence[Claim]) -> dict[int, Binding]:
        """Claims listed after the items they describe, in the same order: "the A and the B
        cost €24.99 and €29.99", "rated 4.53 and 4.46 respectively". When a sentence names
        n items and then states n facts of one kind joined only by "and", "or", commas or
        "respectively", the k-th fact binds to the k-th item."""
        text = self.binder.text
        paired: dict[int, Binding] = {}
        by_sentence: dict[tuple[int, int, str], list[Claim]] = {}
        for claim in claims:
            key = (*self.binder.sentence(claim.start), claim.kind)
            by_sentence.setdefault(key, []).append(claim)
        for (first, _last, _), group in by_sentence.items():
            if len(group) < 2:
                continue
            refs = [r for r in self.binder.strong if first <= r.start and r.end <= group[0].start]
            if len(refs) != len(group) or any(
                group[0].start <= r.start < group[-1].end for r in self.binder.strong
            ):
                continue
            joins = [text[a.end : b.start] for a, b in pairwise(group)]
            if not all(_LIST_JOIN.fullmatch(_STRIP_MARKS.sub("", j)) for j in joins):
                continue
            for ref, claim in zip(refs, group, strict=True):
                paired[claim.start] = Binding(ref.items, True)
        return paired

    def _judge(self, claim: Claim) -> tuple[bool, str]:
        text = self.binder.text
        sentence = self.binder.sentence(claim.start)
        framing = qualifier(text, claim.start, claim.end, sentence)
        compare = self.compare[claim.kind]
        binding = (
            self._paired.get(claim.start)
            or self._for_item(claim, sentence)
            or self.binder.bind(claim.start, claim.end)
        )
        shown = self._show(claim)
        noun = self.noun.get(claim.kind, claim.kind)
        if framing.contradicted:
            if self._was_typed(claim) or binding is None:
                return True, ""
            if any(self._matches(claim, item, "exact") for item in binding.items):
                label = binding.items[0].label
                return False, f"the answer denies that {label} has a {noun} of {shown}"
            return True, ""
        free = [v for v in self.free.get(claim.kind, ()) if compare(claim.value, v, "exact")]
        # A question (or a quick reply) may suggest a budget or a limit; it claims nothing.
        asking = text[sentence[0] : sentence[1]].rstrip(" *_\n").endswith("?")
        if framing.budget:
            if free or asking or self._was_typed(claim):
                return True, ""
            return False, f"{shown} is not a budget or limit the shopper or the tools gave"
        if binding is not None and any(
            self._matches(claim, item, framing.mode) for item in binding.items
        ):
            return True, ""
        if free:
            return True, ""
        own = framing.mode in ("less", "more") and self._was_typed(claim)
        if own and self._own_comparison(claim, sentence):
            return True, ""  # the shopper's own figure: "the last two are over €50"
        if self._derived(claim, framing.mode, sentence):
            return True, ""
        if self._combination(claim, framing.mode, sentence):
            return True, ""
        if framing.mode == "exact" and self._was_typed(claim) and self._own_money(claim, sentence):
            return True, ""  # the shopper's own figure: "with €15 for two"
        if binding is None and framing.mode in ("less", "more"):
            if asking or self._was_typed(claim):
                return True, ""  # the shopper's own limit ("under €30"), or a suggestion
            # "Here is everything under €30:" holds for every item the answer names (or,
            # when it names none, every item the tools returned).
            named = self.binder.items_between(0, len(text)) or self.binder.items
            priced = [item for item in named if item.values.get(claim.kind)]
            if priced and all(self._matches(claim, item, framing.mode) for item in priced):
                return True, ""
        if binding is None:
            return False, f"{shown} is not attributed to any item the tools returned (unattributed)"
        item = binding.items[0]
        actual = item.values.get(claim.kind, ())
        if not actual:
            return False, f"{shown} is not a {noun} the tools returned for {item.label}"
        listed = ", ".join(self._format(claim.kind, value) for value in actual[:4])
        return False, f"{shown} does not match the {noun} of {item.label} ({listed})"

    def _for_item(self, claim: Claim, sentence: tuple[int, int]) -> Binding | None:
        """The item a value is "for": "€24.99 against €39.99 for the **Eva trousers**"."""
        after = [r for r in self.binder.strong if claim.end <= r.start < sentence[1]]
        gap = self.binder.text[claim.end : after[0].start].lower() if after else ""
        return Binding(after[0].items, True) if after and _FOR_ITEM.fullmatch(gap) else None

    def _own_comparison(self, claim: Claim, sentence: tuple[int, int]) -> bool:
        """Whether "under/over <the shopper's figure>" compares against their figure rather
        than stating one item's value: its clause names no item and is not about a single
        one ("it's under €20" confirms a figure for the item before)."""
        first, last = clause_span(self.binder.text, claim.start, claim.end, sentence)
        if self.binder.items_between(first, last):
            return False
        return not _SINGULAR_SUBJECT.match(self.binder.text[first:last])

    def _derived(self, claim: Claim, mode: str, sentence: tuple[int, int]) -> bool:
        """Whether the claim is a total, difference or multiple its words frame as one,
        of the values of the items its sentence names (or, when the sentence names fewer
        than two, the items the whole answer names; when it names none, the total of every
        item the tools returned), and equals it."""
        if claim.kind not in self.arithmetic or not isinstance(claim.value, int | float):
            return False
        text = self.binder.text
        clause = _clause(text, claim.start, claim.end, sentence)
        said = _STRIP_MARKS.sub("", text[sentence[0] : sentence[1]]).lower()
        if _DISCOUNT.search(said):
            return False
        # A total may be framed anywhere in its sentence ("Together, A, B and C run to 1,157
        # pages"); a difference only in its own clause, since "the cheaper one at €19.99"
        # states one item's price.
        total = bool(_TOTAL.search(said))
        difference = bool(_DIFFERENCE.search(clause))
        count = self._multiplier(text, claim, clause, sentence)
        if not (total or difference or count):
            return False
        in_sentence = self.binder.items_between(*sentence, weak=True)
        scopes = [in_sentence]
        if len(in_sentence) < 2:
            scopes.append(self.binder.items_between(0, len(text), weak=True))
        for items in scopes:
            values = [float(v) for item in items for v in item.values.get(claim.kind, ())[:1]]
            candidates: list[float] = []
            if total:
                candidates += _subset_sums(values)
            if difference:
                candidates += [abs(a - b) for i, a in enumerate(values) for b in values[i + 1 :]]
                if _BUDGETISH.search(clause):
                    given = [*self._typed, *map(float, self.free.get(claim.kind, ()))]
                    sums = [*values, *_subset_sums(values)]
                    candidates += [abs(g - s) for g in given for s in sums]
            if count:
                bound = self.binder.bind(claim.start, claim.end)
                own = [*items, *(bound.items if bound else ())]
                candidates += [
                    count * float(v) for item in own for v in item.values.get(claim.kind, ())
                ]
            if any(same_derived(float(claim.value), c, mode) for c in candidates):
                return True
        if total and not self.binder.strong:
            # An answer that names no item ("the trilogy is 1,157 pages in total") may give
            # the total of everything the tools returned.
            values = [float(v) for i in self.binder.items for v in i.values.get(claim.kind, ())[:1]]
            return len(values) > 1 and same_derived(float(claim.value), sum(values), mode)
        return False

    def _combination(self, claim: Claim, mode: str, sentence: tuple[int, int]) -> bool:
        """The permissive rule for derived numbers: a number equal to the sum of the values
        of any two or three items the answer names (or of all of them), the difference of
        two, or a figure the shopper or the tools gave minus one value or such a sum ("€20
        left"), is accepted whatever its wording, unless it is framed as a discount or as
        one item's own value ("the Summer Dress is €54.98"). A wrong total is then caught
        only when it matches no such combination: a trade-off made to keep false alarms on
        correct arithmetic low."""
        if claim.kind not in self.arithmetic or not isinstance(claim.value, int | float):
            return False
        if mode not in ("exact", "about"):
            return False  # "under €20" bounds values; it is not a sum of them
        text = self.binder.text
        if _DISCOUNT.search(_STRIP_MARKS.sub("", text[sentence[0] : sentence[1]]).lower()):
            return False
        if self._single_item(claim, sentence):
            return False
        items = self.binder.items_between(0, len(text), weak=True) or self.binder.items
        values = [float(v) for item in items for v in item.values.get(claim.kind, ())[:1]]
        sums = _small_sums(values)
        candidates = [*sums, *(abs(a - b) for a, b in combinations(values, 2))]
        given = [*self._typed, *map(float, self.free.get(claim.kind, ()))]
        candidates += [abs(g - s) for g in given for s in [*values, *sums]]
        claimed = float(claim.value)
        # To the cent: with this many candidates, a cent's slack ("€14.99" for €24.99 -
        # €9.99) would accept near misses.
        exact = mode == "exact" and not claimed.is_integer()
        return any(
            abs(claimed - c) < _CENT if exact else same_derived(claimed, c, mode)
            for c in candidates
        )

    def _single_item(self, claim: Claim, sentence: tuple[int, int]) -> bool:
        """Whether the claim states one item's own value: right after the item's name ("the
        **Summer Dress** is €54.98", "| **Hanky skirt** | Purple | €17.99 |", "Fight Club
        (218 pages)"), after a singular pronoun ("it's €54.98"), or right before "for the
        <item>", with no total, difference or arithmetic sign in its clause."""
        text = self.binder.text
        first, last = clause_span(text, claim.start, claim.end, sentence)
        clause = _STRIP_MARKS.sub("", text[first:last]).lower()
        if _TOTAL.search(clause) or _DIFFERENCE.search(clause) or _ARITHMETIC.search(clause):
            return False
        if self._multiplier(text, claim, clause, sentence):
            return False
        strong = self.binder.strong
        before = [r for r in strong if sentence[0] <= r.start and r.end <= claim.start]
        if before:
            ref = before[-1]
            gap = _STRIP_MARKS.sub("", text[ref.end : claim.start]).lower()
            if _VALUE_GAP.match(gap):
                earlier = [r for r in before[:-1] if r.end <= ref.start]
                joined = earlier and _JOINED.fullmatch(
                    _STRIP_MARKS.sub("", text[earlier[-1].end : ref.start]).lower()
                )
                return not joined
        lead = _STRIP_MARKS.sub("", text[first : claim.start]).lower()
        if _PRONOUN_VALUE.match(lead):
            return True
        after = [r for r in strong if claim.end <= r.start < sentence[1]]
        return bool(after and _FOR_ITEM.fullmatch(text[claim.end : after[0].start].lower()))

    def _own_money(self, claim: Claim, sentence: tuple[int, int]) -> bool:
        """Whether the words before a figure the shopper typed frame it as theirs."""
        text = self.binder.text
        before = _STRIP_MARKS.sub("", text[max(sentence[0], claim.start - 40) : claim.start])
        return bool(_OWN_MONEY.search(before.lower()))

    @staticmethod
    def _multiplier(text: str, claim: Claim, clause: str, sentence: tuple[int, int]) -> int:
        """The count a multiple is framed by ("4 pairs for €23.96"), else 0."""
        before = _STRIP_MARKS.sub("", text[max(sentence[0], claim.start - 60) : claim.start])
        match = _MULTIPLE_BEFORE.search(before.lower())
        if match is None:
            match = _MULTIPLE_SIGN.search(clause)
            if match is None:
                return 0
        raw = next(group for group in match.groups() if group)
        count = int(raw) if raw.isdigit() else _COUNT_WORDS[raw]
        return count if 2 <= count <= 20 else 0

    def _matches(self, claim: Claim, item: Item, mode: str) -> bool:
        compare = self.compare[claim.kind]
        return any(compare(claim.value, value, mode) for value in item.values.get(claim.kind, ()))

    def _was_typed(self, claim: Claim) -> bool:
        value = claim.value
        number = value[0] if isinstance(value, tuple) else value
        if isinstance(number, str):
            try:
                number = parse_number(number)
            except ValueError:
                return False
        return isinstance(number, int | float) and any(
            abs(number - typed) < 1e-9 for typed in self._typed
        )

    def _show(self, claim: Claim) -> str:
        return self._format(claim.kind, claim.value) if claim.kind == "price" else claim.raw

    def _format(self, kind: str, value: Any) -> str:
        describe = self.describe.get(kind)
        if describe is not None:
            return describe(value)
        return f"€{value:.2f}" if kind == "price" else str(value)


# --- Names --------------------------------------------------------------------------------

# Bold, italic and quoted spans. Straight single quotes are apostrophes too often to count.
_MARKED = re.compile(
    r"\*\*(?P<b>[^*\n]{1,120}?)\*\*"
    r"|__(?P<u>[^_\n]{1,120}?)__"
    r"|(?<![\w*])\*(?![\s*])(?P<i>[^*\n]{1,120}?)(?<!\s)\*(?![\w*])"
    r"|(?<![\w_])_(?![\s_])(?P<j>[^_\n]{1,120}?)(?<!\s)_(?![\w_])"
    r"|\"(?P<q>[^\"\n]{2,120}?)\""
    r"|\u201c(?P<c>[^\u201d\n]{2,120}?)\u201d"
)
# Words that make up headings and emphasis ("**Why it works**", "**Top pick**"), never names.
HEADING_WORDS = frozenset(
    {
    "a", "an", "the", "and", "or", "of", "for", "with", "in", "on", "to", "at", "by", "from",
    "as", "is", "are", "be", "it", "its", "it's", "this", "that", "these", "those", "my",
    "your", "our", "their", "you", "you'll", "we", "i", "me", "one", "two", "three", "four",
    "five", "first", "second", "third", "next", "last", "also", "more", "most", "less", "best",
    "better", "good", "great", "perfect", "top", "new", "old", "other", "another", "same",
    "quick", "easy", "simple", "classic", "fun", "cosy", "cozy", "light", "dark", "why", "how",
    "what", "which", "who", "when", "where", "here", "there", "now", "then", "today", "tonight",
    "works", "work", "worth", "note", "notes", "tip", "tips", "heads", "up", "important",
    "warning", "summary", "overview", "details", "detail", "bonus", "extra", "update",
    "updated", "pick", "picks", "choice", "choices", "option", "options", "alternative",
    "alternatives", "idea", "ideas", "suggestion", "suggestions", "recommendation",
    "recommendations", "favourite", "favorite", "match", "matches", "result", "results", "look",
    "looks", "style", "total", "totals", "budget", "price", "prices", "cost", "rating",
    "ratings", "rated", "score", "reviews", "review", "yes", "no", "okay", "ok", "sure",
    "please", "thanks", "thank", "sorry", "done", "saved", "save", "proposed", "proposal",
    "list", "lists", "shortlist", "step", "steps", "method", "instructions", "ingredients",
    "ingredient", "recipe", "recipes", "garnish", "glass", "serve", "serving", "makes", "make",
    "try", "read", "reading", "similar", "like", "liked", "readers", "reader", "what's", "whats",
    "if", "you're", "men's", "women's", "kids", "kid's", "girls", "boys",
    "unisex", "adult", "adults", "theme", "mood", "vibe", "plan", "evening", "night", "day",
    "morning", "weekend", "outfit", "outfits", "piece", "pieces", "book", "books", "drink",
    "drinks", "cocktail", "cocktails", "mocktail", "mocktails", "pairing", "pairings",
    "alcohol", "alcohol-free", "alcoholic", "non-alcoholic", "contains", "optional", "free",
    "non", "left", "over", "under", "cheaper", "cheapest", "swap", "swapped", "replaced",
    "instead", "version", "selection", "catalog", "collection", "set", "series", "novel",
    "novels", "author", "authors", "genre", "genres", "because", "so", "but", "not", "only",
    "just", "all", "both", "each", "every", "any", "some", "many", "few",
    }
)  # fmt: skip
_LABEL = re.compile(r"^([\w'\u2019&/ -]{1,30}):\s+(?=\S)")
_WORDISH = re.compile(r"[\w'\u2019&.-]+")
_SPLIT_BY = re.compile(r"\s+by\s+", re.IGNORECASE)
_TRIM = " \t.,;:!?()[]{}\u2014\u2013-'\u2019\"\u201c\u201d"


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    name: str


def marked_spans(text: str) -> list[Span]:
    """Bold, italic and quoted spans of `text`, without a leading "Label: " and trailing
    punctuation. Spans that are labels themselves ("**Ingredients:**") are left out."""
    spans = []
    for match in _MARKED.finditer(text):
        group = next(g for g in ("b", "u", "i", "j", "q", "c") if match.group(g) is not None)
        inner = match.group(group)
        start = match.start(group)
        tail = text[match.end() : match.end() + 2]
        if inner.rstrip().endswith(":") or tail.lstrip().startswith(":"):
            continue
        label = _LABEL.match(inner)
        if label:
            start += label.end()
            inner = inner[label.end() :]
        stripped = inner.strip(_TRIM)
        if stripped:
            offset = inner.find(stripped)
            spans.append(Span(start + offset, start + offset + len(stripped), stripped))
    return spans


def _significant(words: Sequence[str]) -> list[str]:
    return [w for w in words if w.casefold() not in HEADING_WORDS]


def name_like(name: str, vocabulary: Callable[[str], bool]) -> bool:
    """Whether a marked span reads as a name (a title, a drink, a product): it starts with
    a capital, is at most ten words, has no amounts, and, besides heading words and the
    domain's `vocabulary` (genres, colours, ingredients), most of its words are capitalised.
    "**The Way of Kings**" and "**Paper Plane**" are names; "**Perfect for summer**",
    "**Fantasy**" and "**Why it works**" are not."""
    if not name[:1].isupper() or any(c in name for c in "€$£¥%"):
        return False
    words = _WORDISH.findall(name)
    if not words or len(words) > 10 or name.endswith(("!", "?")):
        return False
    if vocabulary(name):
        return False
    if (
        1 < len(words) <= 3
        and words[-1].casefold() in HEADING_WORDS
        and not any(w[:1].isupper() for w in words[1:])
    ):
        return False  # a category heading: "**Picture books**", "**Classic picks**"
    rest = [w for w in _significant(words) if not vocabulary(w)]
    if not rest:
        return False
    capitalised = [w for w in rest if w[:1].isupper() or w[:1].isdigit()]
    return len(capitalised) * 2 > len(rest) or (len(words) == 1 and bool(capitalised))


def _folded(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\s+", " ", re.sub(r"[*_`\"\u201c\u201d]", "", text)).casefold().strip(_TRIM)


def names_known(name: str, known: Sequence[str]) -> bool:
    """Whether `name` is, contains or is part of one of the `known` names (as whole words)."""
    folded = _folded(name)
    if not folded:
        return True
    for other in known:
        other = _folded(other)
        if not other:
            continue
        if folded == other:
            return True
        if _contains(other, folded) or (len(other) >= 4 and _contains(folded, other)):
            return True
    return False


def _contains(haystack: str, needle: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack) is not None


def check_marked_names(
    text: str,
    known: Sequence[str],
    tally: Tally,
    *,
    kind: str,
    vocabulary: Callable[[str], bool] = lambda _: False,
    also_name_like: Callable[[str], bool] = lambda _: False,
    skip: Callable[[str], bool] = lambda _: False,
) -> None:
    """Each bold, italic or quoted span that reads as a name must name something in `known`
    (this turn's items, their people, names the tools or the shopper wrote). `also_name_like`
    lets a domain count spans `name_like` misses (sentence-case product names); `skip` lets
    it leave out names another check already counted."""
    for span in marked_spans(text):
        parts = [span.name]
        by = _SPLIT_BY.split(span.name, maxsplit=1)
        if len(by) == 2 and by[0].strip():
            parts = [by[0].strip(_TRIM)]  # "**The Hobbit by J.R.R. Tolkien**": the title
        name = parts[0]
        if not name or skip(name):
            continue
        if not (name_like(name, vocabulary) or also_name_like(name)):
            continue
        ok = names_known(name, known)
        tally.add(kind, ok, f"{name} was not returned by the tools", key=_folded(name))


# "by Patrick Rothfuss", "by J.R.R. Tolkien", "by Ursula K. Le Guin": capitalised words.
_PERSON_WORD = r"[A-Z][\w.'\u2019-]*"
_BYLINE = re.compile(
    rf"\bby\s+\**({_PERSON_WORD}(?:\s+(?:{_PERSON_WORD}|de|van|von|le|la|du|der|da|di)){{0,4}})"
)
# What may stand between an item reference and its "by": closing marks, an ID, a year.
# A verb of authorship may come between too: "**Scarlet** was written by", "is by".
_BYLINE_GAP = re.compile(
    r"^[\s*_\"\u201d\u2019,]*(?:[(\[][^()\[\]\n]{0,30}[)\]][\s*_,]*)*"
    r"(?:(?:which |that )?(?:is |was |were )?(?:(?:co-?)?(?:written|authored|penned)\s+)?)?$"
)
# A sentence about the item named before: "It was written by", "The book is co-written by".
_BYLINE_PRONOUN = re.compile(
    r"(?:^|[.!?]\s+|\n[\s*+-]*)(?:it|the book|the novel|this one|that one|this book)\s+"
    r"(?:is|was|['\u2019]s)\s+(?:also\s+)?(?:co-?)?(?:written|authored|penned)\s+$",
    re.IGNORECASE,
)
# More authors after the first: "by A and B", "by A together with B", "co-written with B".
_CO_AUTHOR = re.compile(
    rf"\s*,?\s*(?:together with|along with|alongside|with|and|&)\s+"
    rf"({_PERSON_WORD}(?:\s+(?:{_PERSON_WORD}|de|van|von|le|la|du|der|da|di)){{0,4}})"
)
_CO_WRITTEN = re.compile(
    rf"\bco-?(?:written|authored|wrote)\s+(?:by|with)\s+\**"
    rf"({_PERSON_WORD}(?:\s+(?:{_PERSON_WORD}|de|van|von|le|la|du|der|da|di)){{0,4}})"
    rf"|\bco-?authors?\s+\**({_PERSON_WORD}(?:\s+{_PERSON_WORD}){{0,4}})"
)
# "<Title> by <Author>" without marks: two or more capitalised words (with small words
# between) right before "by".
_SMALL = r"(?:of|the|and|a|an|in|on|to|for|at|from|with|or|is|&)"
_UNMARKED_TITLE = re.compile(
    rf"((?:[A-Z][\w'\u2019:-]*|\d+)(?:\s+(?:{_SMALL}|[A-Z][\w'\u2019:-]*|\d+))*\s+(?:[A-Z][\w'\u2019:-]*|\d+))"
    rf"\s+by\s+{_PERSON_WORD}"
)


def person_matches(named: str, people: Sequence[str]) -> bool:
    """Whether "by <named>" names one of `people`: the full name, or its last name(s)."""
    key = _person_key(named)
    if not key:
        return True
    for person in people:
        full = _person_key(person)
        if key == full or full.endswith(f" {key}") or key.startswith(f"{full} "):
            return True
    return False


def _person_key(name: str) -> str:
    name = name.replace("\u2019", "'").casefold()
    return " ".join(re.sub(r"[^\w' ]+", " ", name).split())


def _co_authors(text: str, end: int, titles: Sequence[str]) -> list[str]:
    """The authors named after a byline's first one ("by A and B", "by A together with
    B"), not a title that follows ("by A and **Emma** by ...")."""
    found = []
    while (match := _CO_AUTHOR.match(text, end)) is not None:
        person = match.group(1).rstrip(".,;:")
        if _BYLINE_TAIL.match(text, match.end()) or names_known(person, titles):
            break
        found.append(person)
        end = match.end()
    return found


# What follows a title rather than a person: "Emma by", "Emma (1815)", "Emma [id 3]".
_BYLINE_TAIL = re.compile(r"\s*[*_]*\s*(?:by\b|\(|\[id\b|#)", re.IGNORECASE)


def check_bylines(
    text: str,
    binder: Binder,
    tally: Tally,
    *,
    known: Sequence[str],
    typed: str = "",
) -> None:
    """ "<item> by <name>" must name one of that item's people; an unmarked "<Title> by
    <Author>" that names no returned item is reported like an unknown name."""
    titles = [name for item in binder.items for name in item.names]
    for match in _BYLINE.finditer(text):
        named = match.group(1).rstrip(".,;:")
        refs = [r for r in binder.refs if r.end <= match.start()]
        ref = refs[-1] if refs else None
        if ref is None:
            continue
        if not _BYLINE_GAP.match(text[ref.end : match.start()]):
            strong = [r for r in binder.strong if r.end <= match.start()]
            ref = strong[-1] if strong else None
            if ref is None or not _BYLINE_PRONOUN.search(text[ref.end : match.start()]):
                continue
        people = [p for item in ref.items for p in item.people]
        if not people:
            continue
        title = ref.items[0].label
        for person in [named, *_co_authors(text, match.end(), titles)]:
            ok = person_matches(person, people)
            key = f"{title}|{person}".casefold()
            tally.add("author", ok, f"{title} is not by {person}", key=key)
    for match in _CO_WRITTEN.finditer(text):
        person = (match.group(1) or match.group(2)).rstrip(".,;:")
        refs = [r for r in binder.strong if r.end <= match.start()]
        people = [p for item in refs[-1].items for p in item.people] if refs else []
        if not people:
            continue
        title = refs[-1].items[0].label
        ok = person_matches(person, people)
        tally.add("author", ok, f"{title} is not by {person}", key=f"{title}|{person}".casefold())
    for match in _UNMARKED_TITLE.finditer(text):
        title = match.group(1).strip(_TRIM)
        # Leading sentence words ("Try", "Read") are not part of a title.
        words = title.split()
        while words and words[0].casefold() in HEADING_WORDS:
            words = words[1:]
        title = " ".join(words)
        if len(words) < 2 or names_known(title, [*known, typed]):
            continue
        tally.add("title", False, f"{title} was not returned by the tools", key=_folded(title))


# --- Labels and claims no data supports ---------------------------------------------------

_CLAUSE_START = re.compile(r"[.!?;:\n(\u2014]|,\s|\bbut\b|\bthough\b|\balthough\b")
_HEDGE = re.compile(
    r"\bnot\b|n['\u2019]t\b|\bnever\b|\bno\b|\bnothing\b|\bnone\b|\bcannot\b|\bunable\b|"
    r"\bwithout\b|\bwhether\b|\bif\b|\bcheck\b|\bverify\b|\bconfirm\b|\bguarantee\b|"
    r"\bunsure\b|\bunknown\b|\bask\b|\bmay\b|\bmight\b"
)


_BOLD_NAME = re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__")


def hedged(text: str, start: int) -> bool:
    """Whether the clause before position `start` negates or hedges what follows ("I can't
    guarantee it's nut-free", "check whether it's gluten-free"). Bold names do not count:
    "**And Then There Were None** is fine for children" hedges nothing."""
    before = text[max(0, start - 120) : start]
    cuts = list(_CLAUSE_START.finditer(before))
    clause = before[cuts[-1].end() :] if cuts else before
    return bool(_HEDGE.search(_BOLD_NAME.sub(" ", clause).lower()))


# A hedge later in the same sentence: "it's nut-free, but check the label".
_HEDGE_AFTER = re.compile(
    r"\bcheck(?:ing)? (?:the |its |their |with |whether |if )|\bdouble[- ]check|\bverify|"
    r"\bi can(?:no|['\u2019])?t (?:confirm|guarantee|promise|be sure|say)|\bcan(?:no|['\u2019])?t "
    r"(?:confirm|guarantee|promise)|\bnot (?:sure|certain)|\bunsure|\bconsult|\bask (?:your|a)\b"
)
_SENTENCE_STOP = re.compile(r"[.!?](?:\s|$)|\n")


def hedged_after(text: str, end: int) -> bool:
    """Whether the rest of the sentence after position `end` hedges what came before."""
    rest = text[end : end + 160]
    stop = _SENTENCE_STOP.search(rest)
    return bool(_HEDGE_AFTER.search((rest[: stop.start()] if stop else rest).lower()))


# The data named as the source of a claim: "going by the ingredient list", "according to
# the recipe", "based on the listed ingredients", "on paper".
_DATA_SOURCE = re.compile(
    r"\b(?:going (?:by|from|off)|according to|based on|judging (?:by|from)|from|per|by|"
    r"looking at|as (?:listed|written) in|on|in)\s+(?:the|its|their|this|these|those|each)?\s*"
    r"(?:listed\s+|drinks?['\u2019]?s?\s+|catalog(?:ue)?['\u2019]?s?\s+)?"
    r"(?:ingredient(?:s)?(?: lists?)?|recipes?|catalog(?:ue)?(?: data| entry| listings?)?|"
    r"listings?|data)\b"
    r"|\bon paper\b|\bas listed\b|\bby the (?:book|list)\b",
    re.IGNORECASE,
)


def attributed_to_data(text: str, position: int) -> bool:
    """Whether the sentence around `position` names the data as the claim's source."""
    start = max(text.rfind(". ", 0, position), text.rfind("\n", 0, position)) + 1
    stop = _SENTENCE_STOP.search(text, position)
    return bool(_DATA_SOURCE.search(text[start : stop.start() if stop else len(text)]))


def check_unsupported(
    text: str,
    pattern: re.Pattern[str],
    tally: Tally,
    *,
    reason: str,
    kind: str = "claim",
    hedge_after: bool = False,
    supported: Sequence[str] = (),
    sourced: Callable[[re.Match[str]], bool] | None = None,
) -> None:
    """Claims no catalog data can support (`pattern`), unless the answer negates or hedges
    them (with `hedge_after`, also later in the sentence: "but check the label"), or the
    evidence's own text says them (`supported`: a description that reads "nickel-free"):
    each one is an issue. With `sourced`, a claim its sentence attributes to the data
    ("dairy-free going by the ingredient list") is allowed when `sourced` finds the data
    bears it out (no dairy in the recipe)."""
    said = [_folded(s) for s in supported if s.strip()]
    for match in pattern.finditer(text):
        if hedged(text, match.start()):
            continue
        if hedge_after and hedged_after(text, match.end()):
            continue
        if sourced is not None and attributed_to_data(text, match.start()) and sourced(match):
            continue
        phrase = _folded(match.group(0))
        if phrase and any(phrase in s for s in said):
            continue
        tally.add(kind, False, f"\u201c{match.group(0)}\u201d: {reason}")


# What a health claim promises to help with: "a sore throat", "colds", "your immune system".
_AILMENT = (
    r"(?:(?:a|an|the|your|any|those|that)\s+)?(?:(?:sore|scratchy|tickly)\s+throats?|colds?|"
    r"coughs?|(?:the )?flu|fevers?|headaches?|hangovers?|nausea|(?:upset |sore )?stomachs?|"
    r"stomach ?aches?|indigestion|digestion|insomnia|inflammation|immunity|immune systems?|"
    r"congestion|cramps?|(?:the )?sniffles|chills|a cold or (?:the )?flu)\b"
)
# Allergen, skin-health and age-suitability promises: no catalog in any domain records them.
SAFETY_CLAIMS = re.compile(
    r"\b(?:nut|peanut|tree[- ]nut|gluten|wheat|dairy|lactose|milk|egg|soy|allergen|allergy|"
    r"latex|nickel|fragrance|scent)[- ]free\b"
    r"|\b(?:allergy|allergen|allergic)[- ](?:safe|friendly)\b"
    r"|\b(?:free (?:of|from)|contains no|has no|without any) (?:nuts?|peanuts?|gluten|dairy|"
    r"lactose|allergens?|eggs?|latex|nickel)\b"
    r"|\b(?:hypo-?allergenic|non-?allergenic|non-?irritating|non-?toxic|"
    r"dermatologi(?:st|cally)[- ](?:tested|approved|recommended))\b"
    r"|\b(?:gentle|kind|safe|suitable|fine|good|ideal|great) (?:on|for) (?:\w+ )?sensitive skin\b"
    r"|\bwon['\u2019]t (?:irritate|cause (?:a )?rash)\b"
    r"|\b(?:completely |totally |perfectly |100% |guaranteed |entirely )?safe (?:for|to|with|"
    r"during|while|when) (?:anyone|everyone|everybody|kids|children|minors|teens?|teenagers|"
    r"toddlers|babies|infants|pregnan\w*|you|your|people|those|someone|a nut|nut|allerg\w*|"
    r"drink(?:ing)? while)\b"
    r"|\b(?:suitable|fine|ok|okay|appropriate|age[- ]appropriate|safe) for (?:(?:a|an|your|my|"
    r"young|younger|little|small|older|most|all) )*(?:kids?|children|child|minors|toddlers|"
    r"babies|infants|tweens|teens?|teenagers|\d{1,2}[- ]year[- ]olds?|ages? \d{1,2}|"
    r"(?:readers|kids|children) (?:aged|of) \d{1,2}|(?<=young )readers|(?<=younger )readers)\b"
    r"|\b(?:for )?(?:kids|children|readers|ages?) (?:aged )?\d{1,2}\s?(?:\+|and up\b|and over\b|"
    r"and older\b)"
    r"|\b(?:kid|child|pregnancy|allergy|toddler|baby)[- ](?:safe|friendly|proof)\b"
    r"|\bguaranteed(?: to be)? safe\b"
    # Health benefits: "a good remedy for a sore throat", "good for a cold", "boosts
    # immunity".
    r"|\b(?:(?:remed(?:y|ies)|cures?|medicine|tonic|treatment)\s+(?:for|against)|"
    r"cures?|heals?|treats?|soothes?|settles?|calms?|eases?|relieves?|fights?|"
    r"(?:fights?|wards?) off|prevents?|helps? (?:with|fight|prevent|ease|soothe|relieve)|"
    r"(?:good|great|perfect|ideal|helpful|best) (?:for|against)|works? (?:wonders )?(?:for|on))"
    rf"\s+{_AILMENT}"
    r"|\bmedicinal\b|\bboost(?:s|ing)? (?:your |the )?(?:immun\w*|metabolism)\b"
    r"|\bimmun(?:e|ity)[- ](?:boost\w*|support\w*)\b",
    re.IGNORECASE,
)
SAFETY_REASON = "no catalog records allergen, diet, health or age-suitability facts"


def check_safety(
    text: str,
    tally: Tally,
    *,
    pattern: re.Pattern[str] = SAFETY_CLAIMS,
    reason: str = SAFETY_REASON,
    supported: Sequence[str] = (),
    sourced: Callable[[re.Match[str]], bool] | None = None,
) -> None:
    """Allergen, health and age-safety promises, unless negated or hedged before or after
    ("I can't confirm it's nut-free", "it's nut-free, but check the label"), stated by
    the evidence's own text (`supported`), or attributed to the data and borne out by it
    (`sourced`: "dairy-free going by the ingredient list" for recipes without dairy)."""
    check_unsupported(
        text,
        pattern,
        tally,
        reason=reason,
        hedge_after=True,
        supported=supported,
        sourced=sourced,
    )


# Stock, sizes, delivery and discounts: nothing in any catalog supports them.
SHOP_CLAIMS = re.compile(
    r"\b(?:in|out of|back in) stock\b|\bsold out\b|\bavailable in (?:all|every|your) sizes?\b"
    r"|\bin (?:all|every) sizes?\b|\bships?(?: today| tomorrow| free| fast| within)\b"
    r"|\b(?:free|fast|next[- ]day|express) (?:shipping|delivery|returns)\b"
    r"|\bdelivered (?:today|tomorrow|by)\b|\bon sale\b|\b\d{1,2}\s?% off\b"
    r"|(?:\u20ac\s?)?\b\d+(?:[.,]\d+)? off (?:this|today|now)\b|\b(?:discounted|clearance)\b",
    re.IGNORECASE,
)
SHOP_REASON = "stock, sizes, delivery and discounts are not in the catalog"


@dataclass(frozen=True)
class FlagClaim:
    """A phrase that states an item's label: `values` are the labels it allows (e.g. the
    phrase "alcohol-free" allows {"free"}); `negated_values` those it allows after "not"."""

    pattern: re.Pattern[str]
    values: frozenset[str]
    negated_values: frozenset[str]
    describe: str


_FLAG_NEGATION = re.compile(r"(?:\bnot|n['\u2019]t|\bnever|\bno longer)\s+(?:\w+\s+){0,2}$")
_ABOUT_ANOTHER = re.compile(
    r"^[\s*_]*(?:version|alternative|option|swap|twist|substitute|take|variant|counterpart|"
    r"twin|cousin|sibling|instead|choice|pick)s?\b",
    re.IGNORECASE,
)
_PRONOUN_SUBJECT = re.compile(r"(?:it|this|that|both|they|it's|it\u2019s|its)\b", re.IGNORECASE)
# A plural subject that opens a sentence: "Both are alcohol-free", "They're all
# non-alcoholic".
_PLURAL_SUBJECT = re.compile(
    r"(both|they|these|those|all (?:three|four|of them|of these)|each of them)\b"
    r"(?:\s+(?:of them|drinks|recipes))?\s*(?:are|['\u2019]re|were|contain|have)\b"
)
# The phrase is the predicate of the subject before it: "the Shirley Temple is the
# alcohol-free version", "both are non-alcoholic versions"; not "there is", "try a".
_PREDICATE = re.compile(
    r"(?<!there )(?<!here )\b(?:is|are|['\u2019]s|['\u2019]re|was|were)\s+"
    r"(?:(?:a|an|the|both|all|just|really|actually|also|simply)\s+)*$"
)
_PLURAL = re.compile(r"\b(?:all|both|these|those|every|each|everything|here are|picks|options)\b")


def check_flags(
    text: str,
    binder: Binder,
    tally: Tally,
    *,
    flag: str,
    claims: Sequence[FlagClaim],
    kind: str,
    quoted: Sequence[str] = (),
) -> None:
    """Phrases that label an item (`claims`) must match its `flag`. A phrase binds to the
    nearest item in its sentence, else to the last one named before it. A plural phrase
    with no item in its sentence ("all of these are alcohol-free:") covers the items its
    sentence or the list after it names. A sentence that repeats tool text word for word
    (`quoted`: "An alcohol-free drink: Figgy Thyme → Masala Chai") is the tool's own."""
    quoted_text = [_folded(q) for q in quoted if q.strip()]
    taken: list[tuple[int, int]] = []
    for claim in claims:
        for match in claim.pattern.finditer(text):
            start, end = match.span()
            if any(s < end and start < e for s, e in taken):
                continue
            taken.append((start, end))
            sentence = binder.sentence(start)
            said = _folded(text[sentence[0] : sentence[1]])
            if said and any(said in q for q in quoted_text):
                continue
            before = text[max(sentence[0], start - 40) : start].lower()
            negated = bool(_FLAG_NEGATION.search(_STRIP_MARKS.sub("", before)))
            allowed = claim.negated_values if negated else claim.values
            phrase = ("not " if negated else "") + claim.describe
            in_sentence = binder.items_between(*sentence)
            said_lower = text[sentence[0] : sentence[1]].lower()
            plural_subject = _PLURAL_SUBJECT.match(_STRIP_MARKS.sub("", said_lower).lstrip("-*+ "))
            opens_list = text[: sentence[1]].rstrip().endswith(":")
            if not in_sentence and plural_subject and not opens_list:
                # "Both are alcohol-free": the drinks named just before.
                items = _named_before(binder, sentence[0], plural_subject.group(1))
            elif not in_sentence and _PLURAL.search(said_lower):
                items = binder.items_between(sentence[1], _list_end(text, sentence[1]))
                items = items if opens_list else []
            elif _ABOUT_ANOTHER.match(text[end:]) and not _PREDICATE.search(
                _STRIP_MARKS.sub("", text[sentence[0] : start]).lower()
            ):
                items = []  # "try an alcohol-free version": about another drink
            else:
                binding = binder.bind(start, end, nearest=True)
                pronoun = _PRONOUN_SUBJECT.match(text[sentence[0] : sentence[1]].lstrip("-*+ "))
                items = list(binding.items) if binding and (binding.explicit or pronoun) else []
            for item in items:
                value = item.flags.get(flag)
                if value is None:
                    continue
                ok = value in allowed
                tally.add(kind, ok, f"{item.label} is not {phrase}", key=f"{item.id}|{start}")


def _named_before(binder: Binder, position: int, subject: str) -> list[Item]:
    """The items a plural subject at `position` stands for: the last two named before it
    for "both", else those named in its paragraph before it."""
    paragraph = binder.text.rfind("\n\n", 0, position) + 1
    refs = [r for r in binder.strong if r.end <= position]
    if subject.startswith("both"):
        items: list[Item] = []
        for ref in reversed(refs):
            items += [i for i in ref.items if i not in items]
            if len(items) >= 2:
                break
        return items[:2]
    return binder.items_between(paragraph, position)


def _list_end(text: str, start: int) -> int:
    """The end of the list or paragraph that starts at `start`."""
    blank = re.search(r"\n\s*\n(?![ \t]*(?:[-*+]|\d+[.)])\s)", text[start:])
    return start + blank.start() if blank else len(text)


# --- Currency -----------------------------------------------------------------------------

# "$24.99", "£ 30", "24 USD", "30 pounds": prices in another currency than the catalog's.
OTHER_CURRENCY = re.compile(
    r"[$£¥]\s?\d+(?:[.,]\d+)*|\b\d+(?:[.,]\d+)*\s?(?:USD|GBP|dollars?|pounds?(?:\s+sterling)?)\b",
    re.IGNORECASE,
)


def check_currency(text: str, tally: Tally) -> None:
    """Amounts in dollars, pounds or yen: never a catalog price, and answers quote euros, so
    each one is reported, even the shopper's own figure ("your $150 budget"): the answer
    says how it was read in euros instead."""
    for match in OTHER_CURRENCY.finditer(text):
        raw = match.group(0)
        tally.add("price", False, f"{raw} is not a price in euros, the catalog's currency")
