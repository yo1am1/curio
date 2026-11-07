"""Agent behaviour: trace events, human-in-the-loop pauses, memory and grounding checks."""

import pytest
from langchain_core.messages import AIMessage

from curio.app.assistants import ShoppingAssistant
from curio.app.schemas import (
    ActionDecision,
    ChatTurn,
    ReplyEvent,
    ShopperPreferences,
    StepEvent,
    TurnContext,
)
from curio.domains.fashion.agent.grounding import check_grounding
from curio.domains.fashion.models import ProductFilter, ScoredProduct
from curio.domains.fashion.navigator import Evidence, NoPersonalHistoryError
from curio.engine.signing import ActionSigner, InvalidActionError

NO_SEARCH = {"query": "", "wants_products": False}


def action(name, args, call_id="tool_1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


class FakeNavigator:
    def __init__(self):
        self.calls = []

    def related(self, product_id, relation, top_k):
        self.calls.append(("related", product_id, relation))
        return [Evidence(1, ("Bought together by 3 shoppers.",))]

    def personal(self, shopper_id, top_k):
        self.calls.append(("personal", shopper_id))
        if shopper_id == 404:
            raise NoPersonalHistoryError("unknown")
        return [Evidence(2, ("Buy again: last purchased on 2020-09-01.",))]


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


def build(model, search, catalog, **kwargs):
    return ShoppingAssistant(model, search, catalog, navigator=FakeNavigator(), **kwargs)


def test_stream_emits_plan_search_and_verification_before_the_reply(
    chat_model_replying, search, catalog
):
    search.results = [ScoredProduct(3, 0.9)]
    model = chat_model_replying({"query": "red dress"}, "The **Summer Dress** is €24.99.")
    events = list(build(model, search, catalog).stream("A red dress", []))

    assert all(isinstance(event, StepEvent) for event in events[:-1])
    assert isinstance(events[-1], ReplyEvent)
    names = [event.step.name for event in events[:-1]]
    assert names == ["Understand request", "SearchCatalog", "Draft answer", "Grounding check"]
    reply = events[-1].reply
    assert reply.status == "complete"
    assert reply.grounding.status == "verified" and reply.grounding.checked == 1
    assert [s.name for s in reply.steps] == names
    assert reply.suggestions[0] == "What goes with the Summer Dress?"


def test_ask_shopper_pauses_with_quick_replies(chat_model_replying, search, catalog):
    model = chat_model_replying(
        NO_SEARCH,
        action("AskShopper", {"question": "Who is it for?", "options": ["Women", "Men", "Men"]}),
        "never reached",
    )
    reply = build(model, search, catalog).reply("I need an outfit", [])

    assert reply.status == "needs_input"
    assert reply.text == "Who is it for?"
    assert reply.clarification.options == ["Women", "Men"]
    assert reply.steps[-1].status == "pending"
    assert reply.suggestions == []


def test_shortlist_requires_approval_then_resumes(chat_model_replying, search, catalog):
    model = chat_model_replying(
        NO_SEARCH,
        action("SaveToShortlist", {"product_ids": [1, 3, 999]}),
        "Saved the Slim Jeans for you.",
    )
    assistant = build(model, search, catalog)
    proposal = assistant.reply("Keep the jeans and the dress", [])

    assert proposal.status == "needs_approval"
    pending = proposal.pending_action
    assert pending.tool == "SaveToShortlist" and pending.args == {"product_ids": [1, 3, 999]}
    assert [p.product_id for p in pending.products] == [1, 3]
    assert not proposal.applied  # nothing changes before the shopper decides

    history = [
        ChatTurn(role="user", content="Keep the jeans and the dress"),
        ChatTurn(role="assistant", content=proposal.text, product_ids=[1, 3]),
    ]
    decision = ActionDecision(action=pending, approved=True, edited_args={"product_ids": [1]})
    done = assistant.resume(decision, history)

    assert done.status == "complete"
    assert [e.model_dump(exclude_none=True) for e in done.applied] == [
        {"kind": "shortlist", "product_ids": [1]}
    ]
    assert done.steps[0].kind == "approval" and done.steps[0].summary.startswith("Approved")
    assert done.text == "Saved the Slim Jeans for you."


def test_declined_proposal_changes_nothing(chat_model_replying, search, catalog):
    model = chat_model_replying(
        NO_SEARCH, action("SaveToShortlist", {"product_ids": [1]}), "No problem."
    )
    assistant = build(model, search, catalog)
    pending = assistant.reply("Save the jeans", []).pending_action

    reply = assistant.resume(ActionDecision(action=pending, approved=False), [])

    assert not reply.applied
    assert reply.steps[0].status == "skipped"
    assert reply.text == "No problem."


def test_resumed_turn_cannot_pause_for_another_proposal(chat_model_replying, search, catalog):
    model = chat_model_replying(
        NO_SEARCH,
        action("SaveToShortlist", {"product_ids": [1]}),
        action("SaveToShortlist", {"product_ids": [2]}, "tool_2"),
        "Done.",
    )
    assistant = build(model, search, catalog)
    pending = assistant.reply("Save the jeans", []).pending_action

    reply = assistant.resume(ActionDecision(action=pending, approved=True), [])

    assert reply.status == "complete"
    assert [e.product_ids for e in reply.applied] == [[1]]
    assert any(step.status == "blocked" for step in reply.steps)


@pytest.mark.parametrize(
    "tamper",
    [
        {"args": {"product_ids": [2]}},
        {"tool": "RememberPreferences"},
        {"request": "something else"},
        {"signature": "0" * 64},
    ],
)
def test_forged_or_modified_proposals_are_rejected(tamper, chat_model_replying, search, catalog):
    model = chat_model_replying(NO_SEARCH, action("SaveToShortlist", {"product_ids": [1]}))
    assistant = build(model, search, catalog)
    pending = assistant.reply("Save the jeans", []).pending_action
    forged = pending.model_copy(update=tamper)
    with pytest.raises(InvalidActionError):
        assistant.resume(ActionDecision(action=forged, approved=True), [])


@pytest.mark.parametrize(
    "edits",
    [{"product_ids": [1, 2]}, {"note": "x"}, {"product_ids": "1"}],
)
def test_edits_can_only_narrow_a_proposal(edits, chat_model_replying, search, catalog):
    model = chat_model_replying(NO_SEARCH, action("SaveToShortlist", {"product_ids": [1, 3]}))
    assistant = build(model, search, catalog)
    pending = assistant.reply("Save both", []).pending_action
    with pytest.raises(InvalidActionError):
        assistant.resume(ActionDecision(action=pending, approved=True, edited_args=edits), [])


def test_proposals_expire(chat_model_replying, search, catalog):
    clock = Clock()
    model = chat_model_replying(NO_SEARCH, action("SaveToShortlist", {"product_ids": [1]}))
    assistant = build(
        model, search, catalog, signer=ActionSigner(b"k" * 32, ttl_seconds=60, clock=clock)
    )
    pending = assistant.reply("Save the jeans", []).pending_action
    clock.now += 61
    with pytest.raises(InvalidActionError, match="expired"):
        assistant.resume(ActionDecision(action=pending, approved=True), [])


def test_auto_approve_policy_applies_without_pausing(chat_model_replying, search, catalog):
    model = chat_model_replying(
        NO_SEARCH, action("SaveToShortlist", {"product_ids": [2]}), "Saved the Run Shoe."
    )
    context = TurnContext(auto_approve=["SaveToShortlist"])
    reply = build(model, search, catalog).reply("Save the shoes", [], context)

    assert reply.status == "complete"
    assert [e.product_ids for e in reply.applied] == [[2]]
    assert any(step.summary.startswith("Auto-approved") for step in reply.steps)


def test_remembered_preferences_are_validated_then_fill_missing_filters(
    chat_model_replying, search, catalog
):
    model = chat_model_replying(
        NO_SEARCH,
        action(
            "RememberPreferences",
            {"sections": ["menswear", "Kidswear"], "colours": ["Purple"], "max_price": 40},
        ),
    )
    assistant = build(model, search, catalog)
    pending = assistant.reply("I always shop menswear under €40", []).pending_action
    assert pending.tool == "RememberPreferences"

    model = chat_model_replying({"query": "jeans"}, "Here you go.")
    assistant = build(model, search, catalog, signer=assistant._signer)
    approved = assistant.resume(ActionDecision(action=pending, approved=True), [])
    (effect,) = approved.applied
    assert effect.preferences == ShopperPreferences(sections=["Menswear"], max_price=40)

    model = chat_model_replying({"query": "jeans"}, "Here you go.")
    reply = build(model, search, catalog).reply(
        "Jeans please", [], TurnContext(preferences=effect.preferences)
    )
    assert search.calls[-1][2] == ProductFilter(sections=("Menswear",), max_price=40)
    assert "memory" in reply.steps[0].summary


def test_unknown_preferences_are_a_tool_error_not_a_proposal(chat_model_replying, search, catalog):
    model = chat_model_replying(
        NO_SEARCH,
        action("RememberPreferences", {"colours": ["Ultraviolet"]}),
        "I can't remember that colour.",
    )
    reply = build(model, search, catalog).reply("Remember ultraviolet", [])
    assert reply.status == "complete" and reply.pending_action is None
    assert any(step.status == "error" for step in reply.steps)


def test_find_related_uses_recommender_evidence(chat_model_replying, search, catalog):
    model = chat_model_replying(
        NO_SEARCH,
        action("FindRelated", {"product_id": 2, "relation": "complement"}),
        "Pair them with the Slim Jeans at €29.99.",
    )
    assistant = build(model, search, catalog)
    reply = assistant.reply("What goes with the running shoes?", [])

    assert assistant._navigator.calls == [("related", 2, "complement")]
    assert [p.product_id for p in reply.products] == [1]
    assert reply.grounding.status == "verified"
    assert "pieces bought with Run Shoe" in reply.steps[1].summary


@pytest.mark.parametrize(
    ("shopper_id", "products"),
    [(None, []), (404, []), (7, [2])],
)
def test_personal_picks_need_a_linked_history(
    shopper_id, products, chat_model_replying, search, catalog
):
    model = chat_model_replying(NO_SEARCH, action("PersonalPicks", {}), "Here are your picks.")
    reply = build(model, search, catalog).reply(
        "What would suit me?", [], TurnContext(shopper_id=shopper_id)
    )
    assert [p.product_id for p in reply.products] == products
    assert reply.steps[1].status == ("ok" if products else "error")


def test_unverified_price_is_repaired_once(chat_model_replying, search, catalog):
    search.results = [ScoredProduct(1, 0.9)]
    model = chat_model_replying(
        {"query": "jeans"},
        "The Slim Jeans cost €19.99.",
        "The Slim Jeans cost €29.99.",
    )
    reply = build(model, search, catalog).reply("jeans", [])
    assert reply.text == "The Slim Jeans cost €29.99."
    assert reply.grounding.status == "repaired"
    assert reply.grounding.issues == ["€19.99 does not match any catalog price in the evidence"]


def test_unrepairable_answer_falls_back_to_catalog_facts(chat_model_replying, search, catalog):
    search.results = [ScoredProduct(1, 0.9)]
    model = chat_model_replying({"query": "jeans"}, "Only €5 today!", "Still €5, product #123456.")
    reply = build(model, search, catalog).reply("jeans", [])
    assert reply.grounding.status == "fallback"
    assert "Slim Jeans · Blue · €29.99" in reply.text


def test_turn_deadline_stops_before_another_model_call(chat_model_replying, search, catalog):
    clock = Clock()
    search.results = [ScoredProduct(1, 0.9)]

    class SlowSearch:
        def by_text(self, *args, **kwargs):
            clock.now += 120
            return search.by_text(*args, **kwargs)

    model = chat_model_replying({"query": "jeans"}, "never reached")
    reply = ShoppingAssistant(model, SlowSearch(), catalog, clock=clock).reply("jeans", [])
    assert reply.steps[-1].name == "Time budget"
    assert "Slim Jeans" in reply.text


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("Run Shoe €59.99 vs Slim Jeans €29.99: €30 apart.", "verified"),
        ("Under your €45 budget.", "verified"),
        ("Around €60.", "verified"),
        ("Only €12.50!", "fallback"),
        ("See [id 77].", "fallback"),
        ("No prices here.", "not_applicable"),
    ],
)
def test_grounding_check(text, status, catalog):
    report = check_grounding(text, catalog.get_many([1, 2]), [45])
    assert report.status == status


