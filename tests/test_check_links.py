"""scripts.check_links: relative links and images in the README and docs must resolve."""

from scripts.check_links import ROOT, anchors, broken_links, markdown_files


def test_every_relative_link_in_the_readme_and_docs_resolves():
    problems = [p for path in markdown_files() for p in broken_links(path)]
    assert problems == []


def test_broken_files_and_headings_are_reported_and_code_is_not_read(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "other.md").write_text("# Other page\n\n## Same\n\n## Same\n")
    (tmp_path / "pic.png").write_bytes(b"")
    page = tmp_path / "README.md"
    page.write_text(
        "# Title\n\n[ok](docs/other.md#other-page) [second](docs/other.md#same-1) "
        "![img](pic.png) [here](#title) [web](https://example.com/x)\n"
        "[gone](docs/missing.md) [bad anchor](docs/other.md#nope) ![lost](lost.png)\n"
        "<img src='also-lost.png'>\n\n`[not a link](nowhere.md)`\n\n"
        "```\n[nor this](nowhere.md)\n```\n"
    )
    assert broken_links(page, tmp_path) == [
        "README.md: docs/missing.md (no such file)",
        "README.md: docs/other.md#nope (no such heading)",
        "README.md: lost.png (no such file)",
        "README.md: also-lost.png (no such file)",
    ]


def test_anchors_follow_githubs_rules():
    assert anchors(
        "# Add a domain\n## 1. The data contract\n## Why `x`? (two)\n## Add a domain"
    ) == {
        "add-a-domain",
        "1-the-data-contract",
        "why-x-two",
        "add-a-domain-1",
    }
    assert ROOT.joinpath("README.md").exists()
