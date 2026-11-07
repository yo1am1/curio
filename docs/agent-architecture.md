# Curio agent: architecture and review

This page describes how Curio's agent plans, acts, verifies and hands control back to
the shopper, in the fashion, cocktail and book domains, and in "Plan my evening", which
composes them. It also records the review of
the recommendation system and the earlier assistant that led to this design.

## One engine, four toolkits

The loop lives once, in `curio.engine` (`engine/agent.py`, `AgentEngine`). Each domain
plugs in a `Toolkit` (`engine/toolkit.py`): its tool schemas and policies, planning, how
READ tools run, how APPROVE effects are validated, the grounding check, the deterministic
fallback and the reply fields. `BaseToolkit` gives every optional hook (context cleaning,
history, planning, refresh, proposal display, `vet_proposal`, suggestions) a no-op default, so
a toolkit subclass writes only what its domain needs; every domain toolkit and the tea example
subclass it.

The code has three layers, and `tests/test_architecture.py` enforces their boundaries:

- `curio.engine`: the loop, the `Toolkit` protocol and `BaseToolkit`, policies and the shared `AskShopper`,
  signed proposals (`ActionSigner`, `narrow_args`), the generic grounding machinery (price and
  ID checks, name matching, `merge_reports`), the chat model factory and the wire models every
  turn shares. It imports no domain and not the app.
- `curio.domains.<name>`: a domain's catalog and recommenders beside its `agent/` package
  (toolkit, tools, prompts, grounding check and session schemas: its context fields, card IDs
  and approved effects). A domain imports the engine and itself; only the composed domains
  build on others: `pairings` on `books` and `cocktails`, and `evening` on `fashion`,
  `books`, `cocktails` and `pairings`.
- `curio.app`: settings, the domain registry and the API, which also serves the React web
  app (`web/`). `app/schemas.py` combines
  every domain's payloads into the one `/chat` wire format, and `app/assistants.py` binds
  each toolkit to the engine with it (`Schemas`).

[`examples/hello_domain`](../examples/hello_domain) is a complete small domain on the same
engine, without the app.

| | Fashion (`ShoppingAssistant`) | Cocktails (`CocktailAssistant`) | Books (`BookAssistant`) | Evening (`EveningAssistant`) |
|---|---|---|---|---|
| Toolkit | `domains/fashion/agent/` | `domains/cocktails/agent/` | `domains/books/agent/`; PairCocktail from `domains/pairings/agent/` | `domains/evening/agent/` |
| Planning | Structured `SearchIntent` from the LLM, then an initial catalog search | The message is the query; no extra model call before the tool loop | As cocktails | As cocktails; the plan step lists the remembered tastes of every domain |
| Grounding | Prices and colours bound to their product, totals, differences and budgets, product IDs and names, other currencies, percentages, stock, delivery and safety claims | Drink IDs and names, recipe amounts and ingredients bound to their drink, alcohol labels, allergen and health claims | Book IDs and titles, authors, ratings, page counts (and their totals) and years bound to their book; prices, store and age-safety claims; paired drinks as in cocktails | All of these, against the plan's parts |
| Reply items | `products` | `cocktails` | `books`; a pairing's drinks in `cocktails` and `pairing` | The plan in `evening`; its pieces, book and drink in `products`, `books`, `cocktails` |

