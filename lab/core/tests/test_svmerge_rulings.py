"""The M16-12 rulings for `wowlab sv merge` (docs/LAB_PLAN.md §13.4, the
2026-11-04 amendment; owner decision of 2026-10-21 on Q8,
docs/handoffs/outside-evaluation-2026-10.md).

- An array part (keys 1..n) is one value: `--take theirs` keeps theirs'
  list whole, never merged index by index (addon-author:F1).
- `--key X` runs a normal merge inside `X`; the wholesale copy is
  `--key X --replace` (addon-author:F2).
- The plan lists every key the merge removes from the target.
- The loader notes print before the prompt (addon-author:F13).

Constructed (labelled `constructed`): the panel's `BarAddon.lua`, a
SavedVariables file of a made-up addon, planted in the two M11-03
characters' folders of the captured install copied into `tmp_path` (as
`test_cli.py` builds it). It stands for the panel's sandbox; no capture
holds an addon with a list and a profile that differ between two
characters. The user data directory is redirected and the process table is
a fake one, so nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    ACCOUNT,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard, luadata, svmerge

ACCT = f"WTF/Account/{ACCOUNT}"
CHAR_A = "Labchard-Labrealmg"
CHAR_B = "Labcharb-Labrealmf"
BAR = "BarAddon.lua"
BAR_A = f"{ACCT}/1/{CHAR_A}/SavedVariables/{BAR}"
BAR_B = f"{ACCT}/1/{CHAR_B}/SavedVariables/{BAR}"


def _crlf(*lines: str) -> bytes:
    """A document in the layout every Forever capture shows."""
    return ("\r\n" + "\r\n".join(lines) + "\r\n").encode("ascii")


# The panel's two files (addon-author:F1, F2): the target holds a two-ore
# list and `outline = true`; the source a three-ore list and no outline.
OURS = _crlf(
    "BarCharDB = {",
    '["tracked"] = {',
    '"Copper Ore",',
    '"Tin Ore",',
    "},",
    '["profile"] = {',
    '["font"] = "Friz",',
    '["size"] = 10,',
    '["outline"] = true,',
    "},",
    "}",
)
THEIRS = _crlf(
    "BarCharDB = {",
    '["tracked"] = {',
    '"Silver Ore",',
    '"Copper Ore",',
    '"Tin Ore",',
    "},",
    '["profile"] = {',
    '["font"] = "Morpheus",',
    '["size"] = 12,',
    "},",
    "}",
)
TRACKED = 'BarCharDB["tracked"]'
OUTLINE = 'BarCharDB["profile"]["outline"]'


def _doc(data: bytes) -> luadata.LuaDocument:
    return luadata.parse(data)


def _data(result: svmerge.MergeResult) -> Any:
    return luadata.parse(luadata.serialize(result.document)).to_python()


def _paths(items: Any) -> list[str]:
    return [item.path for item in items]


# ─── library: an array part is one value ────────────────────────────────────


def test_take_theirs_keeps_the_list_whole_constructed() -> None:
    result = svmerge.merge(_doc(OURS), _doc(THEIRS), take="theirs")
    assert _data(result)["BarCharDB"]["tracked"] == ["Silver Ore", "Copper Ore", "Tin Ore"]
    tracked = [c for c in result.conflicts if c.path.startswith(TRACKED)]
    assert [(c.path, c.ours, c.theirs, c.resolved) for c in tracked] == [
        (TRACKED, '{"Copper Ore", "Tin Ore"}', '{"Silver Ore", "Copper Ore", "Tin Ore"}', "theirs")
    ], "one conflict for the list, never one per index"
    assert TRACKED in _paths(result.taken)
    assert not any(a.path.startswith(TRACKED) for a in result.absent)
    assert list(result.removed) == [], "the longer list removes nothing"


def test_two_way_a_differing_list_is_one_unresolved_conflict_constructed() -> None:
    result = svmerge.merge(_doc(OURS), _doc(THEIRS))
    assert TRACKED in _paths(result.conflicts)
    assert not any(p.startswith(TRACKED + "[") for p in _paths(result.conflicts))
    assert result.unresolved
    assert luadata.serialize(result.document) == OURS


def test_take_ours_keeps_the_target_list_constructed() -> None:
    result = svmerge.merge(_doc(OURS), _doc(THEIRS), take="ours")
    assert _data(result)["BarCharDB"]["tracked"] == ["Copper Ore", "Tin Ore"]
    assert list(result.removed) == []


def test_taking_a_shorter_list_lists_it_once_at_its_path_constructed() -> None:
    result = svmerge.merge(_doc(THEIRS), _doc(OURS), take="theirs")
    assert _data(result)["BarCharDB"]["tracked"] == ["Copper Ore", "Tin Ore"]
    assert [(r.path, r.value, r.entries) for r in result.removed] == [
        (TRACKED, '{"Silver Ore", "Copper Ore", "Tin Ore"}', (3, 2))
    ], "the list replaced, once, never one removal per index"
    out = luadata.serialize(result.document)
    assert out.count(b'"Tin Ore"') == 1 and b'"Silver Ore"' not in out


def test_equal_lists_are_no_conflict_whatever_the_spelling_constructed() -> None:
    ours = _crlf("DB = {", '["l"] = {', '"a",', '"b",', "},", "}")
    theirs = _crlf("DB = {", '["l"] = {', '[1] = "a",', '[2] = "b",', "},", "}")
    result = svmerge.merge(_doc(ours), _doc(theirs), take="theirs")
    assert list(result.conflicts) == [] and list(result.taken) == []
    assert luadata.serialize(result.document) == ours


def test_three_way_a_list_changed_on_one_side_is_taken_whole_constructed() -> None:
    base = _crlf("DB = {", '["l"] = {', '"a",', '"b",', "},", "}")
    theirs = _crlf("DB = {", '["l"] = {', '"a",', '"b",', '"c",', "},", "}")
    result = svmerge.merge(_doc(base), _doc(theirs), base=_doc(base))
    assert list(result.conflicts) == []
    assert _paths(result.taken) == ['DB["l"]']
    assert _data(result) == {"DB": {"l": ["a", "b", "c"]}}
    ours_only = svmerge.merge(_doc(theirs), _doc(base), base=_doc(base))
    assert list(ours_only.conflicts) == [] and list(ours_only.taken) == []
    assert luadata.serialize(ours_only.document) == theirs, "ours' own change stays"


def test_three_way_both_sides_appended_is_one_conflict_constructed() -> None:
    base = _crlf("DB = {", '["l"] = {', '"a",', "},", "}")
    ours = _crlf("DB = {", '["l"] = {', '"a",', '"b",', "},", "}")
    theirs = _crlf("DB = {", '["l"] = {', '"a",', '"c",', "},", "}")
    result = svmerge.merge(_doc(ours), _doc(theirs), base=_doc(base))
    assert [(c.path, c.ours, c.theirs, c.base) for c in result.conflicts] == [
        ('DB["l"]', '{"a", "b"}', '{"a", "c"}', '{"a"}')
    ]
    assert _data(result) == {"DB": {"l": ["a", "b"]}}


def test_the_keys_after_the_list_are_still_merged_key_by_key_constructed() -> None:
    ours = _crlf("DB = {", '["l"] = {', '"a",', '["n"] = 1,', "},", "}")
    theirs = _crlf("DB = {", '["l"] = {', '"a",', '["n"] = 2,', "},", "}")
    result = svmerge.merge(_doc(ours), _doc(theirs), take="theirs")
    assert _paths(result.conflicts) == ['DB["l"]["n"]'], "the list is the same; n is a leaf"
    assert _data(result) == {"DB": {"l": {1: "a", "n": 2}}}


def test_a_list_of_tables_taken_lists_what_it_removes_inside_constructed() -> None:
    ours = _crlf("DB = {", '["l"] = {', "{", '["x"] = 1,', '["y"] = 1,', "},", "},", "}")
    theirs = _crlf("DB = {", '["l"] = {', "{", '["x"] = 2,', "},", "},", "}")
    result = svmerge.merge(_doc(ours), _doc(theirs), take="theirs")
    assert [(c.path, c.ours, c.theirs) for c in result.conflicts] == [
        ('DB["l"]', "list (1 entry, 1 differ)", "list (1 entry, 1 differ)")
    ]
    assert [(r.path, r.entries) for r in result.removed] == [('DB["l"]', (1, 1))]
    assert _data(result) == {"DB": {"l": [{"x": 2}]}}


def test_a_list_taken_with_a_gap_holds_exactly_theirs_keys_constructed() -> None:
    ours = _crlf("DB = {", '["l"] = {', '"a",', '"b",', '"c",', "},", '["n"] = 1,', "}")
    theirs = _crlf("DB = {", '["l"] = {', '[1] = "a",', '[3] = "c",', "},", '["n"] = 1,', "}")
    result = svmerge.merge(_doc(ours), _doc(theirs), take="theirs")
    assert _data(result) == {"DB": {"l": {1: "a", 3: "c"}, "n": 1}}
    assert [(r.path, r.entries) for r in result.removed] == [('DB["l"]', (3, 2))]


def test_a_list_in_the_middle_of_a_table_stays_there_constructed() -> None:
    ours = _crlf("DB = {", '["a"] = 1,', '"x",', '"y",', '["z"] = 2,', "}")
    theirs = _crlf("DB = {", '["a"] = 1,', '"q",', '["z"] = 2,', "}")
    result = svmerge.merge(_doc(ours), _doc(theirs), take="theirs")
    out = luadata.serialize(result.document)
    assert luadata.parse(out).to_python() == {"DB": {1: "q", "a": 1, "z": 2}}
    assert out.index(b'"q"') < out.index(b'["z"]'), "where ours' list was"


def test_a_table_replaced_by_a_scalar_lists_its_keys_as_removed_constructed() -> None:
    ours = _crlf("DB = {", '["p"] = {', '["x"] = 1,', "},", "}")
    theirs = _crlf("DB = {", '["p"] = false,', "}")
    result = svmerge.merge(_doc(ours), _doc(theirs), take="theirs")
    assert [(r.path, r.value) for r in result.removed] == [('DB["p"]["x"]', "1")]


# ─── library: --key merges inside, --replace copies whole ────────────────────


def test_key_keeps_the_target_only_key_constructed() -> None:
    result = svmerge.merge(_doc(OURS), _doc(THEIRS), keys=["BarCharDB.profile"], take="theirs")
    profile = _data(result)["BarCharDB"]["profile"]
    assert profile == {"font": "Morpheus", "size": 12, "outline": True}
    assert [(a.path, a.missing_from) for a in result.absent] == [(OUTLINE, "theirs")]
    assert list(result.removed) == []
    assert _data(result)["BarCharDB"]["tracked"] == ["Copper Ore", "Tin Ore"], "outside --key"


def test_key_without_take_is_a_conflict_inside_the_subtree_constructed() -> None:
    result = svmerge.merge(_doc(OURS), _doc(THEIRS), keys=["BarCharDB.profile"])
    assert _paths(result.conflicts) == [
        'BarCharDB["profile"]["font"]',
        'BarCharDB["profile"]["size"]',
    ]
    assert result.unresolved


def test_key_copies_an_empty_destination_whole_constructed() -> None:
    # Conductor's ruling, 2026-11-05: an empty DST is treated as a missing one.
    ours = _crlf("DB = {", '["a"] = {', '["x"] = 1,', "},", '["b"] = {', "},", "}")
    result = svmerge.merge(_doc(ours), _doc(ours), keys=['DB.a=DB["b"]'])
    assert _paths(result.taken) == ['DB["b"]'] and list(result.absent) == []
    assert _data(result) == {"DB": {"a": {"x": 1}, "b": {"x": 1}}}


def test_replace_of_a_table_holding_a_list_lists_the_list_once_constructed() -> None:
    ours = _crlf("DB = {", '["p"] = {', '["l"] = {', '"a",', '"b",', "},", "},", "}")
    theirs = _crlf("DB = {", '["p"] = {', '["l"] = {', '"a",', "},", "},", "}")
    result = svmerge.merge(_doc(ours), _doc(theirs), keys=["DB.p"], replace=True)
    assert [(r.path, r.value, r.entries) for r in result.removed] == [
        ('DB["p"]["l"]', '{"a", "b"}', (2, 1))
    ]


def test_key_copies_a_destination_the_target_lacks_whole_constructed() -> None:
    ours = _crlf("DB = {", '["a"] = {', '["x"] = 1,', "},", "}")
    result = svmerge.merge(_doc(ours), _doc(ours), keys=['DB.a=DB["b"]'])
    assert _paths(result.taken) == ['DB["b"]']
    assert _data(result) == {"DB": {"a": {"x": 1}, "b": {"x": 1}}}


def test_replace_copies_whole_and_lists_every_removed_key_constructed() -> None:
    result = svmerge.merge(_doc(OURS), _doc(THEIRS), keys=["BarCharDB.profile"], replace=True)
    assert _data(result)["BarCharDB"]["profile"] == {"font": "Morpheus", "size": 12}
    assert [(r.path, r.value) for r in result.removed] == [(OUTLINE, "true")]
    assert list(result.conflicts) == [] and _paths(result.taken) == ['BarCharDB["profile"]']


@pytest.mark.parametrize(
    "kwargs",
    [{"replace": True}, {"replace": True, "keys": ["BarCharDB.profile"], "base": True}],
    ids=["no-key", "with-base"],
)
def test_replace_needs_a_key_and_no_base(kwargs: dict[str, Any]) -> None:
    if kwargs.pop("base", False):
        kwargs["base"] = _doc(OURS)
    with pytest.raises(svmerge.MergeError, match="replace"):
        svmerge.merge(_doc(OURS), _doc(THEIRS), **kwargs)


def test_keep_probe_drops_removed_probe_paths_constructed() -> None:
    ours = _crlf("WowLabCharDB = {", '["probe"] = {', '["loads"] = 4,', '["x"] = 1,', "},", "}")
    theirs = _crlf("WowLabCharDB = {", '["probe"] = {', '["loads"] = 5,', "},", "}")
    result = svmerge.merge(_doc(ours), _doc(theirs), keys=["WowLabCharDB.probe"], replace=True)
    assert _paths(result.removed) == ['WowLabCharDB["probe"]["x"]']
    kept, _ = svmerge.keep_probe(_doc(ours), result)
    assert list(kept.removed) == []
    assert luadata.serialize(kept.document) == ours


# ─── CLI: the panel's two cases ──────────────────────────────────────────────


@pytest.fixture
def bar(flavor: Path) -> Path:
    """The panel's files, planted in the two characters' folders."""
    (flavor / BAR_A).write_bytes(OURS)
    (flavor / BAR_B).write_bytes(THEIRS)
    return flavor


def _report(result: Any) -> dict[str, Any]:
    report = json.loads(result.stdout)
    assert isinstance(report, dict)
    return report


def _merge(*extra: str, input: str | None = None) -> Any:
    return run("sv", "merge", BAR, "--from", CHAR_B, "--into", CHAR_A, *extra, input=input)


def test_cli_take_theirs_keeps_the_list_whole_constructed(bar: Path) -> None:
    plan = _merge("--take", "theirs", input="n\n")
    assert TRACKED in plan.output and "settings removed from the target" not in plan.output
    result = _merge("--take", "theirs", "--yes", "--json")
    assert result.exit_code == 0, result.output
    cli.SvMergeReport.model_validate_json(result.stdout)
    report = _report(result)
    assert report["written"] is True and report["removed"] == []
    assert TRACKED in {c["path"] for c in report["conflicts"]}
    data = luadata.parse((bar / BAR_A).read_bytes()).to_python()
    assert data["BarCharDB"]["tracked"] == ["Silver Ore", "Copper Ore", "Tin Ore"]
    assert data["BarCharDB"]["profile"]["outline"] is True, "absent is never deleted"
    assert (bar / BAR_B).read_bytes() == THEIRS


def test_cli_key_profile_keeps_outline_true_constructed(bar: Path) -> None:
    result = _merge("--key", "BarCharDB.profile", "--take", "theirs", "--yes", "--json")
    assert result.exit_code == 0, result.output
    report = _report(result)
    assert report["replace"] is False and report["removed"] == []
    assert {(a["path"], a["missing_from"]) for a in report["absent"]} == {(OUTLINE, "theirs")}
    data = luadata.parse((bar / BAR_A).read_bytes()).to_python()
    assert data["BarCharDB"]["profile"] == {"font": "Morpheus", "size": 12, "outline": True}
    assert data["BarCharDB"]["tracked"] == ["Copper Ore", "Tin Ore"]


def test_cli_key_without_take_lists_the_conflicts_and_writes_nothing_constructed(
    bar: Path,
) -> None:
    result = _merge("--key", "BarCharDB.profile", "--yes")
    assert result.exit_code == 1, result.output
    assert 'BarCharDB["profile"]["font"]' in result.output
    assert (bar / BAR_A).read_bytes() == OURS
    assert guard.history() == ()


def test_cli_replace_plan_lists_the_removed_key_before_the_prompt_constructed(
    bar: Path,
) -> None:
    declined = _merge("--key", "BarCharDB.profile", "--replace", input="n\n")
    assert declined.exit_code != 0
    out = declined.output
    assert "1 entries or settings removed from the target:" in out
    assert out.index(OUTLINE) < out.index("Write the merge?")
    assert "(was true)" in out
    assert (bar / BAR_A).read_bytes() == OURS, "declined: nothing written"
    result = _merge("--key", "BarCharDB.profile", "--replace", "--yes", "--json")
    assert result.exit_code == 0, result.output
    report = _report(result)
    assert report["replace"] is True
    assert [(r["path"], r["value"]) for r in report["removed"]] == [(OUTLINE, "true")]
    data = luadata.parse((bar / BAR_A).read_bytes()).to_python()
    assert data["BarCharDB"]["profile"] == {"font": "Morpheus", "size": 12}
    ok("undo", "--yes")
    assert (bar / BAR_A).read_bytes() == OURS


def test_cli_replace_is_pinned_by_expect_constructed(bar: Path) -> None:
    plan = _merge("--key", "BarCharDB.profile", "--replace", "--json", input="n\n")
    report = json.loads(plan.stdout[plan.stdout.index("{") :])  # after the echoed answer
    pin = f"{report['target_sha256']}:{report['merged_sha256']}"
    wrong = _merge("--key", "BarCharDB.profile", "--yes", "--json", f"--expect={pin}")
    assert wrong.exit_code == 3, "without --replace the merge writes other bytes"
    assert (bar / BAR_A).read_bytes() == OURS
    done = _merge("--key", "BarCharDB.profile", "--replace", "--yes", f"--expect={pin}")
    assert done.exit_code == 0, done.output


@pytest.mark.parametrize(
    "extra",
    [("--replace",), ("--key", "BarCharDB.profile", "--replace", "--base", "x")],
    ids=["no-key", "with-base"],
)
def test_cli_replace_usage_errors(bar: Path, extra: tuple[str, ...]) -> None:
    result = _merge(*extra, "--yes")
    assert result.exit_code == 2, result.output
    assert "--replace" in result.stderr
    assert (bar / BAR_A).read_bytes() == OURS


def test_cli_loader_notes_print_before_the_prompt_constructed(bar: Path) -> None:
    # Without the --into character's WowLab.lua the loader check warns and
    # goes on (§13.4): that warning belongs to the plan (addon-author:F13).
    (bar / f"{ACCT}/1/{CHAR_A}/SavedVariables/WowLab.lua").unlink()
    result = _merge("--take", "theirs", input="n\n")
    assert result.exit_code != 0
    out = result.output
    prompt = out.index("Write the merge?")
    assert "the SavedVariables loader was not checked" in out[:prompt]
    assert out.count("the SavedVariables loader was not checked") == 1
    assert out.index("A key listed as absent") < prompt
    written = _merge("--take", "theirs", input="y\n")
    assert written.exit_code == 0, written.output
    assert written.output.count("the SavedVariables loader was not checked") == 1, "not again"


def test_cli_plan_names_a_list_replaced_once_constructed(bar: Path) -> None:
    # The reverse direction: the source's two ores replace the target's three.
    (bar / BAR_A).write_bytes(THEIRS)
    (bar / BAR_B).write_bytes(OURS)
    plan = _merge("--take", "theirs", input="n\n")
    out = plan.output
    line = (
        f'    {TRACKED}  list replaced, 3 entries -> 2 (was {{"Silver Ore", "Copper Ore", '
        '"Tin Ore"})'
    )
    assert line in out.split("\n"), out
    assert f"{TRACKED}[3]" not in out, "never one removal per index"
    assert out.index(line) < out.index("Write the merge?")


# ─── review round 2 ─────────────────────────────────────────────────────────


def test_key_a_scalar_source_over_an_empty_destination_is_a_conflict_constructed() -> None:
    # The empty-DST copy (conductor's ruling, 2026-11-05) holds only when the
    # source's value is a table: `false` over `{}` is a conflict, never silent.
    ours = _crlf("DB = {", '["p"] = {', "},", "}")
    theirs = _crlf("DB = {", '["p"] = false,', "}")
    result = svmerge.merge(_doc(ours), _doc(theirs), keys=["DB.p"])
    assert [(c.path, c.ours, c.theirs) for c in result.conflicts] == [
        ('DB["p"]', "table (0 entries)", "false")
    ]
    assert result.unresolved and list(result.taken) == []
    assert luadata.serialize(result.document) == ours


def test_a_list_with_a_gap_counts_only_its_entries_constructed() -> None:
    ours = _crlf("DB = {", '["l"] = {', '"a",', '"b",', '"c",', '"d",', "},", "}")
    theirs = _crlf("DB = {", '["l"] = {', '[1] = "a",', '[3] = "c",', "},", "}")
    result = svmerge.merge(_doc(ours), _doc(theirs), take="theirs")
    (conflict,) = result.conflicts
    assert conflict.theirs == '{[1] = "a", [3] = "c"}'
    assert [(r.path, r.entries) for r in result.removed] == [('DB["l"]', (4, 2))]
    tables = _crlf("DB = {", '["l"] = {', "[1] = {", "},", "[3] = {", "},", "},", "}")
    shown = svmerge.merge(_doc(ours), _doc(tables)).conflicts[0].theirs
    assert shown == "list (2 entries, a gap at [2])"


@pytest.mark.parametrize(
    "theirs",
    [
        ("Silver Ore",),
        ("Silver Ore", "Copper Ore", "Tin Ore", "Iron Ore"),
    ],
    ids=["shorter", "longer"],
)
def test_a_taken_list_gets_its_own_index_comments_constructed(theirs: tuple[str, ...]) -> None:
    # The retail layout (tabs, LF, `-- [n]` after each positional entry):
    # a dropped entry's comment goes with it, and each written entry gets
    # the comment naming its own index.
    def doc(items: tuple[str, ...]) -> bytes:
        lines = [f'\t\t"{v}", -- [{n}]' for n, v in enumerate(items, start=1)]
        return ('\nDB = {\n\t["l"] = {\n' + "\n".join(lines) + '\n\t},\n\t["n"] = 1,\n}\n').encode()

    ours = doc(("Copper Ore", "Tin Ore", "Iron Ore"))
    result = svmerge.merge(_doc(ours), _doc(doc(theirs)), take="theirs")
    assert luadata.serialize(result.document) == doc(theirs)
