# Multi-domain contract (fashion, cocktails, books, evening)

Shared interfaces for the cocktail domain (v1.0), the book domain and the fashion outfit
builder (1.1.0). Every teammate codes against this file; change it only together with
every consumer. The [books](#books), [fashion outfits](#fashion-outfits),
[book → cocktail pairing](#book--cocktail-pairing) and [Plan my evening](#plan-my-evening)
sections follow the cocktail sections.

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

## Python interfaces (`curio.domains.cocktails`)

Types: `domains/cocktails/models.py` (already written: Cocktail, Ingredient, CocktailHit,
PantryMatch, CocktailPreferences, CocktailQuery, AlcoholFilter, IngredientTerm).

- `Cocktail.alcohol_free` is True only for `alcoholic == "Non alcoholic"`. `Optional alcohol`
  recipes list alcoholic ingredients (wine, rum, Galliano), so every filter treats them as
  alcoholic; they are still labelled "Alcohol optional".
- `IngredientTerm` (stripped, 1-60 characters) bounds every item of
  `CocktailQuery.ingredients`/`exclude_ingredients` and
  `CocktailPreferences.liked_ingredients`/`avoided_ingredients`; blank or longer items are a
  validation error (HTTP 422).

`domains/cocktails/catalog.py`:

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

`domains/cocktails/recommend.py` (pure numpy/python, deterministic, ties by lower drink_id):

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

## Agent (`curio.domains.cocktails.agent`)

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

`app.services.DOMAINS` is the registry (display order): one `DomainSpec(id, label, setup_hint,
runtime_type, load)` per domain. `load(settings, kit)` returns the domain's runtime, or `None`
while its data is missing; a runtime has `assistant: AgentEngine | None`, `item_count()` and
`close()` (`FashionServices`, `CocktailServices`). `Services.domains` maps ids to the loaded
runtimes; `services.fashion` / `services.cocktails` are typed shortcuts. Fashion is served when
`products.parquet`, `users.parquet` and `purchases.parquet` exist; cocktails when
`cocktails.parquet` loads. `ENABLED_DOMAINS` (comma-separated ids) limits what is loaded; an
unknown id is a startup error. Adding a domain: one `DomainSpec`, its id in
`app.schemas.Domain`, its endpoints behind a dependency like `FashionDep`, and one `DomainView`
with its pages in the web app's registry (`web/src/domains/registry.ts`). Books (`BookServices`) are served when `books.parquet` and
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

The UI is the React app in `web/` (served by the API, see the
[technical guide](technical-guide.md#web-app)).

- A domain switch in the header lists every registered domain from `/domains`: a domain that
  is not served stays visible but disabled, with "… is not available" and the server's hint.
  The app opens on Plan my evening when the evening is served, else on the first served
  domain's main page, and a setup page replaces the app when none is served. Routes of a
  domain that is not served lead back to the landing.
- Pages per domain, each with its chat (`/ask/<domain>`) in the sub-navigation. Fashion: For
  you (`/fashion`), Discover, Product connections. Cocktails: recipe search (`/drinks`: search
  and a recipe dialog with similar drinks) and "What can I make?" (`/drinks/pantry`).
- Chat transcripts, shortlists and memory are kept per domain: the session (lists, memory,
  outfit, evening, approval toggles, linked shopper) in `localStorage` (`curio.session.v1`),
  transcripts for the browser tab (`curio.chat.v1` in `sessionStorage`).
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

### Python interfaces (`curio.domains.books`)

Types in `domains/books/models.py`: `Book` (the columns above, with `source_url` instead of
`goodreads_book_id` and `author` = first author), `BookRef`, `BookHit` (`book`, `score`,
`reasons`), `BookPreferences` (`liked_genres`, `avoided_genres`, `avoided_authors`, each at
most 10), `BookQuery` (`query` ≤ 200, `genres` ≤ 5, `mood`, `author`, `min_rating` 0-5,
`max_pages` 1-5000, `exclude_genres`, `exclude_authors`, `boost_genres`, `top_k` ≤ 50),
`BecauseYouLiked` (`liked` 1-20 ids, exclusions, `top_k`). Genre and author terms are
stripped and bounded (`GenreTerm` ≤ 60, `AuthorTerm` ≤ 100 characters; HTTP 422 otherwise).

`domains/books/catalog.py`:

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

`domains/books/recommend.py` (numpy/scipy, deterministic, ties by lower book_id):

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

- The header switch lists Books third. Pages: Search (`/reading`), "Because you liked…"
  (`/reading/because`, seeded from the reading list) and Browse by mood (`/reading/moods`);
  a book dialog with cover, rating, facts, genres, description, shelves, Save, Ask Curio, a
  Goodreads link, "Readers also liked" and "More by <author>".
- The session holds the reading list (`readingList`) and reading tastes
  (`bookPreferences`). The Books pages send remembered avoidances as exclusions and liked
  genres as boosts, like the agent.

## Fashion outfits

The outfit builder is fashion-only and adds fields; nothing existing changes shape.

### Python interfaces (`curio.domains.fashion.outfits`)

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
- `OutfitRequest(occasion, budget, audience, slots, max_item_price, queries)`: `queries`,
  `(slot, query)` pairs, replace the occasion template's query for those slots (types and
  weights stay); `plan_slots(occasion, audience, slots, queries)` takes the same pairs.

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

- The session's `outfit`: the latest reply's `outfit.state`, sent as `outfit` with every
  fashion chat request and cleared by "New conversation".
- A turn with an outfit shows an outfit card (one column per slot with photo, name, price,
  colour and reason; the total against the budget; notes), and product cards only for
  products outside the outfit. On the latest turn each filled slot has "Swap" and "Cheaper"
  and the card has "Save outfit"; all three send follow-up messages to the agent.
- Fashion starters add "Outfit for an occasion".

## Book → cocktail pairing

A cross-domain feature over the books and cocktails domains; nothing existing changes shape
except the additions below.

### Python interfaces (`curio.domains.pairings`)

- `models.py`: `PairingRequest(book_id | book_ids (≤ 20), liked_genres, avoided_genres (≤ 10
  `GenreTerm`s each), alcohol_free, cocktail_preferences: CocktailPreferences | None,
  exclude_drink_ids (≤ 15), seed (0-10,000), top_k (1-5))`, extra fields forbidden, and at
  least a book or a liked genre. `BookPairing(profile: FlavourProfile, pairings:
  list[Pairing], seed, sets, note)`; `FlavourProfile(source, themes: list[ThemeWeight(id,
  label, style, weight, evidence, books)], summary, alcohol_free, alcohol_free_reason,
  book_ids)`; `Pairing(cocktail, score, theme, reasons (1-3))`.
- `themes.py`: `Theme(id, label, style, terms, soft_style, soft_terms, genres, shelves,
  words, signatures, glasses, setting, strength)`, `THEMES` (24 themes plus a fallback) and
  `LORE` (author, drink, text). Every signature, glass and lore drink is a real catalog name
  (checked on the real data).
- `pairer.py`: `BookCocktailPairer(books: BookCatalog, cocktails: Iterable[Cocktail],
  recommender: CocktailRecommender, *, table=THEMES, lore=LORE)`; `pair(request) ->
  BookPairing` raises `UnknownBookError` (a `book_id` not in the catalog) or `PairingError`
  (no known book or genre); `themes_of(book, avoided_genres) -> dict[str, float]`.
- `CocktailRecommender` adds `term_scores(weights) -> {drink_id: cosine}`,
  `ingredients_using(drink_id, words) -> [ingredient names]`, `contains_any(drink_id,
  ingredients) -> bool` and `ingredient_similarity(a, b) -> float`.

Rules: alcohol-free (only `Cocktail.alcohol_free`, never "Optional alcohol") for a children's
or young-adult book by genre or reader shelf, `alcohol_free`, or an alcohol-free
`cocktail_preferences`; never shots, slammers, flaming drinks, overproof spirits or crude
names; avoided ingredients excluded; avoided genres never drive a profile; deterministic,
ties by the lower drink ID; `seed` selects set `seed mod sets`.

### Serving and API

- `BookServices.pairing: BookCocktailPairer | None` and `Services.pairing`, set by
  `services.link_domains` when books and cocktails are both loaded; the book assistant gets
  PairCocktail through `BookAssistant.attach_pairing(pairer, cocktails)`.
- `POST /pairings/book-cocktail` body `PairingRequest` -> `BookPairing`; 404 unknown book,
  422 nothing known to pair or an invalid body, 503 with `BOOKS_MISSING` or
  `COCKTAILS_MISSING` when either domain is not served.

### Agent

| Tool | Policy | Args |
|---|---|---|
| PairCocktail | read | exactly one of book_id, title (≤ 200), use_reading_list; alcohol_free; exclude_drink_ids (≤ 15) |

The book agent reads `TurnContext.cocktail_preferences` for pairings. `AssistantReply.pairing:
BookPairing | None`, with its drinks also in `AssistantReply.cocktails` for a book reply.
Answers cite drinks as "drink #N" and bold names; `PairedDrinkCheck` verifies them against
this turn's PairCocktail result only. `Toolkit.fallback(items, extras)` receives the turn's
extras.

### UI

- Pairing controls appear when `/domains` reports books and cocktails served. The book
  dialog's "Pair a drink" opens a pairing panel with "Alcohol-free only" and "Another
  pairing" (the next `seed`); "Recipe" opens the drink's recipe in the same dialog, with Back
  to the book.
- Books page: "Your reading list's cocktail" when the reading list or liked genres are set. Book chat sends `cocktail_preferences`, stores `pairing` on the
  turn and renders it as a card; the starters add "Pair a cocktail with my reading list".

## Plan my evening

A composed domain, `curio.domains.evening`, the only one that may import `fashion`, `books`,
`cocktails` and `pairings` (`tests/test_architecture.py`; no domain imports `evening`).
Nothing existing changes shape except the additions below; other domains' replies have no
`evening` key.

### Python interfaces (`curio.domains.evening`)

- `themes.py`: `ThemeId = Literal["cosy", "romantic", "glamorous", "summery", "mysterious",
  "thoughtful", "adventurous", "playful"]`; `Theme(id, label, icon, words, feeling,
  outfit_occasion, slots, queries, men_queries, books: tuple[BookSearch, ...], drink_words,
  drink_feeling)` with `queries_for(audience)`; `BookSearch(query, genres, mood, min_rating,
  exclude_genres)`; `THEMES`, `THEMES_BY_ID`, `DEFAULT_THEME` (cosy), `FALLBACK_BOOKS`;
  `detect_theme(occasion, mood=None) -> ThemeMatch(theme, words, source: "mood" |
  "occasion" | "default")` with `evidence(occasion)`.
- `models.py`: `EveningTastes(book_preferences, cocktail_preferences, max_item_price)`;
  `EveningRequest(EveningTastes)` adds `occasion` (2-200, not blank), `budget` (0 < b ≤
  5,000, the outfit's), `audience` (`women` | `men`), `mood` (≤ 40) and `alcohol_free`; extra
  fields are forbidden. `EveningState(occasion, mood, theme, budget, audience, alcohol_free,
  outfit: OutfitState | None, book_id, drink_id, skipped_books (≤ 20), skipped_drinks (≤
  15))`: what a client holds and sends back. `EveningPlan(occasion, theme: ThemeInfo(id,
  label, icon, feeling, evidence), outfit: OutfitPart(outfit: Outfit, reason) | None, book:
  BookPart(book, reason) | None, drink: DrinkPart(cocktail, reasons (≥ 1), pairs_with,
  evening_fit, alcohol_free_reason, note) | None, why, missing: list[MissingPart(part,
  reason)], notes, status: "complete" | "partial" | "empty", change: str | None, state)`;
  `EveningPlan.amounts()` is the outfit's. `EveningSwap(evening, part: "outfit" | "book" |
  "drink", slot, mode: "other" | "cheaper" | "alcohol_free", refine (≤ 100), tastes)`.
- `planner.py`: `EveningPlanner(*, outfits: OutfitPlanner | None, books: BookRecommender |
  None, book_catalog: BookCatalog | None, pairer: BookCocktailPairer | None, cocktails:
  CocktailCatalog | None)`; `parts -> ["outfit", "book", "drink"]` (those it can plan),
  `plan(EveningRequest) -> EveningPlan`, `restore(state, tastes) -> EveningPlan`,
  `swap(state, part, *, slot, mode, refine, tastes) -> EveningPlan` (raises `EveningError`
  for a swap the plan cannot make). `budget_in(text)`, `wants_alcohol_free(text)` read a
  budget and an alcohol-free wish from the occasion. `OUTFIT_NEEDS_FASHION`,
  `BOOK_NEEDS_BOOKS`, `DRINK_NEEDS_COCKTAILS` and `DRINK_NEEDS_BOOK` are the `missing`
  reasons.

Rules: the drink always comes from `BookCocktailPairer.pair` on the plan's book (the client's
drink is kept on restore only when the pairer still pairs it); alcohol-free and the
children's and young-adult rule as in pairings; children's books are never the evening's
book; remembered avoided genres and authors win over a theme's genre; the outfit stays
within the budget; a part that cannot be planned is in `missing`, never an error;
deterministic.

### Serving and API

- `DomainSpec` gains `compose(domains, kit) -> runtime | None` (a composed domain has no
  `load`) and `missing(services) -> str`; `DomainSpec.hint(services)` is the 503 detail and
  the `/domains` hint. `EVENING` (`id="evening"`, label `Evening`) composes
  `EveningServices(planner, assistant)` whenever books and cocktails are served and their
  pairer built; `item_count()` is the number of parts it plans, and its `notice` (the
  outfit's `OUTFIT_NEEDS_FASHION` without fashion) is appended to its `/domains` hint.
  `ENABLED_DOMAINS` accepts `evening` and limits only data domains; naming `evening`
  enables the domains it builds on (`DomainSpec.builds_on`: books, cocktails and, when
  prepared, fashion).
- `POST /evening/plan` body `EveningRequest` -> `EveningPlan`; `POST /evening/swap` body
  `EveningSwap` -> `EveningPlan` (422 for an `EveningError` or an invalid body). Both 503 with
  `EVENING_MISSING` naming the missing books or cocktails.
- `Domain` adds `"evening"`, `ApprovalTool` adds `"SaveEvening"` (`auto_approve` ≤ 7),
  `TurnContext.evening: EveningState | None`, `AppliedEffect` adds `EveningEffect(kind=
  "evening", product_ids, book_ids, drink_ids)` (adds to `shortlist`, `book_shortlist`,
  `cocktail_shortlist`), and `AssistantReply.evening: EveningPlan | None`, serialized last and
  only for evening replies.

### Agent

| Tool | Policy | Args |
|---|---|---|
| PlanEvening | read | occasion (2-200), budget (≤ 5,000), audience (`women`, `men`), mood (≤ 40), alcohol_free |
| SwapEveningPart | read | part (`outfit` needs a slot; `book`, `drink` take none), mode (`other`, `cheaper` for the outfit only, `alcohol_free` for the drink only), refine (≤ 100) |
| AskShopper | ask | shared |
| SaveEvening | approve | product_ids (≤ 5), book_ids (≤ 2), drink_ids (≤ 2), at least one |

PlanEvening streams "Evening theme", "Evening outfit", "Evening book", "Evening drink" (or a
skipped step per missing part), then "PlanEvening". Answers cite pieces as "product #N",
books as "book #N" or "[id N]" and drinks as "drink #N"; `EveningGroundingCheck` merges the
fashion, book and paired-drink checks. Turn items are products, books and drinks keyed in
separate ranges (`BOOK_KEY`, `DRINK_KEY`).

### UI

- The evening's registry entry (`web/src/domains/registry.ts`): label "Evening", the Plan my
  evening page (`/evening`, where a visit lands), chat titled "Plan my evening.", approval
  setting `ask_shortlist` -> `SaveEvening`.
- The session's `evening`: the latest evening reply's `evening.state`, sent as `evening` with
  every evening chat request (with every domain's tastes and lists) and cleared by "New
  conversation". The planner page keeps its plan for the browser tab.
- The plan shows the theme badge and evidence, the "why it works" line, three cards (outfit,
  book, drink) with swap buttons, and Save evening. On the page the buttons call
  `/evening/swap` and Save evening opens an approval dialog with a checkbox per part; in chat
  they send "Another top for my evening", "Cheaper shoes", "Another book", "Another drink",
  "Make the drink alcohol-free" and "Save the evening" to the agent. An applied `evening`
  effect adds each part to its own list.