def test_negative_or_unicode_minus_prices_are_flagged(catalog):
    for text in ("Now just €-2.01!", "Now just €\u22122.01!", "Only -5 EUR"):
        assert check_grounding(text, catalog.get_many([1]), []).status == "fallback"


def test_a_longer_turn_deadline_lets_a_slow_turn_answer(chat_model_replying, search, catalog):
    clock = Clock()
    search.results = [ScoredProduct(1, 0.9)]

    class SlowSearch:
        def by_text(self, *args, **kwargs):
            clock.now += 120
            return search.by_text(*args, **kwargs)

    model = chat_model_replying({"query": "jeans"}, "The **Slim Jeans** are €29.99.")
    assistant = ShoppingAssistant(
        model, SlowSearch(), catalog, clock=clock, turn_deadline_seconds=600
    )
    reply = assistant.reply("jeans", [])
    assert assistant.turn_deadline_seconds == 600
    assert all(step.name != "Time budget" for step in reply.steps)
    assert reply.grounding.status == "verified"


def test_the_default_turn_deadline_is_unchanged(chat_model_replying, search, catalog):
    from curio.engine import TURN_DEADLINE_SECONDS

    model = chat_model_replying({"query": "jeans"})
    assert TURN_DEADLINE_SECONDS == 90.0
    assert ShoppingAssistant(model, search, catalog).turn_deadline_seconds == 90.0


@pytest.mark.parametrize("seconds", [0, -5])
def test_a_turn_deadline_must_be_positive(chat_model_replying, search, catalog, seconds):
    model = chat_model_replying({"query": "jeans"})
    with pytest.raises(ValueError, match="positive"):
        ShoppingAssistant(model, search, catalog, turn_deadline_seconds=seconds)
