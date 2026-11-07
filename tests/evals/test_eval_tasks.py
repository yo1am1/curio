"""The task file: it loads, covers every domain and names only real tools and arguments."""

from collections import Counter

import pytest
from pydantic import ValidationError

from curio.domains.books.agent.tools import BOOK_TOOLS
from curio.domains.cocktails.agent.tools import COCKTAIL_TOOLS
from curio.domains.evening.agent.tools import EVENING_TOOLS
from curio.domains.fashion.agent.intent import SearchIntent
from curio.domains.fashion.agent.tools import TOOLS as FASHION_TOOLS
from evals.run_eval import DEFAULT_TASKS
from evals.tasks import Task, TaskFile, load_tasks, select

DOMAIN_TOOLS = {
    "fashion": {**FASHION_TOOLS, "SearchIntent": SearchIntent},
    "cocktails": COCKTAIL_TOOLS,
    "books": BOOK_TOOLS,
    "evening": EVENING_TOOLS,
}
TASKS = load_tasks(DEFAULT_TASKS)


def test_the_task_set_is_about_forty_tasks_over_four_domains():
    domains = Counter(task.domain for task in TASKS)
    assert 38 <= len(TASKS) <= 50
    assert set(domains) == set(DOMAIN_TOOLS)
    assert all(count >= 10 for count in domains.values())
    categories = Counter(task.category for task in TASKS)
    assert set(categories) == {
        "straightforward",
        "follow_up",
        "clarification",
        "approval",
        "decision",
        "adversarial",
    }
    tags = Counter(tag for task in TASKS for tag in task.tags)
    assert tags["injection"] >= 4
    assert tags["safety"] >= 6


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.id)
def test_tasks_name_real_tools_and_arguments(task):
    tools = DOMAIN_TOOLS[task.domain]
    for turn in task.turns:
        expect = turn.expect
        named = [
            *expect.tools_any,
            *expect.tools_all,
            *expect.tools_forbidden,
            *expect.approval_tool,
        ]
        assert set(named) <= set(tools), f"{task.id}: unknown tools {set(named) - set(tools)}"
        for check in expect.args:
            for name in check.tools:
                assert name in tools, f"{task.id}: unknown tool {name}"
                fields = tools[name].model_fields
                for path in check.where:
                    assert path == "$text" or path.split(".")[0] in fields, f"{task.id}: {path}"


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.id)
def test_every_turn_checks_something(task):
    for turn in task.turns:
        assert turn.expect.model_fields_set, f"{task.id}: a turn without expectations"


def test_decisions_need_a_proposal_before_them():
    for task in TASKS:
        for previous, turn in zip(task.turns, task.turns[1:], strict=False):
            if turn.decision is not None:
                assert previous.expect.status == ["needs_approval"], task.id


def test_select_by_id_or_domain():
    assert {t.domain for t in select(TASKS, ["books"])} == {"books"}
    picked = select(TASKS, ["books-vague,evening-vague"])
    assert [t.id for t in picked] == ["books-vague", "evening-vague"]
    with pytest.raises(ValueError, match="unknown"):
        select(TASKS, ["nope"])


def test_invalid_tasks_are_rejected():
    base = {"id": "x", "domain": "books", "category": "straightforward"}
    with pytest.raises(ValidationError, match="either a message or a decision"):
        Task.model_validate(base | {"turns": [{"expect": {}}]})
    with pytest.raises(ValidationError, match="first turn cannot be a decision"):
        Task.model_validate(base | {"turns": [{"decision": {"approve": True}}]})
    with pytest.raises(ValidationError, match="Extra inputs"):
        Task.model_validate(base | {"turns": [{"message": "hi", "expect": {"stauts": "x"}}]})
    with pytest.raises(ValidationError, match="Duplicate task ids"):
        TaskFile.model_validate({"tasks": [base | {"turns": [{"message": "a"}]}] * 2})
