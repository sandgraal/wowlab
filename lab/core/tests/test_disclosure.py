""" "What leaves your machine", said once (M16-07, docs/LAB_PLAN.md §18.1 item 2).

The drift test: README.md and docs/INSTALL.md each carry the block exactly
as `disclosure.render_markdown()` renders it, between its markers, and
`doctor`'s first-run intro prints `disclosure.lines()`. A copy edited by
hand fails here; `scripts/gen_network_block.py --write` rewrites the
documents. Reads the two documents and the package only; nothing is
written but into `tmp_path`.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import typer

from wowlab_core import cli, disclosure, firstrun

REPO = Path(__file__).resolve().parents[3]
DOCS = (REPO / "README.md", REPO / "docs" / "INSTALL.md")


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_each_document_carries_the_block_once_and_as_rendered(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    assert text.count(disclosure.BEGIN) == 1 and text.count(disclosure.END) == 1
    assert disclosure.render_markdown() in text, (
        f"{doc.name}: the block differs; run `uv run python scripts/gen_network_block.py --write`"
    )
    assert disclosure.render_doc(text) == text


def test_the_first_run_intro_prints_the_block() -> None:
    intro = firstrun.intro_lines(store_path=Path("store"))
    block = disclosure.lines(indent="  - ")
    starts = [i for i in range(len(intro)) if intro[i : i + len(block)] == block]
    assert len(starts) == 1, "the intro carries the block once, whole"
    text = "\n".join(intro)
    for row in disclosure.ROWS:
        assert text.count(row) == 1


def test_the_block_has_the_three_rows() -> None:
    doctor, tables, link = disclosure.ROWS
    assert doctor.startswith("`wowlab doctor --check-builds` downloads")
    assert "`wowlab db2` and `wowlab looks` download" in tables and "`--build`" in tables
    assert "dressing-room link" in link and "public game ids only" in link
    assert "Nothing is uploaded" in disclosure.HEAD


def test_the_old_network_sentences_are_gone() -> None:
    """No copy that says `doctor` asks wago.tools unless `--offline`."""
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        assert "doctor --offline" not in text, doc.name
        assert "`--offline` skips" not in text, doc.name
    intro = "\n".join(firstrun.intro_lines(store_path=Path("store")))
    assert "--offline" not in intro


def _downloaders(source: str) -> set[str]:
    """The top-level functions of a module's source that call
    `_open_gamedata`, directly or through another function of the module."""
    tree = ast.parse(source)
    calls: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls[node.name] = {
                n.func.id
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
    found = {name for name, called in calls.items() if "_open_gamedata" in called}
    while True:
        more = {name for name, called in calls.items() if called & found} - found
        if not more:
            return found
        found |= more


def _leaves(cmd: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    subs = getattr(cmd, "commands", None)
    if subs:
        for name, sub in subs.items():
            yield from _leaves(sub, (*path, name))
    else:
        yield path, cmd.callback


def test_every_command_that_downloads_is_named_in_the_block() -> None:
    """M15-11 (security review): every command whose function reaches the
    game-data client (`_open_gamedata`, the only network client, directly or
    through a helper of its own module) is named in one of the rows, by
    itself or by its group (`wowlab db2`, `wowlab looks`). Graded first on
    the commands known to download, so an empty walk fails."""
    text = "\n".join(disclosure.ROWS)
    package = Path(cli.__file__).parent
    by_module = {
        f"wowlab_core.cli.{p.stem}": _downloaders(p.read_text(encoding="utf-8"))
        for p in package.glob("_*.py")
    }
    named: list[str] = []
    for path, callback in _leaves(typer.main.get_command(cli.app)):
        inner = inspect.unwrap(callback)
        if inner.__name__ not in by_module.get(inner.__module__, set()):
            continue
        words = " ".join(path)
        named.append(words)
        assert any(f"`wowlab {' '.join(path[:k])}" in text for k in range(1, len(path) + 1)), (
            f"wowlab {words} downloads game data but the network block does not name it"
        )
    for known in ("char model", "names fetch", "db2 fetch", "doctor"):
        assert known in named, known


def _gen() -> object:
    spec = importlib.util.spec_from_file_location(
        "gen_network_block", REPO / "scripts" / "gen_network_block.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generator_reports_the_documents_up_to_date(capsys: pytest.CaptureFixture[str]) -> None:
    assert _gen().main(["--check"]) == 0  # type: ignore[attr-defined]
    assert "stale" not in capsys.readouterr().err


def test_a_hand_edited_copy_is_rewritten_constructed() -> None:
    stale = f"before\n{disclosure.BEGIN}\nan old sentence\n{disclosure.END}\nafter\n"
    assert disclosure.render_doc(stale) == f"before\n{disclosure.render_markdown()}after\n"


@pytest.mark.parametrize(
    "text",
    [
        f"{disclosure.BEGIN}\nno end\n",
        f"no begin\n{disclosure.END}\n",
        f"{disclosure.BEGIN}\n{disclosure.BEGIN}\n{disclosure.END}\n",
        f"{disclosure.END}\n{disclosure.BEGIN}\n{disclosure.END}\n",
    ],
    ids=[
        "constructed: begin without end",
        "constructed: end without begin",
        "constructed: begin twice",
        "constructed: end first",
    ],
)
def test_an_unpaired_marker_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        disclosure.render_doc(text)


@pytest.mark.parametrize("mode", ["--check", "--write"])
def test_the_generator_fails_on_a_document_without_its_markers_constructed(
    tmp_path: Path, mode: str
) -> None:
    doc = tmp_path / "README.md"
    doc.write_text("top\nno block here\n", encoding="utf-8")
    gen = _gen()
    gen.DOCS = (doc,)  # type: ignore[attr-defined]
    assert gen.main([mode]) == 1  # type: ignore[attr-defined]
    assert doc.read_text(encoding="utf-8") == "top\nno block here\n"
