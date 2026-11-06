"""Tamper-proof approval proposals for a stateless server.

The server keeps no pending actions: it signs each proposal (HMAC-SHA256) and verifies the
copy the client sends back with the shopper's decision. Edits may only narrow a proposal.
"""

import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from curio.engine.schemas import ActionDecision, Intent, PendingAction


class InvalidActionError(ValueError):
    """A decision referenced a proposal this server did not issue, or it expired."""


class ActionSigner:
    """HMAC-signs pending actions so the stateless server can trust them on resume."""

    def __init__(self, key: bytes | None = None, ttl_seconds: int = 30 * 60, clock=time.time):
        # A per-process random key means proposals do not survive a restart: safe default.
        self._key = key or secrets.token_bytes(32)
        self._ttl = ttl_seconds
        self._clock = clock

    def _digest(
        self,
        action_id: str,
        tool: str,
        args: dict,
        request: str,
        issued_at: int,
        intent: Intent | None,
    ) -> str:
        filters = intent.model_dump(mode="json") if intent is not None else None
        payload = json.dumps(
            [action_id, tool, args, request, issued_at, filters],
            sort_keys=True,
            separators=(",", ":"),
        )
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(
        self,
        tool: str,
        args: dict[str, Any],
        request: str,
        *,
        intent: Intent | None = None,
        action_type: type[PendingAction] = PendingAction,
        **display: Any,
    ) -> PendingAction:
        """A signed proposal; `action_type` may be an app's subclass with display fields."""
        action_id = secrets.token_hex(8)
        issued_at = int(self._clock())
        return action_type(
            id=action_id,
            tool=tool,
            args=args,
            request=request,
            issued_at=issued_at,
            signature=self._digest(action_id, tool, args, request, issued_at, intent),
            intent=intent,
            **display,
        )

    def verify(self, action: PendingAction) -> PendingAction:
        expected = self._digest(
            action.id, action.tool, action.args, action.request, action.issued_at, action.intent
        )
        if not hmac.compare_digest(expected.encode(), action.signature.encode()):
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
            if not isinstance(value, list) or not _subset(value, original):
                raise InvalidActionError(f"Edited {field} must be a subset of the proposal.")
            narrowed[field] = [item for item in original if item in value]
        elif isinstance(original, bool):
            # A proposed flag (e.g. alcohol_free) may be switched off, never on.
            if value is not False and value is not original:
                raise InvalidActionError(f"Edited {field} may only be kept or switched off.")
            narrowed[field] = value
        elif value not in (None, original):
            raise InvalidActionError(f"Edited {field} may only be kept or cleared.")
        else:
            narrowed[field] = value
    return narrowed


def _subset(values: list[Any], original: list[Any]) -> bool:
    """Whether every edited value was proposed; unhashable values never were."""
    try:
        return set(values) <= set(original)
    except TypeError:
        return False
