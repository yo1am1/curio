"""Lay out a held-out grounding set like the corpus, so `test_grounding_corpus` can measure it.

A held-out set is written blind: by someone who has not seen the check, after it was frozen,
as a list of answers with their evidence IDs and labels:

    {"id": 1, "domain": "fashion", "user": "...", "evidence_ids": [...], "answer": "...",
     "label": "correct" | "false", "false_claims": "what is false (false answers only)"}

`false_claims` may also be a list of what is false (v3 lists them).

This adds the catalog rows the answers cite and the catalog vocabulary, as
`grounding_corpus/build.py` does for the corpus. Run from the repository root with the
prepared data in `data/`:

    uv run python -m tests.agents.grounding_heldout.build <held-out.json> <out.json> --name v1
"""

import argparse
import json
from pathlib import Path
from typing import Any

from tests.agents.grounding_corpus.build import items_of, load_catalogs, vocabulary_of

FIELDS = {"fashion": "products", "books": "books", "cocktails": "drinks"}


def layout(raw: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """Corpus-style cases (`source` "heldout") from blind held-out answers."""
    cases = []
    for answer in raw:
        evidence: dict[str, list[Any]] = {
            "products": [], "books": [], "drinks": [], "paired": [], "amounts": [], "cited": [],
        }  # fmt: skip
        evidence[FIELDS[answer["domain"]]] = list(answer["evidence_ids"])
        cases.append(
            {
                "id": f"heldout-{name}/{answer['id']}",
                "source": "heldout",
                "domain": answer["domain"],
                "kind": "answer",
                "label": answer["label"],
                "typed": answer["user"],
                "answer": answer["answer"],
                "evidence": evidence,
                "note": _note(answer.get("false_claims")),
            }
        )
    return cases


def _note(false_claims: str | list[str] | None) -> str:
    """What is false in a false answer (a sentence or a list of them), else "correct"."""
    if isinstance(false_claims, list):
        false_claims = "; ".join(false_claims)
    return false_claims or "correct"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("heldout", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    cases = layout(json.loads(args.heldout.read_text()), args.name)
    catalogs = load_catalogs()
    payload = {
        "cases": cases,
        "items": items_of(cases, catalogs),
        "vocabulary": vocabulary_of(cases, catalogs),
    }
    args.out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
