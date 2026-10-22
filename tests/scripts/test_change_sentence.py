"""One sentence about what can change your files, everywhere it is said (M16-02).

`docs/LAB_PLAN.md` §18.1 item 2: the page, `wowlab ui --help`, INSTALL.md and
the wiki's generated command reference say the same sentence, and none of the
sentences it replaced (which said the page changes nothing, or that commands
that change game files are not offered) is left in any of them.

Reads the package's templates, `cli.py`'s help and INSTALL.md; renders the
command reference in memory. Writes nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# The module test_check_wiki already loaded: loading it again would replace it
# in sys.modules and break identity checks elsewhere (as test_readme does).
from test_check_wiki import gen
from typer.testing import CliRunner

from wowlab_core import cli, ui

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = Path(ui.__file__).parent / "templates"
INSTALL = ROOT / "docs" / "INSTALL.md"

SENTENCE = (
    "Reading never changes anything. Six commands can change game files; each shows "
    "a plan and runs only when you confirm, after a snapshot."
)

# The six, as the UI's confirm step offers them (the leaves with a confirm
# shape that write through the gate; snap gc and doctor change only the store).
SIX = {
    ("snap", "restore"),
    ("undo",),
    ("profile", "apply"),
    ("sv", "merge"),
    ("addon", "install"),
    ("addon", "remove"),
}

# What the places said before, each contradicting the sentence.
OLD = (
    "nothing on this page changes your game files",
    "the Commands tab can save to wowlab's own data",
    "none of them touches the game folder",
    "this page never runs them",
    "are not offered in this version",
    "Nothing that changes a game file is, in this version",
    "Nothing that changes a game file is",
)


def _flat(text: str) -> str:
    """Whitespace collapsed to single spaces, so wrapping does not matter."""
    return re.sub(r"\s+", " ", text).strip()


def _page() -> str:
    return (TEMPLATES / "page.html").read_text(encoding="utf-8")


def _banner() -> str:
    match = re.search(r'<div class="banner-text">(.*?)</div>', _page(), re.S)
    assert match is not None
    return match.group(1)


def _commands_intro() -> str:
    match = re.search(
        r'<section id="commands-view"[^>]*>\s*<p class="intro">(.*?)</p>', _page(), re.S
    )
    assert match is not None
    return match.group(1)


def _hint_note() -> str:
    script = (TEMPLATES / "page.js").read_text(encoding="utf-8")
    match = re.search(r"function drawHints\(hints\) \{(.*?)\n  \}\n", script, re.S)
    assert match is not None
    return match.group(1)


def _install_ui_section() -> str:
    text = INSTALL.read_text(encoding="utf-8")
    match = re.search(r"\n## The explorer in your browser\n(.*?)\n## ", text, re.S)
    assert match is not None
    return match.group(1)


def _docstring() -> str:
    doc = cli.ui_command.__doc__
    assert doc is not None
    return doc


def _help() -> str:
    result = CliRunner().invoke(cli.app, ["ui", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    # Rich draws a box: drop its frame characters before collapsing whitespace.
    return re.sub(r"[│╭╮╰╯─]", " ", result.output)


def _reference_ui() -> str:
    page = gen.render()
    match = re.search(r"\n### wowlab ui\n(.*?)(?=\n#{2,3} |\Z)", page, re.S)
    assert match is not None, "the command reference has a wowlab ui section"
    return match.group(1)


PLACES = {
    "page banner": _banner,
    "Commands intro": _commands_intro,
    "Explorer hint note": _hint_note,
    "INSTALL.md UI section": _install_ui_section,
    "ui docstring": _docstring,
    "ui --help": _help,
    "command reference, wowlab ui": _reference_ui,
}


@pytest.mark.parametrize("place", sorted(PLACES))
def test_the_sentence_is_said_once_in_each_place(place: str) -> None:
    text = _flat(PLACES[place]())
    assert text.count(SENTENCE) == 1, f"{place}: {text[:400]}"


@pytest.mark.parametrize("place", sorted(PLACES))
def test_no_old_contradicting_sentence_is_left(place: str) -> None:
    text = _flat(PLACES[place]())
    for old in OLD:
        assert old not in text, f"{place} still says {old!r}"


def test_six_is_the_number_the_ui_offers() -> None:
    import typer

    leaves = ui._read_tree(typer.main.get_command(cli.app))
    confirmable = {path for path, leaf in leaves.items() if leaf.confirm is not None}
    # The two that change only wowlab's own store.
    assert confirmable - SIX == {("snap", "gc"), ("doctor",)}
    assert confirmable >= SIX


def test_install_lists_character_and_looks() -> None:
    section = _install_ui_section()
    assert "- **Character.**" in section
    assert "- **Looks.**" in section
