# Changelog

All notable changes to Curio are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- **The Python package is now `curio`** (`src/curio`), not `clothes_recommender`: imports,
  `uvicorn curio.api:create_app`, `streamlit run src/curio/ui.py`, the Docker image, the
  Compose services, `scripts/quickstart.sh` and CI use the new name. The PyPI project stays
  `curio-recommendations`. A small `curio.meta` shim keeps sniffio (anyio, httpx) from
  mistaking this package for the Curio async library, which shares the import name.

### Fixed

- Release: the image job publishes whenever ghcr.io lacks the current version's image, not
  only right after the release is created, so a failed push (1.3.0's first push was denied
  while ghcr.io linked the new package) is repaired by the next run.

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
tool-using shopping assistant with a conversation home and floating panel. Earlier
development is recorded in [docs/engineering-workflow.md](docs/engineering-workflow.md)
and [docs/session-handoff.md](docs/session-handoff.md).

[Unreleased]: https://github.com/yo1am1/curio/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/yo1am1/curio/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/yo1am1/curio/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/yo1am1/curio/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/yo1am1/curio/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/yo1am1/curio/compare/96dfa33...v1.0.0
[0.2.0]: https://github.com/yo1am1/curio/tree/96dfa33
