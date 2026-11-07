# One image, one port: the API serves the React app (web/dist) beside its own routes.

# --- The React app ----------------------------------------------------------------------------
FROM node:22.23.4-bookworm-slim AS web
WORKDIR /web
# Dependencies first, so a code change reuses the npm layer.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- The API ----------------------------------------------------------------------------------
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Extras baked into the image: `anthropic` (LLM_PROVIDER=anthropic).
# Dev tools (pytest, ruff, Playwright) stay out.
ARG UV_EXTRAS="--extra anthropic"

# Dependencies first so code changes don't invalidate the (large) torch layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev $UV_EXTRAS --no-install-project --no-cache

COPY README.md ./
COPY src ./src
COPY scripts ./scripts
COPY taxonomy ./taxonomy
RUN uv sync --locked --no-dev $UV_EXTRAS --no-cache

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
CMD ["uvicorn", "curio.app.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
