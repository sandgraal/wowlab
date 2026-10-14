# Probe from review of m12/13-command-reference; reproduces a relative default rendered differently when HOME is "/" (substring match on the home folder)
"""The command reference must be byte-identical on every machine: the daily
wiki job (M12-14) regenerates it on an Ubuntu runner and diffs it.
`_machine_specific` treats a default as machine-specific when
`str(Path.home()) in value`, a substring test. Where HOME is `/` (common in
containers, for a service user, or under `env -i`-style launches that set
HOME=/), every string default holding a `/` matches, so a relative default
such as `pages/looks.html` renders as "depends on the machine" there and as
the value everywhere else. An absolute value is already caught by
`is_absolute()`; the home test only needs to catch a value under home.

Positive control: with HOME at an ordinary folder, the same relative default
renders as its value.

Constructed: parameter objects shaped like Click's; HOME is set per test
with monkeypatch. No current command has such a default; the defect is
latent.
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
    spec = importlib.util.spec_from_file_location("_review_m12_13_home", SCRIPT)
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
        "multiple": False,
        "nargs": 1,
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX HOME semantics")
def test_positive_control_constructed_relative_default_under_an_ordinary_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert gen._render_default(_param(default="pages/looks.html")) == "`pages/looks.html`"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX HOME semantics")
def test_constructed_relative_default_does_not_depend_on_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/")
    assert gen._render_default(_param(default="pages/looks.html")) == "`pages/looks.html`"
