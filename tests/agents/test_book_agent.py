"""Book agent: tools, remembered reading tastes, approvals, grounding and the shared engine."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from pydantic import TypeAdapter

from curio.app.assistants import BookAssistant, CocktailAssistant
from curio.app.schemas import (
    ActionDecision,
    AppliedEffect,
    ChatTurn,
    ReplyEvent,
    StepEvent,
    TurnContext,
)
from curio.domains.books.agent.grounding import BookGroundingCheck
from curio.domains.books.agent.toolkit import STARTERS
from curio.domains.books.models import Book, BookPreferences
from curio.domains.cocktails.recommend import CocktailRecommender
from curio.engine.signing import ActionSigner, InvalidActionError

# The wire type of `AssistantReply.applied`: one effect class per domain, picked by kind.
as_effect = TypeAdapter(AppliedEffect).validate_python


def action(name, args, call_id="tool_1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def build(model, book_catalog, book_recommender, **kwargs):
    return BookAssistant(model, book_catalog, book_recommender, **kwargs)


def record(assistant):
    """Capture every message list the agent model sees."""
    seen = []
    model = assistant._agent

    def invoke(messages):
        seen.append(list(messages))
        return model.invoke(messages)

    assistant._agent = RunnableLambda(invoke)
    return seen


def tool_results(seen):
    return [m.content for m in seen[-1] if isinstance(m, ToolMessage)]


def ids(reply):
    return [b.book_id for b in reply.books]


def tastes(**fields):
    return TurnContext(book_preferences=BookPreferences(**fields))


@pytest.fixture
def agent(chat_model_replying, book_catalog, book_recommender):
    def make(*replies, **kwargs):
        return build(chat_model_replying(*replies), book_catalog, book_recommender, **kwargs)

    return make


# --- Read tools and the trace ------------------------------------------------------------


def test_like_search_streams_steps_and_a_verified_answer(agent):
    assistant = agent(
        action("SearchBooks", {"query": "fantasy like The Name of the Wind"}),
        "Readers of **The Name of the Wind** loved **The Way of Kings** [id 3] (★ 4.64) by "
        "Brandon Sanderson.",
    )
    events = list(assistant.stream("Fantasy like The Name of the Wind", []))
    assert all(isinstance(e, StepEvent) for e in events[:-1])
    assert isinstance(events[-1], ReplyEvent)
    names = [e.step.name for e in events[:-1]]
    assert names == ["Understand request", "SearchBooks", "Draft answer", "Grounding check"]
    reply = events[-1].reply
    assert (reply.domain, reply.products, reply.cocktails) == ("books", [], [])
    assert ids(reply) == [3, 4]
    assert reply.grounding.status == "verified" and reply.grounding.checked == 4
    assert reply.suggestions == [
        "Readers of The Way of Kings also liked…",
        "More by Brandon Sanderson",
        "Save The Way of Kings",
        "Something shorter",
    ]


def test_the_seed_book_counts_as_evidence_even_when_the_model_named_it(agent):
    assistant = agent(
        action("SearchBooks", {"query": "like the name of the wind", "genres": ["fantasy"]}),
        "Since you enjoyed **The Name of the Wind**, try **The Final Empire**.",
    )
    reply = assistant.reply("Something epic please", [])
    assert reply.grounding.status == "verified"


def test_tool_results_carry_evidence_and_details(agent):
    assistant = agent(
        action("SearchBooks", {"query": "short classic novels"}),
        action("InspectBooks", {"book_ids": [5, 404]}, "tool_2"),
        "**Of Mice and Men** [id 5] is 112 pages.",
    )
    seen = record(assistant)
    reply = assistant.reply("Short classic novels", [])
    search, inspect = tool_results(seen)
    assert "[id 5] Of Mice and Men | John Steinbeck | ★ 3.84" in search
    assert "evidence: Genre: Classics; 112 pages" in search
    assert "description: Of Mice and Men is a book by John Steinbeck." in inspect
    assert "shelves: classic, american literature" in inspect
    assert ids(reply) == [5]


def test_readers_also_liked_and_books_by_author(agent):
    assistant = agent(
        action("ReadersAlsoLiked", {"book_id": 1}),
        action("BooksByAuthor", {"author": "brandon sanderson"}, "tool_2"),
        "Done.",
    )
    seen = record(assistant)
    reply = assistant.reply("What else?", [])
    also, by_author = tool_results(seen)
    assert "[id 2]" not in also  # other authors only by default
    assert "960 readers who liked The Name of the Wind also liked this" in also
    assert "Book 1 of Mistborn" in by_author
    steps = {s.name: s for s in reply.steps}
    assert steps["ReadersAlsoLiked"].summary == (
        "Found 3 books readers of The Name of the Wind also liked."
    )
    assert steps["BooksByAuthor"].detail == {"author": "Brandon Sanderson"}


@pytest.mark.parametrize(
    ("name", "args", "error"),
    [
        ("SearchBooks", {"query": "x", "genres": ["Cookbooks"]}, "Unknown genre 'Cookbooks'"),
        ("SearchBooks", {"query": "x", "mood": "Grumpy"}, "Unknown mood 'Grumpy'. Moods: Cosy"),
        ("ReadersAlsoLiked", {"book_id": 404}, "Book 404 is not in the catalog."),
        ("BooksByAuthor", {"author": "Nobody"}, "No author named 'Nobody' in the catalog."),
    ],
)
def test_tool_errors_go_back_to_the_model(agent, name, args, error):
    assistant = agent(action(name, args), "Sorry, nothing matched.")
    seen = record(assistant)
    reply = assistant.reply("Find something", [])
    assert tool_results(seen)[0].startswith(error)
    assert reply.steps[1].status == "error"


def test_invalid_actions_are_rejected_before_anything_runs(agent):
    assistant = agent(
        action("SearchBooks", {"query": ""}),
        action("InspectBooks", {"book_ids": []}, "tool_2"),
        "Nothing.",
    )
    seen = record(assistant)
    reply = assistant.reply("Hmm", [])
    assert all(r.startswith("Invalid action. Use SearchBooks") for r in tool_results(seen))
    assert [s.status for s in reply.steps if s.kind == "tool"] == ["blocked", "blocked"]


# --- Remembered tastes ---------------------------------------------------------------------


def test_remembered_tastes_filter_every_search(agent):
    assistant = agent(action("SearchBooks", {"query": "dark"}), "Here you go.")
    reply = assistant.reply(
        "Something dark", [], tastes(avoided_genres=["horror"], liked_genres=["Mystery"])
    )
    assert ids(reply) == [8]
    plan, search = reply.steps[0], reply.steps[1]
    assert plan.summary == (
        "Applying remembered tastes to every search: likes Mystery; avoiding Horror."
    )
    assert search.detail["exclude_genres"] == ["Horror"]
    assert search.detail["from_memory"] == ["exclude_genres", "boost_genres"]


def test_an_explicit_genre_wins_over_a_remembered_avoidance(agent):
    assistant = agent(action("SearchBooks", {"genres": ["Horror"]}), "Here.")
    reply = assistant.reply("Fine, show me horror", [], tastes(avoided_genres=["Horror"]))
    assert ids(reply) == [7]
    assert "exclude_genres" not in reply.steps[1].detail


def test_avoided_authors_apply_to_readers_also_liked(agent):
    assistant = agent(action("ReadersAlsoLiked", {"book_id": 1}), "Here.")
    reply = assistant.reply("More like it", [], tastes(avoided_authors=["brandon sanderson"]))
    assert ids(reply) == [7]


def test_unknown_client_tastes_are_dropped(agent):
    assistant = agent(action("SearchBooks", {"query": "magic"}), "Here.")
    context = tastes(avoided_genres=["Cookbooks"], avoided_authors=["Nobody", "patrick rothfuss"])
    reply = assistant.reply("magic", [], context)
    assert reply.steps[1].detail["exclude_authors"] == ["Patrick Rothfuss"]
    assert "exclude_genres" not in reply.steps[1].detail
    assert ids(reply) == [4]


# --- Approvals -----------------------------------------------------------------------------


def test_save_books_is_proposed_then_applied_on_approval(agent):
    signer = ActionSigner(b"k" * 32)
    proposing = agent(action("SaveBooks", {"book_ids": [3, 404, 3]}), signer=signer)
    reply = proposing.reply("Save The Way of Kings", [])
    assert reply.status == "needs_approval"
    pending = reply.pending_action
    assert (pending.tool, [b.book_id for b in pending.books]) == ("SaveBooks", [3])
    assert pending.summary == "Proposed: Would save 1 to the reading list: The Way of Kings."
    assert reply.text.startswith("I'd like to add these books to your reading list.")

    resuming = agent("Saved it.", signer=signer)
    done = resuming.resume(ActionDecision(action=pending, approved=True), [])
    assert done.applied[0].kind == "book_shortlist" and done.applied[0].book_ids == [3]
    assert done.steps[0].summary == "Approved: Saved 1 to the reading list: The Way of Kings."
    assert ids(done) == [3]


def test_remember_reading_tastes_maps_to_catalog_names_and_can_be_narrowed(agent):
    signer = ActionSigner(b"k" * 32)
    args = {"liked_genres": ["fantasy", "Cookbooks"], "avoided_authors": ["STEPHEN KING"]}
    reply = agent(action("RememberReadingTastes", args), signer=signer).reply("Remember", [])
    pending = reply.pending_action
    assert pending.summary == ("Proposed: Would remember likes Fantasy; no books by Stephen King.")
    decision = ActionDecision(action=pending, approved=True, edited_args={"avoided_authors": []})
    done = agent("Noted.", signer=signer).resume(decision, [])
    effect = done.applied[0]
    assert effect.kind == "book_preferences"
    assert effect.book_preferences == BookPreferences(liked_genres=["Fantasy"])


def test_tastes_that_match_nothing_are_a_tool_error(agent):
    assistant = agent(action("RememberReadingTastes", {"avoided_genres": ["Cookbooks"]}), "Sorry.")
    seen = record(assistant)
    reply = assistant.reply("Never cookbooks", [])
    assert tool_results(seen)[0].startswith("None of those genres or authors are in the catalog.")
    assert reply.status == "complete"


def test_auto_approved_tastes_apply_to_the_next_search_in_the_turn(agent):
    assistant = agent(
        action("RememberReadingTastes", {"avoided_genres": ["Horror"]}),
        action("SearchBooks", {"query": "dark"}, "tool_2"),
        "Here.",
    )
    context = TurnContext(auto_approve=["RememberReadingTastes"])
    reply = assistant.reply("No horror ever. Something dark?", [], context)
    assert reply.applied[0].book_preferences.avoided_genres == ["Horror"]
    assert ids(reply) == [8]


def test_a_declined_proposal_changes_nothing(agent):
    signer = ActionSigner(b"k" * 32)
    pending = agent(action("SaveBooks", {"book_ids": [1]}), signer=signer).reply("Save", [])
    done = agent("Okay.", signer=signer).resume(
        ActionDecision(action=pending.pending_action, approved=False), []
    )
    assert done.applied == [] and done.steps[0].status == "skipped"
    assert ids(done) == [1]


def test_book_proposals_do_not_replay_in_another_domain(
    agent, chat_model_replying, cocktail_catalog
):
    signer = ActionSigner(b"k" * 32)
    pending = agent(action("SaveBooks", {"book_ids": [1]}), signer=signer).reply("Save", [])
    cocktails = CocktailAssistant(
        chat_model_replying("x"),
        cocktail_catalog,
        CocktailRecommender(cocktail_catalog),
        signer=signer,
    )
    with pytest.raises(InvalidActionError, match="different assistant"):
        cocktails.resume(ActionDecision(action=pending.pending_action, approved=True), [])


# --- Grounding -----------------------------------------------------------------------------


def test_an_invented_rating_is_rewritten_once(agent):
    assistant = agent(
        action("SearchBooks", {"query": "magic"}),
        "**The Final Empire** is rated ★ 4.99.",
        "**The Final Empire** is rated ★ 4.43.",
    )
    reply = assistant.reply("Best magic book?", [])
    assert reply.grounding.status == "repaired"
    assert reply.grounding.issues == ["a rating of 4.99 does not match any book in the evidence"]


def test_an_unverified_title_falls_back_to_catalog_facts(agent):
    assistant = agent(
        action("SearchBooks", {"query": "magic"}),
        "Try **Gone Girl**.",
        "Really, try **Gone Girl**.",
    )
    reply = assistant.reply("Magic", [])
    assert reply.grounding.status == "fallback"
    assert reply.text.startswith("Here are the books I could confirm:\n- The Name of the Wind by")


def test_history_and_reading_list_books_are_evidence(agent):
    history = [
        ChatTurn(role="user", content="Thrillers"),
        ChatTurn(role="assistant", content="Gone Girl it is.", book_ids=[8]),
    ]
    assistant = agent("**Gone Girl** [id 8] and **Twilight** [id 10] are both here.")
    seen = record(assistant)
    reply = assistant.reply("Compare them", history, TurnContext(book_shortlist=[10, 404]))
    assert reply.grounding.status == "verified"
    assert "Referenced books:\n1. [id 8] Gone Girl" in seen[0][-2].content
    assert "reading list: [id 10] Twilight by Stephenie Meyer · ★ 3.57" in seen[0][0].content


def test_fallback_without_items_and_suggestions_without_books(agent):
    reply = agent(action("SearchBooks", {"query": "zzzz"}), "").reply("zzzz", [])
    assert reply.text.startswith("I couldn't confirm matching books.")
    assert reply.suggestions == list(STARTERS)


class TestBookGroundingCheck:
    check = BookGroundingCheck(
        ["The Name of the Wind", "It", "George", "Twilight", "Mystery"],
        other_names=["George Orwell", "Mystery"],
    )

    def test_titles_need_evidence_unless_typed(self, book_catalog):
        wind = book_catalog.get(1)
        assert self.check.check("Read The Name of the Wind.", [wind]).status == "verified"
        report = self.check.check("Read *Twilight*.", [wind])
        assert report.issues == ["Twilight was not returned by any tool"]
        assert self.check.check("Read *Twilight*.", [wind], typed="like twilight").status == (
            "verified"
        )

    def test_ordinary_words_and_names_are_not_titles(self, book_catalog):
        text = "It is a Mystery classic by George Orwell, nothing like It."
        assert self.check.check(text, []).status == "not_applicable"

    def test_ids_and_ratings(self, book_catalog):
        wind = book_catalog.get(1)  # rated 4.55
        assert self.check.check("[id 1] ★ 4.55, rated 4.6/5", [wind]).status == "verified"
        for claim in ("rated 4.4", "★ 4.49", "4.7 stars", "book #2"):
            assert self.check.check(claim, [wind]).status == "fallback", claim
        assert self.check.check("at least 4.5 stars", [wind], typed="4.5 stars").status == (
            "verified"
        )
        assert self.check.check("an average of 3 books", [wind]).status == "not_applicable"


class TestOneWordTitles:
    """One-word titles are ordinary words too ("Beloved", "Remember", "Paradise"); they are
    checked only where the answer marks them as titles."""

    check = BookGroundingCheck(
        ["Beloved", "Remember", "Paradise", "The Hitchhiker's Guide to the Galaxy"],
        other_names=["Toni Morrison", "Douglas Adams", "Fantasy"],
    )

    @staticmethod
    def book(book_id: int, title: str, author: str) -> Book:
        return Book(
            book_id=book_id,
            title=title,
            authors=[author],
            average_rating=4.2,
            ratings_count=1000,
            source_url=f"https://www.goodreads.com/book/show/{book_id}",
        )

    def test_ordinary_capitalised_words_are_not_titles(self):
        guide = self.book(54, "The Hitchhiker's Guide to the Galaxy", "Douglas Adams")
        for text in (
            "Beloved by fans, The Hitchhiker's Guide to the Galaxy [id 54] is a comic classic.",
            "Remember that The Hitchhiker's Guide to the Galaxy [id 54] is short.",
            "A trip to Paradise awaits in The Hitchhiker's Guide to the Galaxy [id 54].",
        ):
            report = self.check.check(text, [guide])
            assert (report.status, report.issues) == ("verified", []), text
        assert self.check.check("Remember: Paradise is near.", []).status == "not_applicable"

    def test_marked_one_word_titles_are_checked(self):
        beloved = self.book(3, "Beloved", "Toni Morrison")
        for text in (
            "Try **Beloved**.",
            "Try *Beloved*.",
            "Try _Beloved_.",
            'Try "Beloved".',
            "Try \u201cBeloved\u201d.",
            "Try Beloved [id 3].",
            "Try Beloved (book #3).",
            "[id 3] Beloved: a ghost story.",
            "Try Beloved by Toni Morrison.",
        ):
            assert self.check.check(text, [beloved]).status == "verified", text
            report = self.check.check(text.replace("3", "4"), [])
            assert "Beloved was not returned by any tool" in report.issues, text


def test_book_effects_serialize_only_their_own_fields():
    effect = as_effect({"kind": "book_shortlist", "book_ids": [1]})
    assert effect.model_dump() == {
        "kind": "book_shortlist",
        "book_ids": [1],
        "book_preferences": None,
    }
    fashion = as_effect({"kind": "shortlist", "product_ids": [2]}).model_dump()
    assert set(fashion) == {"kind", "product_ids", "preferences"}
    context = TurnContext(
        auto_approve=[
            "SaveToShortlist",
            "RememberPreferences",
            "SaveCocktails",
            "RememberTastes",
            "SaveBooks",
            "RememberReadingTastes",
        ]
    )
    assert len(context.auto_approve) == 6
