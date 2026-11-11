""" "What leaves your machine", said once (M16-07, docs/LAB_PLAN.md §18.1 item 2).

The drift test: README.md and docs/INSTALL.md each carry the block exactly
as `disclosure.render_markdown()` renders it, between its markers, and
`doctor`'s first-run intro prints `disclosure.lines()`. A copy edited by
hand fails here; `scripts/gen_network_block.py --write` rewrites the
documents. Reads the two documents and the package only; nothing is
written but into `tmp_path`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from wowlab_core import disclosure, firstrun

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
