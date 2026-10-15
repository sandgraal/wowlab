# Probe from review of m13/05-addon-schema-3; reproduces a field of a client table stored untested once the table is aliased or handed to a helper, passing every test in the grader
"""M13-05 round 3 (tests/addon/test_lab_addon.py, the comment above
`_M13_05_BUILTINS`) says: "a value read as a table is read only as `type(v)`
and as `v.field` handed to clean". `clean` tests the value it is given, so for
a table it tests the table and not its fields; each field must be cleaned
again. `_m13_05_pcall_problems` enforces the rule only on the name `v` the
`pcall` bound, and only when `v` itself is read with `.`, `[` or `:`
somewhere (`table = any(...)`). A cleaned value that the source never reads
as a table may therefore be copied to another name, or handed to one of the
file's own local functions, and read as a table there; neither the copy, the
parameter, nor the uses of `v` (no field read) is flagged. Each of these
edits stores a field of `GetNumBindings`'s cleaned result untested and passes
every test in the grader:

- `local alias = total`, then `ns.Number(alias.field)`;
- `commands = total` (plain assignment to an existing local), then
  `ns.Number(commands.field)`;
- a new `local function peek(v) return v.field end`, then
  `ns.Number(peek(total))`.

The implementer flagged assignment aliasing as worth testing. The same field
read straight from `total` is rejected (control), which shows the rule is
meant to cover it. Constructed inputs (boundary cases for the grader), not
addon sources.
"""

from __future__ import annotations

import importlib.util
import inspect
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]
GRADER = REPO / "tests" / "addon" / "test_lab_addon.py"

STORE = "        record.commands = commands\n"
COMMAND_FN = "local function command(value)\n"

# Each case: (text found exactly once in Actions.lua, its replacement), in order.
Edits = list[tuple[str, str]]

CASES: dict[str, Edits] = {
    "constructed-local-alias": [
        (
            STORE,
            "        local alias = total\n        record.raw = ns.Number(alias.field)\n" + STORE,
        ),
    ],
    "constructed-assignment-alias": [
        (
            STORE,
            "        commands = total\n        record.raw = ns.Number(commands.field)\n"
            "        record.commands = 0\n",
        ),
    ],
    "constructed-own-helper-reads-the-field": [
        (COMMAND_FN, "local function peek(v)\n    return v.field\nend\n\n" + COMMAND_FN),
        (STORE, "        record.raw = ns.Number(peek(total))\n" + STORE),
    ],
}
CONTROLS: dict[str, Edits] = {
    "control-field-read-straight": [
        (STORE, "        record.raw = ns.Number(total.field)\n" + STORE),
    ],
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m13_05_alias_grader_{tag}", GRADER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    module.ROOT = root
    module.ADDON_ROOT = root / "lab" / "addon"
    module.ADDON = module.ADDON_ROOT / "WowLab"
    module.ACTIONS = module.ADDON / "Actions.lua"
    module.TOC = module.ADDON / "WowLab.toc"
    module.STD = module.ADDON_ROOT / "wow_client.yml"
    module.README = module.ADDON_ROOT / "README.md"
    module.SOURCES = sorted(module.ADDON.glob("*.lua"))
    return module


def _failing_tests(tmp_path: Path, tag: str, edits: Edits) -> list[str]:
    shutil.copytree(REPO / "lab" / "addon", tmp_path / "lab" / "addon")
    target = tmp_path / "lab" / "addon" / "WowLab" / "Actions.lua"
    text = target.read_text(encoding="utf-8")
    for found, replacement in edits:
        assert text.count(found) == 1, "the edit did not apply; the addon source moved"
        text = text.replace(found, replacement)
    target.write_text(text, encoding="utf-8")
    grader = _grader(tmp_path, tag)
    failing = []
    for test_name, fn in inspect.getmembers(grader, inspect.isfunction):
        if not test_name.startswith("test_"):
            continue
        calls = (
            [(p,) for p in grader.SOURCES] if "path" in inspect.signature(fn).parameters else [()]
        )
        for args in calls:
            try:
                fn(*args)
            except AssertionError:
                failing.append(test_name)
    return failing


@pytest.mark.parametrize("tag", sorted(CONTROLS))
def test_positive_control_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CONTROLS[tag])


@pytest.mark.parametrize("tag", sorted(CASES))
def test_a_field_of_an_aliased_value_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
