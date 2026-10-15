# Probe from review of m13/05-addon-schema-3; reproduces a client value used raw (never through clean) in Actions.lua passing every test in the grader
"""M13-05 round 2 (tests/addon/test_lab_addon.py,
`test_m13_05_every_client_value_is_asked_about_before_use`) says: "Where the
client has issecretvalue, each gather tests every value a client call gave,
and every field of a table it gave, before the value is compared, used or
stored." `_m13_05_secret_problems` checks that only for values bound from
`pcall(<handle>, ...)` where the handle is one of eight fixed local names, and
only that the *next* mention of each value, in text order, is `v = clean(`.
So each of these ordinary edits to Actions.lua, which keep every existing
`clean` line and add one raw use, passes every test in the grader:

- the action-bar handle called under another name (`local ga = getAction`,
  then `pcall(ga, slot)`), its first value stored;
- `GetActionInfo` called by its global name inside a `pcall`, the same;
- `getAction` called without `pcall`, the same;
- `clean` skipped on one path (`if ok and slot > 1 then`), so slot 1's type,
  id and subtype are compared and stored raw;
- a spellbook item's `spellID` stored from a second call bound to a new name.

Each needs only the edit shown; the in-combat check is untouched, so these are
about values `issecretvalue` would call secret outside combat (restricted
content, [verify] in §13.1 and §15.2), which the round-2 claim covers.
Control: deleting the three `clean` lines under the real `pcall(getAction,
slot)` is rejected. Constructed inputs (boundary cases for the grader), not
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

GET = "            local ok, kind, id, subType = pcall(getAction, slot)\n"
CLEANS = (
    "                kind = clean(isSecret, secret, action, kind)\n"
    "                id = clean(isSecret, secret, action, id)\n"
    "                subType, hiddenSubType = clean(isSecret, secret, action, subType)\n"
)
ITEM_NAME = "        local okText, spellName, subtext = pcall(itemName, slot, bank)\n"

# Each case: (text found exactly once in Actions.lua, its replacement), in order.
Edits = list[tuple[str, str]]

# Each case keeps every existing `clean` line and adds one raw use. The added
# use is stored as `ns.String(...)` or `ns.Number(...)`, so the stored-value
# rules (M11-13) pass; what is missing is only the issecretvalue test.
RAW_KIND = "            record.raw_kind = ns.String(rawKind)\n"
CASES: dict[str, Edits] = {
    "constructed-handle-under-another-name": [
        (
            GET,
            GET
            + "            local ga = getAction\n"
            + "            local _, rawKind = pcall(ga, slot)\n"
            + RAW_KIND,
        ),
    ],
    "constructed-global-called-by-name-in-pcall": [
        (GET, GET + "            local _, rawKind = pcall(GetActionInfo, slot)\n" + RAW_KIND),
    ],
    "constructed-call-without-pcall": [
        (GET, GET + "            local rawKind = getAction(slot)\n" + RAW_KIND),
    ],
    "constructed-clean-skipped-on-one-path": [
        (
            "            if ok then\n                kind = clean(",
            "            if ok and slot > 1 then\n                kind = clean(",
        ),
    ],
    "constructed-field-stored-from-a-second-call": [
        (
            ITEM_NAME,
            ITEM_NAME
            + "        local data = select(2, pcall(itemInfo, slot, bank))\n"
            + '        entry.raw = ns.Number(type(data) == "table" and data.spellID or nil)\n',
        ),
    ],
}
CONTROLS: dict[str, Edits] = {
    "control-clean-removed": [(CLEANS, "")],
}


def _grader(root: Path, tag: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m13_05_secret_grader_{tag}", GRADER)
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
def test_a_raw_client_value_is_rejected(tmp_path: Path, tag: str) -> None:
    assert _failing_tests(tmp_path, tag, CASES[tag])
