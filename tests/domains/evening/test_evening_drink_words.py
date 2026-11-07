"""Words that only decide the drink (a driver, a pregnancy, a sober or alcohol-free evening)
make the evening alcohol-free and never choose or change its theme.

The agent evaluation found "Date night, but I'm the one driving us home" planned as a cosy
night in: "home" (where the car goes) tied with "date night" and cosy won the tie.
"""

import pytest

from curio.domains.evening.models import EveningRequest
from curio.domains.evening.planner import alcohol_free_in, resolve_alcohol_free
from curio.domains.evening.themes import (
    ALCOHOL_FREE_WORDS,
    SAFETY_WORDS,
    THEMES,
    detect_theme,
    drink_words_removed,
    guard_in,
    mentions,
)

DRINK_ONLY = [
    "I'm driving",
    "I'm the one driving us home",
    "I'll drive",
    "I am the driver",
    "designated driver",
    "I'm pregnant",
    "sober",
    "no alcohol",
    "without alcohol",
    "alcohol-free",
    "mocktails",
    "dry January",
]


@pytest.mark.parametrize("said", DRINK_ONLY)
@pytest.mark.parametrize(
    ("occasion", "theme"),
    [
        ("Date night", "romantic"),
        ("anniversary dinner", "romantic"),
        ("murder mystery night", "mysterious"),
        ("summer garden party", "summery"),
        ("game night with friends", "playful"),
    ],
)
def test_a_drink_only_word_never_changes_the_theme(said, occasion, theme):
    for text in (f"{occasion}, {said}", f"{said}, {occasion}", f"{occasion}, but {said}"):
        match = detect_theme(text)
        assert match.theme.id == theme, text
        assert match.source == "occasion"
        assert match.guard is None
        assert alcohol_free_in(text) is not None, text


def test_date_night_with_a_driver_is_romantic_and_alcohol_free():
    for text in (
        "Date night, I'm driving",
        "Date night, but I'm the one driving us home",
        "Date night, I'm the designated driver and need to drive back",
    ):
        match = detect_theme(text)
        assert (match.theme.id, match.source) == ("romantic", "occasion"), text
        assert all(word.lower() not in ("home", "back") for word in match.words)
        assert resolve_alcohol_free(text, None) == (True, None)
        free, note = resolve_alcohol_free(text, False)
        assert free and note and "driv" in note


@pytest.mark.parametrize("said", DRINK_ONLY)
def test_a_drink_only_word_alone_names_no_theme(said):
    match = detect_theme(said)
    assert (match.theme.id, match.source, match.words) == ("cosy", "default", ())
    assert detect_theme("Date night", mood=said).theme.id == "romantic"


def test_no_theme_or_guard_word_is_a_drink_only_word():
    """The tables never overlap: a theme or guard word is never read as a reason for an
    alcohol-free drink, except "baby shower", which is an occasion as well."""
    from curio.domains.evening.themes import GUARDS

    words = [w for theme in THEMES for w in theme.words]
    words += [w for guard in GUARDS.values() for w in guard]
    for word in words:
        if word == "baby shower":
            continue
        assert not mentions(SAFETY_WORDS, word), word
        assert not mentions(ALCOHOL_FREE_WORDS, word), word
        assert drink_words_removed(word).strip() == word, word


def test_where_the_car_goes_is_no_theme_and_no_guard():
    assert drink_words_removed("driving us home").split() == [";", "us", ";"]
    # Who is in the car stays: kids still guard the evening.
    assert guard_in("driving the kids home after a movie night") == ("kids", ("kids",))
    assert detect_theme("driving the kids home after a movie night").theme.id == "cosy"
    # "to work" is where the car goes, not a work occasion.
    assert guard_in("date night, I'm driving to work tomorrow") is None
    assert detect_theme("date night, I'm driving to work tomorrow").theme.id == "romantic"
    # A real work occasion is still guarded.
    assert guard_in("business dinner, I'm driving") == ("work", ("business",))


def test_a_drink_only_word_never_negates_the_theme_word_after_it():
    match = detect_theme("without alcohol date night")
    assert (match.theme.id, match.words) == ("romantic", ("date night",))
    assert detect_theme("no alcohol, girls' night").theme.id == "playful"


def test_the_planner_keeps_the_theme_and_makes_the_drink_alcohol_free(planner):
    evening = planner.plan(EveningRequest(occasion="Date night, but I'm the one driving us home"))
    assert evening.theme.id == "romantic"
    assert evening.theme.evidence == "you mentioned Date night"
    assert evening.state.alcohol_free
    assert evening.drink is not None and evening.drink.cocktail.alcohol_free
    # A swap re-reads the theme from the occasion and keeps it.
    swapped = planner.swap(evening.state, "book")
    assert swapped.theme.id == "romantic" and swapped.state.alcohol_free
