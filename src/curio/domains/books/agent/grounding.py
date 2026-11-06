"""Book grounding: book IDs, catalog titles and Goodreads ratings in an answer."""

import re
from collections.abc import Iterable, Sequence

from curio.domains.books.models import Book
from curio.engine.grounding import (
    DASHES,
    amounts_in,
    any_of,
    check_ids,
    grounding_report,
    marked_name,
    name_allowed,
    parse_number,
)
from curio.engine.schemas import GroundingReport

# Book answers cite books as "[id 192]" (the prompt's format) or "book #192".
_BOOK_ID = re.compile(r"\[id (\d+)\]|\bbook #(\d+)\b", re.IGNORECASE)
# "★ 4.55", "rated 4.5", "average rating of 4.55", "4.6/5", "4.5 out of 5", "4.3 stars".
_RATING = re.compile(
    r"(?:[★⭐]|\brated\b|\brating(?:\s+of)?\b|\baverage\s+rating(?:\s+of)?\b)"
    r"\s*:?\s*([0-5](?:[.,]\d{1,2})?)(?![\d.,]*\d)"
    r"|(?<![\d.,])([0-5](?:[.,]\d{1,2})?)\s*(?:/\s*5\b|out of 5\b|stars?\b|★)",
    re.IGNORECASE,
)
# Single-word titles shorter than this ("It", "Room", "Dune") read like ordinary words.
_MIN_SINGLE_WORD_TITLE = 6
# A one-word title ("Beloved", "Paradise", "Remember") is also an ordinary word, so it counts
# as a title only where the answer marks it as one: bold, italic or quoted, next to its
# "[id N]", or followed by "by <author>".
_ID_AFTER = re.compile(rf"\s*[(,:{DASHES}]?\s*(?:\[id \d+\]|\(id \d+\)|book #\d+)", re.IGNORECASE)
_ID_BEFORE = re.compile(rf"(?:\[id \d+\]|book #\d+)\s*[):,{DASHES}]?\s*$", re.IGNORECASE)
_BY = re.compile(r"\s*,?\s+by\s+", re.IGNORECASE)


class BookGroundingCheck:
    """Checks that titles, book IDs and ratings in a book answer come from tool evidence.

    Titles are matched case-sensitively in catalog spelling and only when they are unlikely to
    be ordinary words: several words, or one word of at least six letters ("Twilight" counts,
    "It" and "Room" do not). A one-word title is checked only where the answer marks it as a
    title (bold, italic, quoted, next to its "[id N]" or followed by "by <author>"), so
    "Beloved by fans" or "Remember that" are not read as the novels. A title inside a longer
    title in the answer counts as the longer one. Ratings must equal an evidence book's
    Goodreads average at the precision written ("4.55", or "4.6" for 4.55) or a number the
    reader typed.
    """

    def __init__(self, titles: Iterable[str], *, other_names: Iterable[str] = ()) -> None:
        """`other_names` (authors, genres, moods) are never read as titles: a title equal to
        one ("Mystery") is ignored, and so is a title inside one ("George" in "George
        Orwell")."""
        others = {name for name in other_names if name.strip()}
        folded = {name.casefold() for name in others}
        names = sorted(
            {t for t in titles if _checkable_title(t) and t.casefold() not in folded},
            key=lambda name: (-len(name), name),
        )
        # Letters and digits bound a title, but "_" does not: "_Beloved_" is italic.
        self._titles = (
            re.compile(r"(?<![^\W_])(?:" + "|".join(map(re.escape, names)) + r")(?![^\W_])")
            if names
            else None
        )
        self._single = {name for name in names if len(name.split()) == 1}
        phrases = sorted(others, key=lambda name: (-len(name), name))
        self._others = any_of(phrases, re.IGNORECASE) if phrases else None

    def check(
        self,
        text: str,
        evidence: Sequence[Book],
        typed: str = "",
        *,
        also_allowed: Sequence[str] = (),
    ) -> GroundingReport:
        """`typed` is what the reader wrote: titles and ratings they named may be repeated.
        `also_allowed` are other names this turn's tools returned (paired drinks such as
        "Casino Royale" or "Death in the Afternoon"), which may share a book's title."""
        checked, issues = check_ids(text, _BOOK_ID, (b.book_id for b in evidence), "book")
        names = [b.title for b in evidence] + [b.series for b in evidence if b.series]
        names += list(also_allowed)
        for title in self._title_mentions(text):
            checked += 1
            if not name_allowed(title, names, typed):
                issues.append(f"{title} was not returned by any tool")
        ratings = [b.average_rating for b in evidence]
        typed_numbers = amounts_in(typed)
        for match in _RATING.finditer(text):
            raw = match.group(1) or match.group(2)
            checked += 1
            if not _rating_allowed(raw, ratings, typed_numbers):
                issues.append(f"a rating of {raw} does not match any book in the evidence")
        return grounding_report(checked, issues)

    def _title_mentions(self, text: str) -> list[str]:
        """Titles in `text`, except those inside a longer author, genre or mood name."""
        if self._titles is None:
            return []
        others = [m.span() for m in self._others.finditer(text)] if self._others else []
        mentions = []
        for match in self._titles.finditer(text):
            start, end = match.span()
            if any(s <= start and end <= e and e - s > end - start for s, e in others):
                continue
            if match.group(0) in self._single and not self._marked(text, start, end):
                continue
            mentions.append(match.group(0))
        return list(dict.fromkeys(mentions))

    def _marked(self, text: str, start: int, end: int) -> bool:
        """Whether the answer marks text[start:end] as a title: bold, italic or quoted, next
        to a book ID, or followed by "by" and an author's name."""
        if marked_name(text, start, end, _ID_AFTER, _ID_BEFORE):
            return True
        by = _BY.match(text[end:])
        return bool(by and self._others and self._others.match(text[end:], by.end()))


def _checkable_title(title: str) -> bool:
    words = title.split()
    if len(words) > 1:
        return any(len(word) > 3 and word[0].isupper() for word in words)
    return len(title) >= _MIN_SINGLE_WORD_TITLE and title[:1].isupper()


def _rating_allowed(raw: str, ratings: Sequence[float], typed: Sequence[float]) -> bool:
    value = parse_number(raw)
    decimals = len(raw.replace(",", ".").partition(".")[2])
    if any(abs(value - number) < 1e-9 for number in typed):
        return True
    # "4.6" may round 4.55; "4.55" must be exact; a whole "4" stars may round 4.25.
    tolerance = {0: 0.5, 1: 0.05}.get(decimals, 0.005) + 1e-9
    return any(abs(value - rating) <= tolerance for rating in ratings)
