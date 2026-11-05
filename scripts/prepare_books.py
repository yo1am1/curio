"""Build data/books.parquet and data/book_neighbours.parquet from goodbooks-10k.

Usage:
    uv run python -m scripts.prepare_books [--source DIR_OR_URL] [--no-details]
        [--no-verify] [--data-dir data]

The source is https://github.com/zygmuntz/goodbooks-10k pinned to one commit: books.csv (10,000
books), ratings.csv (~6M ratings), book_tags.csv and tags.csv (reader shelves), and
books_xml/books_xml.zip (page counts and descriptions; skipped with --no-details). Every file
is checked against its SHA-256 at that commit, also when `--source` is a local copy of the
repository; `--no-verify` skips the check for other versions.

Outputs (layout in docs/multidomain-contract.md):
- books.parquet: one row per book with clean titles, series, authors, genres and descriptive
  shelves, cover URL (https Goodreads covers only), page count and a short description.
- book_neighbours.parquet: for each book, the BOOK_NEIGHBOURS books its readers also liked
  most (item-item collaborative filtering over 4-5 star ratings, popularity damped; see
  `neighbours`). Precomputing them keeps API startup fast and the 72 MB ratings file out of
  serving.
"""

import argparse
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from xml.etree import ElementTree

import httpx
import numpy as np
import pandas as pd
from scipy import sparse

from clothes_recommender.books.catalog import (
    NEIGHBOUR_COLUMNS,
    OUTPUT_COLUMNS,
    isbn13,
    trusted_cover_url,
)
from clothes_recommender.books.taxonomy import MAX_TAGS, genres_for, is_descriptive, shelf_label
from clothes_recommender.config import get_settings

SOURCE_COMMIT = "6dd165b555a7b47b2dd36743a425776e641ff50c"
DEFAULT_SOURCE = f"https://raw.githubusercontent.com/zygmuntz/goodbooks-10k/{SOURCE_COMMIT}"
BOOKS_OUTPUT = "books.parquet"
NEIGHBOURS_OUTPUT = "book_neighbours.parquet"


@dataclass(frozen=True)
class SourceFile:
    path: str  # relative to the repository root
    sha256: str  # at SOURCE_COMMIT
    max_bytes: int


BOOKS_CSV = SourceFile(
    "books.csv", "4d21d0e0433128657c752e3a3d0405ac0ec11675d6c70c64cb0abf4ab1acb76a", 10 << 20
)
RATINGS_CSV = SourceFile(
    "ratings.csv", "1ee8a97172bd6d97147a30d9cfb3029ac240bd27ba2aa9365981c051c65113f8", 100 << 20
)
BOOK_TAGS_CSV = SourceFile(
    "book_tags.csv", "8ff2f0659be9ddbe10bf47ba33b5b69024f893f017ac665236f15bc97d862950", 30 << 20
)
TAGS_CSV = SourceFile(
    "tags.csv", "87f316957564fa21c316a4f50afc87f07906f478f990eec027aaca00c536c449", 2 << 20
)
DETAILS_ZIP = SourceFile(
    "books_xml/books_xml.zip",
    "f9724dddfbd4d744e97977c4d631684b2aa238739893749ffc66b7cb8f5f3a66",
    80 << 20,
)

# Collaborative filtering: a rating of LIKED or more means the reader liked the book.
LIKED = 4
BOOK_NEIGHBOURS = 50
# Asymmetric cosine: score(i -> j) = c_ij / (n_i^(1 - DAMPING) * n_j^DAMPING), where c_ij counts
# readers who liked both and n the readers who liked each. DAMPING > 0.5 favours less popular
# neighbours over blockbusters every reader liked; 0.5 would be plain cosine.
DAMPING = 0.65
# Shrinks scores backed by few shared readers: score * c / (c + SHRINKAGE).
SHRINKAGE = 10
MIN_CO_READERS = 5
CHUNK_BOOKS = 1000
RATING_TYPES = {"user_id": "int32", "book_id": "int32", "rating": "int8"}
MAX_DESCRIPTION = 600
MAX_XML_MEMBER = 2 << 20
_SERIES = re.compile(r"^(?P<title>.*?)\s*\((?P<series>[^()]*?),?\s*#(?P<position>[^()]*)\)\s*$")
_HTML_TAG = re.compile(r"<[^>]+>")
_MEMBER = re.compile(r"^books_xml/(\d+)\.xml$")

