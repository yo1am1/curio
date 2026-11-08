"""Cite mode: catalog facts the model refers to and the server renders.

The model does not type a catalog value. It writes a fact reference, and the engine replaces
it with the value from the catalog row a tool returned this turn, so the value is the cited
item's catalog value; cite mode does not check that the cited item is the one the sentence
names. The grammar is one brace-delimited form:

    {KIND:ID}                       an item's name, in bold: {product:843614008}, {book:3}
    {ATTRIBUTE:ID}                  one of its values: {price:843614008}, {rating:3}
    {ATTRIBUTE:KIND:ID}             the same, naming the item's kind: {rating:book:3}
    {ATTRIBUTE:KIND:ID:ARGUMENT}    a value that takes an argument: {amount:drink:12:Gin}
    {total:ATTRIBUTE:ID,ID,...}     the sum of two or more items' values: {total:price:1,2}
    {diff:ATTRIBUTE:ID,ID}          the difference of two (larger minus smaller)

Kinds, attributes and IDs come from the domain's `FactSheet`; an attribute's kind is
implied, so naming it is optional. Names are case-insensitive and spaces around ":" and ","
are ignored. Anything else between braces whose name is a reference name, or that has a
colon after a word, is a reference too, so a mistyped or unknown one ("{weight:12}") is
caught instead of shown.

Resolution uses only the items in the sheet, which a toolkit builds from the turn's
evidence (what its tools returned this turn, plus the cards the client carried that its
grounding check already accepts). An unknown kind, attribute or item, an item no tool
returned, a value the item lacks or an argument it does not know is a verification
failure: the engine asks for one rewrite, then falls back, as for any unverified claim.
So is a malformed reference: an ID that is not plain ASCII digits (at most 19), the same
item twice in a total or difference, a value reference with a typed digit next to it
("4{rating:3}", "{pages:3},000"), or a reference nested inside braces.

`render` returns the client's text, where no reference is left (a failed one is dropped),
and a copy for the free-text check (the backstop for values the model still types): there
the rendered values are masked, so the check never reads them, and item names stay, so a
typed value next to one still binds to it. `checked_report` runs the check on that copy
and leaves out what the rendered names alone account for, so rendered spans are never
counted or flagged by the backstop.
"""

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from curio.engine.grounding import describe_counts
from curio.engine.schemas import GroundingReport

# A reference: "{" name, optionally ":" and a body, "}". The body has no braces or line breaks.
REFERENCE = re.compile(r"\{\s*([A-Za-z][A-Za-z_-]*)\s*(?::([^{}\n]*))?\}")
# What is left of a malformed reference after rendering: "{drink:" with no closing brace.
_LEFTOVER = re.compile(r"\{\s*[A-Za-z][A-Za-z_-]*\s*:")
# A reference opened and not closed before the text that follows: "{drink:" in "{drink:{drink:1}}".
_OPEN = re.compile(r"\{\s*[A-Za-z][A-Za-z_-]*\s*:[^{}\n]*$")
# What a rendered value becomes in the backstop's copy: a character no check reads.
MASK = ""
# Computed references and the attribute kinds of their counts.
TOTAL, DIFFERENCE = "total", "diff"
_COMPUTED = {TOTAL: TOTAL, DIFFERENCE: DIFFERENCE, "difference": DIFFERENCE}
# Markdown that already sets a reference apart: "**{product:1}**" is not bolded twice.
_MARKERS = ("**", "__", "*", "_")


@dataclass(frozen=True)
class Attribute:
    """A value an item of kind `item` carries. `read(item, argument)` returns it, or None
    when the item has none (no year, an ingredient not in the recipe). `show` formats it
    and `show_derived` a total or difference of it (default `show`). `total` and
    `difference` say whether those may be computed; `argument` whether the reference takes
    one after the ID. `kind` names it in the report's counts ("price")."""

    kind: str
    item: str
    read: Callable[[Any, str | None], Any]
    show: Callable[[Any], str]
    total: bool = False
    difference: bool = False
    argument: bool = False
    show_derived: Callable[[Any], str] | None = None

    def derived(self, value: Any) -> str:
        return (self.show_derived or self.show)(value)


