"""An evening budget in dollars or pounds is used as euros, never converted, and the plan
says so in a note the shopper and the model both see."""

import pytest

from curio.domains.evening.models import EveningRequest
from curio.domains.evening.planner import budget_in, budget_note

DOLLAR_NOTE = (
    "Prices are in euros; I used 150 as a € budget. You gave it in US dollars, and Curio does "
    "not convert currencies."
)


@pytest.mark.parametrize(
    ("occasion", "amount"),
    [
        ("business dinner for men, budget $150 for the outfit", 150),
        ("date night under £80", 80),
        ("date night under 80 GBP", 80),
        ("garden party, max 120 USD", 120),
        ("date night under 90 dollars", 90),
        ("date night under 60 pounds", 60),
        ("date night, a $70 budget", 70),
    ],
)
def test_a_foreign_budget_in_the_occasion_is_the_euro_budget_with_a_note(planner, occasion, amount):
    assert budget_in(occasion) == amount
    budget, note = budget_note(occasion)
    assert budget == amount
    assert note is not None and note.startswith(f"Prices are in euros; I used {amount} as a €")
    evening = planner.plan(EveningRequest(occasion=occasion))
    assert evening.state.budget == amount
    assert evening.notes[0] == note
    assert evening.outfit.outfit.budget == amount


def test_euro_budgets_get_no_note(planner):
    assert budget_note("date night under €100") == (100, None)
    assert budget_note("date night under 80 euros") == (80, None)
    evening = planner.plan(EveningRequest(occasion="date night under €60"))
    assert not any("euros" in note for note in evening.notes)


def test_a_given_budget_keeps_the_note_when_the_occasion_wrote_it_in_dollars(planner):
    evening = planner.plan(
        EveningRequest(occasion="business dinner for men, budget $150", budget=150)
    )
    assert evening.state.budget == 150
    assert evening.notes[0] == DOLLAR_NOTE
    # A different budget than the one written was not made from it by Curio: only a reminder.
    other = planner.plan(EveningRequest(occasion="business dinner, budget $150", budget=140))
    assert other.state.budget == 140
    assert other.notes[0] == (
        "Prices are in euros, and Curio does not convert currencies: you gave 150 in US dollars."
    )


def test_an_out_of_range_foreign_budget_is_no_budget():
    budget, note = budget_note("gala under $6000")
    assert budget is None and note.startswith("Planned without a budget")
