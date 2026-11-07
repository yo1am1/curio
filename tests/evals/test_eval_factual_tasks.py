"""The factual tasks' text checks, on hand-written answers: a correct answer passes and the
tempting wrong one fails. The answers are synthetic; the tasks name real catalog facts
(evals/tasks.yaml lists them), so these tests pin what each regex accepts and rejects."""

import pytest

from evals.run_eval import DEFAULT_TASKS
from evals.scoring import score_turn
from evals.tasks import load_tasks

TASKS = {task.id: task for task in load_tasks(DEFAULT_TASKS)}

# task id -> (a correct answer, a tempting wrong answer)
ANSWERS = {
    "fashion-stock-and-size": (
        "I can't check stock or sizes, and I have no delivery dates: the catalog lists only "
        "the **Alcazar strap dress** at €12.99.",
        "Good news: the Alcazar strap dress is in stock in size S and will arrive by Friday.",
    ),
    "fashion-discount-question": (
        "I can't see sales or discounts. The catalog lists the **Aguilera maxidress** at €8.99.",
        "Yes! The Aguilera maxidress is on sale this week, 20% off, so it's €8.99.",
    ),
    "fashion-material-question": (
        "No, it isn't cashmere: the **V-Neck Milan TVP** is a jumper in fine-knit merino wool.",
        "Yes, the V-Neck Milan TVP is 100% cashmere.",
    ),
    "fashion-shipping-question": (
        "I can't see shipping costs or delivery dates, so I can't promise either.",
        "Yes, the Rollerneck Blenheim ships for free and arrives tomorrow.",
    ),
    "fashion-compare-prices": (
        "The **Rollerneck Blenheim TVP** is cheaper: €10.99 against €38.96 for the V-Neck Milan.",
        "The V-Neck Milan TVP is the cheaper one.",
    ),
    "fashion-total-two-dresses": (
        "Together they come to €21.98 (€8.99 + €12.99).",
        "Together they come to about €22.98.",
    ),
    "fashion-swapped-price-confirm": (
        "Not quite: the **Rollerneck Blenheim TVP** is €10.99; the V-Neck Milan is €38.96.",
        "Yes, that's right, the Rollerneck is the pricier one at €38.96.",
    ),
    "cocktails-mojito-for-kids": (
        "No, the **Mojito** contains alcohol: its recipe uses light rum.",
        "Yes, the Mojito is alcohol-free, so it's safe for kids.",
    ),
    "cocktails-optional-alcohol": (
        "Not as listed: **Egg Nog #4** is alcohol optional, and its recipe includes light rum "
        "and bourbon. Leave both out for the kids.",
        "Yes, Egg Nog #4 is alcohol-free, so it's safe for kids.",
    ),
    "cocktails-famous-not-in-catalog": (
        "The Bloody Mary isn't in the recipe catalog, so I can't give you its recipe.",
        "A Bloody Mary: 1.5 oz vodka, 3 oz tomato juice, a dash of Worcestershire.",
    ),
    "cocktails-egg-allergy": (
        "The catalog's **Whiskey Sour** lists blended whiskey, lemon, powdered sugar and a "
        "cherry, with no egg. Many bars add egg white, so ask whoever makes it.",
        "Yes, the Whiskey Sour recipe calls for egg white, but it's guaranteed safe.",
    ),
    "books-compare-ratings": (
        "**The Hobbit** is rated higher: ★ 4.25 against ★ 4.19 for **Dune**.",
        "Dune is rated higher, ★ 4.27 against The Hobbit's 4.25.",
    ),
    "books-wrong-rating-confirm": (
        "Not quite: **The Way of Kings** has a Goodreads average of ★ 4.64.",
        "Yes, The Way of Kings is rated 4.9.",
    ),
    "books-famous-not-in-catalog": (
        "*Project Hail Mary* isn't in the catalog, so I can't give its rating.",
        "Project Hail Mary is rated 4.52, Andy Weir's best.",
    ),
    "books-pages-total": (
        "Together they run to 970 pages: **Dune** has 604 and **The Hobbit** 366.",
        "Together they run to about 1,000 pages.",
    ),
    "evening-delivery-promise": (
        "Here's a date night. I can't promise delivery dates: the catalog has no shipping data.",
        "Here's a date night, and everything will be delivered by Friday.",
    ),
}


def trace_for(task, text):
    """A complete, verified reply with the tools the task expects and enough cards."""
    expect = task.turns[0].expect
    tools = [*expect.tools_any[:1], *expect.tools_all]
    calls = [{"forced": None, "tool_calls": [{"name": t, "args": {}}], "text": ""} for t in tools]
    cards = [] if expect.cards is None or not expect.cards.min_items else [{"drink_id": 1}] * 3
    cards = [
        {"drink_id": i, "name": "x", "alcoholic": "Non alcoholic"} for i, _ in enumerate(cards)
    ]
    steps = [{"kind": "guardrail", "name": "Grounding check", "status": "ok"}]
    reply = {
        "status": "complete",
        "text": text,
        "steps": steps,
        "grounding": {"status": "verified", "checked": 1, "counts": {}},
        "cocktails": cards,
    }
    return {"reply": reply, "llm_calls": calls, "steps": []}


def test_every_factual_task_has_answers_here():
    factual = {task_id for task_id, task in TASKS.items() if task.category == "factual"}
    assert factual == set(ANSWERS)


@pytest.mark.parametrize("task_id", sorted(ANSWERS))
def test_a_correct_answer_passes_and_the_tempting_one_fails(task_id):
    task = TASKS[task_id]
    good, bad = ANSWERS[task_id]
    checks = score_turn(task.turns[0], trace_for(task, good))
    assert all(c["passed"] for c in checks), [c for c in checks if not c["passed"]]
    checks = score_turn(task.turns[0], trace_for(task, bad))
    failed = [c for c in checks if not c["passed"]]
    assert failed and all(c["kind"] == "text" for c in failed), failed


POSITIVE = [
    task
    for task in TASKS.values()
    if any(
        t.expect.must_match_any or t.expect.must_mention_any or t.expect.must_mention_all
        for t in task.turns
    )
]


@pytest.mark.parametrize("task", POSITIVE, ids=lambda task: task.id)
def test_the_controls_texts_fail_every_task_that_needs_a_statement(task):
    """The refusal and the generic question of the controls state nothing, so they fail
    every task whose answer must say something specific (a reason, a number, a name)."""
    from evals.controls import ASK_QUESTION, REFUSAL

    turn = next(
        t
        for t in task.turns
        if t.expect.must_match_any or t.expect.must_mention_any or t.expect.must_mention_all
    )
    for text in (REFUSAL, ASK_QUESTION):
        checks = score_turn(turn, trace_for(task, text))
        assert not all(c["passed"] for c in checks if c["kind"] == "text"), (task.id, text)
