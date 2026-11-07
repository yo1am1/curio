"""Budgets in dollars or pounds: read, used as euros, never converted, and always noted.

The agent evaluation found "$150" silently treated as €150: the app had no currency
handling, so neither the shopper nor the model was told the catalog is in euros.
"""

import pytest

from curio.domains.fashion.currency import (
    budget_in_text,
    currency_note,
    currency_reminder,
    foreign_amounts,
    foreign_note,
    parse_amount,
)


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("budget $150 for the outfit", 150, "USD"),
        ("under $99.50", 99.5, "USD"),
        ("under US$80", 80, "USD"),
        ("max 150 USD", 150, "USD"),
        ("up to USD 200", 200, "USD"),
        ("under 120 dollars", 120, "USD"),
        ("a $150 budget", 150, "USD"),
        ("under £80", 80, "GBP"),
        ("within 60 GBP", 60, "GBP"),
        ("budget of 75 pounds", 75, "GBP"),
        ("under 40 quid", 40, "GBP"),
        ("under €100", 100, "EUR"),
        ("under 80 euros", 80, "EUR"),
        ("budget of 1,500 EUR", 1500, "EUR"),
        ("under 99,50 €", 99.5, "EUR"),
    ],
)
def test_a_budget_is_read_in_the_currency_it_was_written_in(text, amount, currency):
    money = budget_in_text(f"Plan a date night, {text}, please")
    assert money is not None
    assert (money.amount, money.currency) == (amount, currency)
    assert money.foreign == (currency != "EUR")


@pytest.mark.parametrize("text", ["party for 4 friends", "under 80", "size 42 trainers", ""])
def test_no_currency_is_no_budget(text):
    assert budget_in_text(text) is None
    assert foreign_amounts(text) == []
    assert foreign_note(text) is None


def test_euro_amounts_need_no_note():
    assert foreign_note("Black trousers under €30") is None
    assert foreign_note("a dress for 25 euros") is None


def test_a_foreign_budget_is_used_as_euros_and_said_so():
    note = foreign_note("Plan a business dinner for men, budget $150 for the outfit", 150)
    assert note == currency_note(budget_in_text("budget $150"))
    assert note == (
        "Prices are in euros; I used 150 as a € budget. You gave it in US dollars, and Curio "
        "does not convert currencies."
    )
    # The note never writes another currency's sign, so an answer may repeat it.
    assert "$" not in note and "£" not in note
    pounds = foreign_note("an outfit under £80", 80)
    assert "used 80 as a € budget" in pounds and "pounds" in pounds


def test_an_amount_not_used_as_the_budget_only_gets_the_reminder():
    """No conversion is ever made up: a different budget, or none, gets the reminder."""
    for budget in (None, 138.5):
        note = foreign_note("budget $150 for the outfit", budget)
        assert note == (
            "Prices are in euros, and Curio does not convert currencies: you gave 150 in US "
            "dollars."
        )
    assert foreign_note("Is that dress really $20?") == currency_reminder(foreign_amounts("$20")[0])


def test_amounts_keep_their_separators():
    assert parse_amount("1,500") == 1500
    assert parse_amount("1 500") == 1500
    assert parse_amount("99,5") == 99.5
    assert parse_amount("99.50") == 99.5
    assert parse_amount("1.2.3") is None
