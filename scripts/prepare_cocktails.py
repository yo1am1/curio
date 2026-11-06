"""Build data/cocktails.parquet from the cocktail_advisor recipe CSV (TheCocktailDB data).

Usage:
    uv run python -m scripts.prepare_cocktails [--source PATH_OR_URL] [--sha256 HEX]
        [--data-dir data]

The source is `final_cocktails.csv` from https://github.com/yo1am1/cocktail_advisor (425
recipes), pinned to one commit and checked against `DEFAULT_SHA256`, so drink ids stay stable.
Another `--source` is not checked unless `--sha256` is given. Its list columns are Python
literals and are parsed with `ast.literal_eval`, never `eval`. Malformed rows are skipped with a
warning, image links other than https on www.thecocktaildb.com are dropped, recipes repeated
with the same name and ingredients are kept once, and each ingredient name gets one spelling
("Lime Juice"/"Lime juice"). The output layout is documented in docs/multidomain-contract.md.
"""

import argparse
import ast
import csv
import hashlib
import io
import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx
import pandas as pd

from curio.app.config import get_settings
from curio.domains.cocktails.catalog import (
    ALCOHOL_VALUES,
    COLUMNS,
    ingredient_key,
    trusted_image_url,
)

SOURCE_COMMIT = "92e05ae48b3f2af9f62ca5fdcd23b7b7c2f3fba6"
DEFAULT_SOURCE = (
    f"https://raw.githubusercontent.com/yo1am1/cocktail_advisor/{SOURCE_COMMIT}/"
    "data/cocktails/final_cocktails.csv"
)
# SHA-256 of final_cocktails.csv at SOURCE_COMMIT.
DEFAULT_SHA256 = "fac8041d2ac5450632113dfc516a5e52e90646f85b07f8edafbf2f8798aa3394"
OUTPUT_NAME = "cocktails.parquet"
REQUIRED_FIELDS = (
    "id",
    "name",
    "alcoholic",
    "category",
    "glassType",
    "instructions",
    "drinkThumbnail",
    "ingredients",
    "ingredientMeasures",
)
# The real file is ~400 KB; anything far larger is not the recipe CSV.
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_LITERAL_CHARS = 10_000

logger = logging.getLogger(__name__)


class MalformedRowError(ValueError):
    pass


@dataclass
class PrepareReport:
    read: int = 0
    written: int = 0
    duplicates: int = 0
    images_dropped: int = 0
    skipped: Counter[str] = field(default_factory=Counter)

    def summary(self, path: Path) -> str:
        skipped = sum(self.skipped.values())
        reasons = ", ".join(f"{reason}: {n}" for reason, n in sorted(self.skipped.items()))
        return (
            f"Wrote {self.written} recipes to {path} ({self.read} rows read, "
            f"{skipped} malformed skipped{f' [{reasons}]' if reasons else ''}, "
            f"{self.duplicates} duplicates removed, {self.images_dropped} images dropped)."
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help="final_cocktails.csv as a local path or an https URL (default: the GitHub raw "
        "file at a pinned commit).",
    )
    parser.add_argument(
        "--sha256",
        help="Expected SHA-256 of the source file (default: checked only for the pinned source).",
    )
    parser.add_argument(
        "--data-dir", type=Path, help="Output directory; defaults to configured data_dir."
    )
    return parser.parse_args(argv)


def read_source(source: str, timeout: float = 30.0, sha256: str | None = None) -> str:
    """CSV text from a local path or an https URL (env proxies are respected), checked against
    `sha256` when given."""
    data = _read_bytes(source, timeout)
    if sha256 is not None:
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256.strip().lower():
            raise ValueError(f"SHA-256 mismatch for {source}: expected {sha256}, got {actual}")
    return data.decode("utf-8-sig")


def _read_bytes(source: str, timeout: float) -> bytes:
    if source.startswith("https://"):
        with httpx.stream(
            "GET", source, timeout=httpx.Timeout(timeout, connect=10), follow_redirects=True
        ) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_SOURCE_BYTES:
                    raise ValueError(f"{source} is larger than {MAX_SOURCE_BYTES} bytes")
        return bytes(body)
    if "://" in source:
        raise ValueError(f"Only local paths and https URLs are supported, got {source!r}")
    path = Path(source)
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError(f"{path} is larger than {MAX_SOURCE_BYTES} bytes")
    return path.read_bytes()


def _literal_list(value: str, column: str) -> list:
    if len(value) > MAX_LITERAL_CHARS:
        raise MalformedRowError(f"{column} too long")
    try:
        parsed = ast.literal_eval(value.strip())
    except (ValueError, SyntaxError, MemoryError, RecursionError) as error:
        raise MalformedRowError(f"{column} is not a Python literal") from error
    if not isinstance(parsed, list | tuple):
        raise MalformedRowError(f"{column} is not a list")
    return list(parsed)


