#!/usr/bin/env bash
# Curio quick start without Docker: install, prepare the cocktail recipes once, run API + UI.
#
#   scripts/quickstart.sh                 # cocktails
#   CURIO_BOOKS=1 scripts/quickstart.sh   # cocktails and books
#
# Needs uv (https://docs.astral.sh/uv/); uv fetches Python 3.13 itself. The first run
# installs the dependencies and downloads the pinned ~400 KB recipe file; later runs start in
# seconds. Chat works when LLM_* is set in .env (see .env.example). Fashion stays opt-in:
# prepare the H&M data (docs/technical-guide.md#fashion-data), then run with
# ENABLED_DOMAINS=fashion,cocktails,books. CURIO_BOOKS=1 also prepares the book catalog once
# (goodbooks-10k: a checksum-verified ~150 MB download, under a minute and about 1 GB of RAM
# on a laptop; the result is ~9 MB). Books already prepared are always served.
#
# Environment: CURIO_BOOKS (0), ENABLED_DOMAINS (default: cocktails,books; a domain whose data
# is not prepared is skipped), API_PORT (8000), UI_PORT (8501), UI_HOST (127.0.0.1).
#
# Both servers listen on this computer only. UI_HOST=0.0.0.0 opens the UI to your network:
# anyone who can reach it can chat, on your LLM key. The API always stays on 127.0.0.1.
set -euo pipefail

cd "$(dirname "$0")/.."

API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-8501}"
UI_HOST="${UI_HOST:-127.0.0.1}"
export ENABLED_DOMAINS="${ENABLED_DOMAINS:-cocktails,books}"
export API_URL="http://127.0.0.1:${API_PORT}"
# Local traffic between the UI and the API must not go through an HTTP proxy.
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

if ! command -v uv >/dev/null 2>&1; then
    echo "Curio needs uv: https://docs.astral.sh/uv/getting-started/installation/" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

say "Installing dependencies (first run only takes a while)"
uv sync --locked --extra ui --extra anthropic --quiet

if [[ ! -s data/cocktails.parquet ]]; then
    say "Preparing the cocktail recipes (one-off ~400 KB download)"
    uv run --no-sync python -m scripts.prepare_cocktails
fi

if [[ "${CURIO_BOOKS:-0}" == 1 && ! ( -s data/books.parquet && -s data/book_neighbours.parquet ) ]]; then
    say "Preparing the book catalog (one-off ~150 MB download, under a minute)"
    uv run --no-sync python -m scripts.prepare_books
fi

say "Starting the API on ${API_URL} (domains: ${ENABLED_DOMAINS})"
uv run --no-sync uvicorn clothes_recommender.api:create_app --factory \
    --host 127.0.0.1 --port "$API_PORT" &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null || true; wait "$api_pid" 2>/dev/null || true' EXIT INT TERM

health() {
    uv run --no-sync python - "$API_URL" <<'PY'
import json
import sys
import urllib.request

with urllib.request.urlopen(f"{sys.argv[1]}/health", timeout=2) as response:
    print(json.dumps(json.load(response)))
PY
}

for _ in $(seq 1 180); do
    if ! kill -0 "$api_pid" 2>/dev/null; then
        echo "The API stopped during startup; see the log above." >&2
        exit 1
    fi
    if status="$(health 2>/dev/null)"; then
        break
    fi
    sleep 1
done
if [[ -z "${status:-}" ]]; then
    echo "The API did not answer on ${API_URL}/health within 3 minutes." >&2
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

if [[ "$UI_HOST" != 127.0.0.1 && "$UI_HOST" != localhost ]]; then
    echo "    Warning: UI_HOST=${UI_HOST} serves the UI beyond this computer; anyone who can" \
        "reach it can use the chat and your LLM key." >&2
fi

ui_url_host="$UI_HOST"
[[ "$UI_HOST" == 0.0.0.0 || "$UI_HOST" == localhost ]] && ui_url_host=127.0.0.1
say "Curio is ready: open http://${ui_url_host}:${UI_PORT} (Ctrl+C stops both servers)"
uv run --no-sync streamlit run src/clothes_recommender/ui.py \
    --server.address "$UI_HOST" --server.port "$UI_PORT" --server.headless true \
    --browser.gatherUsageStats false
