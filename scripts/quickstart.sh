#!/usr/bin/env bash
# Curio quick start without Docker: install, prepare the cocktail recipes once, build the web
# app if needed, then run one server (the API, which serves the app) and print its URL.
#
#   scripts/quickstart.sh                 # cocktails, books and Plan my evening
#   CURIO_BOOKS=0 scripts/quickstart.sh   # cocktails only
#
# Needs uv (https://docs.astral.sh/uv/); uv fetches Python 3.13 itself. The web app is built
# with npm (Node 22) when web/dist is missing or older than its sources; without npm, a build
# that is already there is served as it is. The first run installs the dependencies and
# downloads the pinned ~400 KB recipe file; later runs start in seconds. Chat works when
# LLM_* is set in .env (see .env.example). Fashion stays opt-in: prepare the H&M data
# (docs/technical-guide.md#fashion-data), then run with ENABLED_DOMAINS=fashion,cocktails,books.
# The first run also prepares the book catalog once (goodbooks-10k: a checksum-verified
# ~150 MB download, under a minute and about 1 GB of RAM on a laptop; the result is ~9 MB),
# unless CURIO_BOOKS=0. Books already prepared are always served.
#
# Environment: CURIO_BOOKS (1), CURIO_WEB_BUILD (1; 0 never builds the web app),
# ENABLED_DOMAINS (default: cocktails,books; a domain whose data is not prepared is skipped),
# DATA_DIR (data), CURIO_PORT (8000), CURIO_HOST (127.0.0.1), ALLOWED_HOSTS
# (localhost,127.0.0.1, plus CURIO_HOST when it names one address).
#
# The server listens on this computer only. CURIO_HOST=0.0.0.0 opens Curio to your network:
# anyone who can reach it can use the app and the API, and chat on your LLM key. The API
# answers only the host names in ALLOWED_HOSTS, so add the name or address others will use.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${CURIO_PORT:-8000}"
HOST="${CURIO_HOST:-127.0.0.1}"
export ENABLED_DOMAINS="${ENABLED_DOMAINS:-cocktails,books}"
export DATA_DIR="${DATA_DIR:-data}"
export WEB_DIST="web/dist"
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-localhost,127.0.0.1}"
if [[ "$HOST" != 0.0.0.0 && "$HOST" != 127.0.0.1 && "$HOST" != localhost ]]; then
    ALLOWED_HOSTS="${ALLOWED_HOSTS},${HOST}"  # the URL printed below names it
fi
# The health check talks to the local server, never through an HTTP proxy.
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"
url_host="$HOST"
[[ "$HOST" == 0.0.0.0 || "$HOST" == localhost ]] && url_host=127.0.0.1
URL="http://${url_host}:${PORT}"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

if ! command -v uv >/dev/null 2>&1; then
    echo "Curio needs uv: https://docs.astral.sh/uv/getting-started/installation/" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

say "Installing dependencies (first run only takes a while)"
uv sync --locked --extra anthropic --quiet

if [[ ! -s "$DATA_DIR/cocktails.parquet" ]]; then
    say "Preparing the cocktail recipes (one-off ~400 KB download)"
    uv run --no-sync python -m scripts.prepare_cocktails
fi

books_ready() { [[ -s "$DATA_DIR/books.parquet" && -s "$DATA_DIR/book_neighbours.parquet" ]]; }
if [[ "${CURIO_BOOKS:-1}" != 0 ]] && ! books_ready; then
    say "Preparing the book catalog (one-off ~150 MB download, under a minute)"
    uv run --no-sync python -m scripts.prepare_books
fi

# The web app: built when web/dist is missing or older than any of its sources.
web_stale() {
    [[ ! -s web/dist/index.html ]] && return 0
    [[ -n "$(find web/src web/public web/index.html web/package.json web/package-lock.json \
        web/vite.config.ts -newer web/dist/index.html -print -quit 2>/dev/null)" ]]
}
if [[ "${CURIO_WEB_BUILD:-1}" != 0 ]] && web_stale; then
    if command -v npm >/dev/null 2>&1; then
        say "Building the web app (npm ci && npm run build in web/)"
        (cd web && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent)
    elif [[ -s web/dist/index.html ]]; then
        echo "    npm is not installed: serving the web app built before (web/dist)." >&2
    else
        echo "Curio's web app is not built and npm is not installed. Install Node 22" \
            "(https://nodejs.org) and run this again, or use Docker:" \
            "docker compose --profile demo up --build" >&2
        exit 1
    fi
fi

say "Starting Curio on ${URL} (domains: ${ENABLED_DOMAINS})"
uv run --no-sync uvicorn curio.app.api:create_app --factory --host "$HOST" --port "$PORT" &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true' \
    EXIT INT TERM

health() {
    uv run --no-sync python - "$URL" <<'PY'
import json
import sys
import urllib.request

with urllib.request.urlopen(f"{sys.argv[1]}/health", timeout=2) as response:
    print(json.dumps(json.load(response)))
PY
}

for _ in $(seq 1 180); do
    if ! kill -0 "$server_pid" 2>/dev/null; then
        echo "The server stopped during startup; see the log above." >&2
        exit 1
    fi
    if status="$(health 2>/dev/null)"; then
        break
    fi
    sleep 1
done
if [[ -z "${status:-}" ]]; then
    echo "Curio did not answer on ${URL}/health within 3 minutes." >&2
    exit 1
fi

uv run --no-sync python - "$status" <<'PY'
import json
import sys

health = json.loads(sys.argv[1])
for domain in health["domains"]:
    state = "ready" if domain["enabled"] else "not prepared"
    chat = ", chat on" if domain["chat_enabled"] else ""
    print(f"    {domain['label']}: {state}{chat}")
if not health["llm_configured"]:
    print(
        "    Chat is off: set LLM_MODEL (and LLM_API_KEY / LLM_BASE_URL) in .env and run this "
        "again.\n    Any OpenAI-compatible server works, including a local Ollama "
        "(see .env.example)."
    )
PY

if [[ "$HOST" != 127.0.0.1 && "$HOST" != localhost ]]; then
    echo "    Warning: CURIO_HOST=${HOST} serves Curio beyond this computer; anyone who can" \
        "reach it can use the app, the API and the chat on your LLM key. It answers the" \
        "host names in ALLOWED_HOSTS (${ALLOWED_HOSTS}); add the one others will use." >&2
fi

say "Curio is ready: open ${URL} (Ctrl+C stops it)"
wait "$server_pid"