@dataclass(frozen=True)
class ItemKind:
    """Items of one kind an answer may cite this turn, by ID, and how each is named."""

    name: Callable[[Any], str]
    items: Mapping[int, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FactSheet:
    """What one turn's answer may refer to: item kinds ("product") and attributes ("price"),
    keyed by the name a reference uses. Toolkits build it from the turn's evidence."""

    kinds: Mapping[str, ItemKind] = field(default_factory=dict)
    attributes: Mapping[str, Attribute] = field(default_factory=dict)

    def merged(self, *others: "FactSheet") -> "FactSheet":
        """This sheet and `others` together (an evening cites products, books and drinks)."""
        kinds, attributes = dict(self.kinds), dict(self.attributes)
        for other in others:
            for name, kind in other.kinds.items():
                if name in kinds:
                    items = {**kinds[name].items, **kind.items}
                    kind = ItemKind(kinds[name].name, items)
                kinds[name] = kind
            attributes.update(other.attributes)
        return FactSheet(kinds, attributes)


class FactReferenceError(Exception):
    """A reference that cannot be rendered; the message is the issue the report lists."""


@dataclass
class Rendered:
    """An answer with its references rendered.

    `text` is what the client sees. `check_text` is the backstop's copy: rendered values
    masked, item names kept. `names` are the rendered item names, which the backstop must
    not count. `counts` are the rendered facts by kind and `errors` the references that
    could not be rendered.
    """

    text: str
    check_text: str
    names: list[str] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)


def is_reference(match: re.Match[str], sheet: FactSheet) -> bool:
    """Whether a braced span is meant as a fact reference: it has a body after a colon, or
    its name is one this sheet (or the computed forms) knows."""
    name = match.group(1).casefold()
    return match.group(2) is not None or name in _known_names(sheet)


def _known_names(sheet: FactSheet) -> set[str]:
    return {*sheet.kinds, *sheet.attributes, *_COMPUTED}


def references(text: str, sheet: FactSheet | None = None) -> list[re.Match[str]]:
    """Every fact reference in `text`."""
    sheet = sheet or FactSheet()
    return [m for m in REFERENCE.finditer(text) if is_reference(m, sheet)]


def has_references(text: str) -> bool:
    return any(m.group(2) is not None for m in REFERENCE.finditer(text))


def render(text: str, sheet: FactSheet) -> Rendered:
    """Replace every fact reference in `text` with its catalog value (see the module
    docstring); a reference that fails is dropped from the text and listed in `errors`."""
    out: list[str] = []
    check: list[str] = []
    result = Rendered("", "")
    position = 0
    for match in references(text, sheet):
        out.append(text[position : match.start()])
        check.append(text[position : match.start()])
        position = match.end()
        try:
            shown, kind, is_name = _resolve_safely(match, sheet)
            if not is_name:
                _not_glued(text, match)
            _not_wrapped(text, match)
        except FactReferenceError as error:
            if str(error) not in result.errors:
                result.errors.append(str(error))
            check.append(MASK)
            continue
        if is_name and not _marked(text, match.start(), match.end()):
            shown = f"**{shown}**"
        out.append(shown)
        result.counts[kind] += 1
        if is_name:
            # The name stays in the backstop's copy, so a value typed next to it binds to it.
            check.append(shown)
            result.names.append(shown)
        else:
            check.append(MASK)
    out.append(text[position:])
    check.append(text[position:])
    for leftover in _LEFTOVER.finditer("".join(out)):
        start = leftover.group(0).strip()
        error = f"a malformed fact reference starting “{start}” was left in the text"
        if error not in result.errors:
            result.errors.append(error)
    result.text = "".join(out)
    result.check_text = "".join(check)
    return result


def _resolve_safely(match: re.Match[str], sheet: FactSheet) -> tuple[str, str, bool]:
    """`_resolve`, with any failure (a domain's reader or formatter raising, say) reported
    as a reference error, so a bad reference never crashes the turn."""
    try:
        return _resolve(match, sheet)
    except FactReferenceError:
        raise
    except Exception as error:
        label = match.group(0)[:60]
        raise FactReferenceError(f"fact reference “{label}” could not be rendered") from error


def _not_glued(text: str, match: re.Match[str]) -> None:
    """A value reference must stand apart from typed digits: "4{rating:7}" or "{pages:7},000"
    would join the catalog value to a number the model typed, which no check reads. A digit
    directly before or after it fails, and so does "." or "," with a digit beyond it; a
    sentence's own full stop or comma ("costs {price:1}.") is fine."""
    before, after = text[: match.start()], text[match.end() :]
    glued_before = before[-1:].isdigit() or (before[-1:] in ".," and before[-2:-1].isdigit())
    glued_after = after[:1].isdigit() or (after[:1] in ".," and after[1:2].isdigit())
    if glued_before or glued_after:
        raise FactReferenceError(
            f"fact reference “{match.group(0)[1:-1].strip()}” touches a typed number; "
            "leave a space around it and type no digits next to it"
        )


