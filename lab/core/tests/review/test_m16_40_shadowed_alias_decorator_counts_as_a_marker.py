# Probe from review of m16/40-grader-diff-gate; reproduces removal_only accepting the removal of an `@NAME` decorator that, where it stands, is not a strict xfail
"""Constructed (not a real capture): grader texts given to `check_file`.

`scripts/check_grader_diff.py` documents an alias block as "a decorator
`@NAME`, where `NAME = <...>.mark.xfail(..., strict=True, ...)` is a
module-level assignment". `find_markers` adds `NAME` to its alias set when
*any* module-level assignment binds it to a strict xfail and never looks at
other bindings. So `@NAME` is a removable marker block even where `NAME` is
rebound at module level, or shadowed in a class body, to a decorator that
adds a check (here `usefixtures` of an environment the test needs). Removing
that decorator is a weakening, and the gate passes it.

Positive control: the same removal with `NAME` bound once, to a strict
xfail, passes (it is an activation); the same removal of a literal
`@pytest.mark.usefixtures(...)` decorator fails.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[4]


def _gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_grader_diff_probe", ROOT / "scripts" / "check_grader_diff.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HEAD = "import pytest\nfrom wowlab_core import guard\n"
TEST = "@CHECK\ndef test_write():\n    assert guard.write()\n"


def _ok(base: str, head: str) -> bool:
    return bool(_gate().check_file(base.encode(), head.encode()).ok)


def test_positive_control_single_binding_is_an_activation() -> None:
    base = HEAD + "CHECK = pytest.mark.xfail(strict=True)\n" + TEST
    assert _ok(base, base.replace("@CHECK\n", ""))


def test_positive_control_literal_usefixtures_removal_fails() -> None:
    base = HEAD + '@pytest.mark.usefixtures("real_env")\ndef test_write():\n    pass\n'
    assert not _ok(base, base.replace('@pytest.mark.usefixtures("real_env")\n', ""))


def test_module_level_rebinding_is_not_a_marker() -> None:
    base = (
        HEAD
        + "CHECK = pytest.mark.xfail(strict=True)\n"
        + 'CHECK = pytest.mark.usefixtures("real_env")\n'
        + TEST
    )
    assert not _ok(base, base.replace("@CHECK\n", ""))


def test_class_scope_shadow_is_not_a_marker() -> None:
    base = (
        HEAD
        + "CHECK = pytest.mark.xfail(strict=True)\n"
        + "class TestWrite:\n"
        + '    CHECK = pytest.mark.usefixtures("real_env")\n'
        + "    @CHECK\n"
        + "    def test_write(self):\n"
        + "        assert guard.write()\n"
    )
    assert not _ok(base, base.replace("    @CHECK\n", ""))