logger = logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help="The goodbooks-10k repository as a local folder or an https base URL (default: "
        "GitHub raw files at a pinned commit).",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Skip books_xml.zip (56 MB): no page counts or descriptions.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Do not check the files' SHA-256 (for a different goodbooks-10k version).",
    )
    parser.add_argument(
        "--data-dir", type=Path, help="Output directory; defaults to configured data_dir."
    )
    return parser.parse_args(argv)


# --- Reading the source ------------------------------------------------------------------


def fetch(source: str, file: SourceFile, target: Path, *, verify: bool = True) -> Path:
    """Copy `file` from `source` (folder or https base URL) to `target`, checking its SHA-256."""
    digest = hashlib.sha256()
    size = 0
    with target.open("wb") as out:
        for chunk in _chunks(source, file):
            size += len(chunk)
            if size > file.max_bytes:
                raise ValueError(f"{file.path} is larger than {file.max_bytes} bytes")
            digest.update(chunk)
            out.write(chunk)
    if verify and digest.hexdigest() != file.sha256:
        raise ValueError(
            f"SHA-256 mismatch for {file.path}: expected {file.sha256}, got {digest.hexdigest()}"
        )
    return target


def _chunks(source: str, file: SourceFile) -> Iterable[bytes]:
    if source.startswith("https://"):
        url = f"{source.rstrip('/')}/{file.path}"
        timeout = httpx.Timeout(120, connect=10)
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
            response.raise_for_status()
            yield from response.iter_bytes(1 << 20)
        return
    if "://" in source:
        raise ValueError(f"Only local folders and https URLs are supported, got {source!r}")
    with (Path(source) / file.path).open("rb") as handle:
        while chunk := handle.read(1 << 20):
            yield chunk


# --- Books ------------------------------------------------------------------------------


def split_title(title: str) -> tuple[str, str | None, str | None]:
    """'Catching Fire (The Hunger Games, #2)' -> ('Catching Fire', 'The Hunger Games', '2')."""
    title = " ".join(title.split())
    match = _SERIES.match(title)
    if match is None or not match.group("title"):
        return title, None, None
    series = " ".join(match.group("series").split()) or None
    position = match.group("position").strip() or None
    return match.group("title").strip(), series, position


def split_authors(authors: str) -> list[str]:
    names = [" ".join(name.split()) for name in authors.split(",")]
    return list(dict.fromkeys(name for name in names if name))


def book_records(books: pd.DataFrame) -> list[dict]:
    records = []
    for row in books.to_dict("records"):
        title, series, position = split_title(str(row["title"]))
        authors = split_authors(str(row["authors"]))
        if not title or not authors:
            logger.warning("Skipping book %s: no title or author", row["book_id"])
            continue
        year = row["original_publication_year"]
        records.append(
            {
                "book_id": int(row["book_id"]),
                "goodreads_book_id": int(row["goodreads_book_id"]),
                "title": title,
                "authors": authors,
                "series": series,
                "series_position": position,
                "year": None if pd.isna(year) else int(year),
                "language": row["language_code"] if isinstance(row["language_code"], str) else None,
                "average_rating": float(row["average_rating"]),
                "ratings_count": int(row["ratings_count"]),
                "image_url": trusted_cover_url(row["image_url"]),
                "isbn": isbn13(row.get("isbn"), row.get("isbn13")),
            }
        )
    return records


def shelves_by_book(book_tags: pd.DataFrame, tags: pd.DataFrame) -> dict[int, dict[str, int]]:
    """goodreads_book_id -> {shelf name: readers who used it}."""
    named = book_tags.merge(tags, on="tag_id", how="inner")
    named = named[named["count"] > 0]
    named["tag_name"] = named["tag_name"].astype(str).str.strip().str.lower()
    summed = named.groupby(["goodreads_book_id", "tag_name"], sort=False)["count"].sum()
    shelves: dict[int, dict[str, int]] = {}
    for (book, shelf), count in summed.items():
        shelves.setdefault(int(book), {})[shelf] = int(count)
    return shelves


def describe(shelf_counts: Mapping[str, int]) -> tuple[list[str], list[str]]:
    """A book's genres and its MAX_TAGS most used descriptive shelves (display form)."""
    descriptive = sorted(
        (shelf for shelf in shelf_counts if is_descriptive(shelf)),
        key=lambda shelf: (-shelf_counts[shelf], shelf),
    )
    return genres_for(shelf_counts), [shelf_label(shelf) for shelf in descriptive[:MAX_TAGS]]


# --- Details (page counts, descriptions) ---------------------------------------------------


