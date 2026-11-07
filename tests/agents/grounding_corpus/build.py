"""Rebuild the grounding corpus from run artefacts and the prepared catalogs.

The corpus (`corpus.json`) is a test fixture: every case is an answer (or a clarifying
question with its quick replies), the evidence the engine had when it checked it, and a
label, `correct` or `false`. `tests/agents/test_grounding_corpus.py` runs the domain checks
over it and gates recall on false answers and the false-alarm rate on correct ones.

Sources:

- `relay`: every text the real model wrote in the first evaluation run
  (`evals/results/claude-relay-1`). Its evidence is rebuilt from the requests the relay
  recorded (`evals/relay_queue`): the item IDs the tools, the session and the history cards
  showed, loaded from the prepared catalogs, the euro figures the tools stated without an
  item (totals, budgets, what is left), the shopper's message and the pairer's reasons.
- `stub`: every text the scripted end-to-end model (`tests/e2e/llm_stub.py`) wrote in a dry
  run of the evaluation (`--stub-results`, `--stub-queue`), rebuilt the same way.
- `written` and `probe` cases in `written.py`, which reuse the evidence of a relay or stub
  case (`base`) or carry their own.

Labels: relay and stub texts are `correct` unless `written.LABELS` says otherwise (each such
label says why). Run from the repository root with the prepared data in `data/`:

    uv run python -m tests.agents.grounding_corpus.build \\
        --stub-results <dry run>/results.jsonl [--stub-results <another run>/results.jsonl] \\
        --stub-queue <the dry runs' relay queue>
"""

import argparse
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

from curio.domains.books.catalog import BookCatalog
from curio.domains.books.taxonomy import MOODS
from curio.domains.cocktails.catalog import CocktailCatalog
from curio.domains.evening.agent.grounding import ordinary_names
from curio.domains.fashion.catalog import ProductCatalog
from curio.domains.fashion.names import display_name
from tests.agents.grounding_corpus.written import LABELS, PROBE_ITEMS, WRITTEN

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
DATA = ROOT / "data"

_EURO = re.compile(r"€\s?(\d{1,5}(?:[.,]\d{1,2})?)")
_FREE_BEFORE = re.compile(
    r"(?:total|budget|of a|of|left|cheapest|minimum|at least|over|short|up to|under|in all|"
    r"remaining|spend)\W{0,3}$",
    re.IGNORECASE,
)
_FREE_AFTER = re.compile(r"^\W{0,3}(?:budget|left|in all|remaining)", re.IGNORECASE)
_CITED = re.compile(r"why: ([^|\n]+)|^(Why it works: .+)$|^(.+→.+)$", re.MULTILINE)
CITATIONS = {
    "fashion": {"product": [r"\[id (\d+)\]"]},
    "cocktails": {"drink": [r"\[id (\d+)\]"]},
    "books": {"book": [r"\[id (\d+)\]", r"\bbook #(\d+)"], "paired": [r"\bdrink #(\d+)"]},
    "evening": {
        "product": [r"\bproduct #(\d+)"],
        "book": [r"\bbook #(\d+)"],
        "drink": [r"\bdrink #(\d+)"],
    },
}
QUESTION = "question"


def load_catalogs() -> dict[str, Any]:
    return {
        "products": ProductCatalog.from_parquet(DATA / "products.parquet"),
        "books": BookCatalog.from_parquet(DATA / "books.parquet"),
        "cocktails": CocktailCatalog.from_parquet(DATA / "cocktails.parquet"),
    }


def queue_index(queue: Path) -> list[tuple[str, dict, dict]]:
    """(seq, request, response) for every answered request, in order."""
    rows = []
    for response_path in sorted(queue.glob("*.response.json")):
        seq = response_path.name.split(".")[0]
        request_path = queue / f"{seq}.request.json"
        if request_path.exists():
            rows.append(
                (seq, json.loads(request_path.read_text()), json.loads(response_path.read_text()))
            )
    return rows


def _session(request: dict) -> str:
    system = request["messages"][0].get("content") or ""
    at = system.rfind("\n\nSession:")
    return system[at:] if at >= 0 else ""


def _turn_messages(request: dict) -> list[dict]:
    """The messages of the answering turn: after the last user message."""
    messages = request["messages"]
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    return messages[last_user + 1 :]


def _ids(texts: Iterable[str], patterns: Iterable[str]) -> list[int]:
    found: dict[int, None] = {}
    for text in texts:
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                found[int(next(g for g in match.groups() if g))] = None
    return list(found)