def _not_wrapped(text: str, match: re.Match[str]) -> None:
    """A reference inside another brace ("{drink:{drink:1}}", "{{drink:1}}") is malformed:
    rendering it would leave the outer braces in the client's text."""
    before, after = text[: match.start()], text[match.end() :]
    opened = _OPEN.match(before[before.rfind("{") :]) if "{" in before else None
    if before.rstrip().endswith("{") or after.lstrip().startswith("}") or opened:
        raise FactReferenceError(
            f"fact reference “{match.group(0)[1:-1].strip()}” is nested inside braces"
        )


def _marked(text: str, start: int, end: int) -> bool:
    before, after = text[:start], text[end:]
    return any(before.endswith(m) and after.startswith(m) for m in _MARKERS)


def _resolve(match: re.Match[str], sheet: FactSheet) -> tuple[str, str, bool]:
    """(the rendered text, its kind for the counts, whether it is an item's name)."""
    name = match.group(1).casefold()
    body = (match.group(2) or "").strip()
    parts = [part.strip() for part in body.split(":")] if body else []
    shown = f"{name}{':' + ':'.join(parts) if parts else ''}"
    label = f"fact reference “{shown if len(shown) <= 60 else shown[:57] + '...'}”"
    if name in sheet.kinds:
        if len(parts) != 1:
            raise FactReferenceError(f"{label} needs exactly one {name} ID")
        item = _item(sheet, name, parts[0], label)
        return sheet.kinds[name].name(item), "name", True
    if name in _COMPUTED:
        return _computed(_COMPUTED[name], parts, sheet, label), _COMPUTED[name], False
    attribute = sheet.attributes.get(name)
    if attribute is None:
        known = ", ".join(sorted({*sheet.kinds, *sheet.attributes, TOTAL, DIFFERENCE}))
        raise FactReferenceError(f"{label} is not a known reference (known: {known})")
    if parts and parts[0].casefold() in sheet.kinds:
        if parts[0].casefold() != attribute.item:
            raise FactReferenceError(f"{label}: {name} belongs to a {attribute.item}")
        parts = parts[1:]
    if not parts:
        raise FactReferenceError(f"{label} needs a {attribute.item} ID")
    argument = ":".join(parts[1:]).strip() or None
    if attribute.argument and argument is None:
        raise FactReferenceError(f"{label} needs a value after the ID")
    if not attribute.argument and argument is not None:
        raise FactReferenceError(f"{label} takes only a {attribute.item} ID")
    item = _item(sheet, attribute.item, parts[0], label)
    value = attribute.read(item, argument)
    if value is None:
        named = sheet.kinds[attribute.item].name(item)
        if argument:
            raise FactReferenceError(f"{label}: {named} has no {name} for “{argument}”")
        raise FactReferenceError(f"{label}: the catalog has no {name} for {named}")
    return attribute.show(value), attribute.kind, False


def _computed(operation: str, parts: Sequence[str], sheet: FactSheet, label: str) -> str:
    if len(parts) < 2:
        raise FactReferenceError(f"{label} needs an attribute and item IDs")
    attribute = sheet.attributes.get(parts[0].casefold())
    allowed = attribute is not None and (
        attribute.total if operation == TOTAL else attribute.difference
    )
    if attribute is None or not allowed:
        word = "total" if operation == TOTAL else "difference"
        computable = sorted(
            name
            for name, a in sheet.attributes.items()
            if (a.total if operation == TOTAL else a.difference)
        )
        raise FactReferenceError(
            f"{label}: a {word} can be computed of {', '.join(computable) or 'nothing here'}"
        )
    rest = list(parts[1:])
    if rest and rest[0].casefold() in sheet.kinds:
        if rest[0].casefold() != attribute.item:
            raise FactReferenceError(f"{label}: {parts[0]} belongs to a {attribute.item}")
        rest = rest[1:]
    if len(rest) != 1:
        raise FactReferenceError(f"{label} needs a comma-separated list of {attribute.item} IDs")
    ids = [raw.strip() for raw in rest[0].split(",") if raw.strip()]
    if operation == TOTAL and len(ids) < 2:
        raise FactReferenceError(f"{label}: a total needs two or more {attribute.item} IDs")
    if operation == DIFFERENCE and len(ids) != 2:
        raise FactReferenceError(f"{label}: a difference needs exactly two {attribute.item} IDs")
    keys = [_item_id(raw, attribute.item, label) for raw in ids]
    if len(set(keys)) != len(keys):
        raise FactReferenceError(f"{label}: each {attribute.item} may be counted only once")
    values = []
    for raw in ids:
        item = _item(sheet, attribute.item, raw, label)
        value = attribute.read(item, None)
        if value is None:
            named = sheet.kinds[attribute.item].name(item)
            raise FactReferenceError(f"{label}: the catalog has no {parts[0]} for {named}")
        values.append(value)
    result = sum(values) if operation == TOTAL else abs(values[0] - values[1])
    if isinstance(result, float):
        result = round(result, 2)
    return attribute.derived(result)


