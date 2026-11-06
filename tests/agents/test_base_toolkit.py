"""BaseToolkit: required methods stay abstract, optional hooks default to no-ops."""

from types import MappingProxyType

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from curio.engine import BaseToolkit, ChatTurn, Intent, Turn, TurnContext
from examples.hello_domain.teas import SaveTeas, TeaToolkit


def test_a_toolkit_without_its_required_methods_cannot_be_built():
    class Partial(BaseToolkit[int]):
        tools = MappingProxyType({})
        policies = MappingProxyType({})

        def key(self, item: int) -> int:
            return item

    with pytest.raises(TypeError, match="abstract"):
        Partial()


def test_optional_hooks_default_to_no_ops():
    toolkit = TeaToolkit()
    context = TurnContext()
    turn = Turn("hi", context, Intent(query="hi"), 0.0, toolkit.key)
    assert toolkit.clean_context(context) is context
    messages, cited = toolkit.history(
        [ChatTurn(role="user", content="hi"), ChatTurn(role="assistant", content="hello")]
    )
    assert [type(m) for m in messages] == [HumanMessage, AIMessage] and cited == []
    assert [step.name for step in toolkit.plan(turn, [])] == ["Understand request"]
    assert toolkit.refresh(turn) is None
    assert toolkit.vet_proposal(turn, SaveTeas(tea_ids=[1])) is None
    assert toolkit.approval_text(SaveTeas(tea_ids=[1])).endswith("Approve, edit or decline.")
    assert toolkit.pending_display([]) == {}
    assert toolkit.suggestions(turn) == []
    assert BaseToolkit.proposed(toolkit, {"tea_ids": [1]}) == []
