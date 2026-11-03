import csv
import json
from pathlib import Path

import httpx
import pandas as pd
import pytest

from clothes_recommender.cocktails.catalog import COLUMNS, CocktailCatalog
from scripts import prepare_cocktails
from scripts.prepare_cocktails import (
    DEFAULT_SOURCE,
    MalformedRowError,
    main,
    parse_args,
    parse_ingredients,
    prepare,
    read_source,
)

HEADER = [
    "",
    "id",
    "name",
    "alcoholic",
    "category",
    "glassType",
    "instructions",
    "drinkThumbnail",
    "ingredients",
    "ingredientMeasures",
    "recipes",
]
IMAGE = "https://www.thecocktaildb.com/images/media/drink/{}.jpg"


def _row(
    source_id: object,
    name: str,
    ingredients: str,
    measures: str,
    *,
    alcoholic: str = "Alcoholic",
    image: str | None = None,
    glass: str = "Cocktail glass",
) -> list[str]:
    image = IMAGE.format(source_id) if image is None else image
    return [
        str(source_id),
        str(source_id),
        name,
        alcoholic,
        "Cocktail",
        glass,
        f"Stir the {name}.",
        image,
        ingredients,
        measures,
        "question ... answer ...",
    ]


ROWS = [
    _row(0, "  Café Brûlot ", "['Brandy', 'Lime Juice', 'Sugar']", "['2 oz ', '  ', None]"),
    _row(1, "Broken", "['Gin'", "['1 oz']"),
    _row(2, "Plain Http", "['Gin', 'lime juice']", "['1 oz', '1 oz']", image="http://x.com/a.jpg"),
    _row(3, "Maybe", "['Gin']", "['1 oz']", alcoholic="Sometimes"),
    _row(4, "café brûlot", "['sugar', 'brandy', 'lime juice']", "['1', '2', '3']"),
    _row(5, "Short Measures", "['Lime juice', 'Soda water']", "['1 oz']", glass="Collins Glass"),
    _row(6, "Evil", "__import__('os').system('echo pwned')", "[]"),
    _row(7, "Too Many Measures", "['Gin']", "['1 oz', '2 oz']"),
    _row(8, "Empty", "[]", "[]"),
    _row(
        9,
        "Virgin Fizz",
        "['Lime juice', '', 'Soda water']",
        "['1 oz', '', '3 oz']",
        alcoholic="Non alcoholic",
        glass="Collins glass",
    ),
    _row("x", "Bad Id", "['Gin']", "['1 oz']"),
    _row(10, "   ", "['Gin']", "['1 oz']"),
    _row(9, "Duplicate Id", "['Gin']", "['1 oz']"),
    ["11", "11", "Truncated"],
]


def _write_csv(path: Path, rows: list[list[str]], header: list[str] = HEADER) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return _write_csv(tmp_path / "final_cocktails.csv", ROWS)


