<div align="center">

# Curio

**An engine for trustworthy recommendation agents: plan, act, verify, and ask before changing
anything.**

[![CI](https://github.com/yo1am1/curio/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/yo1am1/curio/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/yo1am1/curio?color=a0412d)](https://github.com/yo1am1/curio/releases/latest)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
![Python 3.13](https://img.shields.io/badge/python-3.13-3776ab)
![FastAPI · React](https://img.shields.io/badge/FastAPI-React-009688)

[Why three domains](#why-three-domains) · [The engine](#the-engine) ·
[Add a domain](#add-a-domain-in-one-file) · [Plan my evening](#plan-my-evening) ·
[The domains](#the-domains) · [Quick start](#quick-start) ·
[Data and licences](#data-and-licences) · [Changelog](CHANGELOG.md)

</div>

Curio is one agent engine and three recommendation domains built on it: fashion, cocktails
and books. Each domain stresses a different recommendation problem. The engine is the same
for all three. The domains also compose: "Plan my evening" turns one sentence into an outfit,
a book and a drink paired with that book.

![A tour: Plan my evening for a date night, the agent's step trace and grounding check, a save narrowed in the approval drawer, a drink paired with The Great Gatsby, and dark mode](docs/screenshots/curio-overview.webp)

Try it in one command, with no H&M download: `docker compose --profile demo up --build` serves
cocktails, books and Plan my evening at http://localhost:8000 ([quick start](#quick-start)).

## Why three domains

The domains were chosen to be different from each other. The engine runs all three and holds
no code from any of them.

| Domain | The recommendation problem | Data |
|---|---|---|
| **Fashion** | Visual retrieval with CLIP and Qdrant, purchase history, co-purchases and a learned ranker | 14,300 H&M products, 5,000 real shoppers, 14,280 product photos |
| **Cocktails** | Hard constraints: what your pantry covers, missing ingredients, alcohol-free | 425 TheCocktailDB recipes, offline |
| **Books** | Item-item collaborative filtering on about 6 million ratings, with author and series caps | 10,000 goodbooks-10k titles |

**They compose.** Pairings match a book with a drink (books and cocktails). Plan my evening
uses all three.

The second and third domains forced the engine to generalise:

- Each domain declares its own approved effects and context fields; the engine lists none.
- Books reach drinks through a `DrinkPairing` extension point, not an import. A composed
  `DomainSpec` names the domains it `builds_on`.
- A `vet_proposal` hook lets a toolkit refuse a proposal that does not fit the turn.

## The engine

![Architecture: curio.app over the domain-free curio.engine, the fashion, cocktail and book toolkits, and the composed pairings and evening domains](docs/architecture.svg)

- **Plan → act → verify.** `AgentEngine` runs every turn. A domain plugs in a `Toolkit`: its
  tools, their policies, a grounding check and a fallback.
- **Policy-gated tools.** A tool is READ (runs), ASK (pauses with a question) or APPROVE
  (only proposes). Proposals are HMAC-signed and bound to the tool, arguments, domain and
  time. You can approve or narrow one. You cannot widen or replay it.
- **Grounding.** Every price, ID, rating, title and drink name in an answer is checked
  against what the tools returned. A failed check gets one self-correction, then a
  deterministic fallback built from catalog facts.
- **Budgets and streaming.** A turn allows five tool calls and 90 seconds. Every step
  streams to the web app as NDJSON, so you see what the agent did.
- **Stateless server.** The client holds the shortlist, memory and outfit. Client-held text
  is display-only and never counts as evidence.
- **Enforced layering.** `curio.engine` imports no domain. Each `curio.domains.<name>`
  imports the engine and itself. `curio.app` wires them together. A test parses every import
  to keep it that way.

![The agent's step trace for "Black trousers under €30": plan, catalog search, draft and a passed grounding check](docs/screenshots/curio-agent.webp)

Saving or remembering anything is only proposed. You approve it, untick what you don't want,
or decline.

![The approval drawer for saving two pieces, with one unticked](docs/screenshots/curio-approval.webp)

In the chat screenshots the replies come from the scripted test model in
`tests/e2e/llm_stub.py`, because no LLM was configured. The catalog, tool results, step
trace, checks and approvals are the real system.
[How the agent works](docs/agent-architecture.md).

### Evaluated with a real model

An [agent evaluation](docs/agent-eval.md) runs 45 tasks (53 turns) through the real `/chat`
API: plain requests, follow-ups, vague requests, saves that need approval, prompt
injections, catalog gaps, price traps and safety cases such as a pregnant or underage
drinker. Each trace is scored automatically. The first run, on the 42 tasks of 2.1.0:

| | Claude, as the chat model | Scripted baseline |
|---|---|---|
| Tasks passed | **42/42** | 29/42 |
| Grounding verified on the first pass | 100 % | 97 % |
| Nothing claimed saved before approval | 39/39 turns | 40/40 turns |
| Safety checks · injections resisted | 15/15 · 4/4 | 12/15 · 4/4 |

The model answered through a file relay, so latency is not measured, and 42 tasks are a
small set: the [results and limits](docs/agent-eval.md#results) say how the score was
reached. Reading its transcripts found three app issues, fixed in 2.1.1 with a task each:
a driver's date night planned as a cosy night in, a men's sweater's look-alikes from
Ladieswear, and a dollar budget silently read as euros. Any provider runs the same tasks
with one command.

## Add a domain in one file

[`examples/hello_domain/teas.py`](examples/hello_domain/teas.py) is a complete domain in
148 lines: twelve teas, a search tool, a save tool behind approval and a grounding check.
It subclasses `BaseToolkit`, which gives every optional hook a no-op default. The engine
does the rest. An excerpt:

```python
class TeaToolkit(BaseToolkit[Tea]):
    tools: ClassVar = {schema.__name__: schema for schema in (SearchTeas, AskShopper, SaveTeas)}
    policies: ClassVar = dict(
        SearchTeas=Policy.READ, AskShopper=Policy.ASK, SaveTeas=Policy.APPROVE
    )
    grounding_subject = "tea names and IDs"

    def read(self, turn: Turn[Tea], action: SearchTeas) -> tuple[str, AgentStep]:
        words = action.query.lower().split()
        text = {t.tea_id: f"{t.name} {t.kind} {t.notes}".lower() for t in self._teas.values()}
        turn.show([t for t in self._teas.values() if any(w in text[t.tea_id] for w in words)][:5])
        step = AgentStep(kind="tool", name="SearchTeas", summary=f"Found {len(turn.items)} teas.")
        return _lines(turn.items) or "(none)", step

    def apply(self, action: SaveTeas, *, dry_run: bool = False) -> tuple[TeaEffect, list[Tea], str]:
        teas = self.proposed({"tea_ids": list(dict.fromkeys(action.tea_ids))})
        if not teas:
            raise ToolError("None of those tea IDs exist.")
        verb = "Would save" if dry_run else "Saved"
        names = ", ".join(t.name for t in teas)
        return TeaEffect(tea_ids=[t.tea_id for t in teas]), teas, f"{verb} {names}."

    def check(self, turn: Turn[Tea], text: str) -> GroundingReport:
        """Every tea ID, and every catalog tea name, must be one a tool returned."""
        returned = list(turn.evidence.values())
        checked, issues = check_ids(text, _TEA_ID, (t.tea_id for t in returned), "tea")
        for name in dict.fromkeys(m.group(0) for m in self._names.finditer(text)):
            checked += 1
            if name.casefold() not in {t.name.casefold() for t in returned}:
                issues.append(f"{name} was not returned by any tool")
        return grounding_report(checked, issues)

    # key, prompt, proposed, fallback and reply_fields complete the class.
```

Its tests drive the real engine with a scripted model: a grounded answer, an invented tea
caught by the check, and a signed proposal that is narrowed and approved, while widened or
forged ones are refused.

```bash
uv run pytest tests/test_hello_domain.py tests/test_architecture.py
```

[`tests/test_architecture.py`](tests/test_architecture.py) fails if the engine imports a
domain, or if a domain imports another one outside the two composed domains.

## Plan my evening

Plan my evening composes all three domains. Give it an occasion: "rainy Sunday in", "date
night under €100" or "book club, alcohol-free". It picks one theme. It plans an outfit from
the fashion catalog, a book from the book recommender, and a drink paired with that book.
Each part has a reason, and one line says why they go together. You can swap any part, then
save the whole evening through one approval. Without fashion data, as in the quick-start
demo, it plans the book and the drink and says the outfit needs the fashion catalog.

![Date night under €100: an outfit, a book and a drink, with why they go together](docs/screenshots/curio-evening.webp)

### Pair a book with a drink

In the book dialog, "Pair a drink" maps the book's genres, reader shelves and setting words
(Paris, the Jazz Age) to a flavour profile and scores the 425 recipes. Each drink shows the
evidence from both catalogs. The Great Gatsby gets the gin rickeys Tom Buchanan mixes.
Children's and young-adult books, and anyone who asked for alcohol-free, get only
alcohol-free drinks. The pairing is deterministic and offline. The book agent answers "what
should I drink with Dune?" with the same pairer and checks its drink names like its titles.

![Drinks paired with The Great Gatsby, each with the book and recipe evidence](docs/screenshots/curio-pairing.webp)

## The domains

**Fashion.** The feed ranks picks from a shopper's purchase history with a learned ranker.
Each pick says why it is there ("61 shoppers who bought this also bought…", "Buy again").
Search works by description or by photo: photo search embeds the image with CLIP and finds
the nearest products in Qdrant. Ask for an outfit ("a summer outfit for a wedding under
€80") and Curio fills each slot from the photo index within the budget.

![Photo search results for an example garment](docs/screenshots/curio-discover-photo.webp)

Product connections draws a piece's neighbourhood: what shoppers bought with it and the
attributes and category it shares with them. Selecting a piece opens it.

![The product connections graph of a black T-shirt: co-purchased pieces and shared attributes](docs/screenshots/curio-connections.webp)

**Cocktails.** "What can I make?" ranks drinks by the ingredients you have, shows what is
missing and allows up to three missing ingredients. Shots are left out unless a search or a
chat message asks for them. A remembered preference, such as alcohol-free, becomes a hard
filter on every search. Alcohol-free means TheCocktailDB's "Non alcoholic" label. Ingredient
filters cover the listed ingredients only; they are not an allergen or dietary guarantee.
Please drink responsibly.

**Books.** Search by title, author, genre or mood. "Fantasy like The Name of the Wind" starts
from what that book's readers also loved, and each result says how many did. The book agent
remembers genres and authors you avoid. Ratings are Goodreads averages; genres and moods
come from readers' shelves.

## Quick start

One command gives you Curio with cocktails, books and Plan my evening (without the outfit),
and no 3.5 GB H&M download. With Docker:

```bash
docker compose --profile demo up --build
```

Or with [uv](https://docs.astral.sh/uv/) and no Docker:

```bash
scripts/quickstart.sh
```

Both prepare the cocktail recipes (a checksum-verified ~400 KB download) and goodbooks-10k
(~150 MB, under a minute) on the first run. For cocktails only, skip the books with
`DEMO_BOOKS=0` (Docker) or `CURIO_BOOKS=0` (uv). Either way one server runs the API and the
web app: open http://localhost:8000. `scripts/quickstart.sh` builds the web app first when
npm (Node 22) is installed. The quick start serves this computer only; `CURIO_HOST=0.0.0.0`
opens it to your network.

Search and recommendations work without an LLM. Chat needs one: copy `.env.example` to
`.env` and set `LLM_MODEL` (plus `LLM_API_KEY` / `LLM_BASE_URL`). The Docker demo can also
run a local model with Ollama ([details](docs/technical-guide.md#quick-start)).

Every release is also a container image:

```bash
docker pull ghcr.io/yo1am1/curio:latest
```

Fashion is opt-in because its data is large: [prepare the H&M data and image
index](docs/technical-guide.md#fashion-data), then run the full stack:

```bash
uv sync --all-extras
cp .env.example .env
(cd web && npm ci && npm run build)               # the web app, served by the API
uv run uvicorn curio.app.api:create_app --factory # http://localhost:8000
```

With Docker, `docker compose --profile fashion up --build` starts it. Every domain is
optional: the API serves whichever domains are prepared.

## Under the hood

Fashion uses CLIP search in Qdrant, co-purchase and graph recommenders, collaborative
filtering and a learned ranker. Cocktails use TF-IDF and ingredient overlap in memory.
Books use TF-IDF search and item-item collaborative filtering on goodbooks-10k ratings.
Cocktails, books and the pairing need no model and no network call to serve. Every dataset
is downloaded from a pinned commit and checked by SHA-256.

Tests: about 1,570 offline Python tests, 63 Vitest tests of the web app, and 85 end-to-end
tests that run the real API and the React app in Chromium on the real data, with a scripted
LLM. An [agent evaluation](docs/agent-eval.md)
measures a real model instead: 45 tasks over the four domains, scored on tool choice,
arguments, grounding, approvals, clarifying questions, safety and prompt injection. It
works with any OpenAI-compatible or Anthropic provider, or through a file relay for a model
without an API key.

**The web app** is React, TypeScript and Vite in `web/`, with hand-written CSS, light and
dark themes and no chart or component library. The API serves its build on the same port,
so one process (or one container) is the whole app
([web app](docs/technical-guide.md#web-app)).

[Technical guide](docs/technical-guide.md) · [Agent architecture](docs/agent-architecture.md) ·
[LLM setup](docs/technical-guide.md#llm-configuration) ·
[Agent evaluation](docs/agent-eval.md) · [Tests and development](docs/technical-guide.md#development) ·
[All documentation](docs/README.md)

## How it's built and released

CI runs lint, format, the offline tests, the web app's checks and build, and a quick-start
boot checked in Chromium on every pull request and every push to `main`. When CI passes on `main` with a new version, a tag, a GitHub release
and the container image are published automatically.
[How it was built](docs/how-it-was-built.md) · [Security policy](SECURITY.md).

## Data and licences

The code is under the [Apache License 2.0](LICENSE); keep the [NOTICE](NOTICE) credit when you
redistribute it, and cite it with [CITATION.cff](CITATION.cff). The data is not part of this
repository and is not covered by that licence. H&M data is for non-commercial research use.
goodbooks-10k is CC BY-SA 4.0, collected from Goodreads. Cocktail recipes and photos remain
under TheCocktailDB's terms. Details: [data and licences](docs/data-and-licences.md).

Created and maintained by [Yehor Trepalin](https://github.com/yo1am1).
AI development assistance: Claude Code, Codex and Antigravity.
