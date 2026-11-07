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
- The API answers only the host names in `ALLOWED_HOSTS` (default `localhost,127.0.0.1`), so
  a web page cannot reach a local Curio through DNS rebinding. Responses carry a
  Content-Security-Policy, `nosniff`, a referrer policy and frame protection, and the chat
  never renders images from model text, so a prompt-injected answer cannot make the browser
  send data to another host. `/docs` is off unless `API_DOCS=true`.
- LLM keys come from the environment (`.env`) and are never written to logs, traces or
  replies. Never commit a `.env` file.
- Agent approvals are HMAC-signed with `AGENT_SIGNING_KEY` (at least 32 bytes; left empty,
  each process uses a random key). Each proposal is bound to the client's session id and
  can be answered once: the signer records answered proposals in memory until they expire.
  That record is per process, so with several workers (`WEB_CONCURRENCY` > 1, which also
  requires `AGENT_SIGNING_KEY`) a proposal could be answered once per worker unless you plug
  in a shared `NonceStore` (see `curio.engine.signing`).
- `auto_approve` is the shopper's own preference and is held by their client. The signature
  stops the model from approving its own proposals and stops forged, widened, replayed or
  moved proposals; it does not protect the server against its client, which may opt into
  auto-approval. Approved effects only go back to that client.
