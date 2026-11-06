## What and why

<!-- One or two sentences: the change and the reason for it. Link the issue if there is one. -->

## How it was checked

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pytest`
- [ ] Browser suite on real data (`uv run pytest -m e2e tests/e2e`), for UI, agent or data changes
- [ ] Screenshots updated (`scripts.capture_readme`, `scripts.record_overview`), if the UI changed

## Release notes

- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]`, or this change is invisible to users
- [ ] Breaking change (API, configuration or data format), described under "Changed"
