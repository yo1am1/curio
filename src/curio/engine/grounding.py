"""Grounding: check an answer's claims against the evidence its tools returned.

A domain's check finds the claims it can verify in the final answer (prices, IDs, names,
amounts, ratings) and reports each one that no tool returned. These are the shared parts:
euro amounts, cited IDs, name matching, the report and merging several reports. The
engine asks for one rewrite of an unverified answer, then falls back to catalog facts.
"""

import re
from collections.abc import Iterable, Sequence
from itertools import combinations

from curio.engine.schemas import GroundingReport

# "€24.99", "€ 24", "24.99 €", "24 EUR", "24.99 euros"
# Signs are captured so "€-2.01" is checked (and rejected) rather than skipped.
_PRICE = re.compile(
    r"€\s?([-\u2212]?\d{1,5}(?:[.,]\d{1,2})?)|([-\u2212]?\d{1,5}(?:[.,]\d{1,2})?)\s?(?:€|eur\b|euros?\b)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d{1,5}(?:[.,]\d{1,2})?")
# Shoppers say "under €25" for a €24.99 item; a whole-euro mention may round a real price.
_ROUNDING_TOLERANCE = 1.0
_EXACT_TOLERANCE = 0.011
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


def grounding_report(checked: int, issues: list[str]) -> GroundingReport:
    """The report for `checked` claims, of which `issues` are unverified."""
    if not checked:
        return GroundingReport(status="not_applicable")
    return GroundingReport(
        status="fallback" if issues else "verified", checked=checked, issues=issues
    )


def merge_reports(*reports: GroundingReport) -> GroundingReport:
    """One report for several checks of the same answer: any issue means fallback."""
    applied = [r for r in reports if r.status != "not_applicable"]
    if not applied:
        return GroundingReport(status="not_applicable")
    issues = [issue for r in applied for issue in r.issues]
    return GroundingReport(
        status="fallback" if issues else "verified",
        checked=sum(r.checked for r in applied),
        issues=issues,
    )


def parse_number(text: str) -> float:
    """A number as written in prose, with a decimal comma or a minus sign."""
    return float(text.replace(",", ".").replace("\u2212", "-"))


def amounts_in(text: str) -> list[float]:
    """Numbers the shopper typed, which the answer may repeat (budgets, sizes)."""
    return [parse_number(raw) for raw in _NUMBER.findall(text)]


def check_prices(
    text: str, prices: Iterable[float], extra_amounts: Iterable[float] = ()
) -> tuple[int, list[str]]:
    """Euro amounts in `text`: how many there are, and the ones no evidence supports.

    Allowed amounts are evidence prices, differences between two evidence prices, and
    amounts the shopper or their filters supplied (e.g. a €40 budget). A whole-euro amount
    may round a real price ("about €35" for €34.99).
    """
    known = sorted({round(price, 2) for price in prices})
    exact = {*known, *(round(abs(a - b), 2) for a, b in combinations(known, 2))}
    exact |= {round(a, 2) for a in extra_amounts}
    checked = 0
    issues = []
    for match in _PRICE.finditer(text):
        raw = match.group(1) or match.group(2)
        value = parse_number(raw)
        checked += 1
        if any(abs(value - allowed) <= _EXACT_TOLERANCE for allowed in exact):
            continue
        if value.is_integer() and any(abs(value - p) <= _ROUNDING_TOLERANCE for p in known):
            continue
        issues.append(f"€{raw} does not match any catalog price in the evidence")
    return checked, issues


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
