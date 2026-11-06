"""Curio's layering, enforced on the import statements of every module under `src/curio`.

The rule:

1. `curio.engine` is domain-independent. No engine module imports `curio.domains` or
   `curio.app`, at module level or inside a function.
2. A domain (`curio.domains.<name>`) imports the engine and itself, never `curio.app` and
   never another domain. Two composed domains are the exceptions: `pairings`, which is built
   on two domains and may import `books` and `cocktails`, and `evening` ("Plan my evening"),
   which composes an outfit, a book and a paired drink and so may import `fashion`, `books`,
   `cocktails` and `pairings`. No domain may import `evening`.
3. Everything else belongs to `curio.app`, which may import anything.

A domain that needs another one's feature takes it through an extension point it defines
(the book agent's `DrinkPairing`, which `pairings` implements and the app plugs in).

Dynamic imports count too: `importlib.import_module("curio.…")` and `__import__("curio.…")`
with a string literal are read like import statements.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SRC / "curio"
DOMAINS = sorted(p.name for p in (PACKAGE / "domains").iterdir() if (p / "__init__.py").is_file())
# Domain -> the other domains it may import.
ALLOWED_DOMAIN_IMPORTS = {
    "pairings": {"books", "cocktails"},
    "evening": {"fashion", "books", "cocktails", "pairings"},
}


def module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


_DYNAMIC_IMPORTS = frozenset({"import_module", "__import__"})


def _dynamic_import(node: ast.AST) -> str | None:
    """The "curio.…" module a dynamic import call names with a string literal, if any."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    first = node.args[0]
    literal = first.value if isinstance(first, ast.Constant) else None
    if name in _DYNAMIC_IMPORTS and isinstance(literal, str) and literal.startswith("curio."):
        return literal
    return None


def imports(path: Path) -> list[tuple[int, str]]:
    """Every module `path` imports, as (line, absolute dotted name), including dynamic
    imports of a "curio.…" string literal."""
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
        elif (dynamic := _dynamic_import(node)) is not None:
            found.append((node.lineno, dynamic))
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
    assert set(DOMAINS) == {"fashion", "cocktails", "books", "pairings", "evening"}
    assert modules(PACKAGE / "engine")


def test_the_engine_imports_no_domain_and_not_the_app():
    assert violations(PACKAGE / "engine", ("curio.domains", "curio.app")) == []


@pytest.mark.parametrize("domain", DOMAINS)
def test_a_domain_imports_the_engine_and_itself_only(domain):
    others = set(DOMAINS) - {domain} - ALLOWED_DOMAIN_IMPORTS.get(domain, set())
    forbidden = ("curio.app", *(f"curio.domains.{other}" for other in sorted(others)))
    assert violations(PACKAGE / "domains" / domain, forbidden) == []


def test_only_the_composed_domains_build_on_other_domains():
    """The exceptions stay exactly these two, and nothing builds on the evening domain."""
    assert set(ALLOWED_DOMAIN_IMPORTS) == {"pairings", "evening"}
    for domain in set(DOMAINS) - {"evening"}:
        assert violations(PACKAGE / "domains" / domain, ("curio.domains.evening",)) == []


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


def test_the_check_sees_dynamic_imports_of_curio_modules(tmp_path, monkeypatch):
    package = tmp_path / "curio" / "domains" / "books"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "sneaky.py").write_text(
        "import importlib\n"
        "from importlib import import_module\n"
        "def later():\n"
        "    importlib.import_module('curio.domains.cocktails.catalog')\n"
        "    import_module('curio.app.api')\n"
        "    __import__('curio.domains.evening')\n"
        "    importlib.import_module('json')\n"
        "    importlib.import_module(name_from_config)\n"
    )
    monkeypatch.setitem(globals(), "SRC", tmp_path)
    found = violations(package, ("curio.app", "curio.domains.cocktails", "curio.domains.evening"))
    assert [v.split(":", 1)[1] for v in found] == [
        "4 imports curio.domains.cocktails.catalog",
        "5 imports curio.app.api",
        "6 imports curio.domains.evening",
    ]


def test_sniffio_does_not_take_curio_for_the_async_library():
    """This package shares its import name with the Curio async library, which sniffio
    (anyio, httpx) detects by a `curio` module in sys.modules plus `curio.meta`."""
    import sniffio

    import curio  # noqa: F401

    with pytest.raises(sniffio.AsyncLibraryNotFoundError):
        sniffio.current_async_library()