The engine owns everything shared: the tool-call and time budgets, policy enforcement,
signed proposals and resume, clarifying questions, merging approved effects into the
turn, the self-correction step, the streamed trace and the error mapping. `POST /chat`
picks the assistant by `ChatRequest.domain` (default `fashion`) from the loaded domains in
the domain registry (`app.services.DOMAINS`, see the
[technical guide](technical-guide.md#domain-registry)). Every domain is optional: a request
for a domain the server does not serve gets 503 with how to prepare it, and a served domain
without an LLM gets 503 saying how to enable chat. Every assistant is built once at startup
from one shared `AgentKit` (the chat model and the approval signer), so a proposal is bound
to its tool's domain but verifies on any. A new domain's assistant is a `Toolkit` for the
same engine, built by its loader from that kit.

Books were the test of that claim. The book toolkit needed no change to the loop, budgets,
signing, resume, grounding flow, streaming or UI chat code. The book domain declares its own
session fields (`book_preferences`, `book_shortlist` on `BookContext`), card IDs and approved
effects (`BookEffect`, which names the context field each effect field updates); the app adds
them to its wire models and the book approval tools to `ApprovalTool`.

The book → drink pairing is the one cross-domain feature. The book toolkit registers
PairCocktail and exposes a `DrinkPairing` extension point; the pairings domain implements it
(`CocktailPairing`) with the pairer and `PairedDrinkCheck`, and the app plugs it in when both
books and cocktails are served. So `books` never imports `cocktails` or `pairings`.

"Plan my evening" is the composition showcase: a domain whose toolkit uses three others'
recommenders through one deterministic planner, with no change to the engine. Its toolkit's
items are products, books and drinks together (`Turn[Product | Book | Cocktail]`, keyed in
separate ID ranges), its effect `EveningEffect` names three context fields in `adds`, so the
engine's existing effect merging saves one approval to three lists, and its grounding is the
three domains' checks merged, plus three of its own: catalog drink names in any case and
unmarked ("pour a mojito"; names that are also ordinary words, such as "Manhattan" or
"Thriller", only in a drink context; names inside the plan's products, books, authors or own
text are allowed), "<title> by <author>" against that book's authors, and prices in another
currency ($, £), which never match the euro catalog. A save (SaveEvening) may name only the current
plan's parts: the toolkit's `vet_proposal` hook, which the engine calls before it applies or
signs any APPROVE action, refuses other IDs with a tool message. The hook is generic and
optional, so the engine stays domain-free.

## One turn

```mermaid
flowchart TD
    M[Shopper message] --> P[Plan: structured intent + approved memory]
    P --> S[Initial catalog search]
    S --> L{Tool loop<br/>max 5 calls, 90 s}
    L -- READ --> R[Domain READ tools<br/>fashion: SearchCatalog · InspectProducts · FindRelated · PersonalPicks · BuildOutfit · SwapOutfitItem<br/>cocktails: SearchCocktails · InspectCocktails · SimilarCocktails · WhatCanIMake<br/>books: SearchBooks · InspectBooks · ReadersAlsoLiked · BooksByAuthor · PairCocktail<br/>evening: PlanEvening · SwapEveningPart]
    R --> L
    L -- ASK --> G{Question grounded?}
    G -- yes --> Q[AskShopper: question + quick replies]
    G -- "no (once)" --> L
    G -- "no (twice)" --> Q2[A general question, no quick replies] --> W
    L -- APPROVE --> A{Auto-approve<br/>allowed?}
    A -- no --> H[Signed proposal: approve, narrow or decline]
    A -- yes --> X[Apply effect] --> L
    L -- answer --> V[Grounding check: domain evidence]
    V -- unverified --> C[One self-correction] --> V2{Verified?}
    V2 -- no --> F[Deterministic catalog facts]
    V -- verified --> D[Reply + trace + suggestions]
    V2 -- yes --> D
    Q --> W[Turn ends, waiting for shopper]
    H --> W
```

Every phase emits an `AgentStep` (kind, tool, summary, status, duration). The UI
streams them from `POST /chat/stream` as NDJSON, shows live progress, then keeps a
collapsed "How Curio worked" trace on each answer. A READ tool that works in stages returns
a generator instead of a `(result, step)` pair: the engine streams each step it yields as it
happens and records the returned step last. A `ToolError` raised midway keeps the steps
already streamed and ends with an error step. `BuildOutfit` uses this for one step per slot.

## Tools and policies

Each tool is a strict pydantic schema (`extra="forbid"`, bounded lists and lengths).
Invalid calls return a tool error to the model; nothing runs. The policy, not the
model, decides what a valid call may do.

### Fashion

| Tool | Policy | Backed by |
|---|---|---|
| SearchCatalog | read | CLIP text search; keeps budget, audience, type and colour filters |
| InspectProducts | read | Exact catalog rows for up to five IDs |
| FindRelated | read | Graph recommender (complement, alternative) or CLIP item-to-item (similar), kept in the product's audience unless the shopper asks for another (`audience`) |
| PersonalPicks | read | Learned ranker when deployed, collaborative filtering otherwise, with their reasons |
| BuildOutfit | read | `OutfitPlanner`: plans 3-4 slots for the occasion and fills each under one total budget; streams a step per slot |
| SwapOutfitItem | read | Replaces one slot of the current outfit ("other" or "cheaper", optional refining words) within the budget |
| AskShopper | ask | Ends the turn with a question and 2–4 quick replies |
| SaveToShortlist | approve | Validated product IDs; the shopper approves, narrows or declines |
| RememberPreferences | approve | Values mapped to catalog vocabulary; unknown values are a tool error |

`FindRelated` and `PersonalPicks` reach the recommendation system through
`RecommenderNavigator`. The agent sees ranked IDs plus the same evidence the browsing
pages show ("3 shoppers who bought … also bought this"), so explanations stay grounded.

`FindRelated` keeps every relation in the source product's audience
(`toolkit.related_sections`, from the outfit builder's `SECTION_AUDIENCE` and
`AUDIENCE_SECTIONS`): a Menswear piece gets Menswear, a Ladieswear or Divided piece gets
Ladieswear and Divided (both womenswear), a Baby/Children piece gets Baby/Children. Sport has
no audience in the catalog (it is not split by gender), so a Sport piece is not restricted.
The model passes `audience` only when the shopper asks for another one ("women's pieces like
this men's sweater"). The look-alike search filters the photo index by section; the graph
relations read up to 200 neighbours and keep those in the sections, with the same reasons.
The browsing endpoint `GET /products/{id}/similar` keeps its unfiltered photo neighbours:
it has no shopper or conversation, and the product page shows it as the photo index's view
of the piece, so a cross-section look-alike is information there, not a wrong answer.

Prices are euros and Curio has no exchange rates. When the shopper writes an amount in
dollars or pounds ("under $30", "£80", "150 USD", "50 quid"), a budget is used as a euro
budget of the same number, never converted (`domains/fashion/currency.py`): it becomes the
price limit when the intent extractor left it out, and a "Currency" trace step, the session
prompt and every tool result of the turn carry the note "Prices are in euros; I used 30 as a
€ budget. You gave it in US dollars, and Curio does not convert currencies.", so the answer
says it too. An amount that was not used as the budget gets a reminder instead.

#### Outfits: multi-step planning with human approval

"Build me a summer outfit for a wedding under €80" is one `BuildOutfit` call. The planner
(`domains/fashion/outfits.py`, described in the [technical guide](technical-guide.md#outfit-builder)) plans
slots from the occasion (a women's wedding: dress, shoes, bag, accessory), searches each
slot's candidates with CLIP restricted to its product types and the audience's sections,
checks that the cheapest pieces fit the budget, then fills the slots in order. Each slot may
spend what the later slots do not need at minimum, and prefers pieces bought together with,
or looking like, the ones already chosen. The trace shows the plan ("Planned 4 slots for
“summer wedding”: dress, shoes, bag, accessory · for women · budget €80.00"), each slot
("Filled shoes: Paloma · €12.99. Best match for “elegant heeled sandals”.") and the result
("Outfit ready: 4 pieces for €77.96 of a €80.00 budget."). A slot the budget cannot cover
is shown as "Could not fill …" with its cheapest price, never dropped silently.

The tool's audience is the one the shopper gave, else a wearer named in the occasion
("wedding outfit for my husband"), else the one their section filters or remembered
sections point to, else the section of the best match (the answer says so). A
remembered budget (`max_price`) caps every piece. The server keeps no outfit: the reply
carries `outfit` with its `state`, the UI sends that back as `TurnContext.outfit`, and the
toolkit re-reads every piece and price from the catalog. The session prompt lists the
current outfit, so "cheaper shoes" or "another top" become `SwapOutfitItem` calls, and
"Save outfit" becomes a `SaveToShortlist` proposal with every piece, which the shopper can
narrow in the approval card. The model is told to quote the tool's total and never add
prices up itself.

### Cocktails

| Tool | Policy | Backed by |
|---|---|---|
| SearchCocktails | read | `CocktailRecommender.search`: query, required ingredients, alcohol, category |
| InspectCocktails | read | Full recipes (ingredients with amounts, glass, method) for up to five drink IDs |
| SimilarCocktails | read | Ingredient-family overlap with one drink |
| WhatCanIMake | read | Pantry matching for 1–15 ingredients, up to three missing |
| AskShopper | ask | Shared with fashion |
| SaveCocktails | approve | 1–5 validated drink IDs for the cocktail shortlist |
| RememberTastes | approve | Alcohol-free, liked and avoided ingredients, mapped to catalog spelling |

Remembered tastes fill missing filters on every search: an alcohol-free taste sets
`non_alcoholic`, and avoided ingredients become exclusions. WhatCanIMake maps each pantry
word to catalog spelling first ("tonic" becomes "Tonic Water"); ambiguous or unknown words
pass through unchanged.

Safety rules are in the cocktail system prompt and in the toolkit: the agent labels alcohol
status, offers alcohol-free drinks to anyone underage, pregnant or driving, does not
encourage excessive drinking, and never claims allergen safety or health effects.
Ingredient filters only cover listed recipe ingredients. The deterministic fallback adds
"Please drink responsibly." when a listed drink contains alcohol, and the cocktail pages
show the same line.

### Books

| Tool | Policy | Backed by |
|---|---|---|
| SearchBooks | read | `BookRecommender.search`: query (including "like <title>"), genres, mood, minimum rating, maximum pages |
| InspectBooks | read | Full details (description, genres, shelves, rating, pages, year, series) for up to five book IDs |
| ReadersAlsoLiked | read | Item-item collaborative filtering from goodbooks-10k ratings; other authors only by default |
| BooksByAuthor | read | The author's books in the catalog, most rated first |
| PairCocktail | read | `BookCocktailPairer`: drinks for one book (ID or title) or the reading list and remembered tastes, with reasons; `alcohol_free`, `exclude_drink_ids` for another pairing |
| AskShopper | ask | Shared with fashion |
| SaveBooks | approve | 1–5 validated book IDs for the reading list |
| RememberReadingTastes | approve | Liked genres, avoided genres and avoided authors, mapped to catalog names |

Remembered tastes apply to every tool: avoided genres and authors are left out, and liked
genres rank higher (a 10 % boost, with the reason "A genre you like"). A genre the reader
asks for in the same call wins over a remembered avoidance of it. Genres and moods must
be catalog names; an unknown one is a tool error that lists the valid ones, so the model
can retry. The book that a "like <title>" search started from counts as evidence, so the
answer may name it. The prompt tells the model to quote ratings exactly, to say ratings are
Goodreads averages and not to reveal endings.

#### Cross-domain: pairing a drink with a book

PairCocktail is the agent's one cross-domain tool. "What should I drink with The Name of the
Wind?" is one call with the title; "pair a cocktail with my reading list" uses the reading
list, liked genres and avoided genres from the turn context. It reads the same deterministic
pairer as `POST /pairings/book-cocktail` ([technical guide](technical-guide.md#book--cocktail-pairing)),
so the agent cites the pairer's reasons rather than inventing its own. The tool is
registered with every book agent; when the server does not serve cocktails it returns a tool
error saying pairing is unavailable, and the session prompt says so.

Safety follows the cocktail rules: a children's or young-adult book is always alcohol-free,
whatever the model asks; the cocktail domain's remembered tastes (`TurnContext.cocktail_preferences`,
which the UI sends with book requests) apply, so an alcohol-free taste keeps every pairing
alcohol-free and avoided ingredients stay out; the prompt adds the cocktail safety rules
(alcohol status for each drink, no encouragement of excessive drinking, no health or allergen
claims), and the fallback ends with the pairer's note ("Please drink responsibly."). The
reply carries the drinks in `cocktails` and the explained `pairing`; its suggestions are
"Another pairing", "Make it alcohol-free" and readers-also-liked. For another pairing the
model sees the drinks earlier answers showed ("Drinks shown (an earlier pairing): drink #269
Old Fashioned") and passes them as `exclude_drink_ids`.

### Evening

| Tool | Policy | Backed by |
|---|---|---|
| PlanEvening | read | `EveningPlanner.plan`: occasion (plus budget, audience, mood, alcohol-free); streams a step for the theme, the outfit, the book and the drink, and one per missing part |
| SwapEveningPart | read | `EveningPlanner.swap` on the plan the client holds: another or a cheaper outfit piece (part outfit with its slot), another book (its drink is re-paired), another or an alcohol-free drink |
| AskShopper | ask | Shared; used when the request names no occasion ("plan my evening") |
| SaveEvening | approve | Validated product, book and drink IDs; one `EveningEffect` adds them to the fashion shortlist, the reading list and the saved cocktails. The shopper can untick any of them |

Remembered tastes from every domain apply to every call: avoided and liked genres and avoided
authors to the book, the alcohol-free taste and avoided ingredients to the drink, the
per-piece price limit to the outfit, and a remembered menswear or womenswear section chooses
the audience. The server keeps no plan: the reply carries `evening` with its `state`, the UI
sends it back as `TurnContext.evening`, and the toolkit restores it from the catalogs (and
re-pairs the drink) for the session prompt, so "another book", "cheaper shoes" or "make the
drink alcohol-free" become SwapEveningPart calls and "save the evening" a SaveEvening
proposal with every part. Children's and young-adult books always get alcohol-free drinks,
as in pairings; the prompt adds the cocktail safety rules, and the fallback ends with the
pairer's note.

## Human in the loop

The API is stateless. The shortlist, remembered preferences, linked shopping history
and approval policy live on the client and are sent with each request (`TurnContext`).

1. When the agent calls an approval tool, the server validates the arguments against
   the catalog and returns `status="needs_approval"` with a `pending_action`.
2. The proposal is HMAC-SHA256 signed over its ID, tool, arguments, originating
   request, the proposing turn's search filters (`intent`), issue time and the client's
   `session_id` (a random id the web app keeps per tab and sends in every request). It
   expires after 30 minutes. On resume the engine rebuilds the turn from those filters.
3. The client answers with `decision: {action, approved, edited_args}` and the same
   `session_id`. Edits may only remove values (a subset of product IDs, dropped
   preferences). A forged, modified, widened or expired proposal, or one sent from another
   session, is rejected with HTTP 409.
4. Each proposal is answered once. The signer's `NonceStore` records an answered proposal
   until it expires, so approving or declining it again gets HTTP 409 ("already
   answered"). A refused edit does not count as an answer, and when the turn fails before
   its reply reaches the client (a provider error, a dropped connection), the proposal is
   released so the shopper can answer it again.
5. On approval the server returns an `applied` effect for the client to store, merges it
   into the turn's context the way the UI does, and the agent continues the turn to
   confirm. Later searches in that turn use the approved change. An effect that touches
   no items (remembering preferences) leaves the shown cards in place. A resumed turn
   cannot pause for another proposal or question.

Shoppers can opt into auto-approval per tool in the session panel. Auto-approved
actions still run through the same validation and appear in the trace. `auto_approve` is a
user preference held by the client, like the shortlist: the server takes it as sent. The
signature therefore protects against the model approving its own proposals and against
forged, widened, replayed or moved proposals; it does not, and cannot, protect the server
from its own client, which may opt into auto-approval. Effects are only returned to the
client, so this is fine for Curio; a server-side effect (an order, an email) would need a
server-held policy per user instead.

`AGENT_SIGNING_KEY` sets the signing key (at least 32 bytes). Without it, a random
per-process key is used, so pending proposals expire on restart. With several API workers
(`WEB_CONCURRENCY` above 1) the API refuses to start without it. The default nonce store
(`InMemoryNonceStore`) lives in one process, so with several workers a proposal could be
answered once per worker; pass `ActionSigner(..., nonces=<a shared store>)` that implements
the two-method `NonceStore` protocol (`claim`, `release`) over Redis or a database, or run
one worker.

## Grounding guardrail

Grounding checks the facts an answer states against what this turn's tools returned. It is
a deterministic check, not a model: regular expressions find the facts, and each fact is
tied to the item it is about before it is compared. The engine's part (`curio.engine.grounding`)
knows no domain: items, references, binding, number framing, names, labels and the report.
Each domain supplies its vocabulary: how answers cite its items, which numbers its items
carry, which labels matter and which claims no data can support.

### What is checked

**Facts are bound to items.** An item is referenced by its citation (`[id 3]`, `product #12`,
`book #31`, `drink #107`) or its name (a product's display or raw name, a book's title or
series, a drink's name). A word of four or more letters that occurs in only one item's
name also counts ("the jeans" for Slim Jeans) when its sentence names no item in full. A
fact binds to the nearest reference before it in its sentence or bullet, else the nearest
after it, else the last item named in an earlier sentence of the same line, else the last
item named anywhere before, else the only item the answer names (or the only item in the
evidence). The fact must then match *that* item:

| Fact | Domains | Rule |
|---|---|---|
| Price | fashion, evening | That product's price, to the cent; a whole euro may round it ("about €35" for €34.99). "Under €30", "over €20" and "about €25" compare instead of matching. |
| Rating | books, evening | That book's Goodreads average at the precision written (4.6 may round 4.55). "★ 4.55", "rated", "scores", "a Goodreads average of", "4.5/5", "on Goodreads", and in book answers any bare 1.0–5.99 decimal. |
| Page count, year | books, evening | That book's pages (within 5%, "a 300-page novel") and publication year ("published in", "from", "(1985)"). |
| Recipe amount | cocktails, pairings, evening | An amount in that drink's recipe ("1 1/2 oz"). |
| Percentage | fashion, evening | Must appear in that product's description ("100% cotton"). |
| Colour | fashion, evening | A colour word within a few words of a product in its sentence ("**Tambov** – navy", "the **Paris Trench** (beige)", "the navy **Tambov**", or a "Colour:" line under it) must fit its catalog colour. A plain word covers the catalog's shades ("beige" for Light Beige, "white" for Off White), but a shade must be the catalog's ("light grey" is not Grey); "navy" is Dark Blue, "gray" grey, "golden" gold. Colours in a hedged or negated clause ("if you'd like black", "not navy"), about the shopper ("your white sneakers"), across a turn ("black, but the **Waverly hoodie**"), of a part or another thing ("a gold buckle", "a white shirt") or of food ("orange juice") are not read. |
| Ingredient | cocktails, pairings, evening | An ingredient said to be in a drink must be in its recipe: the sentence names the drink and a recipe word ties them ("uses", "made with", "is", a colon, dash or table cell before the list), or it is "<ingredient>-based", or its sentence or bullet lists amounts or only ingredients ("- 8 oz ginger beer" under the drink). A name may be more or less precise than the recipe's ("rum" for Light rum, "lime juice" for Lime), and a few synonyms count (white/light rum, Cointreau/triple sec). The shopper's own ("your ginger beer"), hedged or negated mentions ("if you want ginger beer", "not ginger beer"), substitutes ("swap in"), ingredients in drink or glass names and tool text the answer repeats are not read; nor are ice and water. |
| Author | books, evening | "<title> by <name>" must name one of that book's authors. |
| Alcohol label | cocktails, pairings, evening | "Alcohol-free", "non-alcoholic", "mocktail" only for a drink marked non-alcoholic; "contains alcohol" for one with alcohol or alcohol optional; "alcohol optional" only for an optional one. Negated labels flip ("not alcohol-free"). A plural label with no drink in its sentence ("All of these are alcohol-free:") covers the list after it. |

A number bound to no item is allowed only if it equals a value the tools stated without an
item: an outfit's or evening's total, its budget, what is left, the cheapest option of a
gap, a price filter or a remembered limit. Otherwise it is reported as *unattributed*. An
unbound "under €30" must hold for every item the answer names.

**Totals, differences and multiples are verified, not whitelisted.** A price or page count
that matches no item may be a sum or a difference of the items' values. It is accepted when
its words frame it so and it adds up: a total anywhere in its sentence ("together that's
€74.98", "Buying both would be €46.98", "| **Total** | €23.97 |", "2,786 pages in total"),
a difference in its own clause ("so the Waverly is €5.00 cheaper", "but €5 less", "saves
you €5", "466 pages longer", "€6.98 over your budget" against the shopper's budget), or a
multiple ("4 pairs for €23.96", "two of them come to €11.98"). The operands are the items
the sentence names, or, when it names fewer than two, the items the answer names (any two
or more of them for a total); an answer that names none may give the total of everything
the tools returned. It must match to the cent, or to the precision written ("€76" for
€75.96, "about 1,200 pages" for 1,157, but not "about 1,203"). A wrong total or difference
is reported. A difference never makes a discount: "€5 off", "on sale" or "discounted"
anywhere in the sentence is a discount claim, so the audit's "€5 off" probe is still caught.
A price stated for one item stays that item's price even in a sentence that says "cheaper"
("the cheaper option at €19.99" is checked against that product). When a sentence names n
items and then lists n facts of one kind joined only by "and", commas or "respectively"
("rated 4.53 and 4.46 respectively"), the k-th fact binds to the k-th item.

**What the shopper typed is not evidence.** A number the shopper typed may appear only as
their own figure: in a budget or limit ("under €30", "your €100 budget", "within €40", "a
€50 limit"), attributed to them ("the €4.99 your friend saw", "you mentioned €4.99"),
contradicted ("€8.99, not €4.99", "isn't rated 4.5"), or compared against in a clause that
names no item and is not about a single one ("the last two are over €50"). "Yes, it's
€4.99" or "Yes, the dress is under €20" for an €8.99 dress binds the figure to the dress
and is reported. A question
or quick reply may suggest a budget ("Something under €20?"). Names the shopper typed may
be repeated ("*The Seven Moons of Maali Almeida* isn't in the catalog").

**Names.** Each check keeps its closed-vocabulary pass (catalog titles and drink names that
this turn did not return are reported). Besides that, every bold, italic or quoted span that
reads as a name, and every unmarked "<Title> by <Author>", must name an item this turn's
tools returned, a person of one, a name the tools wrote, or something the shopper typed. A
span reads as a name when it starts with a capital, has at most ten words and no amounts,
and most of its words, leaving out heading words ("Why it works", "Top pick", "Women's")
and the domain's vocabulary (genres, moods, ingredients, glasses, colours, product types,
occasions), are capitalised. A label prefix ("**Dress: River Wrap Dress**") is dropped, and
spans that are labels themselves ("**Ingredients:**") or category headings ("**Picture
books**") are skipped. In fashion, a sentence-case span counts when it is a catalog product
name. So a book, drink or brand outside the catalog ("**Paper Plane**", "**Nike Air Max
90**") is caught.

**Claims no data supports.** Allergen, skin-health and age-suitability promises
("nut-free", "hypoallergenic", "gentle on sensitive skin", "safe for kids", "suitable for
children", "fine for 8-year-olds", "ages 8 and up") are reported in every domain; drinks
add dietary and health promises ("vegan", "caffeine-free", "healthy", "safe during
pregnancy"). Stock, size, delivery and discount promises ("in stock", "ships tomorrow",
"20% off") are reported in fashion, book and evening answers, and a book answer's prices
and formats too ("€9.99 in paperback", "available in hardcover"): books carry none. Each is
reported unless the clause before negates or hedges it ("I can't guarantee it's nut-free",
"check whether it's gluten-free"), or, for safety, the rest of the sentence hedges it ("...
but check the label"), or the product's own description says it. A reader's own figure may
be repeated as theirs in a book answer ("I can't filter by your €15 budget"). Amounts in
dollars, pounds or yen are reported in fashion, book and evening answers, even the
shopper's own figure: the answer should say how the budget was read in euros.
A sentence that repeats tool text word for word (an evening swap's "An alcohol-free drink:
Figgy Thyme → Masala Chai") is the tool's own.

### Questions, repair and fallback

The final answer, every `AskShopper` question and its quick replies are checked the same
way (each quick reply reads as a question of its own). An unverified answer gets one
rewrite request listing the issues; if the rewrite still fails, or tries to call tools, the
reply falls back to a deterministic list of catalog facts. An unverified question goes back
to the model as a tool error (it may ask again or answer); a second unverified question is
replaced by "Could you tell me a little more about what you're looking for?" without quick
replies. The catalog-facts fallback after a tool or time budget, or an empty answer (traced
as "Empty answer"), is checked too, so its report says what it states.

### The report and the badge

`GroundingReport.status` is `verified` only when at least one fact was checked and all
matched; `not_applicable` when the answer stated nothing checkable; `repaired` when the
rewrite passed; `fallback` when catalog facts replaced the answer. `checked` counts the
facts of the first check and `counts` splits them by kind (`price`, `id`, `rating`, `title`,
`name`, `author`, `amount`, `alcohol`, `pages`, `year`, `percent`, `colour`, `ingredient`,
`claim`; `fact` for toolkits that do not split them). The trace's "Grounding check" step and the UI badge say
what was checked: "Checked 3 prices and 2 names against the catalog" (three kinds at most,
then "N other facts"). Nothing is shown for `not_applicable`. "Self-corrected to match the
catalog" and "Unverified answer replaced with catalog facts" keep their labels; their
tooltip says how many claims did not match.

### Measured

`tests/agents/grounding_corpus/corpus.json` is a labelled corpus; `test_grounding_corpus.py`
gates it (recall at least 0.95 on false answers, false alarms at most 0.05 on correct ones)
and `python -m tests.agents.test_grounding_corpus` prints every miss and false alarm. Each
case carries the evidence the engine had: for run texts it is rebuilt from the requests the
model saw (the item IDs of the tool results, session and history cards, loaded from the
prepared catalogs, and the euro figures the tools stated without an item), by
`tests/agents/grounding_corpus/build.py`.

| Source | False answers | Caught | Correct answers | Flagged |
|---|---|---|---|---|
| The audit's 13 probes (F1), on the test fixtures | 13 | 13 | | |
| Real-model texts of the first evaluation run (39 answers, 4 questions) | | | 43 | 0 |
| Scripted end-to-end model, eval dry run plus its e2e scenarios | 4 (its deliberate inventions) | 4 | 68 | 1 |
| Written for this corpus, on the evidence of real turns | 49 | 47 | 35 | 0 |
| **Total** | **66** | **64 (recall 0.970)** | **146** | **1 (false-alarm rate 0.007)** |

The two misses are kept on purpose and are misses by design: a wrong glass and a wrong
genre ("Gone Girl is a gentle cosy mystery"). Glasses, genres, descriptions, plot and
shelves are not checked. (Before v2.1 the wrong colour, "The **Primo slacks** come in navy",
was a third; colours are checked now.) The false alarm is the scripted model's
evening answer for "budget $150", whose "why it works" line repeats the shopper's "$150":
nothing in it is false, but every non-euro amount is reported, so it is rewritten (the
evaluation's dollar-budget task wants no "$" in the answer either). On the same corpus the previous check
(v1) caught 17 of the 66 false answers (0.258), none of the 13 probes, and flagged 3 of the
146 correct texts (two of them questions, which v1 did not check in production).

Read these numbers with their limits. The written cases were written by the same person who
wrote the check, after it existed, and the heading and binding rules were calibrated on the
relay and stub texts, so the corpus is not a held-out test: it shows the check catches the
listed failure classes and flags only one of these 146 texts, not how it does on a new model's
answers. The real-model sample is one run of 39 answers.

### Held-out sets

A held-out set is written blind: by someone who has not seen the check, after it was
frozen, on the evidence of real catalog items. `tests/agents/grounding_heldout/v1.json` (laid
out like the corpus by `grounding_heldout/build.py`) holds the first: 80 answers in fashion,
cocktails and books, 39 false and 41 correct.

| Held-out v1 | False answers caught | Correct answers flagged |
|---|---|---|
| Blind, grounding v2 before the fixes | 33 of 39 (recall 0.846) | 7 of 41 (false-alarm rate 0.171) |
| Grounding v2.1, after using it for development, no longer blind | 39 of 39 (recall 1.000) | 0 of 41 (false-alarm rate 0.000) |

The blind result was far below the corpus's in-sample numbers (0.955 and 0.007). The seven
false alarms were all arithmetic (a total, a difference or the shopper's "over €50"
reported as an item's wrong price); the six misses were a colour, a hypoallergenic claim,
an ingredient ("ginger beer" for a recipe with ginger ale), a book's age suitability, a
book's price and shipping, and another book's rating, which slipped through because a bare
rating before the next bullet's dash was not read. Each was fixed by category (the rules
above), never by string, and the corpus gate still holds. The second row is a regression
floor (`test_grounding_heldout.py`), not an estimate: the set shaped the fixes, so it can no
longer say how the check does on answers it has not seen. A second blind set (v2), written
after these fixes, follows, and its numbers are the ones to quote.

### Limits

- Binding is positional. A price in a sentence that names one product but describes
  another ("the dress (€29.99) and the **Paloma** (€12.99)", where "the dress" is no
  reference) binds to the nearest name and may be reported; a pronoun after two products
  binds to the last one named.
- A fact that equals a total, budget or remainder the tools stated passes wherever it
  stands, even bound to the wrong item.
- Materials (other than percentages), sizes, glasses, genres, shelves, series positions,
  descriptions, plot and ratings counts are not checked; nor is whether a recommendation
  fits the request. Colours are read only next to a product and in the base words the
  catalog uses ("cream", "charcoal" or "olive" are not read); ingredients only where the
  answer ties them to a named drink.
- A total, difference or multiple is accepted when it adds up for some of the items the
  answer names, so a sum that happens to equal the wrong subset passes; one that names its
  items by pronoun ("both Dune books" with one named) may be reported.
- A drink called alcohol-free in a sentence that names no drink, without a list after it,
  is not checked.
- The checks read English phrasing; another language passes unchecked.

## Budgets and failure handling

- At most five tool calls per turn, then a grounded fallback.
- A 90-second wall-clock budget (`AGENT_TURN_DEADLINE_SECONDS`), checked before every model
  call, including the self-correction call. When it is spent the trace shows a "Time budget" step and the
  answer falls back to catalog facts.
- At most `MAX_CONCURRENT_TURNS` turns (default 8) run at once per API process. A turn over
  the cap is refused at once with HTTP 503 and `Retry-After: 5`, before any model call, so
  slow models cannot take every worker thread from search and `/health`.
- Provider failures map to safe HTTP errors (429 retry, 502 otherwise). On the
  streaming endpoint they arrive as an `error` event; provider details stay in server logs.

## Feedback

Each completed answer has thumbs up/down. `POST /chat/feedback` writes the rating and the
names of the tools used to the server log at INFO. The question, answer and comment the
client sends are validated and then dropped: they are not stored, and no route serves
feedback back. (Until 3.0.0, `GET /chat/feedback` returned the ten most recent negative
ratings, with their text, to anyone.)

## Review findings (November 2026)

### Recommendation system

The recommendation side is careful work. Every purchase-derived signal is trained
strictly before the holdout, baselines include the strong buy-again strategy, paired
customer intervals are reported, and frozen confirmation cohorts are protected from
reuse. Recommenders share one protocol, return deterministic tie-breaks and attach
human-readable evidence.

Observations, none changed in this iteration:

- `ContentBasedRecommender` returns raw popularity counts as scores when a shopper has
  no embedded purchases, while every other fallback scores 0. Scores are not comparable
  across fallbacks.
- `GraphRecommender._scores` iterates every product that shares an attribute with each
  purchase. High-degree attributes such as a common colour make this cost grow with
  catalog size; per-feature caps would bound latency.
- The learned ranker's improvement over buy-again is not established (the paired interval
  crosses zero), which the evaluation page already states.

### Earlier assistant

The previous assistant was a clean, bounded two-tool loop with validated read-only
actions. Gaps addressed here:

| Gap | Change |
|---|---|
| The agent could not use the recommendation system | FindRelated and PersonalPicks reuse graph, CLIP and ranker evidence |
| No visibility into what the agent did | Per-step trace, streamed live over NDJSON |
| Answers were not checked against tool results | Price/ID grounding check, one self-correction, deterministic fallback |
| No way to ask a clarifying question | AskShopper with quick replies |
| No actions on shopper state, so no human-in-the-loop surface | Approval-gated shortlist and memory with signed, narrow-only proposals |
| A 6–57 s request behind one spinner | Live step progress and a turn time budget |
| No quality signal from real use | Answer feedback endpoint |
| The UI crashed on empty 204 responses | The client returns an empty result for responses without a body |

## Limits

- The shortlist, reading list and memory last for the browser session only, kept per domain.
- Evening themes are a hand-written, English-only word table with eight themes; an occasion
  with no theme word becomes a cosy evening. The outfit, book and drink are chosen one after
  the other (the drink from the book), not jointly, and nothing checks that the outfit's
  colours suit the book or drink.
- The grounding check verifies prices, IDs, names, titles, authors, ratings, page counts,
  years, recipe amounts, ingredients, colours and alcohol labels, each bound to its item, and
  totals and differences of them, and reports safety, stock and foreign-currency claims; it
  does not verify other descriptive claims (see "Grounding guardrail").
- A book title that is a single short word ("Dune") is checked only when the answer marks
  it (bold, italic or quoted); one that is also a common word ("It") never is, so the agent
  could name such a book without evidence.
- A drink name the answer does not mark as a name (no bold, quotes or "drink #N") is read as
  prose and not checked. Pairing themes are hand-written rules over crowd shelves, not a
  taste model, and lore covers only a handful of authors.
- Cocktail ingredient filters and avoided ingredients are not allergen guarantees.
- Pantry words are mapped to catalog spelling only when the match is unique; "whiskey,
  lemon, sugar" does not yet find Whiskey Sour, whose recipe lists "Blended whiskey" and
  "Powdered sugar".
- Feedback is not stored: the API logs the rating and the tool names, nothing else.
- Unit and browser tests use scripted models. The [agent evaluation](agent-eval.md) measures a
  real model on 61 tasks, a small set that shows failure modes rather than precise rates
  (every rate carries a 95 % interval).
- Outfit coherence is a CLIP query per slot plus co-purchase and photo similarity; there is
  no colour-harmony or style model, so a dress and earrings can clash. Occasion templates
  are keyword-based and English-only, and the photo index has few men's and children's
  shoes. The outfit lives in the client session only.
