#!/usr/bin/env bash
# Curio quick start without Docker: install, prepare the cocktail recipes once, run API + UI.
#
#   scripts/quickstart.sh
#
# Needs uv (https://docs.astral.sh/uv/); uv fetches Python 3.13 itself. The first run
# installs the dependencies and downloads the pinned ~400 KB recipe file; later runs start in
# seconds. Chat works when LLM_* is set in .env (see .env.example). Fashion stays opt-in:
# prepare the H&M data (docs/technical-guide.md#fashion-data), then run with
# ENABLED_DOMAINS=fashion,cocktails.
#
# Environment: ENABLED_DOMAINS (default: cocktails), API_PORT (8000), UI_PORT (8501).
set -euo pipefail

cd "$(dirname "$0")/.."

API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-8501}"
export ENABLED_DOMAINS="${ENABLED_DOMAINS:-cocktails}"
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

say "Curio is ready: open http://localhost:${UI_PORT} (Ctrl+C stops both servers)"
uv run --no-sync streamlit run src/clothes_recommender/ui.py \
    --server.port "$UI_PORT" --server.headless true --browser.gatherUsageStats false
