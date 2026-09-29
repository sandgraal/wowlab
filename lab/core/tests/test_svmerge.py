"""Graders for `wowlab sv merge` and `wowlab_core.svmerge` (docs/LAB_PLAN.md §13.4, M11-09T).

Written before the implementation (ADR-0013): every test carries one
`xfail(strict=True)` marker line, which M11-09 deletes and does not otherwise
edit. Expectations come from §13.4, §13.1 (the lab-addon's `probe`),
docs/LAB_FORMATS.md §4 and its M11-03 amendment, and the M11-03 captures.

Real fixtures (L8): the two characters' `WowLab.lua` from the M11-03 capture
(`1/Labchard-Labrealmg`, level 13, `probe.loads` = 4; `1/Labcharb-Labrealmf`,
level 10, `probe.loads` = 2; the same top-level keys in the same client
iteration order, no `probe.lost`), and the account-wide
`DBM-Party-Vanilla.lua`, which keeps per-character settings inside the
account file keyed by a character string (`["Labchard Labrealmg"]`,
`["Unknown"]`), the case §13.4 turns into a `--key` copy within one file.
The install is the captured tree copied into `tmp_path` as `test_cli.py`
builds it; the user data directory (store, journal, locks) is redirected
into `tmp_path` and the process table is a fake one. Nothing reads or writes
a real install.

Constructed (labelled `constructed` in the test name): small documents for
the three-way rules, which no capture can show (there is no real common
ancestor); edited copies of a real file standing for "a later session"
(`loads` raised, one value changed); a `probe.lost = true` file; a missing
`WowLab.lua`; a target written in another layout (tab indentation, LF), to
show a copied subtree takes the target's style; a real file with one value
changed after a snapshot (`["Enabled"]`, `["filter"]`).

The interface these graders assume, and where §13.4 leads to it (the gaps
it leaves are decided here and reported with M11-09T):

Library, `wowlab_core.svmerge` (imported inside each test, so collection
works before the module exists):

- `merge(ours, theirs, *, base=None, keys=(), take=None) -> MergeResult`,
  on `luadata.LuaDocument`s. Pure: reads nothing, writes nothing. "ours: the
  target; theirs: the source"; `base` present is the three-way merge, absent
  the two-way one. `keys` holds `--key` strings; `take` is `"ours"`,
  `"theirs"` or `None` (`--take`).
- A `--key` string is a path in `wowlab sv dump --path` syntax
  (`Var.key[3]["some key"]`), or `SRC=DST`: theirs' subtree at `SRC` copied to
  `DST` in ours. A copy within one file (§13.4: per-character settings kept
  in the account file) is `merge(doc, doc, keys=["SRC=DST"])`, and on the CLI
  a merge with no `--from`.
- `MergeResult.document`: the merged document, which is ours where nothing is
  taken. `.conflicts`, `.taken`: items with `.path`. `.absent`: items with
  `.path` and `.missing_from` (`"ours"` or `"theirs"`). A path is spelled as
  `wowlab sv dump` prints it: `WowLabCharDB["probe"]["loads"]`, `[n]` for a
  number key or a positional entry (the Lua key it loads as).
- Tables on both sides are merged key by key. A conflict is a leaf: a scalar,
  or a key holding a table on one side and a scalar on the other. A key on one
  side only is `absent`, never "deleted" and never removed from ours. In the
  two-way merge it is also never added to ours; in the three-way merge a key
  theirs added (in neither base nor ours) is a one-sided change and is taken.
- Unchanged nodes keep their bytes, keys keep ours' order, taken numbers keep
  theirs' text, and taken nodes are laid out in ours' style when serialized.

CLI, `wowlab sv merge FILE [--from CHARACTER|SNAPSHOT] --into CHARACTER
[--base SNAPSHOT] [--key PATH|SRC=DST]... [--take ours|theirs]
[--force-loader-check] [--yes] [--json]`:

- `FILE` is a SavedVariables file name (`WowLab.lua`), found in the `--into`
  character's `SavedVariables/` or else the account's; or a path as
  `sv dump` takes it, whose scope `layout` gives.
- `--from` is a snapshot id or a character folder. `--base SNAPSHOT` names
  the common ancestor for a three-way merge; without it the merge is two-way.
- The loader check reads the `--into` character's `WowLab.lua`; "two
  snapshots" are the two newest in the store that hold that file. `loads`
  lower in the newer one: refused (exit 3). Equal: refused too, since a
  loader bug that persists makes every session write `loads = 1` (N, 1, 1,
  …); unless the two snapshot entries are byte-identical (the client did
  not write in between), which passes with the note "no login between the
  two snapshots; the loader was not re-checked" (conductor ruling,
  2026-09-29). Higher: passes. `--force-loader-check` overrides every
  refusal.
- The `<Realm>/<First>/` twin (§13.4: `--into` never resolves to it) is
  covered only indirectly: every grader names a `<digits>/<First>-<Second>`
  folder with `--into`, and the target path comes from `--into`. No grader
  passes the twin itself.

The rulings above are recorded in docs/LAB_PLAN.md §13.4 ("M11-09T
conductor rulings", 2026-09-29).
- Exit codes (§6.11): 0 merged or nothing to change; 1 conflicts left
  unresolved (the report lists them, nothing is written); 2 usage, including
  a character-to-character merge of an account-wide file; 3 refused, by the
  write gate or by the loader check.
- `--json` prints `SvMergeReport`: at least `mode` (`"two-way"` or
  `"three-way"`), `conflicts`, `absent`, `taken` (objects with `path`; an
  absent one also has `missing_from`), `written` (bool), `notes` (list of
  str, the warnings the text output prints).
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from test_cli import (
    ACCOUNT,
    _running,
    _state,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard, luadata

ACCT = f"WTF/Account/{ACCOUNT}"
CHAR_A = "Labchard-Labrealmg"  # level 13, probe.loads = 4 (M11-03)
CHAR_B = "Labcharb-Labrealmf"  # level 10, probe.loads = 2 (M11-03)
LAB = "WowLab.lua"
LAB_A = f"{ACCT}/1/{CHAR_A}/SavedVariables/{LAB}"
LAB_B = f"{ACCT}/1/{CHAR_B}/SavedVariables/{LAB}"
LAB_ACCOUNT = f"{ACCT}/SavedVariables/{LAB}"
DBM_NAME = "DBM-Party-Vanilla.lua"
DBM = f"{ACCT}/SavedVariables/{DBM_NAME}"
DBM_VAR = "DBMPartyVanilla_AllSavedVars"

FIXTURE_FLAVOR = Path(__file__).resolve().parent / "fixtures" / "macos" / "forever"
REAL_A = (FIXTURE_FLAVOR / LAB_A).read_bytes()
REAL_B = (FIXTURE_FLAVOR / LAB_B).read_bytes()
REAL_DBM = (FIXTURE_FLAVOR / DBM).read_bytes()

# The two keys the M11-03 pair has on one side only: the second character's
# class tree has 54 nodes, the first's 52 (positional entries 53 and 54).
_NODES = 'WowLabCharDB["talents"]["class"]["config"]["trees"][1]["nodes"]'
ONLY_IN_B = {f"{_NODES}[53]", f"{_NODES}[54]"}


# `sv merge` of a character's `WowLab.lua` keeps the target's probe, which is
# neither a conflict nor taken, and says so (§13.4, owner ruling for M11-24;
# conductor ruling on M11-24T, 2026-09-29). The library `merge` still lists it.
PROBE_NOTE = "probe kept from the target: it counts that character's logins (§13.4)"
_PROBE = 'WowLabCharDB["probe"]'


def _under_probe(path: str) -> bool:
    return path == _PROBE or path.startswith(_PROBE + "[")


def _svmerge() -> ModuleType:
    return importlib.import_module("wowlab_core.svmerge")


# ─── helpers: expectations computed from the documents, not from a merge ─────


def _path(head: str, *steps: object) -> str:
    """A key path as `wowlab sv dump` spells it."""
    out = head
    for step in steps:
        out += f"[{step}]" if isinstance(step, int) else f'["{step}"]'
    return out


def _children(value: object) -> dict[object, object] | None:
    if isinstance(value, list):
        return {i + 1: v for i, v in enumerate(value)}
    if isinstance(value, dict):
        return value
    return None


def _two_way_oracle(ours: bytes, theirs: bytes) -> tuple[set[str], set[str], set[str]]:
    """§13.4's two-way rule applied to the plain data: (conflicts, keys only in
    ours, keys only in theirs). A conflict is a key whose values differ and
    are not both tables."""
    conflicts: set[str] = set()
    only_ours: set[str] = set()
    only_theirs: set[str] = set()

    def walk(o: dict[object, object], t: dict[object, object], at: str, *steps: object) -> None:
        for key, value in o.items():
            here = _path(at, *steps, key)
            if key not in t:
                only_ours.add(here)
                continue
            oc, tc = _children(value), _children(t[key])
            if oc is not None and tc is not None:
                walk(oc, tc, at, *steps, key)
            elif value != t[key]:
                conflicts.add(here)
        only_theirs.update(_path(at, *steps, key) for key in t if key not in o)

    o_top = luadata.parse(ours).to_python()
    t_top = luadata.parse(theirs).to_python()
    for name, value in o_top.items():
        oc, tc = _children(value), _children(t_top.get(name))
        assert oc is not None and tc is not None
        walk(oc, tc, name)
    return conflicts, only_ours, only_theirs


def _key_order(data: bytes) -> list[str]:
    """Every key path in document order (positional entries as `[n]`)."""
    order: list[str] = []

    def walk(table: luadata.LuaTable, at: str) -> None:
        position = 0
        for entry in table.entries:
            if entry.style is luadata.KeyStyle.POSITIONAL:
                position += 1
                here = f"{at}[{position}]"
            elif isinstance(entry.key, luadata.LuaString):
                here = f'{at}["{entry.key.value}"]'
            elif isinstance(entry.key, luadata.LuaNumber):
                here = f"{at}[{entry.key.raw}]"
            else:
                here = f"{at}[{entry.key!r}]"
            order.append(here)
            if isinstance(entry.value, luadata.LuaTable):
                walk(entry.value, here)

    for assignment in luadata.parse(data).assignments:
        if isinstance(assignment.value, luadata.LuaTable):
            walk(assignment.value, assignment.name)
    return order


def _top_entry_lines(lines: list[bytes], key: bytes) -> tuple[int, int]:
    """First and last line of the entry `key` directly inside the top-level
    table of a client-written file (no indentation, one entry per line, an
    empty table on two lines)."""
    depth = 0
    start = None
    for i, line in enumerate(lines):
        if line.startswith(b"}"):
            depth -= 1
            if start is not None and depth == 1:
                return start, i
        if start is None and depth == 1 and line == key + b" = {":
            start = i
        if line.endswith(b"{"):
            depth += 1
    raise AssertionError(f"no top-level entry {key!r}")


def _splice(ours: bytes, theirs: bytes, key: bytes) -> bytes:
    """`ours` with the top-level entry `key` replaced by theirs' lines."""
    o, t = ours.split(b"\r\n"), theirs.split(b"\r\n")
    o0, o1 = _top_entry_lines(o, key)
    t0, t1 = _top_entry_lines(t, key)
    return b"\r\n".join(o[:o0] + t[t0 : t1 + 1] + o[o1 + 1 :])


