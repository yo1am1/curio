"""HTTP hardening around the API: request-body limits and a cap on concurrent agent turns.

Request bodies: pydantic bounds every field the API reads, but it ignores unknown fields, so
a 200 MB JSON body used to be parsed in full, and Starlette spooled a whole multipart upload
to disk before the route could look at its size. `BodySizeLimit` refuses a body over its
route's limit with 413: at once when `Content-Length` says so, else as soon as the streamed
bytes pass the limit, so at most the limit is ever read.

Concurrent turns: every route is a sync function, so it runs in AnyIO's worker-thread pool
(40 threads). An agent turn holds its thread for as long as the model takes, up to the turn
deadline, so a few dozen slow chats used to stall every endpoint, `/health` included.
`TurnSlots` caps the turns in flight well below the pool size; a turn over the cap is
refused at once with 503 and `Retry-After`, and the other routes always find a free thread.
"""

import inspect
import json
import threading
from collections.abc import Generator, Mapping

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


class _BodyTooLarge(Exception):
    """Raised from `receive` once a streamed body passes its limit."""


class BodySizeLimit:
    """ASGI middleware: 413 for a request body over its route's limit.

    `limits` maps a path to `(bytes, detail)`; other paths get `default`. A declared
    `Content-Length` over the limit is refused without reading the body. Otherwise the bytes
    are counted as they arrive and the request is cut off just past the limit; whatever the
    route then answers (a parse error) is replaced by the 413.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        default: tuple[int, str],
        limits: Mapping[str, tuple[int, str]] | None = None,
    ) -> None:
        self.app = app
        self.default = default
        self.limits = dict(limits or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit, detail = self.limits.get(scope["path"], self.default)
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _too_large(send, detail)
            return
        received = 0
        exceeded = False
        started = False

        async def limited() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLarge
            return message

        async def guarded(message: Message) -> None:
            nonlocal started
            if exceeded and not started:
                return  # the route's answer to a cut-off body; the 413 replaces it
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited, guarded)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not started:
            await _too_large(send, detail)


async def _too_large(send: Send, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
