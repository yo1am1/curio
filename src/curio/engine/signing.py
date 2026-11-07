"""Tamper-proof, single-use approval proposals for a server that keeps no pending actions.

The server signs each proposal (HMAC-SHA256) and verifies the copy the client sends back
with the shopper's decision. Edits may only narrow a proposal. The signature also covers the
client's session id, so a proposal answers only in the session it was made for, and a
`NonceStore` remembers every answered proposal until it expires, so it answers only once.

What the signature does not do: protect the server from its own client. The client holds
the session state, including `auto_approve`, the shopper's choice to let a tool run without
asking. The signature stops the *model* from approving its own proposals and stops anyone
from forging, widening, replaying or moving a proposal; it cannot stop a client from opting
into auto-approval, by design.
"""

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from curio.engine.schemas import ActionDecision, Intent, PendingAction

# HMAC-SHA256 keys shorter than its 32-byte output weaken it; a configured key must reach it.
MIN_KEY_BYTES = 32


class InvalidActionError(ValueError):
    """A decision referenced a proposal this server did not issue, or it expired."""


class NonceStore(Protocol):
    """Remembers answered proposals until they expire, so each one is answered once.

    `claim` is atomic: of two concurrent claims of one nonce, exactly one returns True. The
    default `InMemoryNonceStore` serves one process. Several API workers need a store they
    share (Redis `SET nonce 1 NX EXAT expires_at`, or a database row with a unique key), or
    a proposal could be answered once per worker.
    """

    def claim(self, nonce: str, expires_at: float) -> bool:
        """Record `nonce` until `expires_at` (Unix time); False if it is already recorded."""
        ...

    def release(self, nonce: str) -> None:
        """Forget `nonce`, so its proposal may be answered again (its turn failed)."""
        ...


class InMemoryNonceStore:
    """A `NonceStore` for one process: a dict of nonces to expiry times, purged as it goes."""

    def __init__(self, clock: Callable[[], float] = time.time, purge_every: float = 60.0):
        self._clock = clock
        self._expiry: dict[str, float] = {}
        self._lock = threading.Lock()
        self._purge_every = purge_every
        self._next_purge = clock() + purge_every

    def claim(self, nonce: str, expires_at: float) -> bool:
        with self._lock:
            now = self._clock()
            if now >= self._next_purge:
                self._expiry = {n: t for n, t in self._expiry.items() if t > now}
                self._next_purge = now + self._purge_every
            known = self._expiry.get(nonce)
            if known is not None and known > now:
                return False
            self._expiry[nonce] = expires_at
            return True

    def release(self, nonce: str) -> None:
        with self._lock:
            self._expiry.pop(nonce, None)

    def __len__(self) -> int:
        return len(self._expiry)


class ActionSigner:
    """HMAC-signs pending actions so the stateless server can trust them on resume, and
    records answered ones in `nonces` so none is answered twice."""

    def __init__(
        self,
        key: bytes | None = None,
        ttl_seconds: int = 30 * 60,
        clock: Callable[[], float] = time.time,
        nonces: NonceStore | None = None,
    ):
        if key is not None and len(key) < MIN_KEY_BYTES:
            raise ValueError(f"The signing key must be at least {MIN_KEY_BYTES} bytes.")
        # A per-process random key means proposals do not survive a restart: safe default.
        self._key = key if key is not None else secrets.token_bytes(MIN_KEY_BYTES)
        self._ttl = ttl_seconds
        self._clock = clock
        self._nonces = nonces if nonces is not None else InMemoryNonceStore(clock)

    def _digest(
        self,
        action_id: str,
        tool: str,
        args: dict,
        request: str,
        issued_at: int,
        intent: Intent | None,
        session: str | None,
    ) -> str:
        filters = intent.model_dump(mode="json") if intent is not None else None
        payload = json.dumps(
            _canonical([action_id, tool, args, request, issued_at, filters, session or ""]),
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
        session: str | None = None,
        action_type: type[PendingAction] = PendingAction,
        **display: Any,
    ) -> PendingAction:
        """A signed proposal, bound to the client's `session` id when it sent one;
        `action_type` may be an app's subclass with display fields."""
        action_id = secrets.token_hex(8)
        issued_at = int(self._clock())
        return action_type(
            id=action_id,
            tool=tool,
            args=args,
            request=request,
            issued_at=issued_at,
            signature=self._digest(action_id, tool, args, request, issued_at, intent, session),
            intent=intent,
            **display,
        )

    def verify(self, action: PendingAction, session: str | None = None) -> PendingAction:
        """`action` if this signer issued it, unmodified, for `session`, and it has not
        expired. It may still have been answered: `claim` checks that."""
        expected = self._digest(
            action.id,
            action.tool,
            action.args,
            action.request,
            action.issued_at,
            action.intent,
            session,
        )
        if not hmac.compare_digest(expected.encode(), action.signature.encode()):
            # A proposal resumed from another session fails here too: the id is signed.
            raise InvalidActionError("This proposal was not issued by Curio or was modified.")
        if not 0 <= self._clock() - action.issued_at <= self._ttl:
            raise InvalidActionError("This proposal has expired. Please ask again.")
        return action

    def claim(self, action: PendingAction) -> None:
        """Mark a verified proposal answered; a second answer raises InvalidActionError."""
        nonce = f"{action.id}:{action.signature}"
        if not self._nonces.claim(nonce, action.issued_at + self._ttl + 1):
            raise InvalidActionError("This proposal was already answered. Please ask again.")

    def release(self, action: PendingAction) -> None:
        """Undo `claim` for a turn that failed before its reply reached the client."""
        self._nonces.release(f"{action.id}:{action.signature}")


def _canonical(value: Any) -> Any:
    """`value` with whole floats as ints, so a proposal survives a JSON round trip through a
    client that does not keep `40.0` apart from `40` (JavaScript)."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(item) for item in value]
    return value


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
