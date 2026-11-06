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

- Curio is a portfolio and research project, not a hosted service. The quick start binds the
  UI and the API to `127.0.0.1`; exposing them to a network is your decision (see the README).
- LLM keys come from the environment (`.env`) and are never written to logs, traces or
  replies. Never commit a `.env` file.
- Agent approvals are HMAC-signed with `AGENT_SIGNING_KEY`. Left empty, each process uses a
  random key; set your own long random value when several API workers must share approvals.
