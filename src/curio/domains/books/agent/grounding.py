"""Book grounding: book IDs, titles, authors, ratings, page counts and years in an answer."""

import re
from collections.abc import Callable, Iterable, Sequence

from curio.domains.books.models import Book
from curio.engine.grounding import (
    DASHES,
    Binder,
    Citation,
    Claim,
    FactCheck,
    Item,
    Tally,
    any_of,
    check_bylines,
    check_ids,
    check_marked_names,
    marked_name,
    name_allowed,
    parse_number,
    same_number,
)
from curio.engine.schemas import GroundingReport

# Book answers cite books as "[id 192]" (the prompt's format) or "book #192".
_BOOK_ID = re.compile(r"\[id (\d+)\]|\bbook #(\d+)\b", re.IGNORECASE)
BOOK_CITATIONS = (Citation(_BOOK_ID, "book"),)
# "★ 4.55", "rated 4.5", "average rating of 4.55", "scores 4.9", "a Goodreads average of
# 4.2", "4.6/5", "4.5 out of 5", "4.3 stars", "4.4 on Goodreads".
_RATING = re.compile(
    r"(?:[★⭐]|\brated\b|\brating(?:\s+of)?\b|\baverage\s+rating(?:\s+of)?\b|\bscor(?:es?|ed|ing)"
    r"\b(?:\s+(?:an?|of))?|\bGoodreads\s+(?:average|rating|score)(?:\s+of)?\b)"
    r"\s*:?\s*(?:an?\s+)?([0-5](?:[.,]\d{1,2})?)(?![\d.,]*\d)"
    r"|(?<![\d.,])([0-5](?:[.,]\d{1,2})?)\s*(?:/\s*5\b|out of 5\b|stars?\b|★|on Goodreads\b)",
    re.IGNORECASE,
)
# In a book answer a bare "4.55" or "3.9" is a rating too. What follows it on the same
# line can make it something else (a range "4.2-4.5", "3.5 hours"); the next bullet's dash
# ("- 4.37\n- **Goblet of Fire**") does not.
_BARE_RATING = re.compile(
    r"(?<![\d.,€$£#\w])([1-5][.,]\d{1,2})(?![\d.,]*\d)"
    r"(?![ \t]*(?:%|€|eur|cm|mm|oz|ml|cl|hours?|million|m\b|k\b|[-\u2013\u2014][ \t]*\d))",
    re.IGNORECASE,
)
# "320 pages", "a 300-page novel", "1,007 pp".
_PAGES = re.compile(r"(?<![\d.,])(\d{1,2},\d{3}|\d{2,4})(?:\s+|-)(?:pages?|pp)\b", re.IGNORECASE)
# "published in 1985", "first published 1937", "from 1985", "(1985)", "a 1960 novel".
_YEAR = re.compile(
    r"\b(?:published|written|released|appeared|came out)(?:\s+in)?\s+(\d{4})\b"
    r"|\bfrom\s+(\d{4})\b"
    r"|\((\d{4})\)"
    r"|\b(?:an?|the|his|her|their)\s+(\d{4})\s+(?:novel|classic|book|bestseller|debut|memoir|"
    r"story|thriller|mystery|fantasy)\b",
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
# Page counts quoted roughly ("a 300-page novel" for 304 pages) stay within this share.
_PAGES_SHARE = 0.05


def book_item(book: Book) -> Item:
    values: dict[str, tuple[float, ...]] = {"rating": (book.average_rating,)}
    if book.pages:
        values["pages"] = (float(book.pages),)
    if book.year is not None:
        values["year"] = (float(book.year),)
    names = (book.title, *([book.series] if book.series else []))
    return Item("book", book.book_id, names, values, people=tuple(book.authors))


def same_rating(raw: str, actual: float, mode: str) -> bool:
    """A rating at the precision written: "4.6" may round 4.55, "4.55" must be exact, a
    whole "4" stars may round 4.25; "over 4" and "about 4.5" compare."""
    value = parse_number(raw)
    if mode in ("less", "more"):
        return same_number(value, actual, mode, 1e-9)
    decimals = len(raw.replace(",", ".").partition(".")[2])
    tolerance = {0: 0.5, 1: 0.05}.get(decimals, 0.005) + 1e-9
    if mode == "about":
        tolerance = max(tolerance, 0.1)
    return abs(value - actual) <= tolerance


def same_pages(claimed: float, actual: float, mode: str) -> bool:
    if mode == "exact":
        return abs(claimed - actual) <= max(1.0, actual * _PAGES_SHARE)
    return same_number(claimed, actual, mode, 1.0)


def same_year(claimed: float, actual: float, mode: str) -> bool:
    return same_number(claimed, actual, mode, 0.5)


def book_claims(text: str) -> list[Claim]:
    """Ratings, page counts and years in a book answer."""
    claims: list[Claim] = []
    taken: list[tuple[int, int]] = []
    for match in _RATING.finditer(text):
        group = 1 if match.group(1) else 2
        taken.append(match.span(group))
        raw = match.group(group)
        claims.append(Claim("rating", match.start(group), match.end(group), raw, raw))
    for match in _BARE_RATING.finditer(text):
        start, end = match.span(1)
        if not any(s <= start < e for s, e in taken):
            claims.append(Claim("rating", start, end, match.group(1), match.group(1)))
    for match in _PAGES.finditer(text):
        raw = match.group(1)
        number = float(raw.replace(",", ""))
        claims.append(Claim("pages", match.start(1), match.end(1), raw, number))
    for match in _YEAR.finditer(text):
        group = next(i for i in range(1, 5) if match.group(i))
        raw = match.group(group)
        claims.append(Claim("year", match.start(group), match.end(group), raw, float(raw)))
    return sorted(claims, key=lambda claim: claim.start)


def _rating_compare(claimed: object, actual: object, mode: str) -> bool:
    return same_rating(str(claimed), float(actual), mode)  # type: ignore[arg-type]


BOOK_COMPARE = {"rating": _rating_compare, "pages": same_pages, "year": same_year}


class BookGroundingCheck:
    """Checks the books an answer names and the facts it states about them.

    - Book IDs ("[id 192]", "book #192") must have been returned.
    - Catalog titles, matched case-sensitively in catalog spelling, must have been returned
      (or be a returned book's series) or typed by the reader. Only titles unlikely to be
      ordinary words count: several words, or one word of at least six letters marked as a
      title (bold, italic, quoted, next to its "[id N]" or followed by "by <author>").
    - Any bold, italic or quoted span that reads as a title, and any "<Title> by <Author>",
      must name a returned book (or something the tools or the reader wrote), so a book
      outside the catalog is caught too.
    - "<title> by <name>" must name one of that book's authors.
    - Ratings, page counts and publication years bind to the book they are about (the
      nearest book named in the sentence, else the last one named before) and must match
      it. A rating the reader typed counts only where the answer contradicts it. A page
      total ("2,786 pages in total") must add up the books named.
    """

    def __init__(self, titles: Iterable[str], *, other_names: Iterable[str] = ()) -> None:
        """`other_names` (authors, genres, moods) are never read as titles: a title equal to
        one ("Mystery") is ignored, and so is a title inside one ("George" in "George
        Orwell")."""
        others = {name for name in other_names if name.strip()}
        folded = {name.casefold() for name in others}
        self._vocabulary = folded
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
        skip_name: Callable[[str], bool] = lambda _: False,
    ) -> GroundingReport:
        """`typed` is what the reader wrote; `also_allowed` are other names this turn's
        tools returned (paired drinks such as "Casino Royale" or "Death in the
        Afternoon"), which may share a book's title or be bold in the answer. Bold or
        quoted names `skip_name` accepts (catalog drinks) are left to another check."""
        tally = Tally()
        self.check_into(
            text, evidence, typed, tally, also_allowed=also_allowed, skip_name=skip_name
        )
        return tally.report()

    def check_into(
        self,
        text: str,
        evidence: Sequence[Book],
        typed: str,
        tally: Tally,
        *,
        also_allowed: Sequence[str] = (),
        citations: Sequence[Citation] = BOOK_CITATIONS,
        names: bool = True,
        skip_name: Callable[[str], bool] = lambda _: False,
    ) -> None:
        """Add this answer's book facts to `tally`. With `names` False the caller checks
        bold and quoted names itself (an evening answer names products and drinks too)."""
        id_pattern = citations[0].pattern
        checked, issues = check_ids(text, id_pattern, (b.book_id for b in evidence), "book")
        tally.counts["id"] += checked
        tally.issues += [i for i in issues if i not in tally.issues]
        known = [*(b.title for b in evidence), *(b.series for b in evidence if b.series)]
        known += list(also_allowed)
        for title in self._title_mentions(text):
            ok = name_allowed(title, known, typed)
            tally.add("title", ok, f"{title} was not returned by any tool", key=title.casefold())
        binder = Binder(text, [book_item(b) for b in evidence], citations)
        FactCheck(
            binder,
            BOOK_COMPARE,
            typed=typed,
            noun={"rating": "rating", "pages": "page count"},
            arithmetic=frozenset({"pages"}),
        ).check(book_claims(text), tally)
        people = [author for b in evidence for author in b.authors]
        check_bylines(text, binder, tally, known=[*known, *people], typed=typed)
        if names:
            check_marked_names(
                text,
                [*known, *people, typed],
                tally,
                kind="title",
                vocabulary=self.vocabulary,
                skip=skip_name,
            )

    def vocabulary(self, words: str) -> bool:
        """Authors, genres and moods: never a title on their own."""
        return words.casefold() in self._vocabulary

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
