"""The container's health check: exit 0 when the API on this machine answers /health.

`python -m curio.app.healthcheck` (the Dockerfile's HEALTHCHECK and the compose demo's). It
sends a Host the API accepts, the first plain name in ALLOWED_HOSTS, so a deployment that
allows only its own name (for example `ALLOWED_HOSTS=curio.lan`) still reports healthy.
Uses the standard library only.
"""

import os
import sys
import urllib.error
import urllib.request

DEFAULT_PORT = 8000


def host_header(allowed_hosts: str | None) -> str:
    """The first allowed name without a wildcard, or "localhost"."""
    for name in (allowed_hosts or "").split(","):
        name = name.strip().lower()
        if name and "*" not in name:
            return name
    return "localhost"


def check(port: int = DEFAULT_PORT, allowed_hosts: str | None = None) -> bool:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/health", headers={"Host": host_header(allowed_hosts)}
    )
    # No proxy: the request must reach this machine's API, whatever HTTP_PROXY says.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=4) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main() -> int:
    port = int(os.environ.get("CURIO_HEALTH_PORT", DEFAULT_PORT))
    return 0 if check(port, os.environ.get("ALLOWED_HOSTS")) else 1


if __name__ == "__main__":
    sys.exit(main())
