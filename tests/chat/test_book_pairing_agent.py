"""The book agent's PairCocktail tool: pairing, alcohol-free rules, cross-domain grounding."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from clothes_recommender.books.domain import BookPreferences
from clothes_recommender.books.recommend import BookRecommender
from clothes_recommender.chat.book_agent import PAIRING_UNAVAILABLE, BookAssistant
from clothes_recommender.chat.guardrails import BookGroundingCheck, PairedDrinkCheck
from clothes_recommender.chat.schemas import ChatTurn, TurnContext
from clothes_recommender.cocktails.domain import CocktailPreferences
from clothes_recommender.cocktails.recommend import CocktailRecommender
from clothes_recommender.pairings.pairer import BookCocktailPairer
from tests.conftest import book_neighbours_frame
from tests.pairings.conftest import pairing_books, pairing_cocktails

BIG_SLEEP_ANSWER = (
    "For **The Big Sleep**, pour a **Manhattan** (drink #2, contains alcohol): bourbon, sweet "
    "vermouth and bitters for its noir shelves. Please drink responsibly."
)


def action(name, args, call_id="tool_1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def record(assistant):
    seen = []
    model = assistant._agent

    def invoke(messages):
        seen.append(list(messages))
        return model.invoke(messages)

    assistant._agent = RunnableLambda(invoke)
    return seen


def tool_results(seen):
    return [m.content for m in seen[-1] if isinstance(m, ToolMessage)]


@pytest.fixture
def agent(chat_model_replying):
    books, cocktails = pairing_books(), pairing_cocktails()
    pairer = BookCocktailPairer(books, cocktails, CocktailRecommender(cocktails))
    recommender = BookRecommender(books, book_neighbours_frame())

    def make(*replies, pairing=True):
        if not pairing:
            return BookAssistant(chat_model_replying(*replies), books, recommender)
        return BookAssistant(
            chat_model_replying(*replies), books, recommender, pairer=pairer, cocktails=cocktails
        )

    return make


def drinks(reply):
    return [c.name for c in reply.cocktails]


# --- The tool ------------------------------------------------------------------------------------


def test_pairs_a_book_by_title_with_verified_drinks(agent):
    assistant = agent(action("PairCocktail", {"title": "the big sleep"}), BIG_SLEEP_ANSWER)
    seen = record(assistant)
    events = list(assistant.stream("What should I drink with The Big Sleep?", []))
    steps = [e.step for e in events[:-1]]
    assert [s.name for s in steps] == [
        "Understand request",
        "PairCocktail",
        "Draft answer",
        "Grounding check",
    ]
    assert steps[1].summary == ("Paired The Big Sleep with 3 drinks: Manhattan, Sazerac, Martini.")
    assert steps[1].detail["themes"][0] == "Mystery and noir"
    result = tool_results(seen)[0]
    assert result.startswith("Pairing: The Big Sleep leans Mystery and noir")
    assert "1. drink #2 Manhattan | contains alcohol | Cocktail | Cocktail glass | " in result
    assert "ingredients: Bourbon (2 oz), Sweet Vermouth (1 oz)" in result
    assert "why: The Big Sleep (Mystery · shelved as noir and crime fiction) →" in result
    assert result.endswith(
        "Note: " + "Please drink responsibly. Ingredient filters only cover "
        "listed recipe ingredients; they are not an allergen guarantee."
    )
    reply = events[-1].reply
    assert reply.grounding.status == "verified"
    assert reply.grounding.checked == 3  # the book title, the drink name and its number
    assert drinks(reply) == ["Manhattan", "Sazerac", "Martini"]
    assert reply.pairing is not None and reply.pairing.profile.source == "The Big Sleep"
    assert reply.books == []  # pairing shows drinks, not book cards
    assert reply.suggestions == [
        "Another pairing",
        "Make it alcohol-free",
        "Readers of The Big Sleep also liked…",
    ]


def test_pairs_the_reading_list_with_remembered_tastes(agent):
    assistant = agent(
        action("PairCocktail", {"use_reading_list": True}),
        "Your list leans noir: try a **Manhattan** (drink #2).",
    )
    context = TurnContext(
        book_shortlist=[1, 2, 8],
        book_preferences=BookPreferences(liked_genres=["Fantasy"], avoided_genres=["Horror"]),
    )
    reply = assistant.reply("Pair a cocktail with my reading list", [], context)
    profile = reply.pairing.profile
    assert profile.source == "Your reading list (3 books)"
    assert "horror" not in {t.id for t in profile.themes}
    assert "fantasy" in {t.id for t in profile.themes}
    assert reply.grounding.status == "verified"
    assert "Pair a cocktail with my reading list" in reply.suggestions


def test_a_remembered_alcohol_free_cocktail_taste_applies_to_pairings(agent):
    assistant = agent(
        action("PairCocktail", {"book_id": 1}),
        "Try an **Iced Coffee** (drink #14, alcohol-free).",
    )
    context = TurnContext(cocktail_preferences=CocktailPreferences(alcohol_free=True))
    reply = assistant.reply("Pair The Big Sleep", [], context)
    assert reply.steps[0].summary.endswith(
        "Drink pairings stay alcohol-free (remembered cocktail taste)."
    )
    assert reply.cocktails and all(c.alcohol_free for c in reply.cocktails)
    assert reply.pairing.profile.alcohol_free_reason == (
        "your remembered cocktail taste is alcohol-free"
    )
    assert "Make it alcohol-free" not in reply.suggestions


def test_young_adult_books_get_alcohol_free_drinks_even_if_the_model_does_not_ask(agent):
    assistant = agent(
        action("PairCocktail", {"book_id": 6, "alcohol_free": False}),
        "Try a **Hot Chocolate to Die for** (drink #13, alcohol-free).",
    )
    reply = assistant.reply("Drink for Harry Potter?", [])
    assert reply.cocktails and all(c.alcoholic == "Non alcoholic" for c in reply.cocktails)
    assert reply.steps[1].summary.startswith("Paired Harry Potter and the Sorcerer's Stone with")
    assert " alcohol-free drinks: " in reply.steps[1].summary
    assert reply.pairing.note.startswith("Alcohol-free recipes only")
    assert reply.grounding.status == "verified"


def test_another_pairing_leaves_out_the_drinks_already_shown(agent):
    assistant = agent(
        action("PairCocktail", {"book_id": 1, "exclude_drink_ids": [2, 21, 4]}),
        "Next up: an **Old Fashioned** (drink #1).",
    )
    seen = record(assistant)
    history = [
        ChatTurn(role="user", content="What should I drink with The Big Sleep?"),
        ChatTurn(role="assistant", content="A Manhattan.", drink_ids=[2, 21, 4]),
    ]
    reply = assistant.reply("Another pairing", history)
    assert set(drinks(reply)).isdisjoint({"Manhattan", "Sazerac", "Martini"})
    # Earlier drinks reach the model as context, never as evidence.
    earlier = seen[0][-2].content
    assert earlier.endswith(
        "Drinks shown (an earlier pairing): drink #2 Manhattan; drink #21 Sazerac; drink #4 Martini"
    )
    assert reply.grounding.status == "verified"


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ({"book_id": 999}, "Book 999 is not in the catalog. Find it with SearchBooks first."),
        ({"title": "no such book"}, "Book 'no such book' is not in the catalog."),
        ({"use_reading_list": True}, "The reading list is empty and no liked genres"),
    ],
)
def test_tool_errors_go_back_to_the_model(agent, args, error):
    assistant = agent(action("PairCocktail", args), "Which book should I pair?")
    seen = record(assistant)
    reply = assistant.reply("Pair something", [])
    assert tool_results(seen)[0].startswith(error)
    assert reply.steps[1].status == "error"


@pytest.mark.parametrize(
    "args",
    [{}, {"book_id": 1, "use_reading_list": True}, {"book_id": 1, "title": "Dune"}, {"title": ""}],
)
def test_pair_cocktail_needs_exactly_one_subject(agent, args):
    assistant = agent(action("PairCocktail", args), "Which book?")
    reply = assistant.reply("Pair", [])
    assert reply.steps[1].status == "blocked"
    assert reply.cocktails == []


def test_without_cocktails_pairing_says_it_is_unavailable(agent):
    assistant = agent(
        action("PairCocktail", {"book_id": 1}), "Pairing isn't available.", pairing=False
    )
    seen = record(assistant)
    reply = assistant.reply("Pair The Big Sleep", [])
    assert tool_results(seen)[0] == PAIRING_UNAVAILABLE
    assert "cocktail pairing is unavailable on this server" in seen[0][0].content
    assert reply.cocktails == [] and reply.pairing is None


# --- Grounding ---------------------------------------------------------------------------------


def test_an_invented_drink_is_flagged_and_falls_back_to_the_paired_drinks(agent):
    invented = "With **The Big Sleep**, mix a **Kir Royale** (drink #6)."
    assistant = agent(action("PairCocktail", {"book_id": 1}), invented, invented)
    reply = assistant.reply("What should I drink with The Big Sleep?", [])
    assert reply.grounding.status == "fallback"
    assert set(reply.grounding.issues) == {
        "drink 6 was not returned by PairCocktail",
        "Kir Royale was not returned by PairCocktail",
    }
    assert [s.name for s in reply.steps][-2:] == ["Grounding check", "Self-correction"]
    assert reply.text == (
        "The Big Sleep leans Mystery and noir and Classics. To pair:\n"
        "- Manhattan · contains alcohol\n- Sazerac · contains alcohol\n"
        "- Martini · contains alcohol\n\n"
        "Please drink responsibly. Ingredient filters only cover listed recipe ingredients; "
        "they are not an allergen guarantee."
    )
    assert drinks(reply) == ["Manhattan", "Sazerac", "Martini"]


def test_an_invented_amount_is_rewritten_once(agent):
    assistant = agent(
        action("PairCocktail", {"book_id": 1}),
        "A **Manhattan** (drink #2): 3 oz Bourbon.",
        "A **Manhattan** (drink #2): 2 oz Bourbon.",
    )
    reply = assistant.reply("Pair The Big Sleep", [])
    assert reply.grounding.status == "repaired"
    assert reply.grounding.issues == ["3 oz does not match any amount in the paired recipes"]


def test_drinks_from_an_earlier_turn_are_not_evidence(agent):
    assistant = agent("Have another **Manhattan**.", "Have another **Manhattan**.")
    history = [ChatTurn(role="assistant", content="A Manhattan.", drink_ids=[2])]
    reply = assistant.reply("Thanks!", history)
    assert reply.grounding.status == "fallback"
    assert reply.grounding.issues == ["Manhattan was not returned by PairCocktail"]


def test_titles_the_pairers_reasons_cite_count_as_evidence(agent, chat_model_replying):
    """A lore line cites another book (The Long Goodbye in the real table); answers may too."""
    from clothes_recommender.pairings.themes import Lore

    books, cocktails = pairing_books(), pairing_cocktails()
    lore = [Lore("Raymond Chandler", "Old Fashioned", "Marlowe orders one in Pride and Prejudice")]
    pairer = BookCocktailPairer(books, cocktails, CocktailRecommender(cocktails), lore=lore)
    answer = "As in **Pride and Prejudice**, an **Old Fashioned** (drink #1)."
    assistant = BookAssistant(
        chat_model_replying(action("PairCocktail", {"book_id": 1}), answer),
        books,
        BookRecommender(books, book_neighbours_frame()),
        pairer=pairer,
        cocktails=cocktails,
    )
    reply = assistant.reply("Pair The Big Sleep", [])
    assert reply.cocktails[0].name == "Old Fashioned"
    assert reply.grounding.status == "verified"
    # Without the pairing, the same title is unverified.
    plain = agent("**Pride and Prejudice** is great.", "**Pride and Prejudice** is great.")
    assert plain.reply("Hi", []).grounding.status == "fallback"


def test_places_and_book_titles_are_not_read_as_drinks(agent):
    """ "Manhattan" as a place is prose; a bold book title that is not a drink is a book."""
    assistant = agent(
        action("SearchBooks", {"query": "casino royale"}),
        "**Casino Royale** [id 10] opens in a casino; Bond later flies to Manhattan.",
    )
    reply = assistant.reply("Casino Royale", [])
    assert reply.grounding.status == "verified"


def test_paired_drink_check_rules():
    cocktails = pairing_cocktails()
    check = PairedDrinkCheck(
        [*(c.name for c in cocktails), "Casino"],
        cocktails.ingredients(),
        glass_names={c.glass for c in cocktails},
    )
    manhattan = cocktails.get(2)
    assert check.check("Set in Manhattan.", [manhattan]).status == "not_applicable"
    assert check.check("A **Manhattan**, 2 oz Bourbon.", [manhattan]).status == "verified"
    flagged = check.check("A *Vesper* or a 'Martini'.", [manhattan])
    assert flagged.issues == [
        "Vesper was not returned by PairCocktail",
        "Martini was not returned by PairCocktail",
    ]
    # A drink name inside an evidence book title is the book ("Casino" in "Casino Royale").
    assert check.check("**Casino** Royale", [], also_allowed=["Casino Royale"]).status == "verified"
    # Amounts are only checked when the turn paired drinks; "a 3 part series" is prose.
    assert check.check("A 3 part series.", []).status == "not_applicable"
    # A name next to its drink number counts as marked.
    assert check.check("Sazerac drink #21", [manhattan]).issues == [
        "drink 21 was not returned by PairCocktail",
        "Sazerac was not returned by PairCocktail",
    ]


def test_book_check_accepts_drinks_that_share_a_title():
    check = BookGroundingCheck(["Death in the Afternoon", "The Big Sleep"])
    text = "Sip **Death in the Afternoon** with **The Big Sleep**."
    assert check.check(text, []).status == "fallback"
    assert (
        check.check(text, [], also_allowed=["Death in the Afternoon", "The Big Sleep"]).status
        == "verified"
    )
