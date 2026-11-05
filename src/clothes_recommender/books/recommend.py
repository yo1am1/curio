"""Offline book search, "readers who liked X also liked", more by an author and genre/mood
browse over the prepared goodbooks-10k catalog.

Everything is precomputed at construction with numpy/scipy, deterministic, and ties go to the
lower book_id (goodbooks-10k numbers books by popularity).

Search (`search`):
- TF-IDF cosine over title, series, authors, genres, reader shelves and the description, each
  with its own weight (`FIELD_WEIGHTS`), scaled by the share of query words the book uses and
  by a quality prior (`quality`: Goodreads rating and popularity) that only breaks near-ties.
- "like <title>", "similar to <title>" or "fans of <title>" seeds the search with that book:
  candidates are the books its readers also liked, blended with shelf similarity, minus its
  own series; the other query words ("fantasy like ...") must still match.
- "by <author>" becomes an author filter when the name is in the catalog.
- Genre names ("sci-fi", "ya", "classics") become genre filters (`GENRE_WORDS`) and the
  first mood word ("cosy", "dark", "funny") the mood filter (`MOOD_WORDS`); "short" (not
  "short stories") limits the length to `SHORT_PAGES` pages; "scary" or "uplifting" stand for
  their shelves (`SHELF_HINTS`).
- Filters: genres (all must apply), a `MOODS` entry, author, minimum rating, maximum pages
  (books without a known page count are left out), excluded genres and authors. Boosted
  genres (remembered tastes) rank matching books higher without filtering.
- An empty query with filters is a browse: books ranked by how strongly they carry the mood
  and by the quality prior.

Readers also liked (`similar`): the precomputed item-item neighbours (asymmetric cosine over
4-5 star ratings with popularity damping; see scripts/prepare_books.py), topped up with shelf
similarity when a book has few neighbours. `because` sums them over several liked books.
"""

import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
from scipy import sparse

from clothes_recommender.books.catalog import BookCatalog, name_key
from clothes_recommender.books.domain import Book, BookHit, BookQuery
from clothes_recommender.books.taxonomy import (
    GENRE_WORDS,
    MOOD_WORDS,
    MOODS,
    SHORT_PAGES,
    Mood,
)

# Words per field; repeated words count again, so a title word outweighs a description word.
FIELD_WEIGHTS = {
    "title": 3.0,
    "series": 2.0,
    "authors": 2.0,
    "genres": 2.0,
    "tags": 1.0,
    "description": 0.3,
}
# Query words that stand for shelf words; their weight is shared. Mood words ("dark",
# "funny") become the mood filter instead (`MOOD_WORDS` in taxonomy).
SHELF_HINTS: Mapping[str, tuple[str, ...]] = {
    "scary": ("horror", "scary", "creepy"),
    "spooky": ("horror", "scary", "creepy"),
    "creepy": ("horror", "scary", "creepy"),
    "heartbreaking": ("sad", "tear", "emotional"),
    "uplifting": ("inspirational", "feel", "good"),
    "inspiring": ("inspirational",),
    "spy": ("spy", "espionage"),
}
# Generic request words that never describe a book.
STOPWORDS = frozenset(
    {
        "a", "an", "and", "the", "of", "with", "in", "on", "for", "to", "or", "about", "me",
        "i", "my", "some", "any", "something", "book", "books", "novel", "novels", "read",
        "reads", "reading", "recommend", "recommendation", "recommendations", "want", "looking",
        "find", "show", "give", "good", "great", "best", "please", "that", "is", "are", "it",
        "like", "similar", "by", "from", "who", "which", "would", "could", "should", "what",
        "new", "next", "story", "stories",
    }
)  # fmt: skip
_MOOD_PHRASE = re.compile(
    r"(?<![\w'])("
    + "|".join(re.escape(p) for p in sorted(MOOD_WORDS, key=lambda p: (-len(p), p)))
    + r")(?![\w'])"
)
_GENRE_PHRASE = re.compile(
    r"(?<![\w'])("
    + "|".join(re.escape(p) for p in sorted(GENRE_WORDS, key=lambda p: (-len(p), p)))
    + r")(?![\w'])"
)
SHORT_WORDS = frozenset({"short", "shorter", "quick", "brief"})
MIN_PREFIX = 4
MAX_PER_AUTHOR = 2
MAX_REASON_ITEMS = 3
# Popular books need this many ratings before their average counts fully (Bayesian average).
PRIOR_RATINGS = 1_000
# Share of the final search score that comes from relevance; the rest is the quality prior.
RELEVANCE_SHARE = 0.65
# "like <title>" blends readers-also-liked with shelf similarity this way.
NEIGHBOUR_SHARE = 0.75
_TOKEN = re.compile(r"[a-z0-9]+")
_SEED = re.compile(
    r"\b(?:like|similar to|in the vein of|fans? of|readers of|reminds? me of|as good as|"
    r"because i (?:liked|loved|enjoyed)|if i (?:liked|loved|enjoyed))\s+(.+)$"
)
_BY = re.compile(r"\b(?:by|from|written by)\s+(.+)$")


