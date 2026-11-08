"""Grounding on held-out sets: answers written blind, after the check was frozen.

`grounding_heldout/v1.json` (80 answers: 39 false, 41 correct) was the first blind set.
Measured blind, before any fix, the check caught 33 of the 39 false answers (recall 0.846)
and flagged 7 of the 41 correct ones (false alarms 0.171).

`grounding_heldout/v2.json` (100 answers: 51 false, 49 correct) was the second. Measured
blind on v2.1 (tuned on v1, where it scored 1.0 / 0.0), the check caught 46 of the 51 false
answers (recall 0.902) and flagged 9 of the 49 correct ones (false alarms 0.184).

Both sets were then used for development (the fixes are general, by category, never by
string), so neither is blind any more: the numbers below are regression floors, not
estimates of how the check does on new answers.

`grounding_heldout/v3.json` (120 answers: 60 false, 60 correct, 40 per domain) was the third,
written fresh and scored once, blind, on v2.2 (tuned on v1 and v2, where it scored 1.0 / 0.0):
it caught 48 of the 60 false answers (recall 0.800) and flagged 15 of the 60 correct ones
(false alarms 0.250). That is the number to quote for the free-text check. v3 was not used to
tune the check: its floor is its blind result, and the check's rules were left as they were.
Answers now state catalog facts through fact references the server renders (cite mode,
`curio.engine.facts`); this check is the backstop for values the model still types.
"""

from pathlib import Path

import pytest

from tests.agents.test_grounding_corpus import check_case, measure

HERE = Path(__file__).resolve().parent / "grounding_heldout"
V1 = HERE / "v1.json"
V2 = HERE / "v2.json"
V3 = HERE / "v3.json"
# v1 and v2 after development use (no longer blind): every false answer caught, no correct
# one flagged.
FLOORS = {
    V1: {"false": 39, "correct": 41, "min_recall": 1.0, "max_false_alarms": 0.0},
    V2: {"false": 51, "correct": 49, "min_recall": 1.0, "max_false_alarms": 0.0},
    # Blind (never tuned on): 48 of 60 caught, 15 of 60 flagged.
    V3: {"false": 60, "correct": 60, "min_recall": 0.80, "max_false_alarms": 0.25},
}


@pytest.mark.parametrize("path", FLOORS, ids=lambda path: path.stem)
def test_held_out_set_is_labelled_and_sized(path):
    result = measure(path)
    assert len(result["false"]) == FLOORS[path]["false"]
    assert len(result["correct"]) == FLOORS[path]["correct"]
    assert all(c["note"] != "correct" for c in result["false"])


@pytest.mark.parametrize("path", FLOORS, ids=lambda path: path.stem)
def test_held_out_set_stays_at_its_floor(path):
    result = measure(path)
    missed = [(c["id"], c["note"]) for c in result["missed"]]
    alarms = [(c["id"], check_case(c, path).issues) for c in result["alarms"]]
    assert result["recall"] >= FLOORS[path]["min_recall"], missed
    assert result["false_alarm_rate"] <= FLOORS[path]["max_false_alarms"], alarms
