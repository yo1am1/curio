# Multi-domain contract (fashion, cocktails, books)

Shared interfaces for the cocktail domain (v1.0), the book domain and the fashion outfit
builder (unreleased). Every teammate codes against this file; change it only together with
every consumer. The [books](#books) and [fashion outfits](#fashion-outfits) sections follow
the cocktail sections.

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
in `ui.DOMAIN_VIEWS`. Books (`BookServices`) are served when `books.parquet` and
`book_neighbours.parquet` both load; a missing or broken file disables only books.

## API

- `GET /health` adds `cocktails_enabled: bool` and `cocktail_count: int`; since the domain
  registry also `fashion_enabled: bool`, `llm_configured: bool` and `domains` (the `/domains`
  list). The flat fashion fields (`indexed_products`, `catalog_products`, `chat_enabled`,
  `ranking_enabled`) read `0`/`false` when fashion is not served.
- `GET /domains` -> one entry per registered domain, in display order:
  `{"id": "fashion", "label": "Fashion", "enabled": bool, "chat_enabled": bool,
  "item_count": int, "hint": str | null}`, then the same for `cocktails`. `hint` says how to
  prepare a domain that is not served, or how to enable chat for a served one without it.
- Fashion endpoints (`/products/*`, `/sections`, `/search/*`, `/outfits*`, `/users*`,
  `/demo/customers`, `/evaluation`) return 503 with the fashion setup hint when fashion is not served.
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

## Books

### Data

`scripts/prepare_books.py` reads [zygmuntz/goodbooks-10k](https://github.com/zygmuntz/goodbooks-10k)
at commit `6dd165b555a7b47b2dd36743a425776e641ff50c` (raw GitHub URLs, or `--source` a local
checkout): `books.csv`, `ratings.csv`, `book_tags.csv`, `tags.csv` and
`books_xml/books_xml.zip` (skipped with `--no-details`). Every file is checked against its
SHA-256 at that commit (`BOOKS_CSV`, `RATINGS_CSV`, ... in the script); a mismatch fails the
run unless `--no-verify` is given. It writes, atomically:

`data/books.parquet`:

| column | type | notes |
|---|---|---|
| book_id | int | goodbooks-10k id, 1-10000 (most-rated first) |
| goodreads_book_id | int | for `https://www.goodreads.com/book/show/{id}` |
| title | str | without the "(Series, #n)" suffix |
| authors | str | JSON list; the first is the author, later ones translators or illustrators |
| series, series_position | str or null | from the title suffix ("1", "2.5", "1-3") |
| year | Int64 or null | original publication year; negative for BC |
| language | str or null | Goodreads language code |
| average_rating | float | Goodreads average, 1-5 |
| ratings_count | int | Goodreads ratings behind it |
| pages | Int64 or null | from the XML archive |
| description | str | plain text from the XML archive, at most 600 characters |
| genres | str | JSON list of `books.taxonomy.GENRES` names, strongest first, at most 4 |
| tags | str | JSON list of up to 12 descriptive shelves ("epic fantasy"), most used first |
| image_url | str or null | only `https://images.gr-assets.com/...`; placeholders dropped |
| readers | int | goodbooks-10k users who rated it |
| isbn | str or null | optional column: a checked ISBN-13 (from the zero-padded, checksummed ISBN-10), for the Open Library cover fallback; a file prepared without it loads with no ISBNs |

`data/book_neighbours.parquet` (`book_id`, `neighbour_id`, `score` float32, `co_readers`
int32): for each book its 50 strongest "readers who liked X also liked Y" links. Liked
means 4-5 stars; `score = c / (n_x^0.35 · n_y^0.65) · c / (c + 10)` with `c` readers who
liked both and `n` readers who liked each; pairs with fewer than 5 shared readers are
dropped.

Settings: `Settings.books_path -> data_dir / "books.parquet"`,
`Settings.book_neighbours_path -> data_dir / "book_neighbours.parquet"`. No API key and no
network at serving time for the API (covers load in the browser from Goodreads; for a book
without one the UI asks Open Library by ISBN and shows its placeholder when there is no
cover). UIs credit "Book data: goodbooks-10k from Goodreads (CC BY-SA 4.0)" and "Some
covers: Open Library".

### Python interfaces (`clothes_recommender.books`)

Types in `books/domain.py`: `Book` (the columns above, with `source_url` instead of
`goodreads_book_id` and `author` = first author), `BookRef`, `BookHit` (`book`, `score`,
`reasons`), `BookPreferences` (`liked_genres`, `avoided_genres`, `avoided_authors`, each at
most 10), `BookQuery` (`query` ≤ 200, `genres` ≤ 5, `mood`, `author`, `min_rating` 0-5,
`max_pages` 1-5000, `exclude_genres`, `exclude_authors`, `boost_genres`, `top_k` ≤ 50),
`BecauseYouLiked` (`liked` 1-20 ids, exclusions, `top_k`). Genre and author terms are
stripped and bounded (`GenreTerm` ≤ 60, `AuthorTerm` ≤ 100 characters; HTTP 422 otherwise).

`books/catalog.py`:

```python
class BookCatalog:
    def __init__(self, frame: pd.DataFrame) -> None: ...  # columns as above
    @classmethod
    def from_parquet(cls, path: Path) -> "BookCatalog": ...
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[Book]: ...  # book_id order
    def get(self, book_id: int) -> Book | None: ...
    def get_many(self, book_ids: Iterable[int]) -> list[Book]: ...  # order kept, unknown skipped
    def genres(self) -> list[str]: ...  # most common first
    def canonical_genre(self, name: str) -> str | None: ...
    def canonical_author(self, name: str) -> str | None: ...  # case/accent/punctuation-insensitive
    def by_author(self, name: str) -> list[Book]: ...
    def by_title(self, title: str) -> list[Book]: ...
    def authors(self) -> list[str]: ...
    def titles(self) -> list[str]: ...
```

`books/recommend.py` (numpy/scipy, deterministic, ties by lower book_id):

```python
class BookRecommender:
    def __init__(self, catalog: BookCatalog, neighbours: pd.DataFrame) -> None: ...
    def search(self, query: BookQuery) -> list[BookHit]:
        ...
        # TF-IDF over title, series, authors, genres, shelves, description; "like <title>"
        # seeds with that book's neighbours (its series and itself left out); genre words
        # and the first mood word become filters; "short" = at most 250 pages; "by <author>"
        # = author filter. Empty query: browse by filters and quality.

    def similar(
        self,
        book_id: int,
        top_k: int = 5,
        *,
        other_authors_only: bool = False,
        exclude_genres=(),
        exclude_authors=(),
    ) -> list[BookHit]:
        ...
        # Neighbours, topped up by shelf similarity. Unknown id -> [].

    def because(
        self, liked: Sequence[int], top_k: int = 10, *, exclude_genres=(), exclude_authors=()
    ) -> list[BookHit]:
        ...
        # Summed neighbour scores; seeds and their series left out; at most 2 per author.

    def by_author(self, author: str, top_k: int = 10, *, exclude_book_id=None) -> list[BookHit]: ...
    def seed_of(self, query: str) -> Book | None: ...
    def resolve_title(self, text: str) -> Book | None: ...
```

Every hit has reasons, e.g. "960 readers who liked The Name of the Wind also liked this",
"Also in The Kingkiller Chronicle", "Genre: Classics", "Mood: Cosy (shelved as cozy
mystery)", "112 pages", "A genre you like: Fantasy", "Because you liked …".

### Agent

| Tool | Policy | Args |
|---|---|---|
| SearchBooks | read | query, genres (≤ 3), mood, min_rating (1-5), max_pages (20-2000) |
| InspectBooks | read | book_ids (1-5) |
| ReadersAlsoLiked | read | book_id, other_authors_only (default true) |
| BooksByAuthor | read | author |
| AskShopper | ask | shared |
| SaveBooks | approve | book_ids (1-5), note |
| RememberReadingTastes | approve | liked_genres, avoided_genres, avoided_authors |

Remembered tastes exclude avoided genres and authors from every tool and boost liked genres
in search; an explicitly requested genre wins over a remembered avoidance. Grounding: book
IDs and catalog titles in the answer must have been returned by tools (or be in history,
the reading list or typed), and ratings must match an evidence book's Goodreads average.

### API

- `GET /health` adds `books_enabled: bool` and `book_count: int`; `/domains` lists `books`
  third.
- `GET /books/genres`, `GET /books/moods` -> `list[str]`; `GET /books/index` ->
  `list[BookRef]`, most rated first.
- `POST /books/search` body `BookQuery` -> `list[BookHit]`;
  `POST /books/because` body `BecauseYouLiked` -> `list[BookHit]`.
- `GET /books/{id}` -> `Book` (404 unknown); `GET /books/{id}/similar?top_k=5&other_authors_only=false`
  and `GET /books/{id}/by-author?top_k=5` -> `list[BookHit]`.
- Every `/books` endpoint returns 503 `"The book catalog is not prepared: run scripts.prepare_books."`
  when the domain is not served.
- `ChatRequest.domain` accepts `"books"`; `TurnContext` adds `book_preferences:
  BookPreferences` and `book_shortlist: list[int]` (max 20, the reading list);
  `auto_approve` accepts `SaveBooks` and `RememberReadingTastes` (up to six tools in all);
  `ChatTurn.book_ids` (max 5) carries the cards an answer showed.
- `AssistantReply.books: list[Book]`; `PendingAction.books`; `AppliedEffect.kind` adds
  `"book_shortlist"` (`book_ids`) and `"book_preferences"` (`book_preferences`). Each
  effect kind is serialized with its own domain's fields only.

### UI

- The header switch lists Books third. Navigation: Ask Curio, Books (`/books`: Search,
  "Because you liked…" seeded from the reading list, Browse by mood; a book dialog with
  cover, rating, facts, genres, description, shelves, Save, Ask Curio, a Goodreads link,
  "Readers also liked" and "More by <author>").
- Session keys: `chat_history_books`, `book_shortlist` (reading list), `book_preferences`,
  `chat_queued_books`, `chat_decision_books`. The Books page sends remembered avoidances as
  exclusions and liked genres as boosts, like the agent.
- Book pages credit "Book data: goodbooks-10k from Goodreads (CC BY-SA 4.0)" and say ratings
  are Goodreads averages. Covers load directly from Goodreads (the 318 px variant of the
  dataset's link on cards and dialogs).

## Fashion outfits

The outfit builder is fashion-only and adds fields; nothing existing changes shape.

### Python interfaces (`clothes_recommender.outfits`)

- `SlotName = Literal["top", "bottom", "dress", "outerwear", "shoes", "bag", "accessory"]`,
  `Audience = Literal["women", "men", "kids"]`, `SwapMode = Literal["other", "cheaper"]`.
- `OutfitState(occasion: str (1-200), budget: float | None (0 < b ≤ 10,000), audience,
  pieces: list[OutfitPiece(slot, product_id, reason: str (≤ 300))] (≤ 5), skipped:
  list[int] (≤ 20), notes: list[str (≤ 300)] (≤ 4))`: what a client holds and sends back.
  The server re-reads every piece from the catalog; `reason` and `notes` are display-only
  (shown again after a swap, never sent to the model, never grounding evidence).
- `Outfit(occasion, audience, budget, total, status: "complete" | "partial" | "empty",
  slots: list[OutfitSlot], notes: list[str], notes_carried: bool, minimum: float | None,
  state: OutfitState)`; `OutfitSlot(slot, label, product: Product | None, reason, carried:
  bool, issue, cheapest)`; `carried` marks a reason that came back from the client. An empty slot
  has an `issue` and, when the budget is what kept it out, its `cheapest` price. `minimum`
  is the cheapest complete outfit when the budget cannot cover one. `Outfit.amounts()`
  lists the euro figures an answer may state besides piece prices.
- `OutfitPlanner(search, catalog, *, vectors=None, links=None)`: `search.by_text` as
  `ProductSearch`, `vectors.get_vectors(ids)` as `ProductVectorStore`, and
  `links.bought_together(product_id) -> Mapping[int, (baskets, lift)]` as
  `GraphRecommender`. `build(OutfitRequest) -> Generator[PlannedSlots | FilledSlot, None,
  Outfit]`, `outfit(request) -> Outfit`, `swap(state, slot, mode, refine, *,
  max_item_price) -> Swap(outfit, slot, old, new | None)` (raises `OutfitError` for a slot
  the outfit lacks or an outfit with no known piece), `restore(state) -> Outfit | None`.

### Agent

| Tool | Policy | Args |
|---|---|---|
| BuildOutfit | read | occasion (2-200), budget (total, optional), audience (optional), slots (2-5 distinct, optional) |
| SwapOutfitItem | read | slot, mode (`other` default, `cheaper`), refine (≤ 100) |

`SaveToShortlist` (up to five IDs) saves an outfit through the existing approval flow.
A READ tool may now return a generator: the engine streams each yielded `AgentStep` and
records the returned `(result, step)` last. `BuildOutfit` streams "Plan outfit", one "Fill
slot" per slot, then "BuildOutfit". `Turn.extras` holds domain results that are not catalog
items (the fashion toolkit keeps the outfit there). Fashion grounding also allows the
outfit's `amounts()`, for the outfit built this turn and the one the client sent.

### API

- `TurnContext.outfit: OutfitState | None`; `AssistantReply.outfit: Outfit | None` (set when
  the turn built or changed an outfit).
- `POST /outfits` body `OutfitQuery(occasion, budget, audience, slots, max_item_price)` ->
  `Outfit`; `POST /outfits/swap` body `OutfitSwap(outfit: OutfitState, slot, mode, refine)` ->
  `Outfit` (422 for a slot the outfit does not have). Both return 503 without fashion.

### UI

- Session key `chat_outfit`: the latest reply's `outfit.state`, sent as `outfit` with every
  fashion chat request and cleared by "New conversation".
- A turn with an outfit shows an outfit card (one column per slot with photo, name, price,
  colour and reason; the total against the budget; notes), and product cards only for
  products outside the outfit. On the latest turn each filled slot has "Swap" and "Cheaper"
  and the card has "Save outfit"; all three send follow-up messages to the agent.
- Fashion starters add "Outfit for an occasion".
