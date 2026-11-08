"""The scorer freeze: which task file and scorer a run was scored with, and a check that they
are the frozen ones.

A run records `fingerprint()` (the scorer version and the SHA-256 of the task file, the
scorer and the task schema) in `run.json`, `summary.json` and its report. `FROZEN.json`,
committed before a model is run, holds the fingerprint the next headline run must be scored
with: `run_eval --frozen` refuses to run or rescore when the files differ from it. Changing
the tasks or the scorer after a run has been seen is still possible, but only as a recorded
rescore (`--rescore --rescore-note "..."`), never silently.
"""

import hashlib
import json
from pathlib import Path

from evals.scoring import SCORER_VERSION

EVALS = Path(__file__).resolve().parent
ROOT = EVALS.parent
FROZEN = EVALS / "FROZEN.json"
DEFAULT_TASKS = EVALS / "tasks.yaml"
# The files whose content decides a verdict, besides the task file.
SCORER_FILES = (EVALS / "scoring.py", EVALS / "tasks.py")


def relative(path: Path) -> str:
    """A path relative to the repository root when it is inside it (reports are committed)."""
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(tasks: Path = DEFAULT_TASKS) -> dict:
    """The scorer version and the hashes of the task file, the scorer and the task schema."""
    files = {relative(path): sha256(path) for path in (Path(tasks), *SCORER_FILES)}
    return {"scorer_version": SCORER_VERSION, "files": files}


def short(print_: dict | None) -> str:
    """A one-line form for reports: version and the first 12 hex digits of each hash."""
    if not print_:
        return "not recorded"
    files = ", ".join(f"`{name}` {digest[:12]}" for name, digest in print_["files"].items())
    return f"scorer {print_['scorer_version']}; {files}"


def read_frozen(path: Path = FROZEN) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def differences(current: dict, frozen: dict | None) -> list[str]:
    """Why `current` is not the frozen fingerprint; empty when it is."""
    if frozen is None:
        return ["no FROZEN.json"]
    problems = []
    if current["scorer_version"] != frozen.get("scorer_version"):
        problems.append(
            f"scorer version {current['scorer_version']} != frozen {frozen.get('scorer_version')}"
        )
    wanted = frozen.get("files") or {}
    for name in sorted(set(wanted) | set(current["files"])):
        have, want = current["files"].get(name), wanted.get(name)
        if have != want:
            problems.append(f"{name}: {str(have)[:12]} != frozen {str(want)[:12]}")
    return problems