def _free_amounts(texts: Iterable[str]) -> list[float]:
    amounts: dict[float, None] = {}
    for text in texts:
        for line in text.splitlines():
            cited = re.search(r"\[id \d+\]|product #\d+|book #\d+|drink #\d+", line)
            for match in _EURO.finditer(line):
                before = line[max(0, match.start() - 25) : match.start()]
                after = line[match.end() : match.end() + 15]
                if not cited or _FREE_BEFORE.search(before) or _FREE_AFTER.search(after):
                    amounts[round(float(match.group(1).replace(",", ".")), 2)] = None
    return list(amounts)


def evidence_of(domain: str, request: dict, intents: list[dict]) -> dict[str, Any]:
    """The evidence the engine had for the answer to `request` (see the module docstring)."""
    session = _session(request)
    history = [
        m.get("content") or ""
        for m in request["messages"][1:]
        if m["role"] in ("user", "assistant")
    ]
    turn_tools = [m.get("content") or "" for m in _turn_messages(request) if m["role"] == "tool"]
    everything = [session, *history, *turn_tools]
    citations = CITATIONS[domain]
    evidence: dict[str, Any] = {"products": [], "books": [], "drinks": [], "paired": []}
    if domain == "fashion":
        evidence["products"] = _ids(everything, citations["product"])
    elif domain == "cocktails":
        evidence["drinks"] = _ids(everything, citations["drink"])
    elif domain == "books":
        evidence["books"] = _ids(everything, citations["book"])
        evidence["paired"] = _ids(turn_tools, citations["paired"])
    else:
        evidence["products"] = _ids(everything, citations["product"])
        evidence["books"] = _ids(everything, citations["book"])
        evidence["drinks"] = _ids([session, *turn_tools], citations["drink"])
    amounts = _free_amounts([session, *turn_tools])
    for intent in intents:
        amounts += [
            float(v) for k in ("min_price", "max_price") if (v := intent.get(k)) is not None
        ]
    evidence["amounts"] = sorted(set(amounts))
    cited: list[str] = []
    if domain in ("books", "evening"):
        for text in [*turn_tools, session]:
            for match in _CITED.finditer(text):
                cited.append(next(g for g in match.groups() if g).strip())
    evidence["cited"] = list(dict.fromkeys(cited))
    return evidence


def run_cases(results: Path, queue: Path, source: str) -> list[dict[str, Any]]:
    """A case per text (and per question) the model wrote in one evaluation run."""
    index = queue_index(queue)
    used: set[str] = set()
    cases = []
    for line in results.read_text().splitlines():
        task = json.loads(line)
        for turn in task["turns"]:
            typed = turn.get("message") or ((turn.get("decision") or {}).get("action") or {}).get(
                "request", ""
            )
            intents = [
                call["args"]
                for c in turn.get("llm_calls") or []
                for call in c["tool_calls"]
                if call["name"] == "SearchIntent"
            ]
            for number, call in enumerate(turn.get("llm_calls") or []):
                texts = []
                if call["text"].strip() and not call["tool_calls"]:
                    texts.append(("answer", call["text"].strip()))
                for tool_call in call["tool_calls"]:
                    if tool_call["name"] == "AskShopper":
                        args = tool_call["args"]
                        options = "\n".join(
                            f"- {o.rstrip(' .!?')}?" for o in args.get("options", [])
                        )
                        texts.append((QUESTION, f"{args['question']}\n{options}".strip()))
                for kind, text in texts:
                    seq = _find(index, used, call, kind)
                    if seq is None:
                        continue
                    request = next(r for s, r, _ in index if s == seq)
                    if text.startswith("Not asked: the question states facts"):
                        continue  # the engine's own message, echoed by the stub
                    cases.append(
                        {
                            "id": f"{source}/{task['task_id']}/{turn['index']}/{number}/{kind}",
                            "source": source,
                            "domain": task["domain"],
                            "kind": kind,
                            "label": "correct",
                            "typed": typed,
                            "answer": text,
                            "evidence": evidence_of(task["domain"], request, intents),
                        }
                    )
    return cases


def _find(index: list, used: set[str], call: dict, kind: str) -> str | None:
    for seq, _, response in index:
        if seq in used:
            continue
        if kind == "answer" and (response.get("content") or "").strip() == call["text"].strip():
            used.add(seq)
            return seq
        if (
            kind == QUESTION
            and [c["name"] for c in response.get("tool_calls") or []]
            == [c["name"] for c in call["tool_calls"]]
            and [c.get("arguments") for c in response["tool_calls"]]
            == [c["args"] for c in call["tool_calls"]]
        ):
            used.add(seq)
            return seq
    return None