def tokens(text: str) -> list[str]:
    """Lowercase, accent-free words ("Márquez's" -> marquez, s)."""
    folded = text.casefold()
    if not folded.isascii():
        # Accents fold away; other letters are not [a-z0-9] tokens anyway.
        folded = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    return _TOKEN.findall(folded)


@lru_cache(maxsize=65_536)
def term(word: str) -> str:
    """A light plural rule ("dragons" -> dragon, "stories" -> story, "classics" -> classic)."""
    if len(word) <= 3 or word.endswith(("ss", "us", "is")):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def terms(text: str) -> list[str]:
    return [term(t) for t in tokens(text)]


@dataclass(frozen=True)
class _Seed:
    row: int
    start: int  # the query text from "like" to the end of the title
    end: int


class BookRecommender:
    def __init__(self, catalog: BookCatalog, neighbours: pd.DataFrame) -> None:
        self._catalog = catalog
        self._books: list[Book] = list(catalog)
        self._row = {b.book_id: row for row, b in enumerate(self._books)}
        self._genre_keys = [frozenset(name_key(g) for g in b.genres) for b in self._books]
        self._author_keys = [frozenset(name_key(a) for a in b.authors) for b in self._books]
        self._tag_sets = [frozenset(b.tags) for b in self._books]
        self._quality = quality(self._books)
        self._vocabulary, self._documents = _tfidf([_fields(b) for b in self._books])
        self._by_word = self._documents.tocsc()
        self._shelves = _shelf_matrix(self._books)
        self._neighbours = self._load_neighbours(neighbours)
        self._title_keys: dict[str, list[int]] = defaultdict(list)
        for row, book in enumerate(self._books):
            self._title_keys[name_key(book.title)].append(row)

    # --- Public API ---------------------------------------------------------------------

    def search(self, query: BookQuery) -> list[BookHit]:
        text = query.query.strip()
        seed = self._seed(text)
        if seed is not None:
            text = f"{text[: seed.start]} {text[seed.end :]}"
        if query.author is None and (author := self._author_in(text)):
            name, start, end = author
            query = query.model_copy(update={"author": name})
            text = f"{text[:start]} {text[end:]}"
        text = text.casefold()
        if query.mood is None and (found := _MOOD_PHRASE.search(text)):
            query = query.model_copy(update={"mood": MOOD_WORDS[found.group(1)]})
            text = f"{text[: found.start()]} {text[found.end() :]}"
        text, genres = _genre_phrases(text)
        if genres:
            named = list(dict.fromkeys([*query.genres, *genres]))[:5]
            query = query.model_copy(update={"genres": named})
        words = [w for w in terms(text) if w not in STOPWORDS]
        # "short stories" was taken as a genre above, so "short" here is about length.
        if query.max_pages is None and any(w in SHORT_WORDS for w in words):
            query = query.model_copy(update={"max_pages": SHORT_PAGES})
        words = [w for w in words if w not in SHORT_WORDS]
        mood = self._mood(query.mood)
        candidates = [row for row in range(len(self._books)) if self._allowed(row, query, mood)]
        if seed is not None:
            candidates = [
                r for r in candidates if r != seed.row and not self._same_series(r, seed.row)
            ]
        concepts = self._concepts(words)
        scores = self._scores(candidates, concepts, seed, mood, query, bool(words))
        boost = {name_key(g) for g in query.boost_genres}
        if boost:
            scores = {
                row: score * (1.1 if self._genre_keys[row] & boost else 1.0)
                for row, score in scores.items()
            }
        ranked = sorted(scores, key=lambda row: (-round(scores[row], 9), row))[: query.top_k]
        matched = {w for c in concepts for w in c}
        return [
            BookHit(
                book=self._books[row],
                score=round(scores[row], 6),
                reasons=self._search_reasons(row, query, mood, matched, seed, boost),
            )
            for row in ranked
        ]

    def similar(
        self,
        book_id: int,
        top_k: int = 5,
        *,
        other_authors_only: bool = False,
        exclude_genres: Sequence[str] = (),
        exclude_authors: Sequence[str] = (),
    ) -> list[BookHit]:
        """Books readers of `book_id` also liked, topped up with shelf-similar books."""
        row = self._row.get(book_id)
        if row is None or top_k < 1:
            return []
        excluded = _ExcludeFilter(exclude_genres, exclude_authors)

        def keep(other: int) -> bool:
            if other == row or excluded.hits(self._books[other]):
                return False
            return not (other_authors_only and self._author_keys[other] & self._author_keys[row])

        hits: list[BookHit] = []
        rows, scores, shared = self._neighbours.get(row, _EMPTY)
        for other, score, readers in zip(rows, scores, shared, strict=True):
            if keep(int(other)) and len(hits) < top_k:
                reasons = [self._readers_reason(row, int(readers))]
                hits.append(self._hit(int(other), float(score), reasons + self._link(row, other)))
        if len(hits) < top_k:
            taken = {h.book.book_id for h in hits}
            for other, score in self._shelf_similar(row):
                if len(hits) >= top_k:
                    break
                if keep(other) and self._books[other].book_id not in taken:
                    hits.append(self._hit(other, score * 0.1, self._link(row, other)))
        return hits

    def because(
        self,
        liked: Sequence[int],
        top_k: int = 10,
        *,
        exclude_genres: Sequence[str] = (),
        exclude_authors: Sequence[str] = (),
    ) -> list[BookHit]:
        """Books the readers of several liked books also liked, summed over the seeds; the
        seeds and their series are left out, and each author appears at most MAX_PER_AUTHOR
        times so one prolific author cannot fill the list."""
        seeds = list(dict.fromkeys(self._row[b] for b in liked if b in self._row))
        if not seeds or top_k < 1:
            return []
        excluded = _ExcludeFilter(exclude_genres, exclude_authors)
        totals: Counter[int] = Counter()
        best: dict[int, tuple[float, int, int]] = {}
        for seed in seeds:
            rows, scores, shared = self._neighbours.get(seed, _EMPTY)
            for other, score, readers in zip(rows.tolist(), scores, shared, strict=True):
                totals[other] += float(score)
                if other not in best or score > best[other][0]:
                    best[other] = (float(score), seed, int(readers))
        ranked = sorted(
            (
                row
                for row in totals
                if row not in seeds
                and not any(self._same_series(row, seed) for seed in seeds)
                and not excluded.hits(self._books[row])
            ),
            key=lambda row: (-round(totals[row], 9), row),
        )
        per_author: Counter[str] = Counter()
        diverse = []
        for row in ranked:
            author = name_key(self._books[row].author)
            if per_author[author] < MAX_PER_AUTHOR:
                per_author[author] += 1
                diverse.append(row)
            if len(diverse) == top_k:
                break
        hits = []
        for row in diverse:
            _, seed, readers = best[row]
            reasons = [f"Because you liked {self._books[seed].title}"]
            reasons.append(self._readers_reason(seed, readers))
            also = [s for s in seeds if s != seed and row in self._neighbour_rows(s)]
            if also:
                titles = _join([self._books[s].title for s in also])
                reasons.append(f"Readers of {titles} liked it too")
            hits.append(self._hit(row, totals[row], reasons))
        return hits

    def by_author(
        self, author: str, top_k: int = 10, *, exclude_book_id: int | None = None
    ) -> list[BookHit]:
        """The author's books, most rated first (the catalog spelling is matched ignoring case,
        accents and punctuation). Unknown author -> []."""
        canonical = self._catalog.canonical_author(author)
        if canonical is None or top_k < 1:
            return []
        books = sorted(
            (b for b in self._catalog.by_author(canonical) if b.book_id != exclude_book_id),
            key=lambda b: (-b.ratings_count, b.book_id),
        )
        hits = []
        for book in books[:top_k]:
            reasons = [f"By {canonical}"]
            if book.series:
                position = f"Book {book.series_position} of " if book.series_position else ""
                reasons.append(f"{position}{book.series}")
            row = self._row[book.book_id]
            hits.append(self._hit(row, float(self._quality[row]), reasons))
        return hits

    def seed_of(self, query: str) -> Book | None:
        """The book a "like <title>" query is seeded with, or None."""
        seed = self._seed(query.strip())
        return self._books[seed.row] if seed is not None else None

    def resolve_title(self, text: str) -> Book | None:
        """The catalog book a whole phrase names ("the name of the wind"), or None; the most
        rated one when several books share the title."""
        text = text.strip()
        found = self._seed_rows(text)
        return self._books[found[0]] if found and found[1] == len(text) else None

    # --- Search internals ----------------------------------------------------------------

    def _seed(self, text: str) -> _Seed | None:
        match = _SEED.search(text.casefold())
        if match is None:
            return None
        found = self._seed_rows(text[match.start(1) :])
        if found is None:
            return None
        row, used = found
        return _Seed(row, match.start(), match.start(1) + used)

    def _seed_rows(self, phrase: str) -> tuple[int, int] | None:
        """(row, characters used) of the longest leading part of `phrase` that is a title;
        "the" may be left out. The most rated book wins when titles repeat."""
        words = list(re.finditer(r"\S+", phrase))
        for end in range(len(words), 0, -1):
            used = words[end - 1].end()
            key = name_key(phrase[:used])
            rows = self._title_keys.get(key) or self._title_keys.get(f"the {key}")
            if rows:
                best = min(rows, key=lambda r: (-self._books[r].ratings_count, r))
                return best, used
        return None

    def _author_in(self, text: str) -> tuple[str, int, int] | None:
        """(catalog author, start, end) of a "by <author>" in the query."""
        match = _BY.search(text.casefold())
        if match is None:
            return None
        phrase = text[match.start(1) :]
        words = list(re.finditer(r"\S+", phrase))
        for end in range(len(words), 0, -1):
            canonical = self._catalog.canonical_author(phrase[: words[end - 1].end()])
            if canonical:
                return canonical, match.start(), match.start(1) + words[end - 1].end()
        return None

    def _mood(self, name: str | None) -> tuple[str, Mood] | None:
        if not name:
            return None
        key = name_key(name)
        return next(((m, mood) for m, mood in MOODS.items() if name_key(m) == key), None)

    def _allowed(self, row: int, query: BookQuery, mood: tuple[str, Mood] | None) -> bool:
        book = self._books[row]
        if query.genres and not {name_key(g) for g in query.genres} <= self._genre_keys[row]:
            return False
        if (
            query.exclude_genres
            and {name_key(g) for g in query.exclude_genres} & (self._genre_keys[row])
        ):
            return False
        if query.author and name_key(query.author) not in self._author_keys[row]:
            return False
        if (
            query.exclude_authors
            and {name_key(a) for a in query.exclude_authors} & (self._author_keys[row])
        ):
            return False
        if query.min_rating is not None and book.average_rating < query.min_rating:
            return False
        limit = query.max_pages
        if mood is not None and mood[1].max_pages is not None:
            limit = min(limit or mood[1].max_pages, mood[1].max_pages)
        if limit is not None and (book.pages is None or book.pages > limit):
            return False
        if mood is not None and mood[1].shelves and mood[1].max_pages is None:
            return self._mood_strength(row, mood[1]) > 0
        return True

    def _genre_strength(self, row: int, wanted: Sequence[str]) -> float:
        """Mean of 1 / (1 + position) of the wanted genres in the book's genres (strongest
        first), so a book that is mainly fantasy outranks one that is also fantasy."""
        keys = [name_key(g) for g in self._books[row].genres]
        positions = [keys.index(g) for g in wanted if g in keys]
        return sum(1 / (1 + p) for p in positions) / len(wanted) if positions else 0.0

    def _mood_strength(self, row: int, mood: Mood) -> float:
        """1 when the book's most used shelf expresses the mood, falling with its position."""
        labels = {shelf.replace("-", " ") for shelf in mood.shelves}
        for position, tag in enumerate(self._books[row].tags):
            if tag in labels:
                return 1 / (1 + position)
        return 0.0

    def _concepts(self, words: Sequence[str]) -> list[dict[str, float]]:
        """Per query word, the vocabulary words it stands for: itself, its mood words, or the
        words it starts ("dystop" -> dystopian, dystopia), sharing the word's weight."""
        concepts = []
        for word in words:
            hints = [w for w in SHELF_HINTS.get(word, ()) if w in self._vocabulary]
            if not hints and word in self._vocabulary:
                hints = [word]
            elif not hints and len(word) >= MIN_PREFIX:
                hints = [w for w in self._vocabulary if w.startswith(word)][:20]
            if hints:
                concepts.append(dict.fromkeys(hints, 1 / len(hints)))
        return concepts

    def _scores(
        self,
        candidates: list[int],
        concepts: list[dict[str, float]],
        seed: _Seed | None,
        mood: tuple[str, Mood] | None,
        query: BookQuery,
        has_words: bool,
    ) -> dict[int, float]:
        if not candidates:
            return {}
        rows = np.asarray(candidates)
        quality_prior = self._quality[rows]
        if has_words and not concepts:
            return {}  # no book uses any of the query words
        relevance = np.ones(len(rows))
        if seed is not None:
            # The seed ranks; other query words ("fantasy like ...") only have to match.
            if concepts:
                relevance = self._coverage(rows, concepts)
            relevance = relevance * self._seed_relevance(rows, seed.row)
        elif concepts:
            relevance = self._text_relevance(rows, concepts)
        if query.genres:
            wanted = [name_key(g) for g in query.genres]
            relevance = relevance * np.array([self._genre_strength(r, wanted) for r in candidates])
        if seed is None and mood is not None and mood[1].shelves:
            strength = np.array([self._mood_strength(r, mood[1]) for r in candidates])
            relevance = relevance * (0.5 + 0.5 * strength)
        score = relevance * (RELEVANCE_SHARE + (1 - RELEVANCE_SHARE) * quality_prior)
        keep = relevance > 0
        return dict(zip(rows[keep].tolist(), score[keep].tolist(), strict=True))

    def _text_relevance(self, rows: np.ndarray, concepts: list[dict[str, float]]) -> np.ndarray:
        vector = np.zeros(len(self._vocabulary))
        for concept in concepts:
            for word, weight in concept.items():
                vector[self._vocabulary[word]] += weight
        cosine = self._documents[rows] @ vector / (np.linalg.norm(vector) or 1)
        # The square root narrows the gap between a book that repeats a word everywhere and
        # one that merely carries it, so the quality prior can order them.
        return np.sqrt(np.maximum(cosine, 0)) * self._coverage(rows, concepts)

    def _coverage(self, rows: np.ndarray, concepts: list[dict[str, float]]) -> np.ndarray:
        """Share of the query's words each book uses, so books using all of them rank first."""
        used = np.zeros(len(rows))
        for concept in concepts:
            columns = [self._vocabulary[w] for w in concept]
            used += np.asarray(self._by_word[:, columns][rows].sum(axis=1)).ravel() > 0
        return used / len(concepts)

    def _seed_relevance(self, rows: np.ndarray, seed: int) -> np.ndarray:
        neighbour_rows, scores, _ = self._neighbours.get(seed, _EMPTY)
        readers = dict(zip(neighbour_rows.tolist(), scores.tolist(), strict=True))
        top = max(readers.values(), default=1.0)
        cf = np.array([readers.get(int(r), 0.0) / top for r in rows])
        shelves = np.asarray(self._shelves[rows] @ self._shelves[seed].T.toarray()).ravel()
        return NEIGHBOUR_SHARE * cf + (1 - NEIGHBOUR_SHARE) * shelves

    def _search_reasons(
        self,
        row: int,
        query: BookQuery,
        mood: tuple[str, Mood] | None,
        matched: set[str],
        seed: _Seed | None,
        boost: set[str],
    ) -> list[str]:
        book = self._books[row]
        reasons = []
        if seed is not None:
            rows, _, shared = self._neighbours.get(seed.row, _EMPTY)
            readers = dict(zip(rows.tolist(), shared.tolist(), strict=True)).get(row)
            if readers:
                reasons.append(self._readers_reason(seed.row, int(readers)))
            else:
                reasons.append(f"Shelved like {self._books[seed.row].title}")
        in_title = [w for w in dict.fromkeys(tokens(book.title)) if term(w) in matched]
        if in_title:
            reasons.append(f'Title matches "{" ".join(in_title)}"')
        if query.author:
            reasons.append(f"By {self._catalog.canonical_author(query.author) or query.author}")
        elif any(term(w) in matched for w in tokens(book.author)):
            reasons.append(f"By {book.author}")
        genres = [
            g
            for g in book.genres
            if name_key(g) in {name_key(q) for q in query.genres} or set(terms(g)) & matched
        ]
        if genres:
            reasons.append(f"Genre: {_join(genres)}")
        shelves = [
            t
            for t in book.tags
            if set(terms(t)) & matched and t.casefold() not in {g.casefold() for g in book.genres}
        ]
        if shelves:
            reasons.append(f"Shelved as {_join(shelves)}")
        if mood is not None:
            labels = {s.replace("-", " ") for s in mood[1].shelves}
            tagged = [t for t in book.tags if t in labels]
            detail = f" (shelved as {_join(tagged)})" if tagged else ""
            reasons.append(f"Mood: {mood[0]}{detail}")
        if query.max_pages is not None and book.pages is not None:
            reasons.append(f"{book.pages} pages")
        if query.min_rating is not None:
            reasons.append(f"Rated {book.average_rating:.2f}")
        if not genres and boost and (liked := [g for g in book.genres if name_key(g) in boost]):
            reasons.append(f"A genre you like: {_join(liked)}")
        if query.exclude_authors or query.exclude_genres:
            reasons.append("Respects what you avoid")
        if not reasons:
            reasons.append(f"Loved by readers: {book.ratings_count:,} Goodreads ratings")
        return reasons

    # --- Neighbours ------------------------------------------------------------------------

    def _load_neighbours(self, frame: pd.DataFrame) -> dict[int, tuple[np.ndarray, ...]]:
        """Row -> (neighbour rows, scores, shared readers), best first; unknown ids dropped."""
        missing = {"book_id", "neighbour_id", "score", "co_readers"} - set(frame.columns)
        if missing:
            raise ValueError(f"Book neighbours are missing columns: {', '.join(sorted(missing))}")
        frame = frame[frame["book_id"].isin(self._row) & frame["neighbour_id"].isin(self._row)]
        frame = frame[frame["book_id"] != frame["neighbour_id"]]
        # One link per pair: the strongest, should the file repeat a pair.
        frame = frame.sort_values("score", ascending=False, kind="stable").drop_duplicates(
            ["book_id", "neighbour_id"]
        )
        rows = frame["book_id"].map(self._row).to_numpy(dtype=np.int64)
        others = frame["neighbour_id"].map(self._row).to_numpy(dtype=np.int64)
        scores = frame["score"].to_numpy(dtype=np.float64)
        shared = frame["co_readers"].to_numpy(dtype=np.int64)
        order = np.lexsort((others, -np.round(scores, 9), rows))
        rows, others, scores, shared = rows[order], others[order], scores[order], shared[order]
        starts = np.flatnonzero(np.r_[True, rows[1:] != rows[:-1]]) if len(rows) else []
        ends = [*starts[1:], len(rows)]
        return {
            int(rows[start]): (others[start:end], scores[start:end], shared[start:end])
            for start, end in zip(starts, ends, strict=True)
        }

    def _neighbour_rows(self, row: int) -> set[int]:
        return set(self._neighbours.get(row, _EMPTY)[0].tolist())

    def _shelf_similar(self, row: int) -> list[tuple[int, float]]:
        scores = np.asarray(self._shelves @ self._shelves[row].T.toarray()).ravel()
        order = np.lexsort((np.arange(len(scores)), -np.round(scores, 9)))
        return [(int(r), float(scores[r])) for r in order[:200] if scores[r] > 0 and r != row]

    def _readers_reason(self, seed: int, readers: int) -> str:
        noun = "reader" if readers == 1 else "readers"
        return f"{readers:,} {noun} who liked {self._books[seed].title} also liked this"

    def _link(self, row: int, other: int) -> list[str]:
        """How two books relate beyond shared readers: series, author, shared shelves."""
        book, peer = self._books[row], self._books[other]
        if book.series and book.series == peer.series:
            return [f"Also in {book.series}"]
        if shared_authors := [a for a in peer.authors if name_key(a) in self._author_keys[row]]:
            return [f"Also by {shared_authors[0]}"]
        shared = [t for t in peer.tags if t in self._tag_sets[row]]
        return [f"Both shelved as {_join(shared)}"] if shared else []

    def _same_series(self, row: int, other: int) -> bool:
        series = self._books[row].series
        return (
            series is not None
            and series == self._books[other].series
            and (bool(self._author_keys[row] & self._author_keys[other]))
        )

    def _hit(self, row: int, score: float, reasons: list[str]) -> BookHit:
        return BookHit(book=self._books[row], score=round(score, 6), reasons=reasons)


