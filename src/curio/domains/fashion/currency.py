"""Budgets written in text, and what Curio does with a currency other than the euro.

Catalog prices are euros, and Curio has no exchange rates. A budget in dollars or pounds
("$150", "£80", "150 USD", "80 pounds") is never converted with a made-up rate: the amount is
used as a euro budget, and `currency_note` says so in the plan or the tool result, so the
shopper and the model both see it ("Prices are in euros; I used 150 as a € budget …").
"""

import re
from dataclasses import dataclass
from typing import Literal

Currency = Literal["EUR", "USD", "GBP"]
CURRENCY_NAMES: dict[Currency, str] = {"EUR": "euros", "USD": "US dollars", "GBP": "pounds"}

# "150", "99.50", "1,500", "1 500", "99,5": digits with thousands or decimal separators.
_AMOUNT = r"(\d[\d,. ]*\d|\d)"
# A currency before the amount ("€150", "$ 150", "US$150", "USD 150") or after it
# ("150 €", "150 eur", "150 dollars", "80 quid").
_BEFORE = r"(US\$|\$|€|£|eur\b|usd\b|gbp\b)\s?"
_AFTER = (
    r"\s?(€|\$|£|eur\b|euros?\b|usd\b|gbp\b|dollars?\b|bucks\b|pounds?(?:\s+sterling)?\b|"
    r"quid\b)"
)
_MONEY = rf"(?:{_BEFORE}{_AMOUNT}|{_AMOUNT}{_AFTER})"
# A limit word before the amount ("under $150", "budget of £80", "max 150 USD"), or the
# word "budget" after it ("a $150 budget").
_LIMIT = r"(?:under|below|less than|up to|within|max(?:imum)?|budget(?: of| is)?)"
_BUDGET = re.compile(
    rf"{_LIMIT}\s*{_MONEY}|{_MONEY}\s+(?:budget|max(?:imum)?|limit)\b", re.IGNORECASE
)
_ANY_MONEY = re.compile(rf"(?<![\w$€£]){_MONEY}", re.IGNORECASE)
_THOUSANDS = re.compile(r"\d{1,3}(?:[, ]\d{3})+(?:\.\d+)?")
_DECIMAL_COMMA = re.compile(r"\d+,\d{1,2}")


@dataclass(frozen=True)
class Money:
    amount: float
    currency: Currency
    written: str  # as the shopper wrote it: "$150"

    @property
    def foreign(self) -> bool:
        return self.currency != "EUR"


def _currency(sign: str) -> Currency:
    sign = sign.strip().casefold()
    if sign in ("$", "us$", "usd", "dollar", "dollars", "bucks"):
        return "USD"
    if sign in ("£", "gbp", "quid") or sign.startswith("pound"):
        return "GBP"
    return "EUR"


def parse_amount(raw: str) -> float | None:
    """A written amount ("1,500", "99,5", "1 500") as a number, None when it is not one."""
    raw = raw.strip()
    if _THOUSANDS.fullmatch(raw):
        raw = raw.replace(",", "").replace(" ", "")
    elif _DECIMAL_COMMA.fullmatch(raw):
        raw = raw.replace(",", ".")
    try:
        return float(raw.replace(" ", ""))
    except ValueError:
        return None


def _money(groups: tuple[str | None, ...], written: str) -> Money | None:
    """Money from the four groups of `_MONEY`: sign and amount, or amount and sign."""
    sign_before, amount_before, amount_after, sign_after = groups
    raw = amount_before or amount_after or ""
    amount = parse_amount(raw)
    if amount is None:
        return None
    return Money(amount, _currency(sign_before or sign_after or ""), written.strip())


def budget_in_text(text: str) -> Money | None:
    """The first budget `text` writes with a currency ("under €100", "budget $150",
    "a £80 budget"), in the currency it was written in."""
    for match in _BUDGET.finditer(text):
        groups = match.groups()
        found = _money(groups[:4] if any(groups[:4]) else groups[4:], match.group(0))
        if found is not None:
            return found
    return None


def foreign_amounts(text: str) -> list[Money]:
    """Every amount `text` writes in dollars or pounds ("$150", "80 GBP", "50 quid")."""
    found = []
    for match in _ANY_MONEY.finditer(text):
        money = _money(match.groups(), match.group(0))
        if money is not None and money.foreign:
            found.append(money)
    return found


def _figure(amount: float) -> str:
    return f"{amount:.0f}" if amount.is_integer() else f"{amount:.2f}"


def currency_note(money: Money) -> str:
    """What Curio did with a budget in another currency: used as euros, never converted. It
    names the currency in words, never with its sign, so an answer that repeats it states no
    price in another currency."""
    figure = _figure(money.amount)
    return (
        f"Prices are in euros; I used {figure} as a € budget. You gave it in "
        f"{CURRENCY_NAMES[money.currency]}, and Curio does not convert currencies."
    )


def currency_reminder(money: Money) -> str:
    """The note for an amount in another currency that was not used as the budget."""
    return (
        f"Prices are in euros, and Curio does not convert currencies: you gave "
        f"{_figure(money.amount)} in {CURRENCY_NAMES[money.currency]}."
    )


def foreign_note(text: str, budget: float | None = None) -> str | None:
    """The note for the first amount `text` writes in dollars or pounds (a budget first,
    else any amount), or None when it writes none: `currency_note` when `budget` is that
    amount (it was used as euros), else `currency_reminder`."""
    written = budget_in_text(text)
    money = written if written is not None and written.foreign else None
    if money is None:
        found = foreign_amounts(text)
        money = found[0] if found else None
    if money is None:
        return None
    if budget is not None and abs(budget - money.amount) < 0.005:
        return currency_note(money)
    return currency_reminder(money)