def _once(data: bytes, old: bytes, new: bytes) -> bytes:
    assert data.count(old) == 1, old
    return data.replace(old, new)


def _is_subsequence(small: list[bytes], big: list[bytes]) -> bool:
    it = iter(big)
    return all(any(line == other for other in it) for line in small)


def _paths(items: Any) -> set[str]:
    return {item.path for item in items}


def _doc(text: str) -> luadata.LuaDocument:
    return luadata.parse(text.encode("ascii"))


def _crlf(*lines: str) -> str:
    """A constructed document in the layout every Forever capture shows."""
    return "\r\n" + "\r\n".join(lines) + "\r\n"


def _snap(label: str) -> str:
    return str(json.loads(ok("snap", "create", "-m", label, "--json").stdout)["id"])


def _report(result: Any) -> dict[str, Any]:
    report = json.loads(result.stdout)
    assert isinstance(report, dict)
    return report


def _out(result: Any) -> str:
    return str(result.stdout) + str(result.stderr)


def _lost(data: bytes) -> bytes:
    """Constructed: what the addon writes after a load that found no probe."""
    return _once(data, b'["loads"] = 4,\r\n', b'["loads"] = 1,\r\n["lost"] = true,\r\n')


# Constructed edits of the first character's real file ("a later session").
_LOADS_5 = (b'["loads"] = 4,', b'["loads"] = 5,')
_FILTER = (b'["filter"] = 1,', b'["filter"] = 2,')
_EQUIPPED = (b'["equipped"] = 3.3125,', b'["equipped"] = 3.375,')