_EMPTY = (np.empty(0, dtype=np.int64), np.empty(0), np.empty(0, dtype=np.int64))


class _ExcludeFilter:
    def __init__(self, genres: Iterable[str], authors: Iterable[str]) -> None:
        self._genres = {name_key(g) for g in genres}
        self._authors = {name_key(a) for a in authors}

    def hits(self, book: Book) -> bool:
        return bool(
            {name_key(g) for g in book.genres} & self._genres
            or {name_key(a) for a in book.authors} & self._authors
        )


def _genre_phrases(text: str) -> tuple[str, list[str]]:
    """The query without its genre names, and the genres they name ("sci-fi" -> Science
    fiction); "short stories" stays a genre, never the length hint "short"."""
    genres = [GENRE_WORDS[match] for match in _GENRE_PHRASE.findall(text)]
    return _GENRE_PHRASE.sub(" ", text), list(dict.fromkeys(genres))


def quality(books: Sequence[Book]) -> np.ndarray:
    """A 0-1 prior per book: half Bayesian-average rating, half log popularity."""
    if not books:
        return np.empty(0)
    counts = np.array([b.ratings_count for b in books], dtype=float)
    ratings = np.array([b.average_rating for b in books], dtype=float)
    mean = float(np.average(ratings, weights=np.maximum(counts, 1)))
    bayes = (ratings * counts + mean * PRIOR_RATINGS) / (counts + PRIOR_RATINGS)
    spread = bayes.max() - bayes.min()
    rating = (bayes - bayes.min()) / spread if spread > 0 else np.zeros(len(books))
    popularity = np.log1p(counts) / math.log1p(counts.max()) if counts.max() > 0 else counts
    return 0.5 * rating + 0.5 * popularity


