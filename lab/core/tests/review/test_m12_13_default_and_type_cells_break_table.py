# Probe from review of m12/13-command-reference; reproduces a `|` or a line break in a default or a choice splitting the option-table row
"""In a GFM table a `|` ends the cell even inside a code span, and a line
break ends the row. `_cell` escapes `|` for the Help column (the report says
"`|` inside code spans in table cells is escaped"), but the Default and Type
columns are built with `_code(...)` directly: `_render_default` for a string
default and `_render_type` for a choice. A default of `a|b`, a choice named
`a|b`, or a default holding a line feed therefore breaks the row, and the
page is pushed to the public wiki without review (M12-14).

The check renders the table through markdown-it-py (already installed as a
dependency of rich) with the table rule on and reads the option row's four
cells: a split row shows as a cell cut at the `|` or the line break, and
the Help text falls off the end (markdown-it drops cells past the header's).

Positive control: the same row with an ordinary default renders whole.

Constructed: parameter objects shaped like Click's. No current command has
such a default or choice; the defect is latent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from markdown_it import MarkdownIt

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "gen_command_reference.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_review_m12_13_cells", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = _load()


def _option(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "param_type_name": "option",
        "opts": ["--sep"],
        "secondary_opts": [],
        "hidden": False,
        "required": False,
        "envvar": None,
        "show_default": None,
        "is_flag": False,
        "count": False,
        "default": None,
        "multiple": False,
        "nargs": 1,
        "help": "Separator.",
        "type": SimpleNamespace(name="text"),
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _body_row(param: SimpleNamespace) -> list[str]:
    """The rendered text of each cell of the option's row (markdown-it drops
    cells past the header's four, so a split row loses its tail)."""
    lines = gen._param_tables(SimpleNamespace(params=[param]))
    tokens = MarkdownIt("commonmark").enable("table").parse("\n".join(lines))
    rows: list[list[str]] = []
    in_cell = False
    for tok in tokens:
        if tok.type == "tr_open":
            rows.append([])
        elif tok.type in ("td_open", "th_open"):
            in_cell = True
            rows[-1].append("")
        elif tok.type in ("td_close", "th_close"):
            in_cell = False
        elif in_cell and tok.type == "inline":
            rows[-1][-1] += "".join(c.content for c in tok.children or [])
    assert len(rows) >= 2, rows
    return rows[1]


def test_positive_control_constructed_ordinary_default_keeps_its_row() -> None:
    assert _body_row(_option(default=",")) == ["--sep", "text", ",", "Separator."]


@pytest.mark.parametrize(
    ("param", "column", "must_hold"),
    [
        (_option(default="a|b"), 2, ("a|b",)),
        # How a line break is shown is the fix's choice; both halves stay in the cell.
        (_option(default="x\ny"), 2, ("x", "y")),
        (_option(type=SimpleNamespace(name="choice", choices=["a|b", "c"])), 1, ("a|b", "c")),
    ],
    ids=["pipe-default", "newline-default", "pipe-choice"],
)
def test_constructed_default_or_choice_keeps_the_row_whole(
    param: SimpleNamespace, column: int, must_hold: tuple[str, ...]
) -> None:
    row = _body_row(param)
    assert len(row) == 4 and row[0] == "--sep" and row[3] == "Separator.", row
    assert all(part in row[column] for part in must_hold), row