def _take_theirs_expected(out: bytes, *, probe_from: bytes = REAL_B) -> None:
    """The M11-03 pair merged two-way with every conflict taken from theirs
    (the second character) into ours (the first). `probe_from=REAL_A`: the
    `sv merge` of `WowLab.lua` keeps the target's probe (§13.4, owner ruling
    for M11-24); the library `merge` knows no file names and takes it."""
    expected = luadata.parse(REAL_B).to_python()
    nodes = expected["WowLabCharDB"]["talents"]["class"]["config"]["trees"][0]["nodes"]
    del nodes[52:]  # keys only theirs has are listed, not added (two-way)
    expected["WowLabCharDB"]["probe"] = luadata.parse(probe_from).to_python()["WowLabCharDB"][
        "probe"
    ]
    assert luadata.parse(out).to_python() == expected
    assert _key_order(out) == _key_order(REAL_A), "ours' key order, not one invented"
    assert b'["equipped"] = 2.375,\r\n' in out, "theirs' number text"
    assert out.startswith(b"\r\nWowLabCharDB = {\r\n") and out.endswith(b"}\r\n")
    assert b"\t" not in out and out.count(b"\n") == out.count(b"\r\n"), "ours' layout"


# ─── library: the three-way merge (constructed: no capture has a common base) ─


