# Probe from review of m13/47-release-notes-guard; reproduces the notes guard reporting a correctly listed model as missing when its name has a digit.
"""The notes' `--json` guard reads backticked names with `[A-Za-z_.]` only.

`gen_command_reference.json_models` takes a model name with digits
(`_MODEL_NAME`: `[A-Z][A-Za-z0-9_]*`, and `[a-z0-9_]*` for the module), and
the package already has digit-bearing model classes (`CharDBV2`,
`TalentsV3`). A `--json` model such as `CharDBV3Report`, named in help and
listed in the notes, is then not read from the notes and is reported as
missing: a failure no edit to the notes can clear, on every PR.

`constructed` (L8, boundary): the real notes with one invented name added
to the paragraph, and an invented command; nothing on disk is edited.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[4]
SCRIPTS_TESTS = REPO / "tests" / "scripts"


def _release_tests() -> ModuleType:
    if str(SCRIPTS_TESTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_TESTS))
    name = "test_release_m13_47_probe"
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_TESTS / "test_release.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _notes_with(mod: ModuleType, name: str) -> str:
    text: str = mod.TEMPLATE.read_text(encoding="utf-8")
    label: str = mod._JSON_PARAGRAPH
    return text.replace(label, f"{label} (`{name}`)", 1)


def test_positive_control_a_listed_name_without_digits_is_not_missing() -> None:
    mod = _release_tests()
    named = {"wowlab invented": ["CharDBReport"]}
    assert mod._missing(named, mod._listed(_notes_with(mod, "CharDBReport"))) == []


def test_a_listed_name_with_a_digit_is_not_missing() -> None:
    mod = _release_tests()
    # The generator's own rule accepts the name as one model name.
    assert mod.gen._MODEL_NAME.fullmatch("CharDBV3Report")
    named = {"wowlab invented": ["CharDBV3Report"]}
    assert mod._missing(named, mod._listed(_notes_with(mod, "CharDBV3Report"))) == []
