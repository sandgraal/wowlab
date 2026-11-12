"""`wowlab char history` (M17-02, docs/LAB_PLAN.md §19 CW-2) through Typer's
runner, over a snapshot store built in `tmp_path`.

The install is the `macos/` capture copied into `tmp_path` with its flavor
folder named as its provenance row says (`_classic_beta_`). One character
folder, `1/Labchard-Labrealmg`, is given a sequence of real lab-addon
records, one per snapshot, each with its own modification time:

1. `macos/`'s record of this character (schema 1, client 70009);
2. `macos-70058/`'s record of this character (schema 1, client 70058),
   snapshotted twice with the same save time;
3. `macos-70425/`'s record of `Labchard-Labrealme` (schema 3);
4. `macos-70450/`'s record of `Dangling-Participle` (schema 4), whose stored
   object is then deleted, so that snapshot's copy cannot be read;
5. live: `macos-70450/`'s record of `Sangraal-Sett` (schema 4), not in any
   snapshot.

The records are real captures; putting the records of other character
folders in this one, so one folder walks through three schemas, is the
constructed part of the sequence. Inputs labelled `constructed` are boundary
cases (L8): a record of an unknown schema, a damaged manifest. Nothing here
reads or writes a real install: `WOWLAB_WOW_ROOT` points at the copy and the
user data and config directories are redirected into `tmp_path`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import charhistory, cli, install, snapshot

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance rows' flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
CHAR = "1/Labchard-Labrealmg"
SV = "SavedVariables/WowLab.lua"


def _record(tree: str, character: str) -> bytes:
    return (FIXTURES / tree / "forever" / "WTF" / "Account" / ACCOUNT / character / SV).read_bytes()


R1 = ("macos", "1/Labchard-Labrealmg")
R2 = ("macos-70058", "1/Labchard-Labrealmg")
R3 = ("macos-70425", "1/Labchard-Labrealme")
R_LOST = ("macos-70450", "1/Dangling-Participle")
R_LIVE = ("macos-70450", "1/Sangraal-Sett")

T1 = 1_700_000_000_000_000_000
T2 = T1 + 86_400 * 10**9
T3 = T2 + 86_400 * 10**9
T_LOST = T3 + 3_600 * 10**9
T_LIVE = T3 + 86_400 * 10**9

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "userdata" / "wowlab"
    config = tmp_path / "userconfig" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config)
    return data


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos" / "forever", root / FLAVOR)
    for p in (root / FLAVOR).rglob("WowLab.lua"):
        os.utime(p, ns=(T1 - 10**9, T1 - 10**9))
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def _lab_file(root: Path) -> Path:
    return root / FLAVOR / "WTF" / "Account" / ACCOUNT / CHAR / SV


def _rel() -> str:
    return f"{FLAVOR}/WTF/Account/{ACCOUNT}/{CHAR}/{SV}"


def _put(root: Path, data: bytes, mtime_ns: int) -> None:
    path = _lab_file(root)
    path.write_bytes(data)
    os.utime(path, ns=(mtime_ns, mtime_ns))


def _snap(root: Path, day: int) -> snapshot.Manifest:
    return snapshot.SnapshotStore().create(
        root,
        [f"{FLAVOR}/WTF"],
        flavor_folder=FLAVOR,
        now=datetime(2026, 11, day, 12, 0, 0, tzinfo=UTC),
    )


def _tree_state(root: Path) -> dict[str, tuple[str, int]]:
    return {
        str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The install and store described in the module docstring."""
    root = _install(tmp_path, monkeypatch)
    _put(root, _record(*R1), T1)
    s1 = _snap(root, 1)
    _put(root, _record(*R2), T2)
    s2 = _snap(root, 2)
    s2b = _snap(root, 3)  # the same save, snapshotted again
    _put(root, _record(*R3), T3)
    s3 = _snap(root, 4)
    _put(root, _record(*R_LOST), T_LOST)
    lost = _snap(root, 5)
    store = snapshot.SnapshotStore()
    entry = lost.entry(_rel())
    assert entry is not None and entry.sha256 is not None
    store.object_path(entry.sha256).unlink()
    _put(root, _record(*R_LIVE), T_LIVE)
    return {"root": root, "ids": [s1.id, s2.id, s2b.id, s3.id, lost.id], "lost": lost.id}


