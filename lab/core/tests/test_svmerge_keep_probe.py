"""`wowlab_core.svmerge.keep_probe` edge cases (M11-24, implementer's test).

`wowlab sv merge` of a character's `WowLab.lua` keeps the target's probe
(§13.4, owner ruling for M11-24); the M11-24T graders cover it on the real
M11-03 pair. These cover what that pair cannot show. All constructed (small
documents): a probe key spelled as a bare name, a target with no probe, and a
merge that turns `WowLabCharDB` into a scalar.
"""

from __future__ import annotations

import pytest

from wowlab_core import luadata, svmerge


def _doc(*lines: str) -> luadata.LuaDocument:
    return luadata.parse(("\n".join(lines) + "\n").encode())


def test_bare_name_probe_is_kept_and_left_out_of_the_report_constructed() -> None:
    ours = _doc("WowLabCharDB = {", "  probe = { loads = 4 },", "  x = 1,", "}")
    theirs = _doc("WowLabCharDB = {", "  probe = { loads = 2 },", "  x = 2,", "}")
    result = svmerge.merge(ours, theirs, take="theirs")
    assert {c.path for c in result.conflicts} == {
        "WowLabCharDB.probe.loads",
        "WowLabCharDB.x",
    }
    kept, touched = svmerge.keep_probe(ours, result, theirs=theirs)
    assert touched
    assert [c.path for c in kept.conflicts] == ["WowLabCharDB.x"]
    assert [t.path for t in kept.taken] == ["WowLabCharDB.x"]
    assert kept.document.to_python() == {"WowLabCharDB": {"probe": {"loads": 4}, "x": 2}}
    assert b"probe = { loads = 4 }," in luadata.serialize(kept.document)


def test_no_probe_in_the_target_means_none_is_added_constructed() -> None:
    ours = _doc("WowLabCharDB = {", '  ["x"] = 1,', "}")
    theirs = _doc("WowLabCharDB = {", '  ["probe"] = { ["loads"] = 2 },', '  ["x"] = 2,', "}")
    result = svmerge.merge(ours, theirs, keys=["WowLabCharDB"])
    assert result.document.to_python()["WowLabCharDB"]["probe"] == {"loads": 2}
    kept, touched = svmerge.keep_probe(ours, result, theirs=theirs)
    assert touched
    assert kept.document.to_python() == {"WowLabCharDB": {"x": 2}}
    assert luadata.parse(luadata.serialize(kept.document)).to_python() == {"WowLabCharDB": {"x": 2}}


def test_untouched_probe_changes_nothing_constructed() -> None:
    ours = _doc("WowLabCharDB = {", '  ["probe"] = { ["loads"] = 4 },', '  ["x"] = 1,', "}")
    theirs = _doc("WowLabCharDB = {", '  ["probe"] = { ["loads"] = 2 },', '  ["x"] = 2,', "}")
    result = svmerge.merge(ours, theirs, keys=['WowLabCharDB["x"]'])
    same, touched = svmerge.keep_probe(ours, result, theirs=theirs)
    assert same is result and not touched


def test_a_merge_that_makes_the_variable_a_scalar_is_refused_constructed() -> None:
    ours = _doc("WowLabCharDB = {", '  ["probe"] = { ["loads"] = 4 },', "}")
    theirs = _doc("WowLabCharDB = 1")
    result = svmerge.merge(ours, theirs, take="theirs")
    with pytest.raises(svmerge.MergeError, match="probe cannot be kept"):
        svmerge.keep_probe(ours, result, theirs=theirs)