BASE3 = _crlf("DB = {", '["a"] = 1,', '["b"] = 1,', '["c"] = 1,', "}")


def test_three_way_takes_a_change_made_on_one_side_only_constructed() -> None:
    ours = _crlf("DB = {", '["a"] = 1,', '["b"] = 3,', '["c"] = 1,', "}")
    theirs = _crlf("DB = {", '["a"] = 2,', '["b"] = 1,', '["c"] = 1,', "}")
    result = _svmerge().merge(_doc(ours), _doc(theirs), base=_doc(BASE3))
    assert list(result.conflicts) == []
    assert _paths(result.taken) == {'DB["a"]'}, "theirs changed a; ours changed b and keeps it"
    expected = _crlf("DB = {", '["a"] = 2,', '["b"] = 3,', '["c"] = 1,', "}")
    assert luadata.serialize(result.document) == expected.encode()


def test_three_way_takes_the_same_change_once_constructed() -> None:
    # Both sides changed a the same way and both added n: one value each, no conflict.
    same = _crlf("DB = {", '["a"] = 2,', '["b"] = 1,', '["c"] = 1,', '["n"] = true,', "}")
    result = _svmerge().merge(_doc(same), _doc(same), base=_doc(BASE3))
    assert list(result.conflicts) == []
    out = luadata.serialize(result.document)
    assert out == same.encode()
    assert out.count(b'["n"]') == 1 and out.count(b'["a"]') == 1


def test_three_way_takes_a_key_theirs_added_constructed() -> None:
    theirs = _crlf("DB = {", '["a"] = 1,', '["b"] = 1,', '["c"] = 1,', '["d"] = "new",', "}")
    result = _svmerge().merge(_doc(BASE3), _doc(theirs), base=_doc(BASE3))
    assert list(result.conflicts) == []
    assert 'DB["d"]' in _paths(result.taken)
    merged = luadata.parse(luadata.serialize(result.document)).to_python()
    assert merged == {"DB": {"a": 1, "b": 1, "c": 1, "d": "new"}}


def test_three_way_different_changes_are_a_conflict_never_guessed_constructed() -> None:
    svmerge = _svmerge()
    ours = _crlf("DB = {", '["a"] = 2,', '["b"] = 1,', '["c"] = 1,', "}")
    theirs = _crlf("DB = {", '["a"] = 3,', '["b"] = 1,', '["c"] = 4,', "}")
    result = svmerge.merge(_doc(ours), _doc(theirs), base=_doc(BASE3))
    assert _paths(result.conflicts) == {'DB["a"]'}
    assert _paths(result.taken) == {'DB["c"]'}
    assert luadata.parse(luadata.serialize(result.document)).to_python() == {
        "DB": {"a": 2, "b": 1, "c": 4}
    }, "a conflict keeps ours until --take resolves it"
    by_theirs = svmerge.merge(_doc(ours), _doc(theirs), base=_doc(BASE3), take="theirs")
    assert _paths(by_theirs.conflicts) == {'DB["a"]'}, "a resolved conflict is still listed"
    assert (
        luadata.serialize(by_theirs.document)
        == _crlf("DB = {", '["a"] = 3,', '["b"] = 1,', '["c"] = 4,', "}").encode()
    )
    by_ours = svmerge.merge(_doc(ours), _doc(theirs), base=_doc(BASE3), take="ours")
    assert (
        luadata.serialize(by_ours.document)
        == _crlf("DB = {", '["a"] = 2,', '["b"] = 1,', '["c"] = 4,', "}").encode()
    )


def test_three_way_a_key_missing_from_theirs_is_absent_not_deleted_constructed() -> None:
    theirs = _crlf("DB = {", '["b"] = 1,', '["c"] = 1,', "}")
    result = _svmerge().merge(_doc(BASE3), _doc(theirs), base=_doc(BASE3))
    assert [(a.path, a.missing_from) for a in result.absent] == [('DB["a"]', "theirs")]
    assert list(result.conflicts) == []
    assert luadata.serialize(result.document) == BASE3.encode(), "ours keeps the key"


