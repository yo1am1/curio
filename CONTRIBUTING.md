# Contributing to Curio

Curio uses **trunk-based development**: `main` is always releasable, every change reaches it
through a pull request that passes CI, and every version on `main` is published as a tagged
release and a container image automatically. These rules apply to the maintainer too.

## Branches

| Branch | Purpose | Lifetime |
|---|---|---|
| `main` | The only long-lived branch. Protected: no direct pushes, no force pushes, no deletion. | Permanent |
| `feat/<topic>`, `fix/<topic>`, `docs/<topic>`, `ci/<topic>`, `chore/<topic>` | One change each, branched from the latest `main`. | Deleted automatically when merged |

There are no release, develop or archive branches: releases are tags (`v1.2.1`), and history
lives on `main`.

## Making a change

1. Branch from `main`: `git switch -c fix/short-topic origin/main`.
2. Run the checks CI runs before you push:

   ```bash
   uv sync --all-extras
   uv run ruff check . && uv run ruff format --check . && uv run pytest
   ```

   UI, agent or data changes should also pass the browser suite on real data
   (`uv run pytest -m e2e tests/e2e`, see [the technical guide](docs/technical-guide.md#end-to-end-tests)).
3. Add an entry under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for anything a user,
   operator or API client would notice.
4. Open a pull request. Its **title** must follow
   [Conventional Commits](https://www.conventionalcommits.org/), because it becomes the
   squashed commit on `main`: `feat(books): …`, `fix(ci): …`, `docs: …`. Allowed types:
   `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `ci`, `build`, `chore`, `revert`.
5. Merge with **squash and merge** once the required checks are green and review is done.
   `main` keeps a linear history of one commit per pull request.

## Required checks

| Check | Workflow | What it proves |
|---|---|---|
| `checks` | [CI](.github/workflows/ci.yml) | Lint, format, ~1,160 offline tests, the API booting with only the demo domain, every Compose profile |
| `pr-title` | [PR title](.github/workflows/pr-title.yml) | The squash commit will be a valid Conventional Commit |

Never skip, disable or weaken a test to get a green check; fix the cause.

## Releases and deployments

Releases are cut from `main` only, by changing the version, never by pushing tags by hand.

1. In a `chore(release): X.Y.Z` pull request, set `version` in `pyproject.toml` and
   `CITATION.cff`, run `uv lock`, and rename `## [Unreleased]` in the changelog to
   `## [X.Y.Z] - YYYY-MM-DD` (plus the compare links at the bottom).
2. Merge it. When CI passes on that `main` commit, the [Release](.github/workflows/release.yml)
   workflow:
   - creates the tag `vX.Y.Z` and a GitHub release whose notes are that changelog section;
   - builds the Docker image and publishes it to the GitHub Container Registry as
     `ghcr.io/yo1am1/curio:X.Y.Z` and `:latest`, through the `release` environment.
3. A version that already has a release is skipped, so re-runs are safe.

Versions follow [Semantic Versioning](https://semver.org/): a **patch** for fixes and docs, a
**minor** for new features, a **major** for breaking API or configuration changes (call those
out under a "Changed" heading).

Tags `v*` are protected: they cannot be moved or deleted.

## Dependencies

[Dependabot](.github/dependabot.yml) opens grouped weekly pull requests for Python packages
(`uv.lock`), GitHub Actions and the Docker base image. They go through the same checks.

## Security

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md), not in issues.
