# Probe from review of m12/13-command-reference; reproduces a list or tuple default naming an absolute path printed verbatim instead of "depends on the machine"
"""`scripts/gen_command_reference.py` promises (module docstring, and the
M12-13 report) that a default "naming an absolute path is rendered
generically rather than as the value on this machine", because the page is
pushed to the public wiki without human review (M12-14). `_render_default`
applies `_machine_specific` only to a scalar default: a `multiple=True`
option whose default is a list or tuple of paths skips the check and each
element is printed as a code span, home folder included.

Positive control: the same path as a scalar default is rendered as
"depends on the machine" today, so the gap is the sequence branch, not the
path test.

Constructed: parameter objects shaped like Click's, as the ticket's own
boundary tests use (`_param` in tests/scripts/test_gen_command_reference.py).
No command in the current CLI has such a default; the defect is latent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "gen_command_reference.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_review_m12_13_seq_default", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = _load()


def _param(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "required": False,
        "envvar": None,
        "show_default": None,
        "is_flag": False,
        "count": False,
        "default": None,
        "multiple": True,
        "nargs": 1,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_positive_control_constructed_scalar_home_path_is_generic() -> None:
    home_path = Path.home() / "Games" / "wow"
    assert gen._render_default(_param(default=home_path)) == "depends on the machine"


@pytest.mark.parametrize(
    "default",
    [
        [Path.home() / "Games" / "wow"],
        (str(Path.home() / "Games" / "wow"),),
        ["pages", "/opt/wow"],
    ],
    ids=["list-of-home-path", "tuple-of-home-str", "list-with-absolute"],
)
def test_constructed_sequence_default_naming_a_machine_path_is_generic(default: Any) -> None:
    rendered = gen._render_default(_param(default=default))
    assert str(Path.home()) not in rendered, rendered
    assert "/opt/wow" not in rendered, rendered
