"""HTTP hardening around the API: a cap on concurrent agent turns.

Every route is a sync function, so it runs in AnyIO's worker-thread pool (40 threads). An
agent turn holds its thread for as long as the model takes, up to the turn deadline, so a
few dozen slow chats used to stall every endpoint, `/health` included. `TurnSlots` caps the
turns in flight well below the pool size; a turn over the cap is refused at once with 503
and `Retry-After`, and the other routes always find a free thread.
"""

import inspect
import threading
from collections.abc import Generator

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

# How long a client should wait before retrying a turn refused for capacity.
RETRY_AFTER_SECONDS = 5
BUSY = "The assistant is busy with other conversations. Please try again in a few seconds."


class TurnSlot:
    """One admitted turn. `release` is idempotent, so every exit path may call it."""

    def __init__(self, semaphore: threading.BoundedSemaphore) -> None:
        self._semaphore = semaphore
        self._lock = threading.Lock()
        self._held = True

    def release(self) -> None:
        with self._lock:
            if not self._held:
                return
            self._held = False
        self._semaphore.release()


class TurnSlots:
    """At most `limit` agent turns at a time, across `/chat` and `/chat/stream`."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        self._semaphore = threading.BoundedSemaphore(limit)

    def acquire(self) -> TurnSlot:
        """A slot for one turn, or 503 with `Retry-After` when every slot is taken."""
        if not self._semaphore.acquire(blocking=False):
            raise HTTPException(
                status_code=503,
                detail=BUSY,
                headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
            )
        return TurnSlot(self._semaphore)


class SlotStreamingResponse(StreamingResponse):
    """A streamed turn that gives its slot back however the response ends.

    The body generator releases the slot in its own `finally` once it has run. This class
    covers the rest: a response that ends (client gone, error) before the generator started,
    or while it is suspended between events, closes it and releases the slot.
    """

    def __init__(self, content: Generator[str], slot: TurnSlot, **kwargs) -> None:
        super().__init__(content, **kwargs)
        self._generator = content
        self._slot = slot

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            state = inspect.getgeneratorstate(self._generator)
            if state in (inspect.GEN_CREATED, inspect.GEN_SUSPENDED):
                self._generator.close()
            if state != inspect.GEN_RUNNING:
                # A generator still running in a worker thread releases in its `finally`.
                self._slot.release()
