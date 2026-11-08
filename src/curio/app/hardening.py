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


# --- Response headers -------------------------------------------------------------------------

# Where the web app loads pictures from besides itself: recipe photos (TheCocktailDB), book
# covers (Goodreads, then Open Library, whose covers redirect to archive.org) and `blob:` for
# the preview of a photo being uploaded. Model text cannot add hosts: chat Markdown renders
# no images at all, and this list keeps any other image URL from loading.
IMAGE_SOURCES = (
    "'self'",
    "blob:",
    "https://www.thecocktaildb.com",
    "https://images.gr-assets.com",
    "https://covers.openlibrary.org",
    "https://archive.org",
    "https://*.archive.org",
)
# Swagger UI for /docs (only with API_DOCS=true), pinned to one release with SRI hashes.
SWAGGER_CDN = "https://cdn.jsdelivr.net"
SWAGGER_JS = f"{SWAGGER_CDN}/npm/swagger-ui-dist@5.33.3/swagger-ui-bundle.js"
SWAGGER_JS_SRI = "sha384-h00llMzD1afyNow1pDmnZKlHsUZgp9KE9HXLFXF2CvbwsBY0+WBO4f75SfGwvM4K"
SWAGGER_CSS = f"{SWAGGER_CDN}/npm/swagger-ui-dist@5.33.3/swagger-ui.css"
SWAGGER_CSS_SRI = "sha384-Ov4/wv3j2bmct8cDc5X4ngJZohVPzEmc6uDPH8WeljUxO5vtoykvMEfbu9Vh6RaW"


def _policy(**directives: str) -> str:
    return "; ".join(f"{name.replace('_', '-')} {value}" for name, value in directives.items())


# Scripts, styles, fonts and API calls come from the app's own origin only. Inline styles
# stay allowed: `motion` sets element styles and injects a <style> element for some
# animations; scripts never run inline.
APP_POLICY = _policy(
    default_src="'self'",
    script_src="'self'",
    style_src="'self' 'unsafe-inline'",
    img_src=" ".join(IMAGE_SOURCES),
    font_src="'self'",
    connect_src="'self'",
    object_src="'none'",
    base_uri="'self'",
    form_action="'self'",
    frame_ancestors="'none'",
)
DOCS_POLICY = _policy(
    default_src="'self'",
    script_src=f"'self' {SWAGGER_CDN}",
    style_src=f"'self' 'unsafe-inline' {SWAGGER_CDN}",
    img_src="'self' data:",
    connect_src="'self'",
    object_src="'none'",
    base_uri="'self'",
    form_action="'self'",
    frame_ancestors="'none'",
)
BASE_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "x-frame-options": "DENY",
}


class SecurityHeaders:
    """ASGI middleware: CSP, nosniff, referrer policy and frame protection on every
    response (a route's own value for a header wins). The policy is always the app's: the
    Swagger UI routes, which exist only with API_DOCS on, set `DOCS_POLICY` themselves, so
    the web app served at /docs when they are off never gets the wider policy."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        added = {**BASE_HEADERS, "content-security-policy": APP_POLICY}

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                extra = [
                    (name.encode(), value.encode())
                    for name, value in added.items()
                    if name.encode() not in present
                ]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, with_headers)


def swagger_page(title: str, openapi_url: str) -> str:
    """The /docs page: the pinned Swagger UI with SRI, started by a same-origin script."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title} - Swagger UI</title>
<link rel="stylesheet" href="{SWAGGER_CSS}" integrity="{SWAGGER_CSS_SRI}" crossorigin="anonymous">
</head>
<body>
<div id="swagger-ui" data-openapi-url="{openapi_url}"></div>
<script src="{SWAGGER_JS}" integrity="{SWAGGER_JS_SRI}" crossorigin="anonymous"></script>
<script src="/docs/init.js"></script>
</body>
</html>
"""


SWAGGER_INIT = """const root = document.getElementById("swagger-ui");
window.ui = SwaggerUIBundle({
  url: root.dataset.openapiUrl,
  dom_id: "#swagger-ui",
  deepLinking: true,
  presets: [SwaggerUIBundle.presets.apis],
  layout: "BaseLayout",
});
"""
