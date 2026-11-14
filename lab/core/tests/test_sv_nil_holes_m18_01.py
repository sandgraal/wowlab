"""`sv dump`, `sv list --unreadable` and `sv merge` on a positional `nil`
hole (M18-01T; `docs/LAB_FORMATS.md` §4.1, amendment of 2026-11-14).

Written before the implementation (ADR-0013: the readers stand on
`luadata.py`, which is load-bearing). Every grader of behaviour that does
not exist yet carries one `@XFAIL` line, which M18-01 deletes and does not
otherwise edit; the graders without it pass today and must keep passing.

What is graded: the readers that walk values treat the hole as a hole, not
an error. `sv dump` shows it at its own index, `[n]` as §13.4 spells a
positional entry, as a value with no content (`nil`; `LuaNilNode` in
`--json`), and the entries after it keep their numbers.
`sv list --unreadable` does not list a file for its holes, while a keyed
`nil` is still listed as refused with its line and column. `sv merge`
(§13.4 as amended by M16-12) reads two files with the hole and reports what
differs at the right index: the array part is keys `1..n` with all present,
so in `filters` (`false` x4, hole, `true` x7) it is `1..4`, and keys 6 to 12
are merged as keys; a side that fills the hole makes the array part `1..12`,
one list whose conflict names the gap.

The install is built in `tmp_path`: the launch client's root files from the
M16-05 capture (`fixtures/macos-70888/.build.info` and `.flavor.info`) and
the two real files of the M18-01T capture (`fixtures/macos-70938/`, rows in
`fixtures/README.md`, L8), in the layout they were captured in. Files with
`constructed` in the test id add an edited copy of a real file (one value
changed, or the hole filled) or a keyed `nil`. The user data directory is
redirected and the process table is a fake with no client; nothing reads or
writes a real install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, luadata

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROOT_FILES = FIXTURES / "macos-70888"  # the launch client's .build.info / .flavor.info
CAPTURE = FIXTURES / "macos-70938" / "forever"
FLAVOR = "_forever_"  # the provenance rows' flavor folder; tests may name it (L6)
ACCOUNT = "LABACCOUNTA"
ACCT = f"WTF/Account/{ACCOUNT}"
CHARACTER = "Sandgraal-Rizzen"
OTHER = "Labother-Rizzen"  # constructed: a second character folder for the merge
AH_NAME = "Blizzard_AuctionHouseUI.lua"
AH = f"{ACCT}/1/{CHARACTER}/SavedVariables/{AH_NAME}"
AH_OTHER = f"{ACCT}/1/{OTHER}/SavedVariables/{AH_NAME}"
CL = f"{ACCT}/SavedVariables/Blizzard_CombatLog.lua"
KEYED = f"{ACCT}/SavedVariables/KeyedNil.lua"

REAL_AH = (CAPTURE / AH).read_bytes()
REAL_CL = (CAPTURE / CL).read_bytes()
FILTERS = 'g_auctionHouseFilters["filters"]'

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    flavor = root / FLAVOR
    flavor.mkdir(parents=True)
    shutil.copy2(ROOT_FILES / ".build.info", root / ".build.info")
    shutil.copy2(ROOT_FILES / "forever" / ".flavor.info", flavor / ".flavor.info")
    for rel in (AH, CL):
        (flavor / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CAPTURE / rel, flavor / rel)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return flavor


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args), terminal_width=80)


def ok(*args: str) -> str:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return str(result.stdout)


def _out(result: Any) -> str:
    return str(result.stdout) + str(result.stderr)


def _once(data: bytes, old: bytes, new: bytes) -> bytes:
    assert data.count(old) == 1, old
    return data.replace(old, new)


def _state(root: Path) -> dict[str, bytes | None]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None
        for p in sorted(root.rglob("*"))
    }


# ── sv dump ─────────────────────────────────────────────────────────────────


def test_sv_dump_json_shows_the_hole_at_its_index(flavor: Path) -> None:
    report = cli.SvDumpReport.model_validate_json(ok("sv", "dump", AH, "--json"))
    by_id = {v.id: v for v in report.values}
    (filters,) = [
        v for v in report.values if isinstance(v.key, cli.LuaStringNode) and v.key.text == "filters"
    ]
    assert isinstance(filters.value, cli.LuaTableNode)
    assert filters.value.entries == 12, "the hole is an entry; nothing is dropped"
    rows = [v for v in report.values if v.parent == filters.id]
    assert [v.style for v in rows] == ["positional"] * 12
    assert [v.position for v in rows] == list(range(1, 13)), "the entries after it keep theirs"
    assert [v.value.type for v in rows] == ["boolean"] * 4 + ["nil"] + ["boolean"] * 7
    assert isinstance(rows[4].value, cli.LuaNilNode)
    assert [getattr(v.value, "value", None) for v in rows] == [False] * 4 + [None] + [True] * 7
    assert all(v.duplicate is False for v in rows)
    assert by_id[filters.parent].name == "g_auctionHouseFilters"  # type: ignore[index]


def test_sv_dump_text_prints_the_hole_and_the_next_index(flavor: Path) -> None:
    lines = ok("sv", "dump", AH).splitlines()
    assert f"{FILTERS}[4] = false" in lines
    assert f"{FILTERS}[5] = nil" in lines
    assert f"{FILTERS}[6] = true" in lines
    assert f"{FILTERS}[12] = true" in lines
    assert not [ln for ln in lines if ln.startswith(f"{FILTERS}[13]")]


def test_sv_dump_path_after_a_hole_names_the_lua_index(flavor: Path) -> None:
    """`[4]` of `schoolColoring` is the colour table after the hole at `[3]`."""
    path = 'Blizzard_CombatLog_Filters["filters"][1]["colors"]["schoolColoring"][4]["g"]'
    assert ok("sv", "dump", CL, "--path", path) == f"{path} = 0.5\n"
    before = 'Blizzard_CombatLog_Filters["filters"][2]["colors"]["schoolColoring"][2]["g"]'
    assert ok("sv", "dump", CL, "--path", before) == f"{before} = 0.9\n"


def test_sv_dump_json_of_the_combat_log_has_two_nil_rows(flavor: Path) -> None:
    report = cli.SvDumpReport.model_validate_json(ok("sv", "dump", CL, "--json"))
    nils = [v for v in report.values if isinstance(v.value, cli.LuaNilNode)]
    assert [(v.style, v.position) for v in nils] == [("positional", 3), ("positional", 3)]
    for v in nils:
        siblings = [s for s in report.values if s.parent == v.parent and s.style == "positional"]
        assert [s.position for s in siblings] == [1, 2, 3, 4]


# ── sv list --unreadable ────────────────────────────────────────────────────


def _unreadable() -> cli.SvListReport:
    return cli.SvListReport.model_validate_json(ok("sv", "list", "--unreadable", "--json"))


def test_sv_list_unreadable_does_not_list_the_game_files_with_holes(flavor: Path) -> None:
    report = _unreadable()
    assert report.unreadable is True
    assert report.files == [] and report.read_failures == []
    every = cli.SvListReport.model_validate_json(ok("sv", "list", "--json"))
    assert sorted(f.path for f in every.files) == sorted([AH, CL])
    assert ok("sv", "list", "--unreadable").splitlines()[0] == (
        "wowlab read all 2 SavedVariables files as saved data."
    )


def _keyed_nil(flavor: Path) -> None:
    """Constructed: a keyed `nil` inside a table, still refused (§4.1)."""
    (flavor / KEYED).write_bytes(b'\r\nKeyedNil = {\r\n["k"] = nil,\r\n}\r\n')


def test_sv_list_unreadable_lists_a_keyed_nil_with_its_position_constructed(
    flavor: Path,
) -> None:
    """Positive control, true today and after M18-01: the check still
    refuses, at the keyed `nil`."""
    _keyed_nil(flavor)
    report = _unreadable()
    (failure,) = [f for f in report.read_failures if f.path == KEYED]
    assert (failure.kind, failure.line, failure.column) == ("refused", 3, 9)


def test_sv_list_unreadable_lists_only_the_keyed_nil_constructed(flavor: Path) -> None:
    _keyed_nil(flavor)
    report = _unreadable()
    assert [f.path for f in report.files] == [KEYED]
    assert [(f.path, f.line, f.column) for f in report.read_failures] == [(KEYED, 3, 9)]


# ── sv merge ────────────────────────────────────────────────────────────────


def _other(flavor: Path, data: bytes) -> None:
    target = flavor / AH_OTHER
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _merge(*extra: str) -> Any:
    return run("sv", "merge", AH_NAME, "--from", OTHER, "--into", CHARACTER, *extra)


def test_sv_merge_two_files_with_the_same_hole_finds_nothing_to_change(flavor: Path) -> None:
    _other(flavor, REAL_AH)
    before = _state(flavor.parent)
    result = _merge("--yes", "--json")
    assert result.exit_code == 0, _out(result)
    report = json.loads(result.stdout)
    cli.SvMergeReport.model_validate(report)
    assert report["conflicts"] == [] and report["absent"] == [] and report["taken"] == []
    assert report["written"] is False
    assert _state(flavor.parent) == before


def test_sv_merge_conflict_after_a_hole_is_at_the_lua_index_constructed(flavor: Path) -> None:
    """Theirs differs at index 6 only. The array part is `1..4` on both
    sides (the hole is the first missing key), so `[6]` is merged as a key:
    one conflict at `[6]`, not at `[5]` and not the whole list."""
    _other(flavor, _once(REAL_AH, b"nil,\r\ntrue,\r\n", b"nil,\r\nfalse,\r\n"))
    before = _state(flavor.parent)
    result = _merge("--yes", "--json")
    assert result.exit_code == 1, _out(result)
    report = json.loads(result.stdout)
    cli.SvMergeReport.model_validate(report)
    assert [(c["path"], c["ours"], c["theirs"]) for c in report["conflicts"]] == [
        (f"{FILTERS}[6]", "true", "false")
    ]
    assert report["written"] is False
    assert _state(flavor.parent) == before


def test_sv_merge_a_filled_hole_is_one_list_conflict_naming_the_gap_constructed(
    flavor: Path,
) -> None:
    """Theirs holds `true` where ours has the hole: theirs' border is 12,
    so keys `1..12` are one list (§13.4, M16-12), and ours counts only its
    real entries and names the gap."""
    _other(flavor, _once(REAL_AH, b"nil,\r\n", b"true,\r\n"))
    result = _merge("--yes", "--json")
    assert result.exit_code == 1, _out(result)
    report = json.loads(result.stdout)
    cli.SvMergeReport.model_validate(report)
    [conflict] = report["conflicts"]
    assert conflict["path"] == FILTERS
    assert "11 entries" in conflict["ours"]
    assert "a gap at [5]" in conflict["ours"]
    assert "a gap" not in conflict["theirs"]


def test_svmerge_library_reads_the_hole_on_both_sides() -> None:
    """`svmerge.merge` on two parsed copies of the real file: nothing
    differs, and the merged document is the file's own bytes."""
    from wowlab_core import svmerge

    ours = luadata.parse(REAL_AH)
    theirs = luadata.parse(REAL_AH)
    result = svmerge.merge(ours, theirs)
    assert list(result.conflicts) == [] and list(result.absent) == []
    assert list(result.taken) == []
    assert luadata.serialize(result.document) == REAL_AH
