"""The scorer freeze: fingerprints, the comparison with FROZEN.json and relative paths."""

import json

from evals import freeze
from evals.scoring import SCORER_VERSION


def test_a_fingerprint_names_the_task_file_scorer_and_schema_by_relative_path():
    print_ = freeze.fingerprint()
    assert print_["scorer_version"] == SCORER_VERSION
    assert set(print_["files"]) == {"evals/tasks.yaml", "evals/scoring.py", "evals/tasks.py"}
    assert all(len(digest) == 64 for digest in print_["files"].values())


def test_differences_name_each_changed_file(tmp_path):
    tasks = tmp_path / "tasks.yaml"
    tasks.write_text("version: 1\ntasks: []\n")
    current = freeze.fingerprint(tasks)
    frozen = tmp_path / "FROZEN.json"
    frozen.write_text(json.dumps(current))
    assert freeze.differences(current, freeze.read_frozen(frozen)) == []
    tasks.write_text("version: 1\ntasks: []\n# edited\n")
    problems = freeze.differences(freeze.fingerprint(tasks), freeze.read_frozen(frozen))
    assert len(problems) == 1 and problems[0].startswith("tasks.yaml: ")
    other = current | {"scorer_version": "0.0.1"}
    assert any("scorer version" in p for p in freeze.differences(other, current))
    assert freeze.differences(current, None) == ["no FROZEN.json"]


def test_short_form_for_reports():
    text = freeze.short(freeze.fingerprint())
    assert text.startswith(f"scorer {SCORER_VERSION}; `evals/tasks.yaml` ")
    assert freeze.short(None) == "not recorded"


def test_paths_inside_the_repository_are_relative(tmp_path):
    assert freeze.relative(freeze.DEFAULT_TASKS) == "evals/tasks.yaml"
    assert freeze.relative(tmp_path / "x" / "tasks.yaml") == "tasks.yaml"
