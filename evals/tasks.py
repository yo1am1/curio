"""The evaluation's task format: one or more turns, each with machine-checkable expectations.

A task runs in one domain from an optional client state (`context`, `history`), then sends
its turns in order, as the UI would: a message, or a decision on the proposal the previous
turn produced (approve, approve narrowed to the first N values, or decline). Each turn's
`expect` block lists what is checked; see docs/agent-eval.md for every check.
"""

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Domain = Literal["fashion", "cocktails", "books", "evening"]
Status = Literal["complete", "needs_input", "needs_approval"]
Category = Literal[
    "straightforward", "follow_up", "clarification", "approval", "decision", "adversarial"
]
# Grounding outcomes of a complete turn (see `scoring.grounding_outcome`).
GroundingOutcome = Literal[
    "verified", "repaired", "fallback", "not_applicable", "budget_fallback", "empty_fallback"
]
Safety = Literal[
    # Every drink card (reply and proposal) is alcohol-free ("Non alcoholic" in the catalog).
    "alcohol_free_drinks",
    # No book card carries an excluded reader shelf (erotica, ...; books.taxonomy).
    "no_excluded_shelves",
    # Every product card is from the children's section.
    "kids_products_only",
    # An answer that shows drinks states an alcohol status.
    "labels_alcohol_status",
]
DEFAULT_GROUNDING: tuple[GroundingOutcome, ...] = ("verified", "repaired", "not_applicable")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Condition(_Strict):
    """One constraint on a tool argument. Strings compare case-insensitively."""

    eq: Any = None
    ne: Any = None
    le: float | None = None
    ge: float | None = None
    # A list argument holds this value, or a string argument contains this text.
    contains: Any = None
    contains_any: list[Any] | None = None
    # A regular expression the value (as text) matches, ignoring case.
    matches: str | None = None
    # The argument is given (true) or left out (false).
    present: bool | None = None

    @model_validator(mode="after")
    def _one_test(self) -> "Condition":
        if not self.model_fields_set:
            raise ValueError("A condition needs at least one test")
        return self


class ArgCheck(_Strict):
    """Some call to one of `tools` this turn has arguments meeting every condition. Scored
    only when the model called one of the tools (tool selection is scored separately).

    `where` keys are argument names, dotted paths into nested arguments, or `$text`: all the
    arguments as one JSON text (for "gin is in the query or the ingredients")."""

    tools: list[str] = Field(min_length=1)
    where: dict[str, Condition] = Field(min_length=1)
    note: str = ""

    @field_validator("tools", mode="before")
    @classmethod
    def _listify(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value


class Cards(_Strict):
    """Limits on the cards a reply shows."""

    max_price: float | None = None  # every product card
    max_pages: int | None = None  # every book card (unknown page counts fail)
    max_outfit_total: float | None = None  # an outfit's or evening outfit's total


class Expect(_Strict):
    status: list[Status] = Field(default_factory=list)
    tools_any: list[str] = Field(default_factory=list)
    tools_all: list[str] = Field(default_factory=list)
    tools_forbidden: list[str] = Field(default_factory=list)
    args: list[ArgCheck] = Field(default_factory=list)
    approval_tool: list[str] = Field(default_factory=list)
    applied: bool | None = None
    grounding: list[GroundingOutcome] | None = None
    # Case-insensitive substrings and regular expressions the final text must not contain.
    forbid_phrases: list[str] = Field(default_factory=list)
    forbid_regex: list[str] = Field(default_factory=list)
    # Regular expressions for claims: a match is excused when a negation ("not", "can't",
    # "unable", ...) comes just before it or inside it.
    forbid_claims: list[str] = Field(default_factory=list)
    must_mention_all: list[str] = Field(default_factory=list)
    must_mention_any: list[str] = Field(default_factory=list)
    must_match_any: list[str] = Field(default_factory=list)
    safety: list[Safety] = Field(default_factory=list)
    cards: Cards | None = None
    # Ground truth for the clarification metric; by default, whether `status` is only
    # needs_input. "either" leaves the turn out of that metric.
    clarify: bool | Literal["either"] | None = None

    @field_validator(
        "status",
        "tools_any",
        "tools_all",
        "tools_forbidden",
        "approval_tool",
        "grounding",
        mode="before",
    )
    @classmethod
    def _listify(cls, value: object) -> object:
        return [value] if isinstance(value, str) else value

    @model_validator(mode="after")
    def _patterns_compile(self) -> "Expect":
        for pattern in (*self.forbid_regex, *self.forbid_claims, *self.must_match_any):
            re.compile(pattern)
        return self

    @property
    def wants_clarification(self) -> bool | None:
        """Whether the turn should ask a question; None when either is fine."""
        if self.clarify == "either":
            return None
        if self.clarify is not None:
            return self.clarify
        return self.status == ["needs_input"]


class Decision(_Strict):
    """The shopper's answer to the previous turn's proposal."""

    approve: bool
    # Approve only the first N values of every list in the proposal (narrowing).
    keep: int | None = Field(default=None, ge=1)


class TurnSpec(_Strict):
    message: str | None = Field(default=None, min_length=1, max_length=2000)
    decision: Decision | None = None
    # Client state merged in before this turn (e.g. a remembered taste).
    context: dict[str, Any] = Field(default_factory=dict)
    expect: Expect = Field(default_factory=Expect)

    @model_validator(mode="after")
    def _message_or_decision(self) -> "TurnSpec":
        if (self.message is None) == (self.decision is None):
            raise ValueError("A turn has either a message or a decision")
        return self


class HistoryTurn(_Strict):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)
    product_ids: list[int] = Field(default_factory=list, max_length=5)
    drink_ids: list[int] = Field(default_factory=list, max_length=5)
    book_ids: list[int] = Field(default_factory=list, max_length=5)


class Task(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    domain: Domain
    category: Category
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    history: list[HistoryTurn] = Field(default_factory=list)
    turns: list[TurnSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _decisions_follow_turns(self) -> "Task":
        if self.turns[0].decision is not None:
            raise ValueError(f"{self.id}: the first turn cannot be a decision")
        return self


class TaskFile(_Strict):
    version: int = 1
    tasks: list[Task]

    @model_validator(mode="before")
    @classmethod
    def _drop_anchors(cls, data: object) -> object:
        """Top-level `x-...` keys only hold YAML anchors shared by the tasks."""
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not str(k).startswith("x-")}
        return data

    @model_validator(mode="after")
    def _unique_ids(self) -> "TaskFile":
        ids = [task.id for task in self.tasks]
        if duplicates := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"Duplicate task ids: {duplicates}")
        return self


def load_tasks(path: Path) -> list[Task]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return TaskFile.model_validate(data).tasks


def select(tasks: list[Task], only: list[str] | None) -> list[Task]:
    """Tasks whose id or domain is listed (all when `only` is empty)."""
    if not only:
        return tasks
    wanted = {name.strip() for item in only for name in item.split(",") if name.strip()}
    known = {task.id for task in tasks} | {task.domain for task in tasks}
    if unknown := sorted(wanted - known):
        raise ValueError(f"--only names unknown tasks or domains: {', '.join(unknown)}")
    return [task for task in tasks if task.id in wanted or task.domain in wanted]
