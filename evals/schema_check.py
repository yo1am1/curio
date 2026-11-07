"""A small JSON Schema validator for the tool and response schemas a chat request carries.

It covers the subset pydantic and LangChain emit for tool parameters: types, enums and
consts, objects (properties, required, additionalProperties), arrays (items, length),
strings (length, pattern), numbers (bounds), anyOf / oneOf / allOf and local `$ref`s.
Unknown keywords are ignored, so a schema that uses more is checked less strictly, never
rejected wrongly. Pydantic validators that JSON Schema cannot express ("give exactly one
of book_id or title") are not checked here; the agent's tool validation still applies them.
"""

import re
from typing import Any

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "integer": lambda v: (
        (isinstance(v, int) and not isinstance(v, bool))
        or (isinstance(v, float) and v.is_integer())
    ),
}


def validate(value: Any, schema: dict, root: dict | None = None) -> list[str]:
    """Every way `value` breaks `schema`, as readable messages; empty when it is valid."""
    errors: list[str] = []
    _check(value, schema, root if root is not None else schema, "$", errors)
    return errors


def _resolve(schema: dict, root: dict) -> dict:
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 20:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            return {}
        target: Any = root
        for part in ref[2:].split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        extra = {k: v for k, v in schema.items() if k != "$ref"}
        schema = {**target, **extra}
        seen += 1
    return schema


def _check(value: Any, schema: Any, root: dict, path: str, errors: list[str]) -> None:
    if schema is True or schema is None or schema == {}:
        return
    if schema is False:
        errors.append(f"{path}: no value is allowed here")
        return
    schema = _resolve(schema, root)
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            branches = [validate(value, branch, root) for branch in schema[keyword]]
            passing = sum(1 for branch in branches if not branch)
            if passing == 0:
                detail = "; ".join(b[0] for b in branches if b)
                errors.append(f"{path}: matches none of the allowed forms ({detail})")
                return
            if keyword == "oneOf" and passing > 1:
                errors.append(f"{path}: matches more than one allowed form")
                return
    for branch in schema.get("allOf", []):
        _check(value, branch, root, path, errors)
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        if not any(_TYPES.get(name, lambda _: True)(value) for name in names):
            errors.append(f"{path}: expected {' or '.join(names)}, got {_kind(value)}")
            return
    if isinstance(value, dict):
        _object(value, schema, root, path, errors)
    elif isinstance(value, list):
        _array(value, schema, root, path, errors)
    elif isinstance(value, str):
        _string(value, schema, path, errors)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        _number(value, schema, path, errors)


def _object(value: dict, schema: dict, root: dict, path: str, errors: list[str]) -> None:
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in value:
            errors.append(f"{path}: missing required field {name!r}")
    extra = schema.get("additionalProperties", True)
    for name, item in value.items():
        if name in properties:
            _check(item, properties[name], root, f"{path}.{name}", errors)
        elif extra is False:
            allowed = ", ".join(properties) or "none"
            errors.append(f"{path}: unknown field {name!r} (allowed: {allowed})")
        elif isinstance(extra, dict):
            _check(item, extra, root, f"{path}.{name}", errors)


def _array(value: list, schema: dict, root: dict, path: str, errors: list[str]) -> None:
    if (low := schema.get("minItems")) is not None and len(value) < low:
        errors.append(f"{path}: needs at least {low} items, got {len(value)}")
    if (high := schema.get("maxItems")) is not None and len(value) > high:
        errors.append(f"{path}: allows at most {high} items, got {len(value)}")
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            _check(item, items, root, f"{path}[{index}]", errors)


def _string(value: str, schema: dict, path: str, errors: list[str]) -> None:
    if (low := schema.get("minLength")) is not None and len(value) < low:
        errors.append(f"{path}: needs at least {low} characters")
    if (high := schema.get("maxLength")) is not None and len(value) > high:
        errors.append(f"{path}: allows at most {high} characters, got {len(value)}")
    if (pattern := schema.get("pattern")) is not None and not re.search(pattern, value):
        errors.append(f"{path}: does not match the pattern {pattern!r}")


def _number(value: float, schema: dict, path: str, errors: list[str]) -> None:
    if (low := schema.get("minimum")) is not None and value < low:
        errors.append(f"{path}: must be at least {low}")
    if (high := schema.get("maximum")) is not None and value > high:
        errors.append(f"{path}: must be at most {high}")
    if (low := schema.get("exclusiveMinimum")) is not None and value <= low:
        errors.append(f"{path}: must be greater than {low}")
    if (high := schema.get("exclusiveMaximum")) is not None and value >= high:
        errors.append(f"{path}: must be less than {high}")


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    for name in ("boolean", "integer", "number", "string", "array", "object"):
        if _TYPES[name](value):
            return name
    return type(value).__name__
