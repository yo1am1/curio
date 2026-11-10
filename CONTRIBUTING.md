# Contributing to Curio

Curio is a portfolio and community project, maintained by one person in spare time. It is a
reference for building agents that cite their facts and ask before changing anything, and a
base for your own dataset and domain. It is not a product and has no roadmap of new
features.

## What is welcome

- **Bug reports and fixes.** Something that does not work as the README or the docs say.
- **Corrections.** A claim in the documentation that the code does not back.
- **Questions about extending it.** If [docs/extending.md](docs/extending.md) left you
  stuck while adding a dataset or a domain, that is a documentation bug: open an issue.
- **Small improvements to the example domain** in `examples/hello_domain/`.

For a new feature or a new built-in domain, open an issue first. Most will be better as
your own fork or your own domain on top of the engine, which is what the extension contract
is for.

Report a security problem privately, as [SECURITY.md](SECURITY.md) describes, not in a
public issue.

## Set up

You need Python 3.13 with [uv](https://docs.astral.sh/uv/) and Node 22 or newer.

```bash
git clone https://github.com/yo1am1/curio.git && cd curio
uv sync --all-extras
(cd web && npm ci)
scripts/quickstart.sh        # prepares cocktails and books, serves http://localhost:8000
```

Fashion needs the H&M data, which is large and has its own terms
([technical guide](docs/technical-guide.md#fashion-data)). Nothing below requires it.

## Before you open a pull request

Run what CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy                  # type-checks curio.engine
uv run pytest                # the offline suite, about a minute

cd web
npm run lint
npm run typecheck
npm test
npm run build
```

If you changed the web app or an API route, also run the browser suite. It starts the real
API and the built web app in Chromium with a scripted model:

```bash
uv run playwright install chromium
uv run pytest -m e2e tests/e2e
```

Tests that need the H&M data skip without it.

## Rules the tests enforce

- **Layering.** `curio.engine` imports no domain, and a domain imports only the engine and
  itself. `tests/test_architecture.py` fails otherwise.
- **The toolkit contract.** A toolkit passes `check_toolkit`
  (`tests/agents/toolkit_contract.py`).
- **The frozen evaluation.** `evals/tasks.yaml`, `evals/scoring.py` and `evals/tasks.py` are
  frozen by hash (`evals/FROZEN.json`). Do not edit them in a fix; a change to them is a
  documented re-freeze ([docs/agent-eval.md](docs/agent-eval.md)).
- **Documentation links** resolve (`scripts/check_links.py`).

## Pull requests

- One change per pull request, with a test that fails without it.
- Say what you ran and what you saw. If something could not be tested, say that.
- Add a line to the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md) when the change is
  visible to a user or to someone extending the engine.
- Do not commit datasets, product photos or API keys.

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE).
