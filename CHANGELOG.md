# Changelog

All notable changes to Curio are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

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
- README screenshots of the cocktail search and pantry pages, and this changelog.

### Fixed

Fixes from the v1.0 release audit:

- Agent: a resumed turn keeps the proposing turn's filters and remembered preferences;
  approved changes apply to later searches in the same turn; remembering preferences no
  longer empties the shown items; the self-correction call respects the time budget;
  malformed edited decisions and non-ASCII signatures return 409 instead of 502;
  measures such as "1/00 oz" no longer fail a turn; cocktail grounding no longer reads
  "Martini" in "Martini Glass" as a drink; pantry words map to catalog spelling
  ("tonic" → "Tonic Water").
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

[1.0.0]: https://github.com/yo1am1/curio/compare/release/fashion-mvp...release/v1.0
[0.2.0]: https://github.com/yo1am1/curio/tree/release/fashion-mvp