def written_cases(base: dict[str, dict]) -> list[dict[str, Any]]:
    cases = []
    for number, spec in enumerate(WRITTEN):
        parent = base[spec["base"]] if "base" in spec else None
        evidence = dict(parent["evidence"]) if parent else dict(spec["evidence"])
        evidence.update(spec.get("evidence_update", {}))
        cases.append(
            {
                "id": spec.get("id", f"{spec['source']}/{number}"),
                "source": spec["source"],
                "domain": parent["domain"] if parent else spec["domain"],
                "kind": spec.get("kind", "answer"),
                "label": spec["label"],
                "note": spec["note"],
                "typed": spec.get("typed", parent["typed"] if parent else ""),
                "answer": spec["answer"],
                "evidence": evidence,
            }
        )
    return cases


def _rows(catalog: Any, ids: Iterable[int], strip: tuple[str, ...] = ()) -> list[dict]:
    rows = []
    for item in catalog.get_many(ids):
        row = item.model_dump(mode="json", exclude={"display_name"})
        for field in strip:
            row[field] = "" if isinstance(row.get(field), str) else []
        rows.append(row)
    return rows


def items_of(cases: list[dict], catalogs: dict[str, Any]) -> dict[str, dict[str, dict]]:
    """Catalog rows for every ID the cases cite, keyed "products"/"books"/"drinks" → id."""
    ids: dict[str, set[int]] = {"products": set(), "books": set(), "drinks": set()}
    for case in cases:
        if case["source"] == "probe":
            continue
        evidence = case["evidence"]
        ids["products"] |= set(evidence["products"])
        ids["books"] |= set(evidence["books"])
        ids["drinks"] |= set(evidence["drinks"]) | set(evidence["paired"])
    return {
        "products": {str(r["product_id"]): r for r in _rows(catalogs["products"], ids["products"])},
        "books": {
            str(r["book_id"]): r
            for r in _rows(catalogs["books"], ids["books"], strip=("description", "tags"))
        },
        "drinks": {
            str(r["drink_id"]): r
            for r in _rows(catalogs["cocktails"], ids["drinks"], strip=("instructions",))
        },
    }


def vocabulary_of(cases: list[dict], catalogs: dict[str, Any]) -> dict[str, list[str]]:
    """The catalog vocabulary the checks are built with, cut to the entries that occur in a
    case's answer (only those can match), plus the short lists in full."""
    texts = [case["answer"].casefold() for case in cases]

    def occurring(values: Iterable[str]) -> list[str]:
        values = {v for v in values if v and v.strip()}
        return sorted(v for v in values if any(v.casefold() in text for text in texts))

    books: BookCatalog = catalogs["books"]
    cocktails: CocktailCatalog = catalogs["cocktails"]
    products: ProductCatalog = catalogs["products"]
    drink_names = [c.name for c in cocktails]
    raw_names = products.distinct("name")
    words = [
        value
        for field in ("product_type", "product_group", "section", "colour")
        for value in products.distinct(field)
    ]
    ordinary = ordinary_names(drink_names, (f"{b.title} {b.description}" for b in books))
    return {
        "book_titles": occurring(books.titles()),
        "book_other_names": sorted({*occurring(books.authors()), *books.genres(), *MOODS}),
        "drink_names": occurring(drink_names),
        "ingredients": occurring(cocktails.ingredients()),
        "glasses": occurring({c.glass for c in cocktails}),
        "categories": sorted(cocktails.categories()),
        "ordinary_drink_names": occurring(ordinary),
        "product_names": occurring([*raw_names, *map(display_name, raw_names)]),
        "product_words": sorted({*words, *(w for v in words for w in v.split())}),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--relay-results", type=Path, default=ROOT / "evals/results/claude-relay-1/results.jsonl"
    )
    parser.add_argument("--relay-queue", type=Path, default=ROOT / "evals/relay_queue")
    parser.add_argument("--stub-results", type=Path, action="append", required=True)
    parser.add_argument("--stub-queue", type=Path, required=True)
    args = parser.parse_args(argv)
    catalogs = load_catalogs()
    cases = run_cases(args.relay_results, args.relay_queue, "relay")
    for results in args.stub_results:
        cases += run_cases(results, args.stub_queue, "stub")
    for case in cases:
        if (label := LABELS.get(case["id"])) is not None:
            case["label"], case["note"] = label
    cases += written_cases({case["id"]: case for case in cases})
    items = items_of(cases, catalogs)
    items["probe"] = PROBE_ITEMS
    payload = {
        "cases": cases,
        "items": items,
        "vocabulary": vocabulary_of(cases, catalogs),
    }
    out = HERE / "corpus.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    counts = pd.Series([f"{c['source']}/{c['label']}" for c in cases]).value_counts()
    print(counts.to_string())
    print(f"Wrote {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
