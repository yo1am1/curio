"""Compatibility shim: this package shares its import name with the Curio async library.

sniffio (used by anyio, httpx and Starlette) detects Curio by finding a `curio` module in
`sys.modules` and then calls `curio.meta.curio_running()`. This package is not that library
and never runs a Curio event loop, so detection must fall through to asyncio or trio.
"""


def curio_running() -> bool:
    return False
