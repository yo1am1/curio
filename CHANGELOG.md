# Changelog

All notable changes to Curio are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- CI: a "Docker image" workflow builds and smoke-tests the image on pull requests that can
  change it (Dockerfile, dependencies, base images), so those bumps are proven before merge.
- Dependabot skips TypeScript major versions in `web/` until typescript-eslint supports
  TypeScript 7.

## [3.0.0] - 2026-11-07

Curio's UI is now a React app that the API serves on its own port: one process, or one
container, is the whole app.

### Breaking

- The Streamlit UI is removed (`src/curio/app/ui`, `.streamlit/`). The UI is the React app
  at the API's URL (http://localhost:8000 by default); port 8501 and `streamlit run
  src/curio/app/ui/main.py` are gone.
- The `ui` extra and the `streamlit` dependency are removed (and with them the `starlette`
  pin Streamlit needed). `uv sync --extra ui` fails; use `uv sync` or `--all-extras`.
- Docker: the `demo` and `fashion` profiles run one service each (`demo-api`, `api`) on port
  8000; `demo-ui` and `ui` are gone. The image builds the web app in a `node:22` stage and
  exposes only port 8000.
- `scripts/quickstart.sh` starts one server and takes `CURIO_HOST` and `CURIO_PORT` instead
  of `UI_HOST`, `UI_PORT` and `API_PORT`; it still binds 127.0.0.1 unless told otherwise.
- The unused `API_URL` setting is gone (an `.env` that sets it still loads).

### Added

- A React web app in `web/` (Vite, React 19, TypeScript, `motion`, hand-written CSS
  modules), with every page the Streamlit UI had: the shell and domain switch from
  `/domains` (unserved domains disabled with their hint), Plan my evening (plan, swaps and
  Save evening through an approval), chat for every domain over `/chat/stream` with a live
  step trace, grounding badges, item, outfit and pairing cards, quick replies and an approval
  drawer that narrows proposals; the fashion feed with every strategy and its evidence,
  Discover (description and photo search), Product connections (a hand-drawn SVG graph),
  Model quality; recipe search and "What can I make?"; book search, "Because you liked…",
  browse by mood and the reading list's cocktail; product, recipe and book dialogs (with
  "Pair a drink"), Compare for pieces and drinks, and the client-held session in
  localStorage. Light and dark themes follow the system; fonts are bundled.
- The API serves the built app (`WEB_DIST`, `SERVE_WEB`): `/assets/*`, top-level files, and
  `index.html` for page navigations that match no API route, so deep links work. API paths,
  404s and 405s are unchanged.
- `scripts.export_openapi` writes the OpenAPI schema without a server; the web app's types
  are generated from it (`npm run gen:api`), and a test keeps `web/openapi.json` current.
- `scripts/quickstart.sh` builds the web app with npm when `web/dist` is missing or older
  than its sources (`CURIO_WEB_BUILD=0` skips it) and prints the one URL to open.
- CI: a `web` job (lint, typecheck, Vitest, build) and an `e2e-quickstart` job that runs the
  quick start's browser tests in Chromium on the built app. The release smoke test checks
  that the image holds `web/dist/index.html`.
- A Vitest suite for the web app (63 tests) and Playwright tests for every page, ported from
  the Streamlit browser tests one for one, plus the product connections graph in the README.

### Changed

- Every end-to-end browser test drives the React app served by the real API; the
  quick-start tests check that the app lands on the cocktail pages and shows fashion as
  unavailable with its hint.
- `scripts.capture_readme` and `scripts.record_overview` drive the React routes (`--url`),
  and every README image and the overview tour were recaptured.
- The README, the technical guide, the architecture diagram and the other docs describe the
  React app and the single-server setup.

### Fixed

- Approving a proposal whose arguments hold a whole number as a float (a remembered budget
  of 40.0) failed from a JavaScript client, which sends it back as `40`: the signature now
  treats whole floats as integers, and any changed value still fails.

## [2.1.1] - 2026-11-07

### Fixed

Three app issues the first real-model evaluation's transcripts showed, each with unit tests
and an evaluation task ([docs/agent-eval.md](docs/agent-eval.md#app-issues-found-and-fixed-in-211)):

- Plan my evening: driving, a designated driver, a pregnancy, "sober" and other
  alcohol-free words only decide the drink; they never choose or change the theme. "Date
  night, but I'm the one driving us home" was planned as a cosy night in because "home"
  tied with "date night"; it is now romantic with an alcohol-free drink. Where the car goes
  ("driving us home", "driving to work") no longer counts as a theme or a work occasion,
  and "without alcohol" no longer negates the theme word after it. Task
  `evening-driving-date-night`.
- Fashion: FindRelated keeps similar, bought-together and alternative pieces in the source
  product's audience (Menswear stays Menswear; Ladieswear and Divided are womenswear;
  Baby/Children stays children's; Sport, which the catalog does not split by gender, is not
  restricted), unless the shopper asks for another `audience`. Look-alikes of a men's
  rollneck included Ladieswear. Evidence and reasons are unchanged; `GET
  /products/{id}/similar` keeps its unfiltered photo neighbours. Task
  `fashion-similar-same-audience`.
- Budgets in dollars or pounds ("$150", "£80", "150 USD", "80 GBP", "90 dollars", "60
  pounds") are recognised and used as a euro budget of the same number, never converted
  with a made-up rate, and a note says so ("Prices are in euros; I used 150 as a € budget.
  You gave it in US dollars, and Curio does not convert currencies."): on the evening
  plan, in PlanEvening's and every fashion tool's result, in the fashion trace and session
  prompt. A dollar budget the fashion intent left out becomes the price limit. Task
  `fashion-dollar-budget`; the eval scorer gains a `cards.sections` check.

## [2.1.0] - 2026-11-07

### Added

- Agent evaluation (`evals/`, [docs/agent-eval.md](docs/agent-eval.md)): 42 tasks over
  fashion, cocktails, books and evening with machine-checkable expectations, a runner that
  drives the real API with any OpenAI-compatible or Anthropic model, and a scorer for task
  success, tool selection, arguments, grounding, approval discipline, clarification,
  safety, injection resistance and budgets.
- A file relay (`python -m evals.relay`) and answering CLI (`python -m evals.relay_answer`)
  so a model without an API key in the environment can be evaluated by an agent answering
  each call.
- `AGENT_TURN_DEADLINE_SECONDS` sets the agent's turn deadline (default 90 s, unchanged).
- First results ([docs/agent-eval.md](docs/agent-eval.md#results), reports in
  `docs/evals/`): a Claude model answering as the chat model passed 42/42 tasks with every
  answer grounded on the first pass, 15/15 safety checks and 4/4 injections resisted; the
  scripted baseline passed 29/42. Summarised in the README.

### Fixed

- Eval scorer: a negation now excuses a claim when it opens the same clause up to 120
  characters back ("I won't … say that five shots is fine"), and the ski-trip task no
  longer counts the app's "Bought together with …" evidence as a purchase claim. Both
  false positives came from the first relay run and have regression tests.

## [2.0.0] - 2026-11-06

### Highlights

- Curio is now an engine for trustworthy recommendation agents, proven on fashion, cocktails
  and books, three deliberately different domains that compose.
- **Plan my evening** turns one occasion into an outfit, a book and a drink paired with it.
- Content safety: shots, crude drink names and adult books stay out unless asked for.
- The Python package is renamed from `clothes_recommender` to `curio`.

### Breaking

- The package is `curio` (`src/curio`), not `clothes_recommender`. The PyPI project stays
  `curio-recommendations`.
- Entry points: the API is `uvicorn curio.app.api:create_app --factory`, and the UI is
  `streamlit run src/curio/app/ui/main.py`.
- Imports follow three layers: `curio.engine` (domain-free), `curio.domains.<name>` and
  `curio.app`. The engine imports no domain; only `pairings` and `evening` import other
  domains. `tests/test_architecture.py` enforces it.

### Added

- **Plan my evening** (`curio.domains.evening`), a composed domain. The deterministic,
  offline `EveningPlanner` reads a free-text occasion ("rainy Sunday in", "date night under
  €100", "book club, alcohol-free"), picks one of eight themes and plans an outfit
  (fashion's `OutfitPlanner`), a book (the book recommender) and a drink the book → cocktail
  pairer pairs with that book. Each part has a reason, and one "why it works" line cites
  them all. Alcohol-free and the children's and young-adult rule work as in pairings.
  Without fashion it plans the book and the drink and says the outfit needs the catalogue.
- `POST /evening/plan` and `POST /evening/swap` (another or cheaper piece, another book with
  a re-paired drink, another or alcohol-free drink), stateless over a client-held
  `EveningState`.
- The **evening agent** (`/chat` with `domain: "evening"`): PlanEvening and SwapEveningPart
  (READ), AskShopper, and SaveEvening (APPROVE, signed and narrowable), which saves every part
  to its own list through one `EveningEffect`. Grounding reuses the fashion, book and
  paired-drink checks.
- Composed domains in the registry (`DomainSpec.compose`, `builds_on`): `evening` is served
  whenever books and cocktails are, including in the quick-start demo.
- UI: an **Evening** entry in the domain switch and a Plan my evening page with occasion
  chips, a budget, women or men, alcohol-free, three cards with swaps and Save evening
  through an approval dialog. Evening chat shows the same cards.
- `BaseToolkit` in `curio.engine`: no-op defaults for the optional `Toolkit` hooks
  (`clean_context`, `history`, `plan`, `refresh`, `proposed`, `approval_text`,
  `pending_display`, `vet_proposal`, `suggestions`); the required methods stay abstract.
  The tea example and every domain toolkit subclass it.
- `OutfitRequest.queries` sets a query per outfit slot.
- Docs: `docs/data-and-licences.md` (moved from the README) and `docs/how-it-was-built.md`.
- Tests for the planner, the API, the evening agent and the UI, end-to-end runs on the real
  data, and `tests/test_architecture.py` rules for `evening`.

### Changed

- **Curio is presented as one engine for trustworthy recommendation agents.** The README
  leads with why three domains, what the engine guarantees, a domain in one file, Plan my
  evening and the domains, with five screenshots and a shorter overview animation.
- `docs/architecture.svg` shows the three layers and the composed domains.
- The overview animation (`scripts.record_overview`) opens on Plan my evening (a date night
  under €100, with one swap), then the agent's step trace and grounding check, then The
  Great Gatsby's drink pairing.
- **The Python package is now `curio`**: imports, the Docker image, the Compose services,
  `scripts/quickstart.sh` and CI use it. A small `curio.meta` shim keeps sniffio from
  mistaking it for the Curio async library, which shares the import name.
- **Three layers instead of one `chat/` package.** `curio.engine` holds the domain-free agent:
  `AgentEngine`, the `Toolkit` protocol and `BaseToolkit`, policies, signed proposals,
  grounding helpers, the chat model factory and the shared wire models. Each
  `curio.domains.<name>` holds its catalog and recommenders beside an `agent/` package.
  `curio.app` holds settings, the domain registry, the `/chat` wire models, the API, the UI
  and the demo data. Pairings reach the book agent through a `DrinkPairing` extension point.
  Each domain declares its own context fields and approved effects. Routes and JSON replies
  are unchanged.
- **The Streamlit UI is a package** (`curio.app.ui`) instead of one 4,100-line file: `main`
  (the entry point), `shell`, `client`, `common`, `chat`, `effects`, `fashion`, `cocktails`,
  `books` and `evening`. No behaviour change.
- **Content safety.** Search, similar drinks and "What can I make?" leave out crude drink
  names unless the search names the drink. Shots (the Shot category, and shots, slammers
  and flaming drinks by name, such as Jello shots) stay out of "What can I make?" and
  default recipe search, as in pairings, unless the request asks for them. Book search and
  recommendations leave out erotica and abuse shelves unless the query asks for them.
- The UI opens on Plan my evening when the server serves it; the brand reads "Trustworthy
  recommendation agents".
- The one-command demo includes books by default (`DEMO_BOOKS=0` or `CURIO_BOOKS=0` opts
  out), so it has pairings and Plan my evening.
- Fashion shows cleaned product names (`Product.display_name`: "PQ BOSTON SILK SKIRT" →
  "Boston Silk Skirt"); grounding accepts either form.
- Occasion outfits vary per occasion and prefer mid-priced pieces.
- Book cards name the primary author only; descriptions drop Goodreads librarian notes.
- Pairings: 1984 gets Victory Gin, in the style of The Great Gatsby's gin rickey.
- "What can I make?" ranks a drink missing its base spirit below one missing a mixer or
  garnish.
- Docs: `docs/README.md` is an index, and the fashion research notes moved to
  `docs/research/fashion/` with their data.
- Tests mirror the layout: `tests/agents`, `tests/app` and `tests/domains/<domain>`.

### Removed

- The contribution system, since Curio is a solo project: `CONTRIBUTING.md`, the issue forms,
  the pull request template, `CODEOWNERS`, the `PR title` workflow and `.github/rulesets/`.
  CI, the Release workflow, Dependabot and `SECURITY.md` stay.
- `docs/session-handoff.md` and `docs/engineering-workflow.md`, replaced by
  `docs/how-it-was-built.md`.
- `scripts/take_screenshots.py`; `scripts.capture_readme` writes the README images.

### Fixed

- Release: the image job publishes whenever ghcr.io lacks the current version's image, so a
  failed push (1.3.0's first push was denied) is repaired by the next run.
- Approval cards show the cleaned product name ("Jamie Skinny Cropped", not "Jamie Skinny
  Cropped Trs Pid").

## [1.3.0] - 2026-11-06

### Added

- **Container image** on the GitHub Container Registry: every release publishes
  `ghcr.io/yo1am1/curio:X.Y.Z` and `:latest` (smoke-tested before the push) through a
  `release` deployment environment.
- **Repository rules as code**: `CONTRIBUTING.md` (trunk-based flow, squash merges,
  Conventional Commit titles, the release procedure), `SECURITY.md`, a pull request template,
  issue forms, `CODEOWNERS`, Dependabot for `uv.lock`, GitHub Actions and the Docker base
  image, and importable rulesets for `main` and the `v*` tags (`.github/rulesets/`).
- `PR title` workflow: a pull request title must be a Conventional Commit, since it becomes
  the squash commit on `main`.

### Changed

- The Release workflow runs only after CI passes on `main` and releases the exact commit CI
  tested; it fails when the version has no changelog section. Long-lived release and archive
  branches are gone: `main` is the only branch, and releases are tags.

## [1.2.1] - 2026-11-06

### Added

- README overview animation (`docs/screenshots/curio-overview.webp`), recorded by the new
  `scripts.record_overview`, plus a centred header with release, licence and Python
  badges, a contents line and a domain table.

### Changed

- Every README image has rounded corners, matching the architecture diagram: screenshots
  are WebP with transparent corners (`scripts.readme_images`, used by
  `scripts.capture_readme`), about a third smaller than the JPEGs they replace; the overview
  is an animated WebP (1 MB) rather than a 4 MB GIF.
- CI: `actions/checkout` v5 (Node 24).
- Removed 22 superseded development screenshots from `docs/screenshots`;
  `scripts.take_screenshots` now writes to `e2e-artifacts/dev-screenshots/` by default.

### Fixed

- CI failed after every check had passed since v1.1.0: the quick-start API step left a
  `uv run` process in the background holding uv's cache lock, so setup-uv's post-job cache
  prune timed out after 5 minutes. The step now starts uvicorn from the venv and stops it
  on exit.

## [1.2.0] - 2026-11-05

### Added

- **Book → cocktail pairing** ("Pour me something to read with"), cross-domain reasoning
  between books and cocktails:
  - `pairings` package: `BookCocktailPairer` maps a book's genres (strongest first), reader
    shelves and title or description words (settings such as Paris or the Caribbean, eras
    such as the Jazz Age or Victorian England) to weighted flavour themes, 24 in
    `pairings/themes.py` (noir → bitter whiskey, classics → gin, sherry or port, romance →
    sparkling and berry, fantasy → honey and herbs, sci-fi → blue curaçao, thrillers →
    coffee, beach → rum and tropical fruit, cosy → toddies and cocoa, horror → absinthe and
    blood-red, ...). It scores the 425 recipes with the cocktail search index (new
    `CocktailRecommender.term_scores`, `ingredients_using`, `contains_any` and
    `ingredient_similarity`), adds signature drinks, glasses and a few documented literary
    drinks (the Vesper of Casino Royale, the gin rickeys of The Great Gatsby), and draws
    diverse sets: a pick sharing a base spirit, a theme, a name word or most ingredients
    with an earlier one is penalised. `seed` cycles deterministically through the sets.
    Each pairing has 1-3 reasons citing the book and the recipe evidence. Reader-taste mode
    sums the reading list and liked genres; avoided genres never drive a profile.
  - Responsible drinking: a children's or young-adult book (by genre or reader shelf), a
    reader who asks and a remembered alcohol-free cocktail taste get "Non alcoholic" recipes
    only ("Optional alcohol" counts as alcoholic). Shots, flaming drinks, overproof spirits
    and crude names are never offered; avoided ingredients are left out. No health claims.
  - API: `POST /pairings/book-cocktail` (`book_id`, or `book_ids` with liked and avoided
    genres; `alcohol_free`, `cocktail_preferences`, `exclude_drink_ids`, `seed`, `top_k`;
    bounded, extra fields rejected) returns the flavour profile, pairings and the number of
    sets. 503 with the setup hint of the missing domain unless books and cocktails are both
    served; `services.link_domains` builds the pairer after loading.
  - Agent: the book agent's `PairCocktail` READ tool (a book by ID or title, or the reading
    list). `PairedDrinkCheck` verifies drink numbers ("drink #N"), drink names marked as
    names and recipe amounts against the drinks PairCocktail returned in the same turn;
    titles the pairer's reasons cite also count for that turn. Replies carry `pairing` and
    the drinks in `cocktails`; suggestions offer "Another pairing" and "Make it alcohol-free".
    The deterministic fallback lists the paired drinks (`Toolkit.fallback` receives the
    turn's extras).
  - UI: "Pair a drink" in the book dialog (photos, alcohol badges, reasons, an alcohol-free
    toggle and "Another pairing"; "Recipe" opens the drink on the Cocktails page), "Your
    reading list's cocktail" on the Books page, pairing cards in book chat, and a "Pair a
    cocktail with my reading list" starter.
  - Tests: pairer unit tests, golden checks on the real data, API, agent (including an
    invented drink caught by grounding) and AppTest UI tests; pairing scenarios in the
    scripted LLM stub; an E2E suite on the real catalogs with screenshots
    (`tests/e2e/test_pairing_e2e.py`, which writes the real top-3 pairings of 14 famous
    books to `pairings.json`). README screenshot `curio-pairing.jpg`
    (`scripts.capture_readme --only pairing`) and a Pairing link in `docs/architecture.svg`.

### Changed

- **Full photo index** (portfolio item 10): `scripts.fetch_photos` without `--limit` and an
  incremental `scripts.build_index` now cover 14,280 of the 14,300 sampled products (the
  pinned mirror lacks 20 photos), up from 3,586; 368 MB of downsized photos, about 25 minutes
  to fetch and 7 minutes to embed on 4 CPUs. Re-running `scripts.evaluate` on the full index
  doubles the visual-style recommender's catalog coverage (16.2% to 32.4%) while its holdout
  MAP@12 falls from 0.0077 to 0.0052, as it now ranks four times as many candidates; the
  other strategies are unchanged. Men's and children's shoes with photos rose from 13 and 3
  to 26 and 46, so outfit slots have more choice.

## [1.1.0] - 2026-11-05

### Added

- **Outfit builder** (portfolio item 4), multi-step planning with human approval in fashion:
  - `outfits.OutfitPlanner` plans 3-4 slots for an occasion (wedding, office, party, beach,
    sport, casual; summer, winter, autumn and spring variants; women, men or kids, else the
    audience of the best match) and fills each with a CLIP search restricted to the slot's
    product types and the audience's sections, so an outfit is never four pairs of trousers.
    Budget allocation is cheapest-feasible and deterministic: each slot may spend what the
    later slots do not need at minimum, with a soft fair share by slot weight. Later pieces
    prefer ones bought together with (co-purchase graph) or looking like (image embeddings)
    the pieces already chosen, and each piece says why. A budget that cannot cover every
    slot names the slot that does not fit, its cheapest price and the cheapest complete
    outfit. Swaps ("other" or "cheaper", with refining words) stay within the budget and
    never bring back a piece swapped out.
  - Agent tools `BuildOutfit` and `SwapOutfitItem` (READ). The trace streams "Plan outfit",
    one "Fill slot" step per slot and the result; "Save outfit" goes through the existing
    `SaveToShortlist` approval, narrowable per piece. Grounding verifies every piece's price
    and the outfit total (plus what is left of the budget and what a missing slot costs).
  - The engine streams the steps of a staged READ tool (a tool returning a generator), and
    `Turn.extras` holds domain results that are not catalog items.
  - API: `POST /outfits` and `POST /outfits/swap`; `TurnContext.outfit` and
    `AssistantReply.outfit`.
  - UI: an outfit card (slots in a row with photos and reasons, the total against the
    budget, "Swap" and "Cheaper" per slot, "Save outfit"), an "Outfit for an occasion"
    starter chip, and the current outfit kept in the session for follow-ups.
  - Tests: planner unit tests (plans, budget allocation, infeasible budgets, co-purchase and
    visual preference, swaps), agent tests with a scripted model, API and UI tests, outfit
    scenarios in the scripted LLM stub, and an E2E suite on the real index with screenshots
    (`tests/e2e/test_outfit_e2e.py`); README screenshot `curio-outfit.jpg`
    (`scripts.capture_readme --only outfit`).
- **Book domain** (portfolio item 6), the third domain on the shared agent engine:
  - `scripts.prepare_books` downloads goodbooks-10k from a pinned commit, checks every
    file's SHA-256 and writes `data/books.parquet` (10,000 books with clean titles, series,
    authors, genres, reader shelves, page counts, descriptions and https covers) and
    `data/book_neighbours.parquet` (the 50 strongest readers-also-liked links per book,
    precomputed from ~6M ratings). About 40 seconds; ~9 MB of output.
  - `books` package (numpy/scipy only): TF-IDF search over title, author, genres, shelves
    and description with a quality prior; "like <title>" seeds a search with that book's
    readers; genre names, mood words, "short" and "by <author>" become filters;
    item-item collaborative filtering with popularity damping ("readers who liked X also
    liked"); "Because you liked…" over several books; more by an author; browse by 30
    genres and nine moods. Every result carries human-readable reasons.
  - Book agent (`BookAssistant`): SearchBooks, InspectBooks, ReadersAlsoLiked,
    BooksByAuthor, AskShopper, and approval-gated SaveBooks (reading list) and
    RememberReadingTastes (liked genres, avoided genres and authors). Grounding checks
    book IDs, catalog titles and Goodreads ratings.
  - API: `/books/genres`, `/books/moods`, `/books/index`, `POST /books/search`,
    `POST /books/because`, `/books/{id}`, `/books/{id}/similar`, `/books/{id}/by-author`;
    `/health` adds `books_enabled` and `book_count`; chat domain `books` with
    `book_preferences` and `book_shortlist`.
  - UI: Books in the domain switch, a Books page (search, "Because you liked…", browse by
    mood), a book dialog (cover, rating, facts, genres, description, readers also liked,
    more by the author, Save, Ask Curio, Goodreads link), and per-domain book chat, reading
    list and memory.
  - Quick start: `CURIO_BOOKS=1 scripts/quickstart.sh` or `DEMO_BOOKS=1 docker compose
    --profile demo up` also prepares the books; both serve books whenever they are
    prepared. A `prepare-books` compose service (profile `tools`) for the full stack.
  - Tests for the catalog, taxonomy, recommender, prepare script, agent, grounding, API and
    UI (125 offline tests), book scenarios in the scripted LLM stub, and an E2E suite over
    the real catalog with screenshots (`tests/e2e/test_books_e2e.py`).
  - README screenshots of the Books page, the book dialog and the book agent;
    `scripts.capture_readme --only books` refreshes them.
- **One-command quick start** without the 3.5 GB H&M download:
  `docker compose --profile demo up --build`, or `scripts/quickstart.sh` with uv and no
  Docker. Both prepare the cocktail recipes on the first run and start the API and the UI.
  Chat works when `LLM_*` is set; otherwise the app says how to enable it. An optional
  `ollama` compose profile runs a local model for the demo.
- **Domain registry** (`services.DOMAINS`): every domain is optional at serving time. The API
  starts with any subset of prepared domains, including none; adding a domain is one
  `DomainSpec` entry plus its runtime, endpoints and one UI `DomainView`.
- `/health` adds `fashion_enabled`, `llm_configured` and `domains`; each `/domains` entry
  adds `item_count` and a `hint` (how to prepare the domain, or how to enable its chat).
- `ENABLED_DOMAINS` setting (comma-separated ids) to serve only some prepared domains.
- The UI lists only served domains in the header switch, opens on the first one, marks the
  others "not available" with the server's hint, registers no pages for them, and shows a
  setup page when the API serves no domain.
- Tests for fashion-less serving (services, API, UI), a quick-start browser scenario in the
  E2E suite, and a CI step that starts the real API with only the recipes.

### Changed

- **Licence:** the code is now under the Apache License 2.0 with a `NOTICE` file, so
  redistributions must credit "Curio by Yehor Trepalin" and link the repository. Versions up
  to 1.0.0 remain available under MIT. Added `CITATION.cff`.
- The scripted E2E LLM stub answers "outfit" requests without an occasion with the occasion
  question (options "Summer wedding under €80", "Office for women", "Weekend casual") and
  builds the outfit from the answer.
- `TurnContext.auto_approve` accepts up to six approval tools (two per domain).
- `AppliedEffect` serializes each kind with its own domain's fields from a table, and the
  engine applies approved effects to the turn context from a table, so a domain adds
  entries rather than code.
- Fashion endpoints return 503 with how to prepare the H&M data when fashion is not served
  (previously the API could not start without it).
- Docker: the full fashion stack is now the `fashion` profile
  (`docker compose --profile fashion up --build`); plain `docker compose up` starts nothing.
  The image creates `/app/data` for its non-root user.
- `Services` holds the loaded domains (`services.fashion`, `services.cocktails`) instead of
  flat fashion and cocktail fields.

### Fixed

- Outfit builder: a wearer named in the occasion ("wedding outfit for my husband", "for
  her") now sets the audience before the best-match guess, in `POST /outfits` and in chat,
  where it also beats remembered section filters. A companion ("dinner with my wife") does
  not set it. Previously such requests could get a women's outfit and a "from the best
  match" note.
- Book search matches the topic of a request, not its phrasing. "A gift for my mom who loves
  gardening" returned The Girl with All the Gifts and The Five Love Languages; "for a 10 year
  old who loves dragons" love stories and The Girl with the Dragon Tattoo; "world war 2"
  Night World, No. 2 and Persepolis 2; "funny books for a long flight" six titles with
  "Long". Words about the person or occasion (`REQUEST_WORDS`: gift, mom, loves, year old,
  long, flight…) are dropped when anything else describes the book, bare one- or two-digit
  numbers always are, and a title counts (and earns "Title matches") only when it matches two
  query words or the query names the whole title. A reader's age picks the Children's (up to
  12) or Young adult (up to 17) genre, "kid" means Children's, "world war 2"/"wwii"/"second
  world war" match the readers' WWII shelves, and "gardening" also matches botany and farming
  shelves. Results matched only in the description now say so ("Description mentions …")
  instead of "Loved by readers". Golden-query tests on a small catalog and on the real one
  (E2E). An empty neighbour file no longer crashes the recommender.
- Book search is diverse: at most two books per author and one per series (its earliest
  book the search found), in plain and "like <title>" searches. "fantasy" returned seven
  Harry Potter books in the top 10 and "fantasy like The Name of the Wind" five Sanderson
  books in the top 6, Mistborn #2 and #3 among them. A query that names the series or the
  author ("harry potter", "by Brandon Sanderson") keeps all its books.
- Book grounding no longer rejects correct answers over ordinary words that are also one-word
  catalog titles: "Beloved by fans, The Hitchhiker's Guide to the Galaxy [id 54]…",
  "Remember that…" or a mid-sentence "Paradise" fell back to the plain list. A one-word title
  is checked only where the answer marks it as a title: bold, italic or quoted, next to its
  "[id N]", or followed by "by <author>". The answer prompt asks for bold titles.
- Outfit builder: the fallback search (a slot's other types, used when the occasion's own find
  nothing) leaves out types that do not suit the occasion (`OFF_OCCASION`): three "cheaper
  shoes" swaps on a €80 summer wedding ended at "Yaz flip flop (1)" for €1.99. A piece that is
  not the type its slot's query names now says so instead of "Best match for …": a men's
  "first date dinner" accessory reads "Closest accessory to “leather belt” within what is
  left of the budget: a bracelet, not a belt.", and a fallback piece "Not the kind
  “elegant heeled sandals” asks for (sandals or heels): none is available within the budget,
  so this is the closest shoe instead."
- Outfit builder: a swap kept only the new piece's reason and dropped the reasons of the
  unchanged pieces and the outfit's notes. `OutfitPiece.reason` and `OutfitState.notes` now
  carry them through the client-held state, bounded (300 characters, four notes). They are
  client-supplied, so they are display-only: marked `carried`, never sent to the model and
  never grounding evidence.
- Book covers: a third of the books (3,332, The Lies of Locke Lamora among them) had no
  cover. `prepare_books` now keeps a checked ISBN-13 (`isbn`, 9,277 books; goodbooks-10k's
  ISBN-10 lost its leading zeros and its ISBN-13 is a rounded float, so the ISBN-10 is
  padded, checksummed and converted), and the UI shows the Open Library cover for a book
  without a Goodreads one, after a cached HEAD request, so a missing cover (404) or a
  network error shows the placeholder, never a broken image. A `books.parquet` prepared
  without the column still loads (no ISBNs). Credited "Some covers: Open Library" in the UI
  and the README. Re-run `scripts.prepare_books` to add the ISBNs.
- README "Data and licences" and the technical guide's book Terms said covers load from
  Goodreads and descriptions stay local, while three committed README screenshots show
  covers and a description; they now say those screenshots show covers and a description
  excerpt for illustration only, not licensed by this repository.
- The README said book titles in answers are checked; it now says titles from the catalog
  are, and which are not (unknown titles, short or unmarked one-word titles, prose claims).

### Security

- `scripts/quickstart.sh` binds the UI to 127.0.0.1 like the API; it previously listened on
  every interface, so anyone on the network could use the chat and the configured LLM key.
  `UI_HOST=0.0.0.0` opens it to the network on purpose and prints a warning.

### Known limitations

- Outfit coherence comes from per-slot CLIP queries, co-purchases and photo similarity; there
  is no colour-harmony or style model. Occasion detection is keyword-based (English), and
  the partial photo index has few men's (13) and children's (3) shoes.

- Book genres and moods come from Goodreads reader shelves (crowd labels). A third of the
  books have no Goodreads cover in the dataset; the UI falls back to Open Library by ISBN,
  except for 121 of them without a usable ISBN and any Open Library does not have. 9 % have
  no page count; a length filter leaves those out.
- The Docker `demo` and `fashion` profiles still publish ports 8000 and 8501 on every host
  interface (Docker's default); only the uv quick start is local-only.
- Book grounding checks titles from the catalog only. It does not check titles that are
  not in the catalog, single-word titles shorter than six letters ("It", "Dune"), one-word
  titles the answer does not mark as titles (bold, italic, quoted, next to their ID or
  followed by "by <author>"), or plot, year and page claims in prose.
- The book data's licence is CC BY-SA 4.0; covers and descriptions are the publishers' and
  Goodreads' material (see README "Data and licences").
- The first Docker build or `uv sync` still installs PyTorch, which only fashion uses.
- The Ollama profile was checked with `docker compose config` only, not run against a model.

## [1.0.0] - 2026-11-04

Curio now covers two domains, fashion and cocktails, with one agent engine.

### Added

- **Agentic engine** shared by both domains (`chat/engine.py`):
  - Policy-gated tools: READ tools run directly, ASK ends the turn with a question,
    APPROVE tools only propose a change. Tool arguments are strict, bounded schemas;
    invalid calls return a tool error and nothing runs.
  - Human-in-the-loop approvals: proposals are HMAC-SHA256 signed (tool, arguments,
    originating request, the proposing turn's search filters, issue time), expire after
    30 minutes and can only be narrowed. A forged, widened or expired proposal gets HTTP 409.
    Optional per-tool auto-approval.
  - Clarifying questions with quick replies (AskShopper).
  - Grounding checks against tool evidence, one self-correction request, then a
    deterministic answer built from catalog facts.
  - Budgets: at most five tool calls and 90 seconds per turn, checked before every model
    call including the self-correction call.
  - A streamed step trace (`POST /chat/stream`, NDJSON) shown live and kept on each answer.
  - Answer feedback (`POST /chat/feedback`, `GET /chat/feedback`).
- **Cocktail domain**:
  - `scripts.prepare_cocktails`: downloads 425 TheCocktailDB recipes from a pinned
    commit of yo1am1/cocktail_advisor, checks the SHA-256 and writes `data/cocktails.parquet`.
    Serving is offline: no model, index or network call.
  - Search by name, flavour words or ingredients with alcohol and category filters,
    similar drinks by ingredient family, and "What can I make?" pantry matching with up to
    three missing ingredients. Every result carries its reasons.
  - Cocktail agent tools: SearchCocktails, InspectCocktails, SimilarCocktails, WhatCanIMake,
    SaveCocktails and RememberTastes. Grounding checks drink IDs, drink names and recipe
    amounts.
  - Safety: alcohol status labelled on cards and in answers, alcohol-free means "Non alcoholic"
    only ("Optional alcohol" recipes count as alcoholic), no allergen or health claims,
    no encouragement of excessive drinking, "Please drink responsibly." on cocktail pages.
  - API: `GET /domains`, `GET /cocktails/ingredients`, `GET /cocktails/categories`,
    `POST /cocktails/search`, `POST /cocktails/pantry`, `GET /cocktails/{id}`,
    `GET /cocktails/{id}/similar`; `/health` adds `cocktails_enabled` and `cocktail_count`;
    `ChatRequest.domain` (`fashion` by default) with cocktail shortlist and tastes.
- **Domain switch** in the UI header. Each domain has its own navigation, chat history,
  shortlist and memory. The Cocktails page has recipe search, "What can I make?" and a
  recipe dialog with similar drinks.
- Docker: a one-off `prepare-cocktails` compose service (profile `tools`) and the
  `anthropic` extra in the image.
- README screenshots of both domains and the agent (answer, approval, memory, cocktail
  chat), captured from the real data, and this changelog.
- An opt-in browser end-to-end suite (`uv run pytest -m e2e tests/e2e`) over the real API,
  UI, data and CLIP index, with a scripted OpenAI-compatible LLM server and a screenshot
  for every step.

### Fixed

From the end-to-end run on the real H&M sample:

- An empty "related pieces" result no longer clears the cards on screen, and tells the
  agent to try another relation (about a fifth of products have no bought-together evidence).
- On phones the floating assistant is a compact icon button, and pages leave room to scroll
  their last controls clear of it.
- A conversation's first answer no longer renders in the small welcome-sized chat panel,
  and chat answer cards keep names to two lines so their buttons line up.

Fixes from the v1.0 release audit:

- Agent: a resumed turn keeps the proposing turn's filters and remembered preferences;
  approved changes apply to later searches in the same turn; remembering preferences no
  longer empties the shown items; the self-correction call respects the time budget;
  malformed edited decisions and non-ASCII signatures return 409 instead of 502;
  measures such as "1/00 oz" no longer fail a turn; cocktail grounding no longer reads
  "Martini" in "Martini Glass" as a drink; pantry words map to catalog spelling
  ("tonic" → "Tonic Water"); avoided ingredients in agent tools use the same plural and
  spelling normalisation as search; a just-approved value replaces the remembered one
  within the turn; when memory is full the server and UI both keep the newest values.
- Cocktail search: plurals and spelling variants ("limes", "whisky"), flavour words,
  alcohol-free phrases in the query, bounded ingredient terms (HTTP 422 for blank or
  over-long items), and similar drinks by ingredient family.
- UI: remembered values are capped like the API limits, with a notice naming what was
  dropped; an approval card is resolved only after the API answers and can be retried
  on failure; shortlist additions report duplicates and the 20-item limit once; search
  and pantry inputs survive page switches; accessible labels for remove buttons and photos.
- Docker: the Hugging Face cache directory is created for the non-root user, so the
  named volume is writable and CLIP can download.
- Package version, description and API version now say 1.0.0; the README CI badge points
  at `release/v1.0`; the docs describe both domains and the cocktail data terms.

### Known limitations

- The agent has not been evaluated with a live LLM at scale; tests use scripted models.
- Answer feedback is kept in memory and lost when the API restarts.
- The fashion photo index covers 3,537 of 14,300 catalog products.
- Fashion prices are historical H&M values scaled to approximate euros; there is no live
  stock or checkout.
- Cocktail ingredient filters and avoided ingredients only cover the listed recipe
  ingredients. They are not allergen or dietary guarantees.
- Some pantry requests miss recipes that use a more specific ingredient name
  ("whiskey, lemon, sugar" does not find Whiskey Sour, which lists "Blended whiskey").
- Streamlit sets each photo's alt text to its position ("0"); the item name is added as a
  visually hidden caption instead.
- Cocktail data and images are TheCocktailDB's and are not relicensed by this project.

## [0.2.0] - 2026-11-02

Fashion MVP ("fashion edition"): personalized recommendations from historical H&M
purchases with a learned ranker and buy-again baseline, CLIP text and photo search,
product connections from co-purchases and Shopify categories, offline evaluation, and a
tool-using shopping assistant with a conversation home and floating panel. How it was
built is summarised in [docs/how-it-was-built.md](docs/how-it-was-built.md).

[Unreleased]: https://github.com/yo1am1/curio/compare/v3.0.0...HEAD
[3.0.0]: https://github.com/yo1am1/curio/compare/v2.1.1...v3.0.0
[2.1.1]: https://github.com/yo1am1/curio/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/yo1am1/curio/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/yo1am1/curio/compare/v1.3.0...v2.0.0
[1.3.0]: https://github.com/yo1am1/curio/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/yo1am1/curio/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/yo1am1/curio/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/yo1am1/curio/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/yo1am1/curio/compare/96dfa33...v1.0.0
[0.2.0]: https://github.com/yo1am1/curio/tree/96dfa33
