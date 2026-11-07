"""Approvals are single-use and bound to the client's session (the audit's replay probe)."""

import threading

import pytest
from langchain_core.messages import AIMessage

from curio.app.assistants import CocktailAssistant
from curio.app.schemas import ActionDecision, TurnContext
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.engine import InMemoryNonceStore
from curio.engine.signing import MIN_KEY_BYTES, ActionSigner, InvalidActionError

SESSION = "a" * 32
OTHER = "b" * 32


def act(name, args, call_id="t1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _assistant(chat_model_replying, cocktail_catalog, *replies, signer=None):
    model = chat_model_replying(*replies)
    signer = signer or ActionSigner(b"k" * 32)
    recommender = CocktailRecommender(cocktail_catalog)
    return CocktailAssistant(model, cocktail_catalog, recommender, signer=signer)


def test_a_signed_proposal_resumed_twice_is_refused_the_second_time(
    chat_model_replying, cocktail_catalog
):
    """The audit's probe resumed one proposal three times and got `applied` each time."""
    assistant = _assistant(
        chat_model_replying,
        cocktail_catalog,
        act("SaveCocktails", {"drink_ids": [1, 2]}),
        "Saved.",
        "Saved again.",
    )
    context = TurnContext(session_id=SESSION)
    pending = assistant.reply("save mojito and daiquiri", [], context).pending_action
    decision = ActionDecision(action=pending, approved=True)
    first = assistant.resume(decision, [], context)
    assert first.applied
    with pytest.raises(InvalidActionError, match="already answered"):
        assistant.resume(decision, [], context)
    # Declining afterwards, or narrowing, is an answer too.
    with pytest.raises(InvalidActionError, match="already answered"):
        assistant.resume(ActionDecision(action=pending, approved=False), [], context)


def test_a_declined_proposal_cannot_then_be_approved(chat_model_replying, cocktail_catalog):
    assistant = _assistant(
        chat_model_replying, cocktail_catalog, act("SaveCocktails", {"drink_ids": [1]}), "OK."
    )
    pending = assistant.reply("save the mojito", []).pending_action
    assert assistant.resume(ActionDecision(action=pending, approved=False), []).applied == []
    with pytest.raises(InvalidActionError, match="already answered"):
        assistant.resume(ActionDecision(action=pending, approved=True), [])


def test_a_proposal_answers_only_in_its_own_session(chat_model_replying, cocktail_catalog):
    assistant = _assistant(
        chat_model_replying,
        cocktail_catalog,
        act("SaveCocktails", {"drink_ids": [1]}),
        "Saved.",
    )
    pending = assistant.reply("save it", [], TurnContext(session_id=SESSION)).pending_action
    decision = ActionDecision(action=pending, approved=True)
    for context in (TurnContext(session_id=OTHER), TurnContext()):
        with pytest.raises(InvalidActionError):
            assistant.resume(decision, [], context)
    # Refused attempts from elsewhere do not use it up for its own session.
    assert assistant.resume(decision, [], TurnContext(session_id=SESSION)).applied


def test_a_refused_edit_leaves_the_proposal_open(chat_model_replying, cocktail_catalog):
    assistant = _assistant(
        chat_model_replying,
        cocktail_catalog,
        act("SaveCocktails", {"drink_ids": [1, 2]}),
        "Saved one.",
    )
    pending = assistant.reply("save both", []).pending_action
    widened = ActionDecision(action=pending, approved=True, edited_args={"drink_ids": [1, 3]})
    with pytest.raises(InvalidActionError, match="subset"):
        assistant.resume(widened, [])
    narrowed = ActionDecision(action=pending, approved=True, edited_args={"drink_ids": [2]})
    assert [e.drink_ids for e in assistant.resume(narrowed, []).applied] == [[2]]


class FlakyModel:
    """The bound model, except that its first call fails like a provider outage."""

    def __init__(self, model) -> None:
        self._model = model
        self._failed = False

    def invoke(self, messages, *args, **kwargs):
        if not self._failed:
            self._failed = True
            raise ConnectionError("gateway down")
        return self._model.invoke(messages, *args, **kwargs)


def test_a_turn_that_fails_after_approval_can_be_answered_again(
    chat_model_replying, cocktail_catalog
):
    """The effect only reaches the client in the reply; when the model fails before it, the
    shopper must be able to answer the proposal again."""
    assistant = _assistant(
        chat_model_replying,
        cocktail_catalog,
        act("SaveCocktails", {"drink_ids": [1]}),
        "Saved.",
    )
    pending = assistant.reply("save it", []).pending_action
    decision = ActionDecision(action=pending, approved=True)
    assistant._agent = FlakyModel(assistant._agent)
    with pytest.raises(ConnectionError):
        assistant.resume(decision, [])
    assert assistant.resume(decision, []).applied
    with pytest.raises(InvalidActionError, match="already answered"):
        assistant.resume(decision, [])


def test_a_stream_abandoned_after_its_reply_keeps_the_proposal_used(
    chat_model_replying, cocktail_catalog
):
    assistant = _assistant(
        chat_model_replying,
        cocktail_catalog,
        act("SaveCocktails", {"drink_ids": [1]}),
        "Saved.",
    )
    pending = assistant.reply("save it", []).pending_action
    decision = ActionDecision(action=pending, approved=True)
    events = assistant.stream("", [], decision=decision)
    replies = [event for event in events if event.type == "reply"]
    events.close()
    assert replies[0].reply.applied
    with pytest.raises(InvalidActionError, match="already answered"):
        assistant.resume(decision, [])


# --- The signer and its nonce store ----------------------------------------------------------


def test_signing_keys_must_be_at_least_32_bytes():
    assert MIN_KEY_BYTES == 32
    with pytest.raises(ValueError, match="at least 32 bytes"):
        ActionSigner(b"k" * 31)
    with pytest.raises(ValueError):
        ActionSigner(b"")
    ActionSigner(b"k" * 32)
    ActionSigner()  # a random 32-byte key


def test_the_session_is_part_of_the_signature():
    signer = ActionSigner(b"k" * 32)
    pending = signer.issue("SaveCocktails", {"drink_ids": [1]}, "save", session=SESSION)
    assert signer.verify(pending, SESSION) is pending
    for session in (OTHER, None, ""):
        with pytest.raises(InvalidActionError, match="not issued by Curio"):
            signer.verify(pending, session)


def test_claims_are_single_use_until_released():
    signer = ActionSigner(b"k" * 32)
    pending = signer.issue("SaveCocktails", {"drink_ids": [1]}, "save")
    signer.claim(pending)
    with pytest.raises(InvalidActionError, match="already answered"):
        signer.claim(pending)
    signer.release(pending)
    signer.claim(pending)


def test_one_of_many_concurrent_claims_wins():
    signer = ActionSigner(b"k" * 32)
    pending = signer.issue("SaveCocktails", {"drink_ids": [1]}, "save")
    wins: list[bool] = []
    barrier = threading.Barrier(16)

    def claim() -> None:
        barrier.wait()
        try:
            signer.claim(pending)
            wins.append(True)
        except InvalidActionError:
            wins.append(False)

    threads = [threading.Thread(target=claim) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert wins.count(True) == 1


def test_the_in_memory_store_forgets_expired_nonces():
    clock = Clock()
    store = InMemoryNonceStore(clock, purge_every=10)
    assert store.claim("a", clock.now + 5)
    assert not store.claim("a", clock.now + 5)
    clock.now += 6  # expired: a proposal this old fails verification anyway
    assert store.claim("a", clock.now + 5)
    assert store.claim("b", clock.now + 1)
    clock.now += 20
    store.claim("c", clock.now + 5)  # purges a and b
    assert len(store) == 1


def test_a_shared_store_is_honoured_by_every_signer():
    """Several workers share one store (and key): a proposal answered on one is refused on
    the others."""
    shared = InMemoryNonceStore()
    first = ActionSigner(b"k" * 32, nonces=shared)
    second = ActionSigner(b"k" * 32, nonces=shared)
    pending = first.issue("SaveCocktails", {"drink_ids": [1]}, "save")
    first.claim(pending)
    second.verify(pending)
    with pytest.raises(InvalidActionError, match="already answered"):
        second.claim(pending)
