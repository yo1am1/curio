"""examples/hello_domain on the real AgentEngine, driven by a scripted chat model: its CSV
loader, a grounded answer with fact references, an approval round trip and a caught
hallucination (the contract in docs/extending.md)."""

import pytest
from langchain_core.messages import AIMessage

from curio.engine import ActionDecision, ActionSigner, ChatTurn, InvalidActionError
from examples.hello_domain.teas import (
    TEAS_CSV,
    Tea,
    TeaContext,
    TeaEffect,
    TeaReply,
    load_teas,
    tea_agent,
)
from tests.agents.conftest import FakeToolCallingChatModel


def model(*replies: str | tuple[str, dict]) -> FakeToolCallingChatModel:
    """Each reply is the answer text, or a (tool, args) call."""
    messages = [
        AIMessage(reply)
        if isinstance(reply, str)
        else AIMessage("", tool_calls=[{"name": reply[0], "args": reply[1], "id": f"call_{i}"}])
        for i, reply in enumerate(replies)
    ]
    return FakeToolCallingChatModel(messages=iter(messages))


def steps(reply: TeaReply) -> list[tuple[str, str]]:
    return [(step.name, step.status) for step in reply.steps]


def test_the_catalog_loads_from_the_csv_and_validates_each_row(tmp_path):
    teas = load_teas()
    assert len(teas) == 12 and teas[3] == Tea(
        tea_id=4,
        name="Lapsang Souchong",
        kind="black",
        notes="smoky, pine, campfire",
        price_eur=8.5,
    )
    assert TEAS_CSV.name == "teas.csv"
    with pytest.raises(FileNotFoundError):
        load_teas(tmp_path / "missing.csv")
    repeated = tmp_path / "repeated.csv"
    repeated.write_text("tea_id,name,kind,notes,price_eur\n1,A,green,x,1\n1,B,green,y,2\n")
    with pytest.raises(ValueError, match="unique"):
        load_teas(repeated)
    bad = tmp_path / "bad.csv"
    bad.write_text("tea_id,name,kind,notes,price_eur\n0,A,green,x,1\n")
    with pytest.raises(ValueError):
        load_teas(bad)
    own = tmp_path / "own.csv"
    own.write_text("tea_id,name,kind,notes,price_eur\n7,Yerba Mate,herbal,grassy,3.2\n")
    agent = tea_agent(model(("SearchTeas", {"query": "mate"}), "{tea:7}"), teas=load_teas(own))
    assert agent.reply("Mate?", []).text == "**Yerba Mate**"


def test_a_grounded_answer_cites_facts_through_references():
    answer = "Try {tea:4} [id 4] at {price:4}; with {tea:9} the two cost {total:price:4,9}."
    agent = tea_agent(model(("SearchTeas", {"query": "smoky"}), answer))
    reply = agent.reply("Something smoky?", [])
    assert isinstance(reply, TeaReply) and reply.status == "complete"
    assert [t.name for t in reply.teas] == ["Lapsang Souchong", "Da Hong Pao"]
    # The server rendered every reference from the catalog; none reaches the client.
    assert reply.text == (
        "Try **Lapsang Souchong** [id 4] at €8.50; with **Da Hong Pao** the two cost €25.30."
    )
    assert reply.grounding.status == "verified"
    assert reply.grounding.rendered == {"name": 2, "price": 1, "total": 1}
    assert reply.grounding.checked == 1  # the typed "[id 4]", checked by the backstop
    assert steps(reply) == [
        ("Understand request", "ok"),
        ("SearchTeas", "ok"),
        ("Draft answer", "ok"),
        ("Grounding check", "ok"),
    ]
    assert reply.model_dump()["teas"][0]["tea_id"] == 4