def _fields(book: Book) -> list[tuple[str, float]]:
    words: list[tuple[str, float]] = []
    parts = {
        "title": book.title,
        "series": book.series or "",
        "authors": " ".join(book.authors),
        "genres": " ".join(book.genres),
        "tags": " ".join(book.tags),
        "description": book.description,
    }
    for field, text in parts.items():
        words += [(w, FIELD_WEIGHTS[field]) for w in terms(text) if w not in STOPWORDS]
    return words


def _tfidf(documents: list[list[tuple[str, float]]]) -> tuple[dict[str, int], sparse.csr_matrix]:
    """Vocabulary and the L2-normalised TF-IDF matrix (one row per book) with field weights."""
    vocabulary = {w: i for i, w in enumerate(sorted({w for doc in documents for w, _ in doc}))}
    data, rows, columns = [], [], []
    for row, doc in enumerate(documents):
        weights: dict[int, float] = defaultdict(float)
        for word, weight in doc:
            weights[vocabulary[word]] += weight
        rows += [row] * len(weights)
        columns += list(weights)
        data += list(weights.values())
    matrix = sparse.csr_matrix(
        (np.asarray(data, dtype=float), (rows, columns)), shape=(len(documents), len(vocabulary))
    )
    frequency = np.bincount(matrix.indices, minlength=len(vocabulary))
    idf = np.log((1 + len(documents)) / (1 + frequency)) + 1
    return vocabulary, _normalise_rows(matrix @ sparse.diags(idf))


