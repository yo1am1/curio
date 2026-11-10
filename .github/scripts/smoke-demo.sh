#!/usr/bin/env bash
# The README's one-command quick start, from nothing: `docker compose --profile demo up
# --build` builds the image, prepares the recipes and the books in a fresh volume and serves
# the app. This waits until the API is healthy, then checks what a first visitor would use:
# the domains, a recipe search, a book search and a planned evening. It prints how long the
# cold start took. Usage: .github/scripts/smoke-demo.sh
set -euo pipefail
url=http://127.0.0.1:8000
started=$SECONDS

docker compose --profile demo up --build --detach
trap 'docker compose --profile demo logs --tail 60; docker compose --profile demo down --volumes >/dev/null 2>&1' EXIT

# The first run downloads goodbooks-10k (~150 MB) before the API starts.
for _ in $(seq 180); do
  curl -sf "$url/health" >/dev/null && break
  sleep 5
done
curl -sf "$url/health" >/dev/null || { echo "::error::the demo did not become healthy in 15 minutes"; exit 1; }
elapsed=$((SECONDS - started))

json() { curl -sf -H 'content-type: application/json' "$@"; }

json "$url/domains" | python3 -c "
import json, sys
domains = {d['id']: d for d in json.load(sys.stdin)}
on = sorted(name for name, d in domains.items() if d['enabled'])
assert on == ['books', 'cocktails', 'evening'], on
assert not domains['fashion']['enabled']
print('domains:', on)"

json -d '{"query": "something fresh with lime and mint"}' "$url/cocktails/search" | python3 -c "
import json, sys
hits = json.load(sys.stdin)
assert len(hits) >= 3, hits
print('recipe search:', len(hits), 'drinks')"

json -d '{"query": "a fantasy adventure"}' "$url/books/search" | python3 -c "
import json, sys
hits = json.load(sys.stdin)
assert len(hits) >= 3, hits
print('book search:', len(hits), 'books')"

json -d '{"occasion": "rainy Sunday in"}' "$url/evening/plan" | python3 -c "
import json, sys
plan = json.load(sys.stdin)
assert plan['book'] and plan['drink'] and plan['outfit'] is None, plan
print('evening:', plan['book']['book']['title'], '+', plan['drink']['cocktail']['name'])"

curl -sf -H 'accept: text/html' "$url/" | grep -q '<div id="root">'

echo "demo cold start: ${elapsed}s from 'docker compose up --build' to a healthy API"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  echo "Demo cold start: **${elapsed}s** from \`docker compose --profile demo up --build\` to a healthy API (image build and data preparation included)." >> "$GITHUB_STEP_SUMMARY"
fi
