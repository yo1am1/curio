# How Curio was built

Curio is a solo project by Yehor Trepalin. It was built with AI coding assistants under his
direction: he set the scope, the contracts between layers and the acceptance bar, and
reviewed every change before it landed.

- **Small, reviewable steps.** Work was split by file ownership, with the shared interfaces
  agreed first: the `Toolkit` contract, the wire models, the domain registry.
- **Review loops.** Each larger change got an independent read-only review, then a review in
  the browser on the real data. Release audits listed issues in the agent, the search and the
  UI; each fix landed with a regression test.
- **Test gates.** Every change has to pass lint, formatting and the offline suite. The
  suite includes the import-layering test in `tests/test_architecture.py`. A browser suite
  runs the real API and the React app it serves in Chromium on the prepared data, with a
  scripted LLM, so chat runs are reproducible; CI runs the part that needs no H&M data.
- **Evidence over claims.** Recommender changes were measured on a chronological holdout
  and kept only when the evidence held. The records, including the ideas that did not hold,
  are in [research/fashion](research/fashion/recommendation-audit.md).
- **Release automation.** CI runs on every pull request and on every push to `main`. When
  CI passes on `main` with a new version, the Release workflow builds, smoke-tests and pushes
  the container image to ghcr.io, and only then tags that commit and publishes a GitHub
  release with the changelog notes.

The [changelog](../CHANGELOG.md) records what changed in each version.