# The longest ID a reference may carry: a 64-bit integer has at most 19 digits.
_MAX_ID_DIGITS = 19


def _item_id(raw: str, kind: str, label: str) -> int:
    """The ID in a reference: ASCII digits only ("²" and other Unicode digits fail), and
    at most 19 of them, so `int` never raises."""
    raw = raw.strip().lstrip("#")
    if not (raw.isascii() and raw.isdigit()) or len(raw) > _MAX_ID_DIGITS:
        raise FactReferenceError(f"{label}: “{raw[:24]}” is not a {kind} ID")
    return int(raw)


def _item(sheet: FactSheet, kind: str, raw: str, label: str) -> Any:
    key = _item_id(raw, kind, label)
    item = sheet.kinds[kind].items.get(key) if kind in sheet.kinds else None
    if item is None:
        raise FactReferenceError(f"{label}: {kind} {key} was not returned by any tool this turn")
    return item


# --- The report ------------------------------------------------------------------------------


def checked_report(rendered: Rendered, check: Callable[[str], GroundingReport]) -> GroundingReport:
    """The grounding report of a rendered answer: the backstop's `check` of its masked copy,
    without what the rendered names alone account for, plus the rendered facts and the
    references that failed. `verified` when something was rendered or checked and nothing
    failed; `fallback` when anything failed; `not_applicable` when there was nothing."""
    report = check(rendered.check_text)
    counts = Counter(report.counts)
    issues = list(report.issues)
    if rendered.names:
        trusted = check("\n".join(dict.fromkeys(rendered.names)))
        counts -= Counter(trusted.counts)
        issues = [issue for issue in issues if issue not in trusted.issues]
    issues = [*rendered.errors, *(i for i in issues if i not in rendered.errors)]
    counts = Counter({kind: n for kind, n in counts.items() if n > 0})
    facts = dict(rendered.counts)
    if issues:
        status = "fallback"
    elif counts or facts:
        status = "verified"
    else:
        status = "not_applicable"
    return GroundingReport(
        status=status,
        checked=sum(counts.values()),
        issues=issues,
        counts=dict(counts),
        rendered=facts,
    )


def describe_report(report: GroundingReport, against: str = "the catalog") -> str:
    """The trace's summary of a verified report: what was rendered and what was checked."""
    rendered = sum(report.rendered.values())
    parts = []
    if rendered:
        parts.append(f"Rendered {describe_counts(report.rendered)} from {against}")
    if report.checked:
        verb = "checked" if rendered else "Verified"
        parts.append(f"{verb} {describe_counts(report.counts)} against {against}")
    return "; ".join(parts) + "." if parts else "Nothing to check."


def cite_rules(references: str, example: str, *, computed: bool = True) -> str:
    """The answer prompt's cite-mode rules: `references` lists the domain's references and
    `example` shows a sentence written with them; `computed` when it has totals or
    differences. Pass the result as a prompt variable (its braces are not template fields)."""
    arithmetic = (
        ", and never add up or subtract numbers yourself: use {total:...} and {diff:...}"
        if computed
        else ""
    )
    return (
        "State catalog facts ONLY through fact references; the server replaces each one "
        "with the catalog's value before the shopper sees the answer. Use "
        f"{references}. The IDs are the numbers the tools and the session give "
        "([id N], product #N, book #N, drink #N). Never type a price, colour, rating, "
        f"author, year, page count, recipe amount or alcohol label yourself{arithmetic}. "
        "Refer only to items the tools returned in this turn or the session lists; any "
        "other reference fails. Write naturally around the references, for example: " + example
    )