def test_three_way_key_limits_the_merge_to_that_subtree_constructed() -> None:
    # §13.4: "--key limits the merge to named subtrees". Inside DB.p the
    # three-way rules apply (x taken from theirs, y kept from ours: not a
    # whole copy); outside it, q changed differently on both sides and is
    # neither taken nor listed.
    base = _crlf("DB = {", '["p"] = {', '["x"] = 1,', '["y"] = 1,', "},", '["q"] = 1,', "}")
    ours = _crlf("DB = {", '["p"] = {', '["x"] = 1,', '["y"] = 2,', "},", '["q"] = 2,', "}")
    theirs = _crlf("DB = {", '["p"] = {', '["x"] = 3,', '["y"] = 1,', "},", '["q"] = 3,', "}")
    result = _svmerge().merge(_doc(ours), _doc(theirs), base=_doc(base), keys=["DB.p"])
    assert list(result.conflicts) == [] and list(result.absent) == []
    assert _paths(result.taken) == {'DB["p"]["x"]'}
    assert (
        luadata.serialize(result.document)
        == _crlf(
            "DB = {", '["p"] = {', '["x"] = 3,', '["y"] = 2,', "},", '["q"] = 2,', "}"
        ).encode()
    )


def test_merge_keeps_the_target_key_order_and_the_taken_number_text_constructed() -> None:
    # The client writes keys in its own hash order (M11-03 amendment): ours'
    # order stays, whatever order theirs has, and a taken number keeps its text.
    ours = _crlf("DB = {", '["b"] = 1,', '["a"] = 1,', '["c"] = 1,', "}")
    theirs = _crlf("DB = {", '["c"] = 1,', '["a"] = 2.50,', '["b"] = 1,', "}")
    result = _svmerge().merge(_doc(ours), _doc(theirs), base=_doc(BASE3))
    assert (
        luadata.serialize(result.document)
        == _crlf("DB = {", '["b"] = 1,', '["a"] = 2.50,', '["c"] = 1,', "}").encode()
    )


# ─── library: the two-way merge on the M11-03 two-character pair ─────────────


def test_two_way_real_pair_every_differing_key_is_a_conflict() -> None:
    conflicts, only_a, only_b = _two_way_oracle(REAL_A, REAL_B)
    assert (len(conflicts), only_a, only_b) == (235, set(), ONLY_IN_B), "the fixtures as indexed"
    assert _path("WowLabCharDB", "probe", "loads") in conflicts
    assert _path("WowLabCharDB", "schema") not in conflicts
    result = _svmerge().merge(luadata.parse(REAL_A), luadata.parse(REAL_B))
    assert _paths(result.conflicts) == conflicts
    assert len(result.conflicts) == len(conflicts), "each conflict listed once"
    assert {(a.path, a.missing_from) for a in result.absent} == {(p, "ours") for p in ONLY_IN_B}
    assert list(result.taken) == []
    assert luadata.serialize(result.document) == REAL_A, "nothing guessed, nothing changed"


def test_two_way_real_pair_keys_on_one_side_are_listed_from_either_direction() -> None:
    result = _svmerge().merge(luadata.parse(REAL_B), luadata.parse(REAL_A))
    assert {(a.path, a.missing_from) for a in result.absent} == {(p, "theirs") for p in ONLY_IN_B}
    assert luadata.serialize(result.document) == REAL_B, "absent is not deleted"


def test_two_way_real_pair_take_theirs_follows_the_target_layout() -> None:
    result = _svmerge().merge(luadata.parse(REAL_A), luadata.parse(REAL_B), take="theirs")
    _take_theirs_expected(luadata.serialize(result.document))


def test_key_copies_a_subtree_whole_between_the_real_pair() -> None:
    result = _svmerge().merge(
        luadata.parse(REAL_A), luadata.parse(REAL_B), keys=["WowLabCharDB.gear"]
    )
    assert list(result.conflicts) == [] and list(result.absent) == []
    assert _paths(result.taken) == {_path("WowLabCharDB", "gear")}
    assert luadata.serialize(result.document) == _splice(REAL_A, REAL_B, b'["gear"]')


def test_key_missing_from_the_source_is_reported_absent() -> None:
    # The first character has no 53rd class-tree node; copying it into the
    # second is reported, and nothing is deleted.
    missing = f"{_NODES}[53]"
    key = "WowLabCharDB.talents.class.config.trees[1].nodes[53]"
    result = _svmerge().merge(luadata.parse(REAL_B), luadata.parse(REAL_A), keys=[key])
    assert [(a.path, a.missing_from) for a in result.absent] == [(missing, "theirs")]
    assert list(result.taken) == []
    assert luadata.serialize(result.document) == REAL_B


