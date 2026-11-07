"""Grounding on held-out sets: answers written blind, after the check was frozen.

`grounding_heldout/v1.json` (80 answers: 39 false, 41 correct) was the first blind set.
Measured blind, before any fix, the check caught 33 of the 39 false answers (recall 0.846)
and flagged 7 of the 41 correct ones (false alarms 0.171). The set was then used for
development (the fixes are general, by category, never by string), so it is no longer
blind: the numbers below are a regression floor, not an estimate of how the check does on
new answers. That is what the next blind set (v2) measures.
"""

from pathlib import Path

from tests.agents.test_grounding_corpus import check_case, measure

V1 = Path(__file__).resolve().parent / "grounding_heldout" / "v1.json"
# After the fixes, on v1 (no longer blind): every false answer caught, no correct one flagged.
V1_MIN_RECALL = 1.0
V1_MAX_FALSE_ALARMS = 0.0


def test_held_out_v1_is_labelled_and_sized():
    result = measure(V1)
    assert len(result["false"]) == 39 and len(result["correct"]) == 41
    assert all(c["note"] != "correct" for c in result["false"])


def test_held_out_v1_stays_at_its_floor():
    result = measure(V1)
    missed = [(c["id"], c["note"]) for c in result["missed"]]
    alarms = [(c["id"], check_case(c, V1).issues) for c in result["alarms"]]
    assert result["recall"] >= V1_MIN_RECALL, missed
    assert result["false_alarm_rate"] <= V1_MAX_FALSE_ALARMS, alarms
