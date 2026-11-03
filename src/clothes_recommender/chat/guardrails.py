"""Output and action guardrails: grounding checks and tamper-proof approval proposals."""

import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Iterable, Sequence
from itertools import combinations
from typing import Any

from clothes_recommender.chat.schemas import (
    ActionDecision,
    GroundingReport,
    PendingAction,
)
from clothes_recommender.domain import Product

# "€24.99", "€ 24", "24.99 €", "24 EUR", "24.99 euros"
# Signs are captured so "€-2.01" is checked (and rejected) rather than skipped.
_PRICE = re.compile(
    r"€\s?([-\u2212]?\d{1,5}(?:[.,]\d{1,2})?)|([-\u2212]?\d{1,5}(?:[.,]\d{1,2})?)\s?(?:€|eur\b|euros?\b)",
    re.IGNORECASE,
)
_PRODUCT_ID = re.compile(r"\[id (\d+)\]|\bproduct #?(\d{4,})\b|#(\d{6,})\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d{1,5}(?:[.,]\d{1,2})?")
# Shoppers say "under €25" for a €24.99 item; a whole-euro mention may round a real price.
_ROUNDING_TOLERANCE = 1.0
_EXACT_TOLERANCE = 0.011


def _amount(text: str) -> float:
    return float(text.replace(",", ".").replace("\u2212", "-"))


def check_grounding(
    text: str, evidence: Sequence[Product], extra_amounts: Iterable[float] = ()
) -> GroundingReport:
    """Every price and product ID the answer states must come from tool evidence.

    Allowed amounts are evidence prices, differences between two evidence prices, and
    amounts the shopper or their filters supplied (e.g. a €40 budget).
    """
    prices = sorted({round(p.price, 2) for p in evidence})
    exact = {*prices, *(round(abs(a - b), 2) for a, b in combinations(prices, 2))}
    exact |= {round(a, 2) for a in extra_amounts}
    known_ids = {p.product_id for p in evidence}
    issues = []
    checked = 0
    for match in _PRICE.finditer(text):
        raw = match.group(1) or match.group(2)
        value = _amount(raw)
        checked += 1
        if any(abs(value - allowed) <= _EXACT_TOLERANCE for allowed in exact):
            continue
        if value.is_integer() and any(abs(value - p) <= _ROUNDING_TOLERANCE for p in prices):
            continue
        issues.append(f"€{raw} does not match any catalog price in the evidence")
    for match in _PRODUCT_ID.finditer(text):
        product_id = int(next(group for group in match.groups() if group))
        checked += 1
        if product_id not in known_ids:
            issues.append(f"product {product_id} was not returned by any tool")
    if not checked:
        return GroundingReport(status="not_applicable")
    return GroundingReport(
        status="fallback" if issues else "verified", checked=checked, issues=issues
    )


def amounts_in(text: str) -> list[float]:
    """Numbers the shopper typed, which the answer may repeat (budgets, sizes)."""
    return [_amount(raw) for raw in _NUMBER.findall(text)]


class InvalidActionError(ValueError):
    """A decision referenced a proposal this server did not issue, or it expired."""


class ActionSigner:
    """HMAC-signs pending actions so the stateless server can trust them on resume."""

    def __init__(self, key: bytes | None = None, ttl_seconds: int = 30 * 60, clock=time.time):
        # A per-process random key means proposals do not survive a restart: safe default.
        self._key = key or secrets.token_bytes(32)
        self._ttl = ttl_seconds
        self._clock = clock

    def _digest(self, action_id: str, tool: str, args: dict, request: str, issued_at: int) -> str:
        payload = json.dumps(
            [action_id, tool, args, request, issued_at], sort_keys=True, separators=(",", ":")
        )
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self, tool: str, args: dict[str, Any], request: str, **display: Any) -> PendingAction:
        action_id = secrets.token_hex(8)
        issued_at = int(self._clock())
        return PendingAction(
            id=action_id,
            tool=tool,
            args=args,
            request=request,
            issued_at=issued_at,
            signature=self._digest(action_id, tool, args, request, issued_at),
            **display,
        )

    def verify(self, action: PendingAction) -> PendingAction:
        expected = self._digest(
            action.id, action.tool, action.args, action.request, action.issued_at
        )
        if not hmac.compare_digest(expected, action.signature):
            raise InvalidActionError("This proposal was not issued by Curio or was modified.")
        if not 0 <= self._clock() - action.issued_at <= self._ttl:
            raise InvalidActionError("This proposal has expired. Please ask again.")
        return action


def narrow_args(decision: ActionDecision) -> dict[str, Any]:
    """Apply the shopper's edits, which may only remove values from the proposal."""
    proposed = decision.action.args
    edits = decision.edited_args
    if edits is None:
        return proposed
    if set(edits) - set(proposed):
        raise InvalidActionError("Edits may only change proposed fields.")
    narrowed = dict(proposed)
    for field, value in edits.items():
        original = proposed[field]
        if isinstance(original, list):
            if not isinstance(value, list) or not set(value) <= set(original):
                raise InvalidActionError(f"Edited {field} must be a subset of the proposal.")
            narrowed[field] = [item for item in original if item in value]
        elif value not in (None, original):
            raise InvalidActionError(f"Edited {field} may only be kept or cleared.")
        else:
            narrowed[field] = value
    return narrowed
