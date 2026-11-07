"""The relay CLI's JSON Schema subset, on real tool schemas."""

import pytest

from curio.domains.books.agent.tools import PairCocktail
from curio.domains.evening.agent.tools import PlanEvening, SwapEveningPart
from evals.schema_check import validate


def test_valid_arguments_pass():
    schema = SwapEveningPart.model_json_schema()
    assert validate({"part": "outfit", "slot": "shoes", "mode": "cheaper"}, schema) == []
    assert validate({"part": "book"}, schema) == []


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"part": "dessert"}, "is not one of"),
        ({"part": "book", "refine": "x" * 101}, "at most 100 characters"),
        ({}, "missing required field 'part'"),
        ({"part": "book", "colour": "red"}, "unknown field 'colour'"),
        ([], "expected object"),
    ],
)
def test_invalid_arguments_say_why(value, message):
    errors = validate(value, SwapEveningPart.model_json_schema())
    assert any(message in error for error in errors), errors


def test_nullable_and_bounded_numbers():
    schema = PlanEvening.model_json_schema()
    assert validate({"occasion": "date night", "budget": None}, schema) == []
    assert validate({"occasion": "date night", "budget": 100}, schema) == []
    assert any("greater than 0" in e for e in validate({"occasion": "x y", "budget": 0}, schema))
    assert any("pattern" in e for e in validate({"occasion": "  "}, schema))


def test_integers_and_booleans_are_distinct():
    schema = PairCocktail.model_json_schema()
    assert validate({"book_id": 5}, schema) == []
    assert validate({"book_id": 5.0}, schema) == []
    assert validate({"book_id": True}, schema) != []
    assert validate({"alcohol_free": 1, "use_reading_list": True}, schema) != []


def test_local_refs_resolve():
    schema = {
        "$defs": {"Slot": {"type": "string", "enum": ["top", "shoes"]}},
        "type": "object",
        "properties": {"slots": {"type": "array", "items": {"$ref": "#/$defs/Slot"}}},
    }
    assert validate({"slots": ["top"]}, schema) == []
    assert validate({"slots": ["hat"]}, schema) == [
        "$.slots[0]: 'hat' is not one of ['top', 'shoes']"
    ]
