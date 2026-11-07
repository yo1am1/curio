"""Serving the built React app (`web/dist`) from the API, beside the API's own routes.

The API keeps every path where it is. The app's files are served only for GET and HEAD
requests that match no API route:

- `/assets/<file>`: the hashed bundles Vite writes to `dist/assets` (cached for a year);
- `/<file>`: other top-level files in `dist`, such as `favicon.svg`;
- any other path a browser navigates to (an `Accept` header naming `text/html`): `index.html`,
  so a deep link such as `/ask/books` opens the app, which routes it.

Everything else (API clients, unknown paths without `text/html`) gets the API's usual 404.
The folder is looked up on each request, so `npm run build` while the API runs is picked up,
and an API started before the first build serves the app once it exists.
"""

from pathlib import Path

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import FileResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ASSETS_PREFIX = "/assets/"
IMMUTABLE = {"cache-control": "public, max-age=31536000, immutable"}
REVALIDATE = {"cache-control": "no-cache"}


def _inside(root: Path, relative: str) -> Path | None:
    """`root / relative` when it is a file inside `root` (no `..` or symlink escapes)."""
    if not relative or "\x00" in relative:
        return None
    try:
        candidate = (root / relative).resolve()
        base = root.resolve()
    except (OSError, ValueError):
        return None
    if candidate.is_relative_to(base) and candidate.is_file():
        return candidate
    return None


def web_file(dist: Path, path: str, accept: str) -> tuple[Path, dict[str, str]] | None:
    """The file of the built app answering a GET for `path`, with its cache headers."""
    if path.startswith(ASSETS_PREFIX):
        found = _inside(dist / "assets", path.removeprefix(ASSETS_PREFIX))
        return (found, IMMUTABLE) if found else None
    name = path.lstrip("/")
    top_level = name and "/" not in name and not name.startswith(".")
    if top_level and (found := _inside(dist, name)) is not None:
        return found, REVALIDATE
    index = dist / "index.html"
    if "text/html" in accept and index.is_file():
        return index, REVALIDATE
    return None


def serve_web(app: FastAPI, dist: Path) -> None:
    """Answer requests no API route matches with the built app in `dist` (see the module
    docstring); the API's routes, 404s and 405s are unchanged."""
    dist = dist.resolve()
    not_found: ASGIApp = app.router.default

    async def fallback(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in ("GET", "HEAD"):
            request = Request(scope)
            found = web_file(dist, request.url.path, request.headers.get("accept", ""))
            if found is not None:
                path, headers = found
                await FileResponse(path, headers=headers)(scope, receive, send)
                return
        await not_found(scope, receive, send)

    app.router.default = fallback
