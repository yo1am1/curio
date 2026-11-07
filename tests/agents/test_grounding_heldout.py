"""Grounding on held-out sets: answers written blind, after the check was frozen.

`grounding_heldout/v1.json` (80 answers: 39 false, 41 correct) was the first blind set.
Measured blind, before any fix, the check caught 33 of the 39 false answers (recall 0.846)
and flagged 7 of the 41 correct ones (false alarms 0.171).

`grounding_heldout/v2.json` (100 answers: 51 false, 49 correct) was the second. Measured
blind on v2.1 (tuned on v1, where it scored 1.0 / 0.0), the check caught 46 of the 51 false
answers (recall 0.902) and flagged 9 of the 49 correct ones (false alarms 0.184).

Both sets were then used for development (the fixes are general, by category, never by
string), so neither is blind any more: the numbers below are regression floors, not
estimates of how the check does on new answers. A third blind set (v3) measures that.
"""

from pathlib import Path

import pytest

from tests.agents.test_grounding_corpus import check_case, measure

HERE = Path(__file__).resolve().parent / "grounding_heldout"
V1 = HERE / "v1.json"
V2 = HERE / "v2.json"
# After development use (no longer blind): every false answer caught, no correct one flagged.
FLOORS = {
    V1: {"false": 39, "correct": 41, "min_recall": 1.0, "max_false_alarms": 0.0},
    V2: {"false": 51, "correct": 49, "min_recall": 1.0, "max_false_alarms": 0.0},
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
