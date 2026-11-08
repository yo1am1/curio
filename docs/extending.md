# Extending Curio: your own dataset and your own agent domain

**Contract version 1**, for `curio.engine` as of Curio 3.0.0 and the changes listed under
[Unreleased](../CHANGELOG.md). It covers what a domain must provide to run on the engine and
what the engine guarantees in return. Anything not written here is internal and may change
in any release; a change to anything written here is listed in the changelog.

The worked example is [`examples/hello_domain/`](../examples/hello_domain/): a tea guide whose
twelve teas come from [`teas.csv`](../examples/hello_domain/teas.csv) through a loader, with a
search tool, a save tool behind approval, fact references and a backstop.
[`tests/test_hello_domain.py`](../tests/test_hello_domain.py) runs it end to end on the real
engine with a scripted model:

```bash
uv run pytest tests/test_hello_domain.py tests/test_architecture.py
```

Contents: [the data contract](#1-the-data-contract) ·
[the toolkit](#2-the-toolkit) · [tool policies](#3-tool-policies) ·
[effects and approvals](#4-effects-and-approvals) ·
[fact references](#5-fact-references-cite-mode) ·
[the grounding backstop](#6-the-grounding-backstop) ·
[running the agent](#7-running-the-agent) ·
[registering the domain in the app](#8-registering-the-domain-in-the-app) ·
[the tests a domain needs](#9-the-tests-a-domain-needs) ·
[what is not supported](#10-what-is-not-supported)

## 1. The data contract

A catalog item is any Python object; in practice a pydantic `BaseModel`, because the reply
serializes the items it shows. The engine needs exactly one thing from it: a **positive
integer ID, unique within the domain**, returned by the toolkit's `key(item) -> int`. The
engine keys the turn's evidence by it (`Turn.evidence: dict[int, T]`), signed proposals carry
it, and fact references name items by it (`{tea:4}`). Everything else on the item is the
domain's own.

The tea item:

```python
class Tea(BaseModel):
    tea_id: PositiveInt  # the key; unique
    name: str = Field(min_length=1)
    kind: str
    notes: str
    price_eur: PositiveFloat  # per 100 g
```

**Loading.** The engine does not load data. A domain loads its catalog once, before it builds
the toolkit, and passes the items in. The example's loader reads a CSV with a header row and
validates every row with the item model:

```python
def load_teas(path: Path = TEAS_CSV) -> list[Tea]:
    if not path.exists():
        raise FileNotFoundError(f"Tea catalog not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        teas = [Tea.model_validate(row) for row in csv.DictReader(handle)]
    if len({t.tea_id for t in teas}) != len(teas):
        raise ValueError(f"{path}: tea_id values must be unique")
    return teas
```

To bring your own dataset, keep the shape: a file (CSV, Parquet, JSON, a database query), a
loader that validates each row into your item model and rejects duplicate IDs, and a toolkit
built over the loaded items (`TeaToolkit(load_teas(my_path))`, or
`tea_agent(model, teas=load_teas(my_path))`). If your catalog keys are strings (SKUs, UUIDs),
map them to positive integers in the loader and keep the original key as a field. The
built-in domains do the same work in `scripts/prepare_*.py` (download, verify, write
Parquet) and `curio.domains.<name>.catalog` (load the Parquet). The app's loaders check
that the files exist first and leave the domain out with a setup hint when they do not;
`curio.engine.data.DataFileMissingError(path, "scripts.prepare_x")` is the shared error for a
catalog file that is not prepared, and its message names the command to run.

## 2. The toolkit

A domain is a `Toolkit[T]` over its item type `T` (`curio.engine.toolkit`). Subclass
`BaseToolkit[T]`, which gives every optional hook a default.

**Class attributes**

| Attribute | Type | Required | Meaning |
|---|---|---|---|
| `tools` | `Mapping[str, type[BaseModel]]` | yes | Tool name → its argument schema. The model sees each schema (its docstring is the tool's description). Use `extra="forbid"` and bounds on every field: a call that fails validation never runs. |
| `policies` | `Mapping[str, Policy]` | yes | Tool name → `Policy.READ`, `ASK` or `APPROVE` ([section 3](#3-tool-policies)); every tool needs one. |
| `grounding_subject` | `str` | no (`"facts"`) | What the backstop checks, shown in the trace ("tea names, IDs and prices"). |
| `invalid_action` | `str` | no | The tool message for a call that fails its schema. |
| `repair_instruction` | `str` | no | The self-correction request; `{issues}` is filled in. |

**Required methods** (abstract on `BaseToolkit`)

| Signature | Contract |
|---|---|
| `key(self, item: T) -> int` | The item's positive, unique integer ID. |
| `prompt(self, turn: Turn[T], message: str, history: Sequence[BaseMessage]) -> list[BaseMessage]` | The messages for the model: a system prompt, the history, the user's message. Items it shows the model from the session (a saved list) go into the evidence with `turn.remember(items)`. |
| `read(self, turn: Turn[T], action: BaseModel) -> tuple[str, AgentStep]` | Run a READ tool: call `turn.show(items)` with what it found (they become the reply's items and evidence) and return the tool message for the model and a trace step. Raise `ToolError(message)` for a valid call that cannot run; the message goes back to the model. A tool that works in stages may instead return a generator that yields progress steps and returns the pair. |
| `apply(self, action: BaseModel, *, dry_run: bool = False) -> tuple[AppliedEffect, list[T], str]` | Validate an APPROVE action: its effect, the items it touches, and an outcome line ("Saved Assam."). Called with `dry_run=True` when the proposal is made and again, without it, once it is approved. Raise `ToolError` when nothing valid is left. It must not have side effects outside its return value ([section 4](#4-effects-and-approvals)). |
| `check(self, turn: Turn[T], text: str) -> GroundingReport` | The backstop ([section 6](#6-the-grounding-backstop)). |
| `fallback(self, items: Sequence[T], extras: Mapping[str, Any] \| None = None) -> str` | A deterministic answer from catalog facts only, written with fact references, for when the model's answer cannot be verified, is empty or runs out of budget. |
| `reply_fields(self, turn: Turn[T]) -> dict[str, Any]` | The reply's domain fields, such as `{"teas": turn.items}`; the reply model declares them. |

**Optional hooks** (defaults on `BaseToolkit`)

| Signature | Default |
|---|---|
| `facts(self, turn: Turn[T]) -> FactSheet` | An empty sheet: every fact reference fails ([section 5](#5-fact-references-cite-mode)). |
| `clean_context(self, context: TurnContext) -> TurnContext` | Keep the client-held context as it is. Override to drop values this domain does not recognise. |
| `history(self, turns: Sequence[ChatTurn]) -> tuple[list[BaseMessage], list[T]]` | Plain chat messages, no earlier items. Override to return the items earlier turns showed, so they count as evidence. |
| `plan(self, turn: Turn[T], history: Sequence[BaseMessage]) -> Iterator[AgentStep]` | One "Understand request" step. Each yielded step is timed and streamed. |
| `refresh(self, turn: Turn[T], approved: AppliedEffect \| None = None) -> None` | Nothing. Override to re-apply remembered preferences to the turn after a resume or an approved change. |
| `proposed(self, args: Mapping[str, Any]) -> list[T]` | No items. Override to show the items a (declined) proposal named. |
| `approval_text(self, action: BaseModel) -> str` | "Shall I go ahead? Approve, edit or decline." |
| `pending_display(self, items: list[T]) -> dict[str, Any]` | `{}`. Display-only fields added to the proposal (the cards in the approval drawer); they are not signed. |
| `vet_proposal(self, turn: Turn[T], action: BaseModel) -> None` | Accept. Raise `ToolError` to refuse a proposal that does not fit this turn; the model gets the message. |
| `suggestions(self, turn: Turn[T]) -> list[str]` | `[]`. Next-step chips for a completed answer. |

**`Turn[T]`** is the mutable state of one turn: `message`, `context` (the client-held
`TurnContext`), `items` (the latest results shown), `evidence` (every item the answer may
cite: results, items from history, the saved list), `extras` (domain results that are not
catalog items), `steps` and `applied`. Only `show`, `remember` and `extras` are meant to be
written by a toolkit.

## 3. Tool policies

The policy decides what happens to a call that passed its schema (`curio.engine.tools`):

- **READ** runs at once through `read`. It only looks things up.
- **ASK** is for the shared `AskShopper` tool only (a question of 3-200 characters with
  2-4 distinct quick replies). It ends the turn with a question; the user's choice is the
  next message. A domain tool of its own marked ASK is treated as APPROVE.
- **APPROVE** never runs on the model's say-so. The engine calls `vet_proposal`, then
  `apply(action, dry_run=True)`, and ends the turn with a signed proposal. The user
  approves it, narrows it or declines it in the next request. The exception is a tool the
  user listed in `TurnContext.auto_approve`, which is applied at once.

Budgets apply to every domain: at most `MAX_TOOL_CALLS` (5) tool calls per turn, the last
`MAX_HISTORY_TURNS` (10) history turns, and a wall-clock deadline of
`TURN_DEADLINE_SECONDS` (90, per engine through `turn_deadline_seconds`), checked before every
model call. When a budget ends the turn, the user gets the fallback.

## 4. Effects and approvals

An APPROVE tool's result is an **effect**: a subclass of `AppliedEffect` with a unique
`kind` and the fields the client applies. The server keeps no state; the reply's `applied`
list carries the effect, and the client stores it in its session (`TurnContext`). Two class
variables tell the engine how the effect changes that context for the rest of the turn:

```python
class TeaContext(TurnContext):
    saved: list[PositiveInt] = Field(default_factory=list, max_length=20)


class TeaEffect(AppliedEffect):
    kind: Literal["saved_teas"] = "saved_teas"
    tea_ids: list[int]
    adds: ClassVar = {"tea_ids": "saved"}  # approved IDs are appended to TeaContext.saved
```

`remembers` does the same for a preferences model: approved values are merged into the
context field it names (lists joined, flags and values replaced).

**What the engine guarantees for a proposal** (`curio.engine.signing`):

- It is HMAC-SHA256-signed over its ID, tool, arguments, request, intent, issue time and
  the client's `session_id`, with a key of at least 32 bytes (`ActionSigner(key)`; without a
  key, a random one per process). It expires after 30 minutes (`ttl_seconds`).
- A decision may only narrow it (`narrow_args`): a list may lose values, a flag may be
  switched off, a value may be cleared. A widened, forged, expired or moved one raises
  `InvalidActionError` (HTTP 409 in the app).
- It is answered once: the signer's `NonceStore` records it until it expires. The default
  `InMemoryNonceStore` covers one process; several processes need a shared store
  implementing `claim(nonce, expires_at) -> bool` and `release(nonce)`.
- A proposal resumes only on an engine whose toolkit has that tool as APPROVE, so give
  APPROVE tools names that are unique across the domains one signer serves.

What it does not guarantee: `auto_approve` is the user's own setting, held by their client;
the signature stops the model from approving itself, not a client from opting in. Applying
an effect to anything outside the reply (a database, an order) is the caller's job, after
`resume` returns it.

## 5. Fact references (cite mode)

In cite mode the model does not type catalog values. It writes a reference, and the engine
replaces it with the value from an item this turn's tools returned
(`curio.engine.facts`). A domain enables it by returning a `FactSheet` from `facts(turn)`:

```python
TEA_FACTS = {"price": Attribute("price", "tea", lambda t, _: t.price_eur, "€{:.2f}".format, True)}


def facts(self, turn: Turn[Tea]) -> FactSheet:
    return FactSheet({"tea": ItemKind(lambda t: t.name, dict(turn.evidence))}, TEA_FACTS)
```

- `ItemKind(name, items)`: how an item of that kind is named and the items that may be
  cited, by ID. Build `items` from `turn.evidence` only.
- `Attribute(kind, item, read, show, total=False, difference=False, argument=False,
  show_derived=None)`: `read(item, argument)` returns the value or `None` when the item has
  none; `show` formats it; `total` and `difference` allow `{total:price:1,2}` and
  `{diff:price:1,2}`; `argument` makes the reference take one after the ID
  (`{amount:drink:12:Gin}`).

The grammar: `{tea:4}` renders the name in bold, `{price:4}` or `{price:tea:4}` a value,
`{total:price:4,9}` a sum of two or more, `{diff:price:4,9}` a difference of two. Put the
reference rules in the prompt with `cite_rules(references, example)`.

**Guarantees.** A rendered value is the catalog's value for an item a tool returned this
turn. A reference to an unknown kind, attribute or item, to an item no tool returned, or to
a value the item lacks fails verification: one rewrite, then the fallback. Answers,
rewrites, questions, quick replies and fallbacks are all rendered, so no reference reaches
the client. The report counts rendered facts by kind in `GroundingReport.rendered`.

## 6. The grounding backstop

`check(turn, text)` checks what the model still typed itself. It gets a copy of the answer
in which rendered values are masked and rendered names are kept (so a typed value next to a
name binds to it); what the rendered names alone account for is subtracted, so rendered spans
are never counted or flagged. Return `grounding_report(checked, issues, counts)`:

- `checked`: how many claims were checked; `counts` splits them by kind (`{"price": 2}`).
- `issues`: one sentence per unsupported claim ("Silver Needle was not returned by any
  tool"). Any issue fails verification: one rewrite with the issues, then the fallback.

The check must be deterministic and must accept every value it can find in the evidence. It
should cover at least the IDs and names the domain's items have (`check_ids`, `any_of`), and
each typed number the domain's answers state (the tea check accepts a euro amount only if it
is the price of a returned tea). `curio.engine.grounding` has the building blocks the
built-in domains use for finer checks: `Item` and `Binder` bind a number to the item named
nearest to it, `FactCheck` checks prices, ratings, totals and differences, and there are
checks for marked names, bylines, flags and unsupported safety claims. The backstop is
best-effort: a value written through a reference is the cited item's catalog value (cite
mode does not check that the cited item is the one the sentence names), and typed values are
checked only as far as the check reaches.

## 7. Running the agent

```python
engine = AgentEngine(
    chat_model,
    TeaToolkit(load_teas()),
    signer=ActionSigner(key),
    schemas=Schemas(context=TeaContext, reply=TeaReply),
)
context = TeaContext(session_id="a-random-id-per-conversation")
reply = engine.reply("Save the smoky teas", [], context)
if reply.pending_action:  # an APPROVE tool proposed something
    decision = ActionDecision(
        action=reply.pending_action, approved=True, edited_args={"tea_ids": [4]}
    )  # narrowed
    history = [ChatTurn(role="user", content="Save the smoky teas")]
    done = engine.resume(decision, history, context)  # done.applied
for event in engine.stream("Something smoky?", [], context):  # StepEvents, then a ReplyEvent
    ...
```

`chat_model` is any LangChain `BaseChatModel` that supports tool calling
(`curio.engine.llm.build_chat_model` builds one from settings). `Schemas` names the
subclasses a turn builds (context, reply, intent, pending action).

## 8. Registering the domain in the app

The engine needs nothing more. Serving a domain through the HTTP API and the web app is not
a plugin: the app's wire format and registry list the domains by hand, and adding one means
editing these files, as the books domain did:

1. `src/curio/app/config.py`: a property for the data file (`books_path`).
2. `src/curio/app/schemas.py`: the id in `Domain`, the APPROVE tool names in `ApprovalTool`,
   the effect in the `AppliedEffect` union, the context and chat-turn fields as bases of
   `TurnContext` and `ChatTurn`, and the items' field on `PendingAction` and
   `AssistantReply` (with its place in `_REPLY_ORDER`).
3. `src/curio/app/assistants.py`: an `AgentEngine[T]` subclass that builds the toolkit and
   passes `schemas=WIRE`. Its `reply_fields` must set `"domain"`: the reply's domain
   defaults to `"fashion"`.
4. `src/curio/app/services.py`: a runtime dataclass (`assistant`, `item_count()`,
   `close()`), a loader that returns `None` while the data is missing, a `DomainSpec`
   appended to `DOMAINS`.
5. `src/curio/app/api.py`: the domain's endpoints behind a dependency like `BooksDep`;
   `/chat`, `/chat/stream`, `/health` and `/domains` route by `DomainSpec` with no other
   change.
6. `web/`: a `DomainView` in `src/domains/registry.ts` (`DOMAIN_ORDER`, `DOMAINS`,
   `DOMAIN_PAGES`), a card branch in `src/components/chat/Turn.tsx`, then
   `npm run openapi:export && npm run gen:api` to regenerate `web/openapi.json` and the
   client types.
7. Tests that list the domains by hand (`tests/app/test_optional_domains.py`,
   `tests/app/test_web.py::test_committed_openapi_matches_the_api` and the `/health`
   assertions in `.github/workflows/ci.yml`).

`ENABLED_DOMAINS` then serves it by id, and a missing data file leaves it out with its
setup hint, like the built-in domains.

## 9. The tests a domain needs

Drive the real `AgentEngine` with a scripted chat model
(`tests.agents.conftest.FakeToolCallingChatModel`, which replays `AIMessage`s, tool calls
included), as `tests/test_hello_domain.py` does. At least:

- **The loader**: a valid file loads into items; a missing file, a duplicate ID and an
  invalid row are refused.
- **A grounded answer with references**: the reply's text has the rendered values, no
  reference is left, `grounding.status == "verified"` and `grounding.rendered` counts them.
- **A caught hallucination**: an answer naming an item no tool returned, and a reference to
  one, end in `grounding.status == "fallback"` with the fallback text; a typed wrong value
  is repaired or falls back.
- **The approval round trip**: a proposal is `needs_approval` with nothing applied; a
  narrowed decision applies exactly the narrowed effect; a widened or forged one raises
  `InvalidActionError`.
- **Layering**: `tests/test_architecture.py` fails if `curio.engine` imports a domain or a
  domain imports another one (outside the composed domains it lists).
- **If registered in the app**: its endpoints with and without its data, and a `/chat`
  turn through `TestClient`, as in `tests/app/test_books_api.py`.

## 10. What is not supported

- **Item keys other than positive integers.** Map strings to integers in the loader.
- **Plugins.** There is no entry-point discovery; serving a domain means the edits in
  [section 8](#8-registering-the-domain-in-the-app), and the web app has no generic card for
  an unknown domain.
- **ASK tools of a domain's own.** Only `AskShopper` asks a question.
- **Async.** The engine is synchronous (`reply`, `resume`, `stream`); the API runs turns on
  worker threads, at most `MAX_CONCURRENT_TURNS` (default 8) at once per process.
- **Per-engine budgets** other than the deadline: the tool-call and history limits are
  module constants.
- **Server-side state and effects.** The server stores no session, shortlist or effect;
  executing an approved effect elsewhere is the caller's job.
- **Vocabulary.** The model-facing text of the engine says "the shopper", and the shared
  tool is named `AskShopper`. The price helpers of the backstop and its foreign-currency
  check assume euros; a domain in another currency writes its own price check and
  formats its own `Attribute.show`.
- **Guarantees for prose.** Opinions and descriptions ("cosy", "good for a cold") are not
  facts that can be rendered; only what the backstop checks is checked.
- **Token, cost or usage accounting**, and LLM retries (the provider client is built with
  `max_retries=0`).