def _shelf_matrix(books: Sequence[Book]) -> sparse.csr_matrix:
    """IDF-weighted book x (genre, shelf) matrix with L2-normalised rows; earlier shelves
    (more readers used them) weigh more."""
    vocabulary: dict[str, int] = {}
    data, rows, columns = [], [], []
    for row, book in enumerate(books):
        labels = [f"genre:{g}" for g in book.genres] + list(book.tags)
        for position, label in enumerate(dict.fromkeys(labels)):
            column = vocabulary.setdefault(label, len(vocabulary))
            rows.append(row)
            columns.append(column)
            data.append(1.0 if label.startswith("genre:") else 1 / (1 + 0.1 * position))
    matrix = sparse.csr_matrix((data, (rows, columns)), shape=(len(books), len(vocabulary)))
    frequency = np.bincount(matrix.indices, minlength=len(vocabulary))
    idf = np.log((1 + len(books)) / (1 + frequency)) + 1
    return _normalise_rows(matrix @ sparse.diags(idf))


def _normalise_rows(matrix: sparse.spmatrix) -> sparse.csr_matrix:
    matrix = sparse.csr_matrix(matrix)
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    scale = np.divide(1.0, norms, out=np.zeros_like(norms), where=norms > 0)
    return sparse.csr_matrix(sparse.diags(scale) @ matrix)


def _join(names: Sequence[str]) -> str:
    """'a, b, c' with at most MAX_REASON_ITEMS names and a '+N more' tail."""
    unique = list(dict.fromkeys(names))
    shown = ", ".join(unique[:MAX_REASON_ITEMS])
    extra = len(unique) - MAX_REASON_ITEMS
    return f"{shown} +{extra} more" if extra > 0 else shown
