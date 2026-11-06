"""Curio's layering, enforced on the import statements of every module under `src/curio`.

The rule:

1. `curio.engine` is domain-independent. No engine module imports `curio.domains` or
   `curio.app`, at module level or inside a function.
2. A domain (`curio.domains.<name>`) imports the engine and itself, never `curio.app` and
   never another domain. The one exception is `pairings`, which is built on two domains and
   may import `books` and `cocktails`.
3. Everything else belongs to `curio.app`, which may import anything.

A domain that needs another one's feature takes it through an extension point it defines
(the book agent's `DrinkPairing`, which `pairings` implements and the app plugs in).
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SRC / "curio"
DOMAINS = sorted(p.name for p in (PACKAGE / "domains").iterdir() if (p / "__init__.py").is_file())
# Domain -> the other domains it may import.
ALLOWED_DOMAIN_IMPORTS = {"pairings": {"books", "cocktails"}}


def module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imports(path: Path) -> list[tuple[int, str]]:
    """Every module `path` imports, as (line, absolute dotted name)."""
    package = module_name(path).split(".")
    if path.name != "__init__.py":
        package = package[:-1]
    found = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package[: len(package) - node.level + 1]
                base = ".".join([*anchor, base] if base else anchor)
            # `from curio import app` imports curio.app.
            found += [(node.lineno, f"{base}.{alias.name}") for alias in node.names]
            found.append((node.lineno, base))
    return found


def modules(package: Path) -> list[Path]:
    return sorted(package.rglob("*.py"))


def violations(package: Path, forbidden: tuple[str, ...]) -> list[str]:
    return [
        f"{path.relative_to(SRC)}:{line} imports {name}"
        for path in modules(package)
        for line, name in imports(path)
        if any(name == f or name.startswith(f + ".") for f in forbidden)
    ]


def test_the_layers_exist():
    assert {p.name for p in PACKAGE.iterdir() if p.is_dir() and p.name != "__pycache__"} >= {
        "engine",
        "domains",
        "app",
    }
    assert set(DOMAINS) == {"fashion", "cocktails", "books", "pairings"}
    assert modules(PACKAGE / "engine")


def test_the_engine_imports_no_domain_and_not_the_app():
    assert violations(PACKAGE / "engine", ("curio.domains", "curio.app")) == []


@pytest.mark.parametrize("domain", DOMAINS)
def test_a_domain_imports_the_engine_and_itself_only(domain):
    others = set(DOMAINS) - {domain} - ALLOWED_DOMAIN_IMPORTS.get(domain, set())
    forbidden = ("curio.app", *(f"curio.domains.{other}" for other in sorted(others)))
    assert violations(PACKAGE / "domains" / domain, forbidden) == []


def test_the_check_sees_relative_and_nested_imports(tmp_path, monkeypatch):
    package = tmp_path / "curio" / "engine"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "bad.py").write_text(
        "from ..domains import fashion\n"
        "def later():\n"
        "    import curio.app.api\n"
        "from curio import app\n"
    )
    monkeypatch.setitem(globals(), "SRC", tmp_path)
    found = violations(package, ("curio.domains", "curio.app"))
    lines = {int(v.split(" imports ")[0].rsplit(":", 1)[1]) for v in found}
    assert lines == {1, 3, 4}


def test_sniffio_does_not_take_curio_for_the_async_library():
    """This package shares its import name with the Curio async library, which sniffio
    (anyio, httpx) detects by a `curio` module in sys.modules plus `curio.meta`."""
    import sniffio

    import curio  # noqa: F401

    with pytest.raises(sniffio.AsyncLibraryNotFoundError):
        sniffio.current_async_library()
