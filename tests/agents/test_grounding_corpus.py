"""Grounding v2 measured on a labelled corpus: recall on false answers, false alarms on
correct ones (`tests/agents/grounding_corpus`, built by its `build.py`).

The checks run exactly as the toolkits compose them, on the evidence each answer had. The
gate: at least 95% of the false answers are caught (every audit probe among them) and at
most 5% of the correct answers are flagged. `python -m tests.agents.test_grounding_corpus`
prints the numbers and every miss and false alarm; given a path, it measures another
labelled set in the same layout (`grounding_heldout/v1.json`).
"""

import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from curio.domains.books.agent.grounding import BookGroundingCheck
from curio.domains.books.models import Book
from curio.domains.cocktails.agent.grounding import CocktailGroundingCheck
from curio.domains.cocktails.models import Cocktail
from curio.domains.evening.agent.grounding import EveningGroundingCheck
from curio.domains.fashion.agent.grounding import OCCASION_WORDS, FashionGroundingCheck
from curio.domains.fashion.models import Product
from curio.domains.pairings.agent.grounding import PairedDrinkCheck
from curio.engine.grounding import merge_reports
from curio.engine.schemas import GroundingReport

CORPUS = Path(__file__).resolve().parent / "grounding_corpus" / "corpus.json"
MIN_RECALL = 0.95
MAX_FALSE_ALARMS = 0.05


@cache
def load(path: Path) -> dict[str, Any]:
    """A labelled set: its cases, the catalog rows they cite and the catalog vocabulary."""
    return json.loads(path.read_text())


def corpus() -> dict[str, Any]:
    return load(CORPUS)


@cache
def checks(path: Path = CORPUS) -> dict[str, Any]:
    vocabulary = load(path)["vocabulary"]
    titles, others = vocabulary["book_titles"], vocabulary["book_other_names"]
    drinks, ingredients = vocabulary["drink_names"], vocabulary["ingredients"]
    glasses = vocabulary["glasses"]
    return {
        "fashion": FashionGroundingCheck(
            vocabulary["product_names"], [*vocabulary["product_words"], *OCCASION_WORDS]
        ),
        "cocktails": CocktailGroundingCheck(
            drinks, ingredients, glass_names=glasses, categories=vocabulary["categories"]
        ),
        "books": BookGroundingCheck(titles, other_names=others),
        "pairing": PairedDrinkCheck(drinks, ingredients, glass_names=glasses),
        "evening": EveningGroundingCheck(
            titles=titles,
            other_names=others,
            drink_names=drinks,
            ingredients=ingredients,
            glass_names=glasses,
            ordinary=vocabulary["ordinary_drink_names"],
            product_words=vocabulary["product_words"],
        ),
        "drink_names": {name.casefold() for name in drinks},
    }


def _rows(case: dict[str, Any], kind: str, field: str, path: Path) -> list[dict[str, Any]]:
    items = load(path)["items"]
    table = (items["probe"] if case["source"] == "probe" else items)[kind]
    return [table[str(i)] for i in case["evidence"][field] if str(i) in table]


def check_case(case: dict[str, Any], path: Path = CORPUS) -> GroundingReport:
    """The report the domain's toolkit would give `case`'s answer on its evidence (`path`
    is the labelled set the case comes from)."""
    every = checks(path)
    evidence, text, typed = case["evidence"], case["answer"], case["typed"]
    products = [Product.model_validate(r) for r in _rows(case, "products", "products", path)]
    books = [Book.model_validate(r) for r in _rows(case, "books", "books", path)]
    drinks = [Cocktail.model_validate(r) for r in _rows(case, "drinks", "drinks", path)]
    paired = [Cocktail.model_validate(r) for r in _rows(case, "drinks", "paired", path)]
    amounts, cited = evidence["amounts"], evidence["cited"]
    domain = case["domain"]
    if domain == "fashion":
        return every["fashion"].check(text, products, amounts, typed)
    if domain == "cocktails":
        return every["cocktails"].check(text, drinks, typed)
    if domain == "books":  # BookToolkit.check with the pairing attached
        paired_names = [c.name for c in paired]
        report = every["books"].check(
            text,
            books,
            typed,
            also_allowed=[*paired_names, *cited],
            skip_name=lambda name: name.casefold() in every["drink_names"],
        )
        book_names = [b.title for b in books] + [b.series for b in books if b.series]
        book_names += [author for b in books for author in b.authors]
        pairing = every["pairing"].check(text, paired, typed, also_allowed=book_names, quoted=cited)
        return merge_reports(report, pairing)
    return every["evening"].check(
        text,
        products=products,
        books=books,
        drinks=drinks,
        amounts=amounts,
        typed=typed,
        cited=cited,
    )


def flagged(case: dict[str, Any], path: Path = CORPUS) -> bool:
    return check_case(case, path).status == "fallback"


def measure(path: Path = CORPUS) -> dict[str, Any]:
    cases = load(path)["cases"]
    false = [c for c in cases if c["label"] == "false"]
    correct = [c for c in cases if c["label"] == "correct"]
    caught = [c for c in false if flagged(c, path)]
    alarms = [c for c in correct if flagged(c, path)]
    return {
        "false": false,
        "correct": correct,
        "missed": [c for c in false if c not in caught],
        "alarms": alarms,
        "recall": len(caught) / len(false),
        "false_alarm_rate": len(alarms) / len(correct),
    }


def test_the_corpus_is_labelled_and_sized():
    cases = corpus()["cases"]
    assert len({c["id"] for c in cases}) == len(cases)
    assert {c["label"] for c in cases} == {"correct", "false"}
    by_source = {s: [c for c in cases if c["source"] == s] for s in ("relay", "stub", "probe")}
    assert len(by_source["relay"]) >= 39 and len(by_source["stub"]) >= 40
    assert len(by_source["probe"]) == 13
    written = [c for c in cases if c["source"] == "written"]
    assert sum(c["label"] == "false" for c in written) >= 30
    assert sum(c["label"] == "correct" for c in written) >= 30
    assert all(c.get("note") for c in cases if c["source"] in ("written", "probe"))


@pytest.mark.parametrize(
    "case", [c for c in corpus()["cases"] if c["source"] == "probe"], ids=lambda c: c["id"]
)
def test_every_audit_probe_is_caught(case):
    report = check_case(case)
    assert report.status == "fallback", report


def test_recall_and_false_alarms_meet_the_gate():
    result = measure()
    missed = [(c["id"], c["answer"][:80]) for c in result["missed"]]
    alarms = [(c["id"], check_case(c).issues) for c in result["alarms"]]
    assert result["recall"] >= MIN_RECALL, missed
    assert result["false_alarm_rate"] <= MAX_FALSE_ALARMS, alarms


def main(path: Path = CORPUS) -> None:
    result = measure(path)
    print(
        f"false answers: {len(result['false'])}, caught "
        f"{len(result['false']) - len(result['missed'])} (recall {result['recall']:.3f})"
    )
    print(
        f"correct answers: {len(result['correct'])}, flagged {len(result['alarms'])} "
        f"(false-alarm rate {result['false_alarm_rate']:.3f})"
    )
    for case in result["missed"]:
        print(f"MISSED {case['id']}: {case['answer'][:160]!r} ({case.get('note', '')})")
    for case in result["alarms"]:
        print(f"ALARM {case['id']}: {check_case(case, path).issues}")


if __name__ == "__main__":
    import sys

    main(Path(sys.argv[1]) if len(sys.argv) > 1 else CORPUS)