def _run(*args: str) -> Any:
    return runner.invoke(cli.app, ["char", "history", "--character", CHAR, *args])


def _json(*args: str) -> dict[str, Any]:
    result = _run("--json", *args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    data: dict[str, Any] = json.loads(result.stdout)
    return data


def test_one_row_per_save_oldest_first_across_a_schema_change(history: dict[str, Any]) -> None:
    data = _json()
    rows = data["rows"]
    assert [r["saved_at_ns"] for r in rows] == [T1, T2, T3, T_LIVE]
    assert [r["record_schema"] for r in rows] == [1, 1, 3, 4]
    s1, s2, s2b, s3, _ = history["ids"]
    assert rows[0]["snapshots"] == [s1]
    assert rows[1]["snapshots"] == [s2, s2b], "the same save time is one row"
    assert rows[2]["snapshots"] == [s3]
    assert rows[3]["snapshots"] == [] and rows[3]["live"] is True
    assert not any(r["live"] for r in rows[:3])
    assert data["snapshots_searched"] == 5 and data["snapshots_holding"] == 5
    assert data["character"] == CHAR and data["flavor_folder"] == FLAVOR


def test_an_unreadable_copy_is_listed_and_does_not_stop_the_others(
    history: dict[str, Any],
) -> None:
    data = _json()
    assert len(data["rows"]) == 4
    [bad] = data["unreadable"]
    assert bad["where"] == "snapshot" and bad["snapshot_id"] == history["lost"]
    assert bad["saved_at_ns"] == T_LOST
    assert "missing" in bad["reason"]
    text = _run()
    assert text.exit_code == 0, text.stdout
    assert "Could not read 1 copy (the others were read):" in text.stdout
    assert f"snapshot {history['lost']}: its stored copy cannot be used" in text.stdout


def test_rows_carry_level_item_level_and_spec_as_each_record_holds_them(
    history: dict[str, Any],
) -> None:
    from wowlab_core import dressing, gear, labaddon

    rows = _json()["rows"]
    for row, rec in zip(rows, (R1, R2, R3, R_LIVE), strict=True):
        record = labaddon.parse_char(_record(*rec))
        assert row["level"] == dressing.level_of(record)
        section = record.gear
        assert isinstance(section, labaddon.Gear)
        assert row["item_level"] == gear.average_of(section).equipped
        spec = gear.spec_of(record)
        assert row["spec"]["id"] == spec.id
        client = record.client
        assert isinstance(client, labaddon.Client)
        assert row["client_version"] == client.version


def _slots(rec: tuple[str, str]) -> dict[int, tuple[str, int | None]]:
    from wowlab_core import gear, labaddon

    record = labaddon.parse_char(_record(*rec))
    section = record.gear
    assert isinstance(section, labaddon.Gear)
    filled, _ = gear.rows(section, gear.shipped_slot_table())
    return {s.slot: (s.raw_link, s.item_level) for s in filled}


def test_gear_changes_are_the_slots_whose_item_differs_from_the_save_before(
    history: dict[str, Any],
) -> None:
    rows = _json()["rows"]
    assert rows[0]["changed_from_ns"] is None
    assert rows[0]["gear_not_compared"] == charhistory.NOT_COMPARED_FIRST
    pairs = [(R1, R2), (R2, R3), (R3, R_LIVE)]
    for row, (a, b) in zip(rows[1:], pairs, strict=True):
        before, after = _slots(a), _slots(b)
        expected = sorted(s for s in set(before) | set(after) if before.get(s) != after.get(s))
        assert [g["slot"] for g in row["gear_changes"]] == expected
        assert row["gear_not_compared"] is None
        for g in row["gear_changes"]:
            if g["after"] is not None:
                assert (g["after"]["link"], g["after"]["item_level"]) == after[g["slot"]]
                assert g["after"]["name"] and "|" not in g["after"]["name"]
            if g["before"] is not None:
                assert (g["before"]["link"], g["before"]["item_level"]) == before[g["slot"]]
    assert rows[1]["changed_from_ns"] == T1
    assert any(r["gear_changes"] for r in rows[1:]), "the records differ in gear"


def _currencies(rec: tuple[str, str]) -> dict[int, int | None]:
    from wowlab_core import labaddon

    section = labaddon.parse_char(_record(*rec)).currencies
    if not isinstance(section, labaddon.Currencies):
        return {}
    return {c.id: c.quantity for c in section.list_ if isinstance(c, labaddon.Currency)}


def test_currency_changes_are_the_quantities_that_differ(history: dict[str, Any]) -> None:
    rows = _json()["rows"]
    pairs = [(R1, R2), (R2, R3), (R3, R_LIVE)]
    for row, (a, b) in zip(rows[1:], pairs, strict=True):
        before, after = _currencies(a), _currencies(b)
        expected = {
            cid: (before.get(cid), after.get(cid))
            for cid in set(before) | set(after)
            if before.get(cid) != after.get(cid)
        }
        got = {c["id"]: (c["before"], c["after"]) for c in row["currency_changes"]}
        assert got == expected


def test_text_has_one_head_line_per_save_and_names_the_changes(history: dict[str, Any]) -> None:
    result = _run()
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert "4 saves, from 5 of 5 snapshots and the file in the install, oldest first:" in out
    assert out.count("  from ") == 4
    assert "the oldest record wowlab has" in out
    assert "  gear: slot " in out
    assert "the file in the install" in out
    assert out.rstrip().splitlines()[-1].endswith("--notes shows them")
    for internal in ("M17", "charhistory", "HistoryRow", "[verify]", "docs/"):
        assert internal not in out, internal


def test_reads_only(history: dict[str, Any]) -> None:
    root = history["root"]
    before = _tree_state(root)
    store_before = _tree_state(snapshot.SnapshotStore().path)
    assert _run().exit_code == 0
    assert _run("--json").exit_code == 0
    assert _tree_state(root) == before
    assert _tree_state(snapshot.SnapshotStore().path) == store_before


def test_snapshot_rows_come_from_the_store_not_the_install(history: dict[str, Any]) -> None:
    """The install's file is the newest save; the older rows can only have
    come from the store's objects."""
    rows = _json()["rows"]
    assert rows[0]["record_schema"] == 1 and rows[0]["live"] is False
    _put(history["root"], _record(*R_LIVE), T_LIVE + 10**9)
    rows = _json()["rows"]
    assert [r["saved_at_ns"] for r in rows] == [T1, T2, T3, T_LIVE + 10**9]


def test_with_no_snapshots_the_live_file_is_one_row_and_the_caveat_says_how(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install(tmp_path, monkeypatch)
    _put(root, _record(*R1), T1)
    data = _json()
    assert len(data["rows"]) == 1 and data["rows"][0]["live"] is True
    assert data["snapshots_searched"] == 0 and data["unreadable"] == []
    out = _run().stdout
    assert "1 save, from 0 of 0 snapshots" in out
    assert "take a snapshot after you play" in out


def test_constructed_unknown_schema_in_one_snapshot_is_listed_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install(tmp_path, monkeypatch)
    _put(root, b'WowLabCharDB = {\n["schema"] = 99,\n}\n', T1)
    bad = _snap(root, 1)
    _put(root, _record(*R1), T2)
    _snap(root, 2)
    data = _json()
    assert [r["saved_at_ns"] for r in data["rows"]] == [T2]
    [copy] = data["unreadable"]
    assert copy["snapshot_id"] == bad.id
    assert "schema 99" in copy["reason"]


def test_constructed_damaged_manifest_is_listed_by_name(
    history: dict[str, Any],
) -> None:
    store = snapshot.SnapshotStore()
    (store.manifests_dir / "20261106T000000000000Z-deadbeef.json").write_bytes(b"{not json")
    data = _json()
    assert len(data["rows"]) == 4
    [damaged] = data["damaged_snapshots"]
    assert damaged["name"] == "20261106T000000000000Z-deadbeef.json"
    out = _run().stdout
    assert "snapshot file 20261106T000000000000Z-deadbeef.json does not load" in out


def test_constructed_live_file_unreadable_and_no_snapshots_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install(tmp_path, monkeypatch)
    _put(root, b"WowLabCharDB = {\n", T1)
    result = _run("--json")
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["rows"] == []
    [copy] = data["unreadable"]
    assert copy["where"] == "install" and copy["snapshot_id"] is None


# Every committed record's currency panel list is empty (fixture index, the
# CurrencyTypes row), so a change in quantities is constructed: R2's bytes
# with its empty list replaced by one that names three currencies.
_EMPTY_CURRENCIES = b'["list"] = {\n},\n["filtered"] = true,\n["rows"] = 0,'


def _lf(rec: tuple[str, str]) -> bytes:
    """The record with its CRLF line ends as LF, so the edits below can name
    them one way (the client writes CRLF; the reader takes either)."""
    return _record(*rec).replace(b"\r\n", b"\n")


def _with_currencies(rec: tuple[str, str], quantities: dict[int, int]) -> bytes:
    data = _lf(rec)
    assert data.count(_EMPTY_CURRENCIES) == 1
    listed = b"".join(
        b'{\n["id"] = %d,\n["quantity"] = %d,\n},\n' % (cid, qty) for cid, qty in quantities.items()
    )
    return data.replace(
        _EMPTY_CURRENCIES, b'["list"] = {\n' + listed + b'},\n["filtered"] = true,\n["rows"] = 0,'
    )


def _cache_currency_names(data: Path, build: str) -> None:
    """The recorded CurrencyTypes export in the cache under `build` (constructed)."""
    folder = data / "gamedata" / "tables" / build
    folder.mkdir(parents=True)
    shutil.copy2(FIXTURES / "wago" / "CurrencyTypes.1.60.1.70058.csv", folder / "CurrencyTypes.csv")


def test_constructed_currency_changes_with_names_from_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_dirs: Path
) -> None:
    root = _install(tmp_path, monkeypatch)
    _put(root, _with_currencies(R2, {515: 3, 1792: 100}), T1)
    _snap(root, 1)
    _put(root, _with_currencies(R2, {515: 3, 1792: 250, 9999999: 7}), T2)
    first = _json()
    _cache_currency_names(user_dirs, first["names_build"])
    data = _json()
    assert data["rows"][1]["currency_changes"] == [
        {"id": 1792, "name": "Honor Points", "before": 100, "after": 250},
        {"id": 9999999, "name": None, "before": None, "after": 7},
    ]
    assert first["rows"][1]["currency_changes"][0]["name"] is None, "nothing cached, no name"
    out = _run().stdout
    assert "  currency: Honor Points (currency 1792): 100 to 250" in out
    assert "  currency: currency 9999999: none to 7" in out


def test_constructed_currencies_switched_off_are_not_compared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install(tmp_path, monkeypatch)
    _put(root, _with_currencies(R2, {515: 3}), T1)
    _snap(root, 1)
    data = _lf(R2)
    start = data.index(b'["currencies"] = {\n["events_unregistered"]')
    tail = data.index(_EMPTY_CURRENCIES) + len(_EMPTY_CURRENCIES)
    end = data.index(b"},\n", tail) + 3
    _put(
        root,
        data[:start] + b'["currencies"] = {\n["absent"] = "switched off",\n},\n' + data[end:],
        T2,
    )
    row = _json()["rows"][1]
    assert row["currency_changes"] == []
    assert "switched off" in row["currencies_not_compared"]
