# Multi-domain contract (fashion + cocktails)

Shared interfaces for the v1.0 cocktail domain. Every teammate codes against this file;
change it only together with every consumer.

## Data

`scripts/prepare_cocktails.py` reads `final_cocktails.csv` from
https://github.com/yo1am1/cocktail_advisor (`data/cocktails/final_cocktails.csv`, 425 recipes,
columns `,id,name,alcoholic,category,glassType,instructions,drinkThumbnail,ingredients,
ingredientMeasures,recipes`; `ingredients`/`ingredientMeasures` are Python-literal lists, parse
with `ast.literal_eval`, never `eval`). It accepts `--source PATH_OR_URL` (default: the raw
GitHub URL pinned to commit `92e05ae48b3f2af9f62ca5fdcd23b7b7c2f3fba6`, checked against SHA-256
`fac8041d2ac5450632113dfc516a5e52e90646f85b07f8edafbf2f8798aa3394`; a mismatch fails the run)
and `--sha256 HEX` (checks any source; another `--source` is not checked without it). It
writes `data/cocktails.parquet` with columns:

| column | type | notes |
|---|---|---|
| drink_id | int | positive, unique; `id + 1` from the CSV (CSV ids start at 0) |
| name | str | stripped |
| alcoholic | str | `Alcoholic`, `Non alcoholic` or `Optional alcohol` (see below) |
| category | str | e.g. `Cocktail`, `Ordinary Drink`, `Shot` |
| glass | str | |
| instructions | str | |
| image_url | str or null | only `https://www.thecocktaildb.com/...` kept |
| ingredients | str | JSON list of `{"name", "measure"}` (measure null when blank) |

`source_url` is the constant attribution link `https://www.thecocktaildb.com/` (the CSV ids are
not TheCocktailDB drink ids, so per-drink pages cannot be linked). UIs credit
"Recipe data: TheCocktailDB". The data is not committed; it is downloaded like the H&M data.

Settings: `Settings.cocktails_path -> data_dir / "cocktails.parquet"`. The domain is enabled
when that file exists. No API key and no network at serving time.

## Python interfaces (`clothes_recommender.cocktails`)

Types: `cocktails/domain.py` (already written: Cocktail, Ingredient, CocktailHit,
PantryMatch, CocktailPreferences, CocktailQuery, AlcoholFilter, IngredientTerm).

- `Cocktail.alcohol_free` is True only for `alcoholic == "Non alcoholic"`. `Optional alcohol`
  recipes list alcoholic ingredients (wine, rum, Galliano), so every filter treats them as
  alcoholic; they are still labelled "Alcohol optional".
- `IngredientTerm` (stripped, 1-60 characters) bounds every item of
  `CocktailQuery.ingredients`/`exclude_ingredients` and
  `CocktailPreferences.liked_ingredients`/`avoided_ingredients`; blank or longer items are a
  validation error (HTTP 422).

`cocktails/catalog.py`:

```python
class CocktailCatalog:
    def __init__(self, frame: pd.DataFrame) -> None: ...  # columns as in the table above
    @classmethod
    def from_parquet(cls, path: Path) -> "CocktailCatalog": ...
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[Cocktail]: ...
    def get(self, drink_id: int) -> Cocktail | None: ...
    def get_many(
        self, drink_ids: Iterable[int]
    ) -> list[Cocktail]: ...  # order kept, unknown skipped
    def ingredients(self) -> list[str]: ...  # sorted distinct ingredient names (catalog spelling)
    def categories(self) -> list[str]: ...
    def canonical_ingredient(self, name: str) -> str | None: ...  # case/space-insensitive lookup
```

`cocktails/recommend.py` (pure numpy/python, deterministic, ties by lower drink_id):

```python
class CocktailRecommender:
    def __init__(self, catalog: CocktailCatalog) -> None: ...
    def search(self, query: CocktailQuery) -> list[CocktailHit]:
        ...
        # TF-IDF cosine over name, category, glass and ingredient names (+ name boost),
        # scaled by the share of query words the recipe uses; words are normalised for
        # plurals and spelling variants ("limes", "whisky" -> "whiskey"), flavour words
        # ("fresh", "creamy", "fruity", ...) stand for ingredient hints, and "alcohol-free",
        # "non-alcoholic" or "mocktail" set alcohol=non_alcoholic when it is "any".
        # `ingredients` must all be in the recipe; `exclude_ingredients` must all be absent
        # (same normalisation: "eggs" excludes "Egg"); filter terms are normalised once per
        # query. alcohol: non_alcoholic keeps Cocktail.alcohol_free ("Non alcoholic" only),
        # alcoholic keeps the rest. Empty query with filters ranks by filter match then name.
        # reasons e.g. "Contains lime, mint", "Alcohol-free".

    def similar(
        self, drink_id: int, top_k: int = 5, alcohol: AlcoholFilter = "any"
    ) -> list[CocktailHit]:
        ...
        # IDF-weighted overlap of ingredient families (cosine; "Lime" ~ "Lime juice",
        # "White rum" ~ "Light rum"); excludes the drink itself;
        # reasons "Shares rum, lime juice". Unknown id -> [].

    def pantry(
        self,
        ingredients: Sequence[str],
        *,
        alcohol: AlcoholFilter = "any",
        max_missing: int = 2,
        top_k: int = 10,
    ) -> list[PantryMatch]:
        ...
        # Ingredient matching is case- and plural-insensitive and treats "Lime" as covering
        # "Lime juice" only via an explicit small alias table (document it). Rank: fewest missing, then
        # coverage, then name. Empty pantry -> [].
```

