# Security policy

## Supported versions

Only the latest release on `main` receives fixes. Older tags are not patched.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private
[security advisory form](https://github.com/yo1am1/curio/security/advisories/new) instead.
Include the version, the steps to reproduce, and the impact you expect.

You can expect a first reply within a week. Once a fix is released, the advisory is published
with credit to you unless you ask otherwise.

## Scope notes

- Curio is a portfolio and research project, not a hosted service. It has no login, no
  rate limit per user and no cost cap: anyone who can reach the API can use the chat, which
  spends the configured LLM key. Both quick starts serve this computer only:
  `scripts/quickstart.sh` binds `127.0.0.1`, and `docker compose` publishes port 8000 on
  `127.0.0.1` and does not publish Qdrant at all. `CURIO_HOST` opens either to a network on
  purpose (`CURIO_HOST=0.0.0.0`, plus your host name in `ALLOWED_HOSTS`); put an
  authenticating reverse proxy in front if anyone you do not trust can reach it.
- LLM keys come from the environment (`.env`) and are never written to logs, traces or
  replies. Never commit a `.env` file.
- Agent approvals are HMAC-signed with `AGENT_SIGNING_KEY`. Left empty, each process uses a
  random key; set your own long random value when several API workers must share approvals.
