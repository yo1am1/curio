"""The checks every domain toolkit must pass (docs/extending.md, contract version 1).

`check_toolkit(toolkit)` is static: it needs no model, no data beyond what the toolkit was
built with, and it never runs a tool. Call it from a new domain's tests; it raises
AssertionError naming the part of the contract that does not hold.
`tests/agents/test_toolkit_contract.py` runs it on the fashion, cocktail, book and example
toolkits.
"""

import inspect
from typing import Any, get_type_hints

from pydantic import BaseModel

from curio.engine import AskShopper, Intent, Policy, Toolkit, Turn, TurnContext
from curio.engine.facts import FactSheet

# The Toolkit protocol's methods, read from the protocol so a new one cannot be forgotten.
PROTOCOL_METHODS = sorted(
    name
    for name, member in vars(Toolkit).items()
    if inspect.isfunction(member) and not name.startswith("_")
)
PROTOCOL_ATTRIBUTES = sorted(get_type_hints(Toolkit))


def check_toolkit(
    toolkit: Any, context: TurnContext | None = None, intent: Intent | None = None
) -> None:
    """Assert that `toolkit` holds the static part of the Toolkit contract.

    `context` and `intent` are an empty instance of the domain's own TurnContext and Intent
    subclasses, for a domain that has them (the default is the engine's base classes)."""
    name = type(toolkit).__name__
    for attribute in PROTOCOL_ATTRIBUTES:
        assert hasattr(toolkit, attribute), f"{name} has no `{attribute}`"
    for method in PROTOCOL_METHODS:
        assert callable(getattr(toolkit, method, None)), f"{name} has no `{method}()`"

    _check_tools(name, toolkit)
    _check_texts(name, toolkit)
    _check_turn_hooks(name, toolkit, context or TurnContext(), intent or Intent(query="hello"))


def _check_tools(name: str, toolkit: Any) -> None:
    tools, policies = toolkit.tools, toolkit.policies
    assert tools, f"{name}.tools is empty"
    assert set(policies) == set(tools), (
        f"{name}: every tool needs exactly one policy; "
        f"differing: {sorted(set(policies) ^ set(tools))}"
    )
    for tool, schema in tools.items():
        assert isinstance(schema, type) and issubclass(schema, BaseModel), (
            f"{name}.tools[{tool!r}] is not a pydantic model"
        )
        assert tool == schema.__name__, (
            f"{name}.tools[{tool!r}] is {schema.__name__}: the model calls a tool by its "
            "schema's name"
        )
        assert schema.model_config.get("extra") == "forbid", (
            f"{tool} must reject unknown fields (model_config = ConfigDict(extra='forbid'))"
        )
        assert (schema.__doc__ or "").strip(), (
            f"{tool} needs a docstring: it is the tool description the model reads"
        )
        policy = policies[tool]
        assert isinstance(policy, Policy), f"{name}.policies[{tool!r}] is not a Policy"
        if policy is Policy.ASK:
            assert schema is AskShopper, f"{tool}: ASK is for AskShopper only; use APPROVE"
        if schema is AskShopper:
            assert policy is Policy.ASK, "AskShopper must have Policy.ASK"
    assert Policy.READ in policies.values(), f"{name} has no READ tool to look anything up"


def _check_texts(name: str, toolkit: Any) -> None:
    for attribute in ("invalid_action", "grounding_subject"):
        value = getattr(toolkit, attribute)
        assert isinstance(value, str) and value.strip(), f"{name}.{attribute} is empty"
    repair = toolkit.repair_instruction
    assert "{issues}" in repair, f"{name}.repair_instruction must contain {{issues}}"
    assert "a wrong price" in repair.format(issues="a wrong price"), (
        f"{name}.repair_instruction has a placeholder other than {{issues}}"
    )
    # With nothing verified to show, the fallback is still an answer, never an error.
    fallback = toolkit.fallback([])
    assert isinstance(fallback, str) and fallback.strip(), f"{name}.fallback([]) is empty"


def _check_turn_hooks(name: str, toolkit: Any, empty: TurnContext, intent: Intent) -> None:
    """The hooks the engine calls on every turn, on an empty turn (no tool has run)."""
    context = toolkit.clean_context(empty)
    assert isinstance(context, TurnContext), f"{name}.clean_context must return a TurnContext"
    # A second pass changes nothing: the engine may clean an already clean context.
    assert toolkit.clean_context(context) == context, f"{name}.clean_context is not idempotent"
    messages, cited = toolkit.history([])
    assert messages == [] and cited == [], f"{name}.history([]) must be ([], [])"
    turn: Turn[Any] = Turn("hello", context, intent, 0.0, toolkit.key)
    assert isinstance(toolkit.facts(turn), FactSheet), f"{name}.facts must return a FactSheet"
    fields = toolkit.reply_fields(turn)
    assert isinstance(fields, dict), f"{name}.reply_fields must return a dict"
    suggestions = toolkit.suggestions(turn)
    assert isinstance(suggestions, list) and all(isinstance(s, str) for s in suggestions), (
        f"{name}.suggestions must return a list of strings"
    )
    # Plain text with no facts states nothing to check: it must not be flagged.
    report = toolkit.check(turn, "Hello! What are you looking for?")
    assert not report.issues, f"{name}.check flags a greeting: {report.issues}"
    assert toolkit.pending_display([]) == {} or isinstance(toolkit.pending_display([]), dict)