def clean_description(raw: str) -> str:
    """Plain text from Goodreads' HTML blurb, cut at a word boundary."""
    text = " ".join(html.unescape(_HTML_TAG.sub(" ", raw)).split())
    if len(text) <= MAX_DESCRIPTION:
        return text
    return text[:MAX_DESCRIPTION].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def read_details(path: Path) -> dict[int, tuple[int | None, str]]:
    """goodreads_book_id -> (pages, description) from the Goodreads XML archive.

    Only `books_xml/<id>.xml` members up to MAX_XML_MEMBER bytes are read, and documents with a
    DOCTYPE (entity declarations) are skipped, so a tampered archive cannot expand in memory.
    """
    details: dict[int, tuple[int | None, str]] = {}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if not _MEMBER.match(info.filename) or info.file_size > MAX_XML_MEMBER:
                continue
            data = archive.read(info)
            if b"<!DOCTYPE" in data[:2048].upper():
                continue
            try:
                book = ElementTree.fromstring(data).find("book")
            except ElementTree.ParseError:
                logger.warning("Skipping unreadable %s", info.filename)
                continue
            if book is None or not (book.findtext("id") or "").strip().isdigit():
                continue
            pages = (book.findtext("num_pages") or "").strip()
            details[int(book.findtext("id").strip())] = (
                int(pages) if pages.isdigit() and 0 < int(pages) < 20_000 else None,
                clean_description(book.findtext("description") or ""),
            )
    return details


# --- Ratings ------------------------------------------------------------------------------


def neighbours(ratings: pd.DataFrame, book_ids: Sequence[int]) -> pd.DataFrame:
    """Each book's BOOK_NEIGHBOURS most similar books by readers who liked both.

    Liked means rated LIKED or more. Scores are asymmetric cosine with popularity damping
    (`DAMPING`) and shrinkage by shared readers (`SHRINKAGE`); pairs with fewer than
    MIN_CO_READERS shared readers are dropped. Ties go to the lower book_id.
    """
    column = {book_id: i for i, book_id in enumerate(book_ids)}
    liked = ratings[(ratings["rating"] >= LIKED) & ratings["book_id"].isin(column)]
    users = pd.factorize(liked["user_id"])[0]
    books = liked["book_id"].map(column).to_numpy()
    matrix = sparse.csr_matrix(
        (np.ones(len(liked), dtype=np.float32), (users, books)),
        shape=(int(users.max()) + 1 if len(users) else 0, len(book_ids)),
    )
    matrix.data[:] = 1.0  # a reader liking a book twice still counts once
    counts = np.asarray(matrix.sum(axis=0)).ravel()
    by_book = matrix.tocsc()
    target_norm = np.power(np.maximum(counts, 1), DAMPING)
    rows: list[pd.DataFrame] = []
    for start in range(0, len(book_ids), CHUNK_BOOKS):
        stop = min(start + CHUNK_BOOKS, len(book_ids))
        shared = (by_book[:, start:stop].T @ matrix).toarray()
        for offset in range(stop - start):
            shared[offset, start + offset] = 0
        source_norm = np.power(np.maximum(counts[start:stop], 1), 1 - DAMPING)[:, None]
        scores = shared / (source_norm * target_norm[None, :]) * shared / (shared + SHRINKAGE)
        scores[shared < MIN_CO_READERS] = 0
        for offset in range(stop - start):
            row = scores[offset]
            candidates = np.flatnonzero(row > 0)
            if not len(candidates):
                continue
            order = np.lexsort((candidates, -np.round(row[candidates], 9)))[:BOOK_NEIGHBOURS]
            chosen = candidates[order]
            rows.append(
                pd.DataFrame(
                    {
                        "book_id": book_ids[start + offset],
                        "neighbour_id": np.asarray(book_ids)[chosen],
                        "score": row[chosen].astype(np.float32),
                        "co_readers": shared[offset, chosen].astype(np.int32),
                    }
                )
            )
    if not rows:
        return pd.DataFrame(columns=list(NEIGHBOUR_COLUMNS))
    frame = pd.concat(rows, ignore_index=True)
    frame["book_id"] = frame["book_id"].astype("int64")
    frame["neighbour_id"] = frame["neighbour_id"].astype("int64")
    return frame[list(NEIGHBOUR_COLUMNS)]


# --- Output -------------------------------------------------------------------------------


