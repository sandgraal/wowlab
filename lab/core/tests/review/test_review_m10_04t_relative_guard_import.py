# Probe from review of m10/04-luadata-parser-tests; reproduces the L3 import grader
# accepting `from .guard import …` although its docstring says luadata never imports guard.
"""One defect found by running the M10-04T import check at fae43f0 on sample sources.

`test_module_imports_no_interpreter_and_evaluates_nothing` maps a relative
`ImportFrom` to the base `wowlab_core` and drops `node.module`, so
`from .guard import write_file` yields `["wowlab_core", "wowlab_core.write_file"]`
and never matches `"wowlab_core.guard"`. The absolute form and `from . import
guard` are refused (positive controls, passing today).

The grader only reads `luadata.__file__`, so a stand-in object pointing at a
one-line source in `tmp_path` exercises it without a `luadata`. Constructed
input; no install (ADR-0012).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PARSER = Path(__file__).resolve().parents[1] / "parser"
if str(PARSER) not in sys.path:
    sys.path.insert(0, str(PARSER))

import test_luadata_constructed as graders  # noqa: E402


def _check(source: str, tmp_path: Path) -> None:
    module = tmp_path / "luadata.py"
    module.write_text(source, encoding="utf-8")
    graders.test_module_imports_no_interpreter_and_evaluates_nothing(
        SimpleNamespace(__file__=str(module))
    )


def test_positive_control_plain_source_passes(tmp_path: Path) -> None:
    _check("import re\nfrom dataclasses import dataclass\n", tmp_path)


@pytest.mark.parametrize(
    "source",
    ["from wowlab_core.guard import write_file\n", "from . import guard\n"],
    ids=["absolute", "relative-package"],
)
def test_positive_control_guard_import_is_refused(source: str, tmp_path: Path) -> None:
    with pytest.raises(AssertionError):
        _check(source, tmp_path)


def test_relative_submodule_guard_import_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AssertionError):
        _check("from .guard import write_file\n", tmp_path)
