# Probe from review of m11/24-sv-merge-loader; restores keep_probe coverage lost when the implementer's supplementary tests were deleted: awkward probe spellings and shapes, with --take theirs and with a whole-variable --key.
"""Constructed (L8: hostile and boundary cases; small documents, no fixture).

docs/LAB_PLAN.md §13.4, owner ruling for M11-24, "`probe` stays ours": when
`WowLab.lua` itself is merged, the `probe` table of the `--into` file is kept
whatever `--take` or `--key` says; M11-24T conductor ruling 2: its paths are
in neither `conflicts` nor `taken`. `wowlab sv merge` does this through
`svmerge.keep_probe(ours, result)`; the M11-24T graders cover it on the real
M11-03 pair, whose probe is spelled `["probe"]` once. These cover what that
pair cannot show:

- theirs spells the key with an escape (`["pro\\98e"]` loads as `probe`);
- both spell it as a bare name (`probe = {...}`);
- ours holds `probe` twice (Lua loads the last);
- ours has no probe (none is added from theirs);
- theirs' `probe` is a scalar;
- theirs' `WowLabCharDB` is a scalar: ours' probe has nowhere to go, so
  `MergeError` (the CLI's exit 2), never a write that drops it.

Each runs with `take="theirs"` and with `keys=["WowLabCharDB"]` (the whole
variable, copied whole in the two-way merge). All pass at 6281e9e; they pin
the behaviour for later changes. The merged document always serializes to
text the parser reads back to the same values.
"""

from __future__ import annotations

from typing import Any

import pytest

from wowlab_core import luadata, svmerge

TAKE_THEIRS: dict[str, Any] = {"take": "theirs"}
WHOLE_KEY: dict[str, Any] = {"keys": ["WowLabCharDB"]}
MODES = pytest.mark.parametrize(
    "mode", [TAKE_THEIRS, WHOLE_KEY], ids=["take-theirs", "key-whole-variable"]
)


def _doc(text: str) -> luadata.LuaDocument:
    return luadata.parse(text.encode())


def _under_probe(path: str) -> bool:
    parsed = svmerge.parse_path(path)
    return (
        parsed.head == "WowLabCharDB"
        and bool(parsed.steps)
        and parsed.steps[0].kind == "string"
        and parsed.steps[0].data == b"probe"
    )


def _kept(ours: str, theirs: str, mode: dict[str, Any]) -> svmerge.MergeResult:
    ours_doc = _doc(ours)
    result, touched = svmerge.keep_probe(ours_doc, svmerge.merge(ours_doc, _doc(theirs), **mode))
    assert touched, "the probe needed keeping"
    for listed in (result.conflicts, result.taken, result.absent):
        assert not [x.path for x in listed if _under_probe(x.path)], listed
    reread = luadata.parse(luadata.serialize(result.document)).to_python()
    assert reread == result.document.to_python()
    return result


CASES = {
    "escaped-key-in-theirs": (
        'WowLabCharDB = {\n["probe"] = {["loads"] = 4},\n["x"] = 1,\n}\n',
        'WowLabCharDB = {\n["pro\\98e"] = {["loads"] = 2},\n["x"] = 2,\n}\n',
        {"probe": {"loads": 4}, "x": 2},
    ),
    "bare-name-key": (
        "WowLabCharDB = {\nprobe = {loads = 4},\nx = 1,\n}\n",
        "WowLabCharDB = {\nprobe = {loads = 2},\nx = 2,\n}\n",
        {"probe": {"loads": 4}, "x": 2},
    ),
    "duplicate-probe-in-ours": (
        'WowLabCharDB = {\n["probe"] = {["loads"] = 9},\n["probe"] = {["loads"] = 4},\n'
        '["x"] = 1,\n}\n',
        'WowLabCharDB = {\n["probe"] = {["loads"] = 2},\n["x"] = 2,\n}\n',
        {"probe": {"loads": 4}, "x": 2},
    ),
    "no-probe-in-the-target": (
        'WowLabCharDB = {\n["x"] = 1,\n}\n',
        'WowLabCharDB = {\n["probe"] = {["loads"] = 2},\n["x"] = 2,\n}\n',
        {"x": 2},
    ),
    "scalar-probe-in-theirs": (
        'WowLabCharDB = {\n["probe"] = {["loads"] = 4},\n["x"] = 1,\n}\n',
        'WowLabCharDB = {\n["probe"] = 7,\n["x"] = 2,\n}\n',
        {"probe": {"loads": 4}, "x": 2},
    ),
}


@MODES
@pytest.mark.parametrize("case", sorted(CASES))
def test_the_targets_probe_is_kept_constructed(case: str, mode: dict[str, Any]) -> None:
    ours, theirs, expected = CASES[case]
    result = _kept(ours, theirs, mode)
    assert result.document.to_python() == {"WowLabCharDB": expected}


@MODES
def test_the_last_of_duplicate_probes_is_the_one_kept_constructed(mode: dict[str, Any]) -> None:
    ours, theirs, _ = CASES["duplicate-probe-in-ours"]
    out = luadata.serialize(_kept(ours, theirs, mode).document)
    assert b'["loads"] = 2' not in out, "theirs' probe is gone from the text too"


@MODES
def test_a_scalar_variable_from_theirs_is_refused_not_written_constructed(
    mode: dict[str, Any],
) -> None:
    ours = _doc('WowLabCharDB = {\n["probe"] = {["loads"] = 4},\n}\n')
    result = svmerge.merge(ours, _doc("WowLabCharDB = 1\n"), **mode)
    with pytest.raises(svmerge.MergeError, match="probe cannot be kept"):
        svmerge.keep_probe(ours, result)