def _measure(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        raise MalformedRowError("measure is not text")
    return " ".join(str(value).split()) or None


def parse_ingredients(names_literal: str, measures_literal: str) -> list[dict[str, str | None]]:
    """Pairs ingredient names with measures; missing trailing measures become None."""
    names = _literal_list(names_literal, "ingredients")
    measures = _literal_list(measures_literal, "ingredientMeasures") if measures_literal else []
    if len(measures) > len(names):
        raise MalformedRowError("more measures than ingredients")
    measures += [None] * (len(names) - len(measures))
    ingredients = []
    for name, measure in zip(names, measures, strict=True):
        if not isinstance(name, str):
            raise MalformedRowError("ingredient name is not text")
        name = " ".join(name.split())
        if name:
            ingredients.append({"name": name, "measure": _measure(measure)})
    if not ingredients:
        raise MalformedRowError("no ingredients")
    return ingredients


def parse_row(row: dict[str, str | None]) -> dict:
    """One CSV row as a cocktails.parquet record; raises MalformedRowError."""
    if any(row.get(column) is None for column in REQUIRED_FIELDS):
        raise MalformedRowError("missing columns")
    try:
        source_id = int(row["id"].strip())
    except ValueError as error:
        raise MalformedRowError("id is not an integer") from error
    if source_id < 0:
        raise MalformedRowError("negative id")
    name = " ".join(row["name"].split())
    if not name:
        raise MalformedRowError("blank name")
    alcoholic = row["alcoholic"].strip()
    if alcoholic not in ALCOHOL_VALUES:
        raise MalformedRowError("unknown alcoholic value")
    return {
        "drink_id": source_id + 1,
        "name": name,
        "alcoholic": alcoholic,
        "category": " ".join(row["category"].split()),
        "glass": " ".join(row["glassType"].split()),
        "instructions": row["instructions"].strip(),
        "image_url": row["drinkThumbnail"].strip() or None,
        "ingredients": parse_ingredients(row["ingredients"], row["ingredientMeasures"]),
    }


def _most_used_spellings(values: Iterable[str]) -> dict[str, str]:
    """Key -> the most used spelling; ties go to the alphabetically first one."""
    counts = Counter(values)
    best: dict[str, str] = {}
    for spelling in sorted(counts, key=lambda s: (-counts[s], s)):
        best.setdefault(ingredient_key(spelling), spelling)
    return best


def _unify_spellings(records: list[dict]) -> None:
    ingredient_names = _most_used_spellings(
        i["name"] for record in records for i in record["ingredients"]
    )
    glasses = _most_used_spellings(record["glass"] for record in records if record["glass"])
    for record in records:
        for ingredient in record["ingredients"]:
            ingredient["name"] = ingredient_names[ingredient_key(ingredient["name"])]
        if record["glass"]:
            record["glass"] = glasses[ingredient_key(record["glass"])]


def _dedupe_key(record: dict) -> tuple[str, tuple[str, ...]]:
    names = sorted(ingredient_key(i["name"]) for i in record["ingredients"])
    return ingredient_key(record["name"]), tuple(names)


def build_records(rows: Iterable[dict[str, str | None]], report: PrepareReport) -> list[dict]:
    records: list[dict] = []
    seen_ids: set[int] = set()
    seen_recipes: set[tuple[str, tuple[str, ...]]] = set()
    for line, row in enumerate(rows, start=2):
        report.read += 1
        try:
            record = parse_row(row)
        except MalformedRowError as error:
            report.skipped[str(error)] += 1
            logger.warning("Skipping malformed row on line %d: %s", line, error)
            continue
        if record["drink_id"] in seen_ids:
            report.skipped["duplicate id"] += 1
            logger.warning("Skipping row on line %d: duplicate id", line)
            continue
        key = _dedupe_key(record)
        if key in seen_recipes:
            report.duplicates += 1
            continue
        if record["image_url"] is not None and trusted_image_url(record["image_url"]) is None:
            report.images_dropped += 1
            record["image_url"] = None
        seen_ids.add(record["drink_id"])
        seen_recipes.add(key)
        records.append(record)
    _unify_spellings(records)
    return records


def to_frame(records: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {**record, "ingredients": json.dumps(record["ingredients"], ensure_ascii=False)}
            for record in records
        ],
        columns=list(COLUMNS),
    )
    frame["drink_id"] = frame["drink_id"].astype("int64")
    frame["image_url"] = frame["image_url"].astype(object).where(frame["image_url"].notna(), None)
    return frame.sort_values("drink_id", ignore_index=True)


def write_parquet(frame: pd.DataFrame, path: Path) -> None:
    """Writes atomically so a failed run never leaves a half-written catalog behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, suffix=".parquet.tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(source: str, data_dir: Path, sha256: str | None = None) -> PrepareReport:
    """`sha256` defaults to `DEFAULT_SHA256` for the pinned source and to no check otherwise."""
    if sha256 is None and source == DEFAULT_SOURCE:
        sha256 = DEFAULT_SHA256
    text = read_source(source, sha256=sha256)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    missing = [column for column in REQUIRED_FIELDS if column not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"{source} is not the cocktail CSV: missing {', '.join(missing)}")
    report = PrepareReport()
    records = build_records(reader, report)
    if not records:
        raise ValueError(f"No valid recipes in {source}")
    output = data_dir / OUTPUT_NAME
    write_parquet(to_frame(records), output)
    report.written = len(records)
    print(report.summary(output))
    return report


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        prepare(args.source, args.data_dir or get_settings().data_dir, args.sha256)
    except (OSError, ValueError, httpx.HTTPError) as error:
        logger.error("Could not prepare cocktails: %s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
