#!/usr/bin/env bash
# Smoke-test a built Curio image: it starts as the non-root user, its HEALTHCHECK turns
# healthy, the API answers /health and serves the React app, and no build tool is left in it.
# Usage: [SMOKE_PORT=8000] .github/scripts/smoke-image.sh IMAGE
set -euo pipefail
image=$1
port=${SMOKE_PORT:-8000}

docker run --rm "$image" python -c "import curio.app.api, curio.domains.pairings; print('imports ok')"
docker run --rm "$image" test -s web/dist/index.html
if docker run --rm "$image" sh -c 'command -v uv'; then
  echo "::error::uv is in the runtime image"
  exit 1
fi
test "$(docker run --rm "$image" id -u)" = 1000

container=$(docker run -d -p 127.0.0.1:$port:8000 "$image")
trap 'docker logs "$container" | tail -50; docker rm -f "$container" >/dev/null' EXIT
for _ in $(seq 60); do
  status=$(docker inspect -f '{{.State.Health.Status}}' "$container")
  [ "$status" = healthy ] && break
  [ "$status" = unhealthy ] && { echo "::error::the container is unhealthy"; exit 1; }
  sleep 2
done
test "$status" = healthy
curl -sf localhost:$port/health | python3 -c "import json, sys; h = json.load(sys.stdin); assert 'domains' in h, h"
curl -sf -H 'accept: text/html' localhost:$port/ | grep -q '<div id="root">'
echo "smoke test passed"