def test_key_copy_within_one_real_account_file() -> None:
    src = f'{DBM_VAR}["Labchard Labrealmg"]'
    dst = f'{DBM_VAR}["Labcharb Labrealmf"]'
    doc = luadata.parse(REAL_DBM)
    result = _svmerge().merge(doc, doc, keys=[f"{src}={dst}"])
    assert list(result.conflicts) == [] and list(result.absent) == []
    assert _paths(result.taken) == {dst}
    out = luadata.serialize(result.document)
    before = luadata.parse(REAL_DBM).to_python()[DBM_VAR]
    after = luadata.parse(out).to_python()[DBM_VAR]
    assert after == {**before, "Labcharb Labrealmf": before["Labchard Labrealmg"]}
    assert _is_subsequence(REAL_DBM.split(b"\r\n"), out.split(b"\r\n")), "nothing else moved"
    assert b"\t" not in out and out.count(b"\n") == out.count(b"\r\n")


def test_a_copied_subtree_takes_the_target_documents_style_constructed() -> None:
    # Target: tab indentation and LF (the §4.2 retail form); source: the
    # Forever layout. The copy is laid out as the target is (§13.4).
    ours = '\nAddonDB = {\n\t["keep"] = 1,\n\t["profile"] = {\n\t\t["scale"] = 0.85,\n\t},\n}\n'
    theirs = _crlf(
        "AddonDB = {",
        '["keep"] = 2,',
        '["profile"] = {',
        '["scale"] = 1.25,',
        '["bars"] = {',
        '["main"] = true,',
        "},",
        "},",
        "}",
    )
    result = _svmerge().merge(_doc(ours), _doc(theirs), keys=["AddonDB.profile"])
    assert luadata.serialize(result.document) == (
        b'\nAddonDB = {\n\t["keep"] = 1,\n\t["profile"] = {\n\t\t["scale"] = 1.25,\n'
        b'\t\t["bars"] = {\n\t\t\t["main"] = true,\n\t\t},\n\t},\n}\n'
    )


# ─── CLI: two characters (two-way) ───────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_cli_two_characters_conflicts_are_listed_and_nothing_is_written(root: Path) -> None:
    before = _state(root)
    text = run("sv", "merge", LAB, "--from", CHAR_B, "--into", CHAR_A, "--yes")
    assert text.exit_code == 1, _out(text)
    listed = sorted(p for p in _two_way_oracle(REAL_A, REAL_B)[0] if not _under_probe(p))
    assert all(p in _out(text) for p in listed[:5]), "the conflicts are listed"
    assert _path("WowLabCharDB", "probe", "loads") not in _out(text), "the probe is no conflict"
    assert PROBE_NOTE in _out(text)
    result = run("sv", "merge", LAB, "--from", CHAR_B, "--into", CHAR_A, "--yes", "--json")
    assert result.exit_code == 1, _out(result)
    cli.SvMergeReport.model_validate_json(result.stdout)
    report = _report(result)
    conflicts, _, _ = _two_way_oracle(REAL_A, REAL_B)
    assert report["mode"] == "two-way"
    # The probe differs between any two characters; M11-24 keeps it out of the conflicts.
    assert {c["path"] for c in report["conflicts"]} == {p for p in conflicts if not _under_probe(p)}
    assert report["notes"].count(PROBE_NOTE) == 1
    assert {(a["path"], a["missing_from"]) for a in report["absent"]} == {
        (p, "ours") for p in ONLY_IN_B
    }
    assert report["written"] is False
    assert _state(root) == before
    assert guard.history() == ()


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_cli_take_theirs_writes_only_the_target_through_guard(root: Path, flavor: Path) -> None:
    result = run(
        "sv", "merge", LAB, "--from", CHAR_B, "--into", CHAR_A, "--take", "theirs", "--yes"
    )
    assert result.exit_code == 0, _out(result)
    # The probe stays the target's (M11-24T, superseding the M11-09T copy).
    _take_theirs_expected((flavor / LAB_A).read_bytes(), probe_from=REAL_A)
    assert (flavor / LAB_B).read_bytes() == REAL_B, "the source is only read"
    (record,) = guard.history()
    assert record.state == "committed"
    assert [p.path for p in record.paths] == [LAB_A]
    ok("undo", "--yes")
    assert (flavor / LAB_A).read_bytes() == REAL_A


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_cli_take_ours_keeps_the_target_and_lists_the_conflicts(flavor: Path) -> None:
    result = run(
        "sv", "merge", LAB, "--from", CHAR_B, "--into", CHAR_A,
        "--take", "ours", "--yes", "--json",
    )  # fmt: skip
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    conflicts, _, _ = _two_way_oracle(REAL_A, REAL_B)
    assert {c["path"] for c in report["conflicts"]} == {
        p for p in conflicts if not _under_probe(p)
    }, "resolved, still listed; the probe is no conflict (M11-24)"
    assert report["notes"].count(PROBE_NOTE) == 1
    assert (flavor / LAB_A).read_bytes() == REAL_A
    assert (flavor / LAB_B).read_bytes() == REAL_B