## Agent (`clothes_recommender.chat`)

The plan → act → verify loop, budgets, approvals (signed proposals), streaming steps and
grounding stay shared. Cocktail tools:

| Tool | Policy | Args |
|---|---|---|
| SearchCocktails | read | query, ingredients, alcohol, category |
| InspectCocktails | read | drink_ids (1-5) |
| SimilarCocktails | read | drink_id, alcohol |
| WhatCanIMake | read | ingredients (1-15), alcohol, max_missing (0-3) |
| AskShopper | ask | shared |
| SaveCocktails | approve | drink_ids (1-5), note |
| RememberTastes | approve | alcohol_free, liked_ingredients, avoided_ingredients |

Remembered tastes fill missing filters (alcohol-free, avoided ingredients) like fashion memory.
Grounding for cocktails: drink IDs and catalog cocktail names mentioned in the answer must have
been returned by tools (or be in history/shortlist); ingredient amounts must appear in the
evidence recipes. Never claim allergen safety, medical effects or that alcohol is safe; label
alcohol status; no encouragement of excessive drinking.

## Serving: every domain is optional

`services.DOMAINS` is the registry (display order): one `DomainSpec(id, label, setup_hint,
runtime_type, load)` per domain. `load(settings, kit)` returns the domain's runtime, or `None`
while its data is missing; a runtime has `assistant: AgentEngine | None`, `item_count()` and
`close()` (`FashionServices`, `CocktailServices`). `Services.domains` maps ids to the loaded
runtimes; `services.fashion` / `services.cocktails` are typed shortcuts. Fashion is served when
`products.parquet`, `users.parquet` and `purchases.parquet` exist; cocktails when
`cocktails.parquet` loads. `ENABLED_DOMAINS` (comma-separated ids) limits what is loaded; an
unknown id is a startup error. Adding a domain: one `DomainSpec`, its id in
`chat.schemas.Domain`, its endpoints behind a dependency like `FashionDep`, and one `DomainView`
in `ui.DOMAIN_VIEWS`.

## API

- `GET /health` adds `cocktails_enabled: bool` and `cocktail_count: int`; since the domain
  registry also `fashion_enabled: bool`, `llm_configured: bool` and `domains` (the `/domains`
  list). The flat fashion fields (`indexed_products`, `catalog_products`, `chat_enabled`,
  `ranking_enabled`) read `0`/`false` when fashion is not served.
- `GET /domains` -> one entry per registered domain, in display order:
  `{"id": "fashion", "label": "Fashion", "enabled": bool, "chat_enabled": bool,
  "item_count": int, "hint": str | null}`, then the same for `cocktails`. `hint` says how to
  prepare a domain that is not served, or how to enable chat for a served one without it.
- Fashion endpoints (`/products/*`, `/sections`, `/search/*`, `/users*`, `/demo/customers`,
  `/evaluation`) return 503 with the fashion setup hint when fashion is not served.
- `GET /cocktails/ingredients` -> `list[str]`; `GET /cocktails/categories` -> `list[str]`.
- `GET /cocktails/{id}` -> `Cocktail` (404 unknown).
- `POST /cocktails/search` body `CocktailQuery` -> `list[CocktailHit]`.
- `GET /cocktails/{id}/similar?top_k=5&alcohol=any` -> `list[CocktailHit]`.
- `POST /cocktails/pantry` body `{"ingredients": [...], "alcohol": "any", "max_missing": 2, "top_k": 10}`
  -> `list[PantryMatch]`.
- All cocktail endpoints return 503 `"Cocktail recipes are not prepared: run scripts.prepare_cocktails."`
  when the catalog is missing.
- `POST /chat` and `/chat/stream`: `ChatRequest.domain: Literal["fashion", "cocktails"] = "fashion"`.
  Cocktail context fields on `TurnContext`: `cocktail_preferences: CocktailPreferences`,
  `cocktail_shortlist: list[int]` (max 20). `auto_approve` also accepts `SaveCocktails`,
  `RememberTastes`. 503 when the requested domain is not served (its setup hint) or has no
  agent (how to enable chat).
- `AssistantReply` adds `domain` and `cocktails: list[Cocktail]` (empty for fashion; `products`
  empty for cocktails). `intent` stays (cocktail turns fill `SearchIntent(query=message)`).
- `AppliedEffect.kind` adds `"cocktail_shortlist"` (ids in `drink_ids: list[int]`) and
  `"cocktail_preferences"` (`cocktail_preferences: CocktailPreferences`).
- `PendingAction.products` stays; cocktail proposals fill `PendingAction.cocktails: list[Cocktail]`.

## UI

- A domain switch in the header, stored in `st.session_state["domain"]`, listing the domains
  the API serves (from `/health` `domains`); it is disabled with one domain, each domain that
  is not served shows "… is not available" with the server's hint, the app opens on the first
  served domain, and a setup page replaces the app when none is served. Pages of a domain that
  is not served are not registered.
- Navigation per domain. Fashion: Ask Curio, Fashion, Discover, Product connections.
  Cocktails: Ask Curio, Cocktails (`/cocktails`: search + "What can I make?" pantry + recipe dialog
  with similar drinks).
- Chat history, shortlist and memory are kept per domain (`chat_history` stays the fashion key;
  cocktails use `chat_history_cocktails`, `cocktail_shortlist`, `cocktail_preferences`).
- Cocktail pages credit "Recipe data: TheCocktailDB" and say "Please drink responsibly."
  Images load directly from the https image URL.
