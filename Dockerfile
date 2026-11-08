# One image, one port: the API serves the React app (web/dist) beside its own routes.
# Base images are pinned by exact version and digest, so a rebuild gets the same bytes;
# Dependabot (docker ecosystem) proposes updates to both.

# --- The React app ----------------------------------------------------------------------------
FROM node:26.12.1-bookworm-slim@sha256:88e480776c6149ff1d098cd304f44bf18057fb5d284a3329b78162d29821e28c AS web
WORKDIR /web
# Dependencies first, so a code change reuses the npm layer.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- The Python environment (uv stays in this stage) -------------------------------------------
FROM python:3.13.17-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962 AS python

# The same uv as CI (.github/workflows/ci.yml), which writes uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Extras baked into the image: `anthropic` (LLM_PROVIDER=anthropic).
# Dev tools (pytest, ruff, Playwright) stay out.
ARG UV_EXTRAS="--extra anthropic"

# Dependencies first so code changes don't invalidate the (large) torch layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev $UV_EXTRAS --no-install-project --no-cache

COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev $UV_EXTRAS --no-cache

# --- The API ----------------------------------------------------------------------------------
# The same base image as the stage above (so the venv's interpreter links resolve), without uv.
FROM python:3.13.17-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app

COPY --from=python /app/.venv ./.venv
COPY README.md ./
COPY src ./src
COPY scripts ./scripts
COPY taxonomy ./taxonomy

# The built app; the API serves it from WEB_DIST (default web/dist, relative to /app).
COPY --from=web /web/dist ./web/dist

# Data is mounted at runtime (see docker-compose.yml), not baked into the image.
# Named volumes take the owner of their mount point, so the Hugging Face cache and the data
# directory (the demo's recipe volume) must exist and belong to `app`; otherwise Docker creates
# them as root, and CLIP cannot download and the demo cannot write its recipes.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /home/app/.cache/huggingface /app/data \
    && chown -R app:app /home/app/.cache /app/data
USER app

EXPOSE 8000
# /health answers once the API is up, with or without prepared data; the check sends a Host
# from ALLOWED_HOSTS (curio/app/healthcheck.py).
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --start-interval=2s --retries=3 \
    CMD ["python", "-m", "curio.app.healthcheck"]
CMD ["uvicorn", "curio.app.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