def test_saving_needs_a_signed_approval_that_can_only_be_narrowed():
    signer = ActionSigner(b"k" * 32)
    agent = tea_agent(model(("SaveTeas", {"tea_ids": [4, 9]})), signer=signer)
    proposal = agent.reply("Save both smoky teas", [])
    assert proposal.status == "needs_approval" and proposal.applied == []
    pending = proposal.pending_action
    assert (pending.tool, pending.args) == ("SaveTeas", {"tea_ids": [4, 9]})
    assert signer.verify(pending) is pending
    assert [t.tea_id for t in proposal.teas] == [4, 9]

    widened = ActionDecision(action=pending, approved=True, edited_args={"tea_ids": [4, 5]})
    with pytest.raises(InvalidActionError):
        agent.resume(widened, [])
    forged = pending.model_copy(update={"args": {"tea_ids": [1]}})
    with pytest.raises(InvalidActionError):
        agent.resume(ActionDecision(action=forged, approved=True), [])

    resumed = tea_agent(model("Saved **Lapsang Souchong** [id 4]."), signer=signer)
    history = [ChatTurn(role="user", content="Save both smoky teas")]
    done = resumed.resume(
        ActionDecision(action=pending, approved=True, edited_args={"tea_ids": [4]}),
        history,
        TeaContext(saved=[1]),
    )
    assert done.status == "complete" and done.grounding.status == "verified"
    assert done.applied == [TeaEffect(tea_ids=[4])]
    assert done.steps[0].summary == "Approved: Saved Lapsang Souchong."


def test_an_answer_naming_a_tea_no_tool_returned_falls_back_to_catalog_facts():
    invented = "Try **Silver Needle** [id 3], it is smoky."
    agent = tea_agent(model(("SearchTeas", {"query": "smoky"}), invented, invented))
    reply = agent.reply("Something smoky?", [])
    assert reply.grounding.status == "fallback"
    assert reply.grounding.issues == [
        "tea 3 was not returned by any tool",
        "Silver Needle was not returned by any tool",
    ]
    assert steps(reply)[-2:] == [("Grounding check", "error"), ("Self-correction", "blocked")]
    assert "Silver Needle" not in reply.text
    assert reply.text.splitlines() == [
        "Teas I could confirm:",
        "- **Lapsang Souchong** [id 4]: €8.50",
        "- **Da Hong Pao** [id 9]: €16.80",
    ]


def test_a_typed_wrong_price_or_a_reference_to_an_unreturned_tea_is_caught():
    wrong = "Try **Lapsang Souchong** [id 4], only €4.50."
    agent = tea_agent(model(("SearchTeas", {"query": "smoky"}), wrong, "{tea:4} costs {price:4}."))
    reply = agent.reply("Something smoky?", [])
    assert reply.grounding.status == "repaired"
    assert reply.text == "**Lapsang Souchong** costs €8.50."
    unreturned = "Try {tea:3} at {price:3}."
    agent = tea_agent(model(("SearchTeas", {"query": "smoky"}), unreturned, unreturned))
    reply = agent.reply("Something smoky?", [])
    assert reply.grounding.status == "fallback"
    assert "tea 3 was not returned by any tool this turn" in reply.grounding.issues[0]
    assert "Silver Needle" not in reply.text and "{" not in reply.text


def test_a_toolkit_may_vet_a_proposal_before_it_is_signed(monkeypatch):
    """The engine's optional `vet_proposal` hook refuses a proposal with a tool message."""
    from curio.engine import ToolError
    from examples.hello_domain.teas import TeaToolkit

    def vet(self, turn, action):
        if 9 in action.tea_ids:
            raise ToolError("Tea 9 is not on offer today.")

    monkeypatch.setattr(TeaToolkit, "vet_proposal", vet, raising=False)
    agent = tea_agent(model(("SaveTeas", {"tea_ids": [4, 9]}), "Only Lapsang then."))
    reply = agent.reply("Save both smoky teas", [])
    assert reply.status == "complete" and reply.pending_action is None
    assert ("SaveTeas", "error") in steps(reply)


def test_tea_names_are_checked_in_any_case():
    invented = "Try **silver needle** [id 4], or SILVER NEEDLE."
    agent = tea_agent(model(("SearchTeas", {"query": "smoky"}), invented, invented))
    reply = agent.reply("Something smoky?", [])
    assert reply.grounding.status == "fallback"
    assert "silver needle was not returned by any tool" in reply.grounding.issues
    grounded = tea_agent(model(("SearchTeas", {"query": "smoky"}), "Try lapsang souchong [id 4]."))
    assert grounded.reply("Something smoky?", []).grounding.status == "verified"