def to_frame(records: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                **record,
                **{key: json.dumps(record[key], ensure_ascii=False) for key in _LISTS},
            }
            for record in records
        ],
        columns=list(OUTPUT_COLUMNS),
    )
    for column in ("year", "pages"):
        frame[column] = frame[column].astype("Int64")
    for column in ("series", "series_position", "language", "image_url", "isbn"):
        frame[column] = frame[column].astype(object).where(frame[column].notna(), None)
    return frame.sort_values("book_id", ignore_index=True)


_LISTS = ("authors", "genres", "tags")


def write_parquet(frame: pd.DataFrame, path: Path) -> None:
    """Writes atomically so a failed run never leaves a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, suffix=".parquet.tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class PrepareReport:
    books: int = 0
    with_cover: int = 0
    with_isbn: int = 0
    with_pages: int = 0
    with_genres: int = 0
    ratings: int = 0
    neighbour_rows: int = 0
    seconds: float = 0.0

    def summary(self, data_dir: Path) -> str:
        return (
            f"Wrote {self.books} books ({self.with_cover} with covers, {self.with_isbn} with "
            f"ISBNs, {self.with_pages} with "
            f"page counts, {self.with_genres} with genres) and {self.neighbour_rows} "
            f"readers-also-liked links from {self.ratings:,} ratings to {data_dir} "
            f"in {self.seconds:.0f} s."
        )


def prepare(
    source: str, data_dir: Path, *, details: bool = True, verify: bool = True
) -> PrepareReport:
    started = time.monotonic()
    report = PrepareReport()
    with TemporaryDirectory() as folder:
        files = [BOOKS_CSV, RATINGS_CSV, BOOK_TAGS_CSV, TAGS_CSV] + (
            [DETAILS_ZIP] if details else []
        )
        local: dict[str, Path] = {}
        for file in files:
            logger.info("Reading %s", file.path)
            local[file.path] = fetch(
                source, file, Path(folder) / Path(file.path).name, verify=verify
            )
        # ISBNs as text: pandas would read them as numbers and lose leading zeros.
        books = pd.read_csv(local[BOOKS_CSV.path], dtype={"isbn": str, "isbn13": str})
        _require(books, BOOKS_CSV, ("book_id", "goodreads_book_id", "title", "authors"))
        records = book_records(books)
        shelves = shelves_by_book(
            pd.read_csv(local[BOOK_TAGS_CSV.path]), pd.read_csv(local[TAGS_CSV.path])
        )
        found = read_details(local[DETAILS_ZIP.path]) if details else {}
        ratings = pd.read_csv(
            local[RATINGS_CSV.path],
            dtype={"user_id": "int32", "book_id": "int32", "rating": "int8"},
        )
        _require(ratings, RATINGS_CSV, ("user_id", "book_id", "rating"))
    ratings = ratings[ratings["rating"].between(1, 5)].drop_duplicates(
        ["user_id", "book_id"], keep="last"
    )
    readers = ratings["book_id"].value_counts()
    for record in records:
        genres, tags = describe(shelves.get(record["goodreads_book_id"], {}))
        pages, description = found.get(record["goodreads_book_id"], (None, ""))
        record |= {
            "genres": genres,
            "tags": tags,
            "pages": pages,
            "description": description,
            "readers": int(readers.get(record["book_id"], 0)),
        }
    if not records:
        raise ValueError(f"No valid books in {source}")
    links = neighbours(ratings, [record["book_id"] for record in records])
    write_parquet(to_frame(records), data_dir / BOOKS_OUTPUT)
    write_parquet(links, data_dir / NEIGHBOURS_OUTPUT)
    report.books = len(records)
    report.with_cover = sum(r["image_url"] is not None for r in records)
    report.with_isbn = sum(r["isbn"] is not None for r in records)
    report.with_pages = sum(r["pages"] is not None for r in records)
    report.with_genres = sum(bool(r["genres"]) for r in records)
    report.ratings = len(ratings)
    report.neighbour_rows = len(links)
    report.seconds = time.monotonic() - started
    print(report.summary(data_dir))
    return report


def _require(frame: pd.DataFrame, file: SourceFile, columns: Iterable[str]) -> None:
    if missing := [column for column in columns if column not in frame.columns]:
        raise ValueError(f"{file.path} is not the goodbooks-10k file: missing {missing}")


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        prepare(
            args.source,
            args.data_dir or get_settings().data_dir,
            details=not args.no_details,
            verify=not args.no_verify,
        )
    except (OSError, ValueError, zipfile.BadZipFile, httpx.HTTPError) as error:
        logger.error("Could not prepare books: %s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
