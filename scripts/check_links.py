"""Check every relative link and image in README.md and docs/**/*.md.

Usage:
    uv run python -m scripts.check_links [FILE ...]

A link is broken when its file or folder does not exist, or when it points to a heading
(`file.md#anchor` or `#anchor`) that the Markdown file does not have. Anchors follow
GitHub's rules: lower case, punctuation dropped, spaces as hyphens, "-1" for a repeat.
Absolute URLs (http, https, mailto) are not fetched. Exits 1 and lists each broken link.
"""

import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
# [text](target "title") and ![alt](target); the target has no spaces or parentheses.
_INLINE = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_REFERENCE = re.compile(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+\"[^\"]*\")?\s*$", re.M)
_HTML = re.compile(r"""<(?:img|a)\b[^>]*?\b(?:src|href)=["']([^"']+)["']""", re.I)
_FENCE = re.compile(r"^(```|~~~).*?^\1", re.M | re.S)
_CODE = re.compile(r"`[^`\n]*`")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)


def markdown_files(root: Path = ROOT) -> list[Path]:
    return [root / "README.md", *sorted((root / "docs").rglob("*.md"))]


def _prose(text: str) -> str:
    """The text without fenced code blocks and inline code, where links are not links."""
    return _CODE.sub("", _FENCE.sub("", text))


def anchors(text: str) -> set[str]:
    """The heading anchors GitHub generates for a Markdown file."""
    seen: dict[str, int] = {}
    found = set()
    for heading in _HEADING.findall(_FENCE.sub("", text)):
        plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # link text only
        plain = re.sub(r"[`*_]|<[^>]+>", "", plain)
        slug = re.sub(r"[^\w\- ]", "", plain.strip().lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        found.add(slug if count == 0 else f"{slug}-{count}")
    return found


def links(text: str) -> Iterable[str]:
    prose = _prose(text)
    yield from _INLINE.findall(prose)
    yield from _REFERENCE.findall(prose)
    yield from _HTML.findall(prose)


def broken_links(path: Path, root: Path = ROOT) -> list[str]:
    """Each broken relative link in `path`, as "file: target (why)"."""
    text = path.read_text(encoding="utf-8")
    problems = []
    for target in links(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
            continue  # http:, https:, mailto: ...
        file_part, _, anchor = target.partition("#")
        resolved = (path.parent / unquote(file_part)).resolve() if file_part else path
        name = path.relative_to(root)
        if not resolved.exists():
            problems.append(f"{name}: {target} (no such file)")
            continue
        known = anchors(resolved.read_text(encoding="utf-8")) if resolved.suffix == ".md" else ()
        if anchor and resolved.suffix == ".md" and unquote(anchor).lower() not in known:
            problems.append(f"{name}: {target} (no such heading)")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    files = [Path(a).resolve() for a in args] or markdown_files()
    problems = [problem for path in files for problem in broken_links(path)]
    checked = sum(1 for path in files for _ in links(path.read_text(encoding="utf-8")))
    for problem in problems:
        print(problem)
    print(f"{len(files)} files, {checked} links and images, {len(problems)} broken")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