def test_cli_key_copies_a_subtree_between_the_real_pair(flavor: Path) -> None:
    result = run(
        "sv", "merge", LAB, "--from", CHAR_B, "--into", CHAR_A,
        "--key", "WowLabCharDB.gear", "--yes", "--json",
    )  # fmt: skip
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["mode"] == "two-way" and report["written"] is True
    assert report["conflicts"] == []
    assert (flavor / LAB_A).read_bytes() == _splice(REAL_A, REAL_B, b'["gear"]')


def test_cli_merge_is_refused_while_the_client_runs(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _state(root)
    _running(monkeypatch, flavor)
    result = run(
        "sv", "merge", LAB, "--from", CHAR_B, "--into", CHAR_A, "--take", "theirs", "--yes"
    )
    assert result.exit_code == 3, _out(result)
    assert "refused by the write gate" in result.stderr
    assert _state(root) == before
    assert guard.history() == ()


@pytest.mark.parametrize(
    "file",
    [DBM_NAME, LAB_ACCOUNT],
    ids=["account-only-name", "account-path-of-a-name-both-scopes-have"],
)
def test_cli_account_wide_file_is_refused_character_to_character(root: Path, file: str) -> None:
    before = _state(root)
    result = run("sv", "merge", file, "--from", CHAR_B, "--into", CHAR_A, "--yes")
    assert result.exit_code == 2, _out(result)
    assert "account-wide" in result.stderr
    assert _state(root) == before
    assert guard.history() == ()


# ─── CLI: a snapshot as the source (three-way with a base) ───────────────────


def test_cli_three_way_with_a_snapshot_base_constructed(flavor: Path) -> None:
    # Base: the real file. Theirs (a snapshot): a later session (loads 5)
    # that changed the currency filter. Ours (the disk): the same later
    # session (loads 5) with another average. Loads goes up across the two
    # snapshots, so the loader check passes.
    target = flavor / LAB_A
    base_id = _snap("base")
    theirs = _once(_once(REAL_A, *_LOADS_5), *_FILTER)
    target.write_bytes(theirs)
    theirs_id = _snap("theirs")
    ours = _once(_once(REAL_A, *_LOADS_5), *_EQUIPPED)
    target.write_bytes(ours)
    result = run(
        "sv", "merge", LAB, "--from", theirs_id, "--base", base_id, "--into", CHAR_A,
        "--yes", "--json",
    )  # fmt: skip
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["mode"] == "three-way" and report["written"] is True
    assert report["conflicts"] == []
    assert _path("WowLabCharDB", "currencies", "filter") in {t["path"] for t in report["taken"]}
    assert target.read_bytes() == _once(ours, *_FILTER)


def test_cli_account_wide_file_from_a_snapshot_constructed(flavor: Path) -> None:
    # §13.4: the account-wide case is `--from <snapshot>` (an earlier state).
    # The disk then changes one value inside ["Unknown"] (constructed edit);
    # copying that subtree back from the snapshot restores the real bytes.
    snap_id = _snap("before")
    target = flavor / DBM
    at = REAL_DBM.index(b'["Enabled"] = true,', REAL_DBM.index(b'\r\n["Unknown"] = {'))
    edited = REAL_DBM[:at] + b'["Enabled"] = false,' + REAL_DBM[at + len(b'["Enabled"] = true,') :]
    target.write_bytes(edited)
    result = run(
        "sv", "merge", DBM_NAME, "--from", snap_id, "--into", CHAR_A,
        "--key", f'{DBM_VAR}["Unknown"]', "--yes",
    )  # fmt: skip
    assert result.exit_code == 0, _out(result)
    assert target.read_bytes() == REAL_DBM


# ─── CLI: --key within one file ──────────────────────────────────────────────


def test_cli_key_copy_within_one_account_file(flavor: Path) -> None:
    src = f'{DBM_VAR}["Labchard Labrealmg"]'
    dst = f'{DBM_VAR}["Labcharb Labrealmf"]'
    result = run("sv", "merge", DBM_NAME, "--into", CHAR_A, "--key", f"{src}={dst}", "--yes")
    assert result.exit_code == 0, _out(result)
    out = (flavor / DBM).read_bytes()
    before = luadata.parse(REAL_DBM).to_python()[DBM_VAR]
    assert luadata.parse(out).to_python()[DBM_VAR] == {
        **before,
        "Labcharb Labrealmf": before["Labchard Labrealmg"],
    }
    assert _is_subsequence(REAL_DBM.split(b"\r\n"), out.split(b"\r\n"))
    (record,) = guard.history()
    assert [p.path for p in record.paths] == [DBM]


# ─── CLI: the loader check (§13.4, the lab-addon's probe from §13.1) ─────────


def _copy_within(*extra: str) -> Any:
    """A merge that would succeed: a `--key` copy inside the account file,
    checked against the first character's `WowLab.lua`."""
    key = f'{DBM_VAR}["Labchard Labrealmg"]={DBM_VAR}["Labcharb Labrealmf"]'
    return run("sv", "merge", DBM_NAME, "--into", CHAR_A, "--key", key, "--yes", *extra)


def _loads_went_down(flavor: Path) -> None:
    """Two snapshots of the first character's `WowLab.lua`: 4, then 2 (the
    second character's real file standing in for a session whose load failed)."""
    _snap("loads 4")
    (flavor / LAB_A).write_bytes(REAL_B)
    _snap("loads 2")


def _loads_equal_bytes_differ(flavor: Path) -> None:
    """Constructed: two snapshots of the first character's `WowLab.lua`, both
    at `loads` 4 but with different bytes (the client wrote in between and the
    counter did not go up: what a persisting loader bug leaves, N, 1, 1, …)."""
    _snap("loads 4")
    (flavor / LAB_A).write_bytes(_once(REAL_A, *_FILTER))
    _snap("loads 4 again, rewritten")


NO_LOGIN_NOTE = "no login between the two snapshots; the loader was not re-checked"


def test_cli_loader_check_refuses_when_probe_lost_is_true_constructed(
    root: Path, flavor: Path
) -> None:
    (flavor / LAB_A).write_bytes(_lost(REAL_A))
    before = _state(root)
    result = _copy_within()
    assert result.exit_code == 3, _out(result)
    assert "probe.lost" in result.stderr
    assert _state(root) == before
    assert guard.history() == ()


def test_cli_loader_check_refuses_when_two_snapshots_show_loads_not_going_up(
    root: Path, flavor: Path
) -> None:
    _loads_went_down(flavor)
    before = _state(root)
    result = _copy_within()
    assert result.exit_code == 3, _out(result)
    assert "loads" in result.stderr
    assert _state(root) == before
    assert guard.history() == ()


def test_cli_loader_check_passes_when_loads_goes_up(flavor: Path) -> None:
    (flavor / LAB_A).write_bytes(REAL_B)  # loads 2
    _snap("loads 2")
    (flavor / LAB_A).write_bytes(REAL_A)  # loads 4
    _snap("loads 4")
    result = _copy_within()
    assert result.exit_code == 0, _out(result)
    assert (flavor / DBM).read_bytes() != REAL_DBM


def test_cli_loader_check_refuses_equal_loads_when_the_file_changed_constructed(
    root: Path, flavor: Path
) -> None:
    _loads_equal_bytes_differ(flavor)
    before = _state(root)
    result = _copy_within()
    assert result.exit_code == 3, _out(result)
    assert "loads" in result.stderr
    assert _state(root) == before
    assert guard.history() == ()


def test_cli_loader_check_passes_byte_identical_snapshots_with_a_note(flavor: Path) -> None:
    # Two snapshots with nothing written in between: equal loads says nothing.
    _snap("first")
    _snap("second, no login since")
    result = _copy_within("--json")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    assert any(NO_LOGIN_NOTE in note for note in report["notes"])
    assert (flavor / DBM).read_bytes() != REAL_DBM


@pytest.mark.parametrize(
    "cause", ["probe-lost-constructed", "loads-went-down", "loads-equal-constructed"]
)
def test_cli_force_loader_check_overrides_the_refusal(flavor: Path, cause: str) -> None:
    if cause == "probe-lost-constructed":
        (flavor / LAB_A).write_bytes(_lost(REAL_A))
    elif cause == "loads-went-down":
        _loads_went_down(flavor)
    else:
        _loads_equal_bytes_differ(flavor)
    result = _copy_within("--force-loader-check")
    assert result.exit_code == 0, _out(result)
    assert (flavor / DBM).read_bytes() != REAL_DBM
    (record,) = guard.history()
    assert [p.path for p in record.paths] == [DBM]


def test_cli_loader_check_warns_and_continues_without_a_capture_constructed(
    flavor: Path,
) -> None:
    (flavor / LAB_A).unlink()
    result = _copy_within("--json")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    assert any(LAB in note for note in report["notes"])
    assert (flavor / DBM).read_bytes() != REAL_DBM
    text = run(
        "sv", "merge", DBM_NAME, "--into", CHAR_A,
        "--key", f'{DBM_VAR}["Labchard Labrealmg"]={DBM_VAR}["Labcharb Labrealmd"]', "--yes",
    )  # fmt: skip
    assert text.exit_code == 0, _out(text)
    assert LAB in _out(text), "the text output carries the same warning"