def test_prepare_writes_valid_recipes_only(
    source: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    report = prepare(str(source), data_dir)

    frame = pd.read_parquet(data_dir / "cocktails.parquet")
    assert list(frame.columns) == list(COLUMNS)
    assert frame["drink_id"].tolist() == [1, 3, 6, 10]
    assert frame["name"].tolist() == ["Café Brûlot", "Plain Http", "Short Measures", "Virgin Fizz"]
    assert report.read == len(ROWS)
    assert report.written == 4
    assert report.duplicates == 1
    assert report.images_dropped == 1
    assert sum(report.skipped.values()) == 9
    assert "Wrote 4 recipes" in capsys.readouterr().out

    catalog = CocktailCatalog.from_parquet(data_dir / "cocktails.parquet")
    cafe = catalog.get(1)
    assert [(i.name, i.measure) for i in cafe.ingredients] == [
        ("Brandy", "2 oz"),
        ("Lime juice", None),
        ("Sugar", None),
    ]
    assert cafe.image_url == IMAGE.format(0)
    assert catalog.get(3).image_url is None
    assert [(i.name, i.measure) for i in catalog.get(6).ingredients] == [
        ("Lime juice", "1 oz"),
        ("Soda water", None),
    ]
    assert [i.name for i in catalog.get(10).ingredients] == ["Lime juice", "Soda water"]
    assert catalog.get(10).alcohol_free
    assert {catalog.get(6).glass, catalog.get(10).glass} == {"Collins Glass"}


def test_ingredient_json_keeps_unicode(source: Path, tmp_path: Path) -> None:
    prepare(str(source), tmp_path)
    raw = pd.read_parquet(tmp_path / "cocktails.parquet")["ingredients"].iloc[0]
    assert json.loads(raw)[0] == {"name": "Brandy", "measure": "2 oz"}


def test_unify_prefers_the_most_used_spelling(tmp_path: Path) -> None:
    rows = [
        _row(0, "A", "['Lime Juice']", "['1']"),
        _row(1, "B", "['lime juice']", "['1']"),
        _row(2, "C", "['lime juice']", "['1']"),
    ]
    prepare(str(_write_csv(tmp_path / "in.csv", rows)), tmp_path)
    assert CocktailCatalog.from_parquet(tmp_path / "cocktails.parquet").ingredients() == [
        "lime juice"
    ]


@pytest.mark.parametrize(
    ("names", "measures", "error"),
    [
        ("['Gin'", "[]", "not a Python literal"),
        ("'Gin'", "[]", "not a list"),
        ("open('x')", "[]", "not a Python literal"),
        ("[1, 2]", "[]", "ingredient name is not text"),
        ("['Gin']", "[['1 oz']]", "measure is not text"),
        ("['Gin']", "['1', '2']", "more measures"),
        ("['', ' ']", "[]", "no ingredients"),
        ("[" + "'Gin', " * 3000 + "]", "[]", "too long"),
    ],
)
def test_parse_ingredients_rejects_malformed_lists(names: str, measures: str, error: str) -> None:
    with pytest.raises(MalformedRowError, match=error):
        parse_ingredients(names, measures)


def test_parse_ingredients_pads_and_normalises_measures() -> None:
    assert parse_ingredients("['Gin', 'Tonic  water']", "['1  1/2 oz ', 2]") == [
        {"name": "Gin", "measure": "1 1/2 oz"},
        {"name": "Tonic water", "measure": "2"},
    ]
    assert parse_ingredients("['Gin']", "") == [{"name": "Gin", "measure": None}]


def test_prepare_rejects_other_files(tmp_path: Path) -> None:
    other = _write_csv(tmp_path / "other.csv", [["1", "x"]], header=["id", "name"])
    with pytest.raises(ValueError, match="missing alcoholic"):
        prepare(str(other), tmp_path)
    broken = _write_csv(tmp_path / "broken.csv", [ROWS[1], ROWS[3]])
    with pytest.raises(ValueError, match="No valid recipes"):
        prepare(str(broken), tmp_path)
    assert not (tmp_path / "cocktails.parquet").exists()


@pytest.mark.parametrize("source", ["http://example.com/x.csv", "file:///etc/passwd"])
def test_read_source_rejects_non_https_urls(source: str) -> None:
    with pytest.raises(ValueError, match="Only local paths and https URLs"):
        read_source(source)


def _mock_stream(monkeypatch: pytest.MonkeyPatch, handler) -> list[str]:
    requested: list[str] = []

    def stream(method: str, url: str, **kwargs):
        requested.append(url)
        assert kwargs["timeout"] is not None
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.stream(method, url)

    monkeypatch.setattr(prepare_cocktails.httpx, "stream", stream)
    return requested


def test_read_source_downloads_https(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = source.read_bytes()
    requested = _mock_stream(monkeypatch, lambda request: httpx.Response(200, content=body))
    assert read_source(DEFAULT_SOURCE) == body.decode()
    assert requested == [DEFAULT_SOURCE]


def test_read_source_limits_size_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prepare_cocktails, "MAX_SOURCE_BYTES", 10)
    _mock_stream(monkeypatch, lambda request: httpx.Response(200, content=b"x" * 11))
    with pytest.raises(ValueError, match="larger than 10 bytes"):
        read_source(DEFAULT_SOURCE)
    _mock_stream(monkeypatch, lambda request: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        read_source(DEFAULT_SOURCE)


def test_cli_defaults_and_main(source: Path, tmp_path: Path) -> None:
    args = parse_args([])
    assert args.source == DEFAULT_SOURCE
    assert DEFAULT_SOURCE.startswith("https://raw.githubusercontent.com/yo1am1/cocktail_advisor/")
    main(["--source", str(source), "--data-dir", str(tmp_path / "out")])
    assert len(CocktailCatalog.from_parquet(tmp_path / "out" / "cocktails.parquet")) == 4


def test_main_exits_nonzero_on_errors(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--source", str(tmp_path / "missing.csv"), "--data-dir", str(tmp_path)])
    assert exit_info.value.code == 1
