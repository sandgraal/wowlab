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
import re
import shutil
from collections.abc import Callable
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


_PAYLOAD = re.compile(r"\|Hitem:([^|]*)\|h\[([^\]]*)\]")


def _instance(link: str) -> tuple[str, ...]:
    """A link's payload fields with field 8 (link level) and 9 (spec), the
    character's, blanked: the item instance. The name and colour are
    display text and are left out."""
    match = _PAYLOAD.search(link)
    assert match is not None
    fields = match.group(1).split(":")
    fields[8:10] = ["", ""]
    return tuple(fields)


def _gear_links(data: bytes) -> dict[int, tuple[str, int | None]]:
    from wowlab_core import labaddon

    section = labaddon.parse_char(data).gear
    assert isinstance(section, labaddon.Gear)
    return {s.slot: (s.link, s.item_level) for s in section.slots}


def _slots(rec: tuple[str, str]) -> dict[int, tuple[tuple[str, ...], int | None]]:
    return {n: (_instance(link), lvl) for n, (link, lvl) in _gear_links(_record(*rec)).items()}


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
                assert (_instance(g["after"]["link"]), g["after"]["item_level"]) == after[g["slot"]]
                assert g["after"]["name"] and "|" not in g["after"]["name"]
            if g["before"] is not None:
                assert (_instance(g["before"]["link"]), g["before"]["item_level"]) == before[
                    g["slot"]
                ]
            both = g["before"] is not None and g["after"] is not None
            assert bool(g["differs"]) == both, "named when both sides hold an item"
    assert rows[1]["changed_from_ns"] == T1
    assert any(r["gear_changes"] for r in rows[1:]), "the records differ in gear"


def test_a_level_up_alone_is_no_gear_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """70009 at level 13 and 70425 at level 15 (real records): the links of
    the items worn in both differ in field 8, the link level, and are not
    counted as changes."""
    root = _install(tmp_path, monkeypatch)
    _put(root, _record(*R1), T1)
    _snap(root, 1)
    _put(root, _record(*R3), T2)
    [first, second] = _json()["rows"]
    assert (first["level"], second["level"]) == (13, 15)
    before, after = _gear_links(_record(*R1)), _gear_links(_record(*R3))
    level_only = [
        n
        for n in set(before) & set(after)
        if before[n] != after[n]
        and _instance(before[n][0]) == _instance(after[n][0])
        and before[n][1] == after[n][1]
    ]
    assert level_only, "the fixtures hold items whose link differs only in the link level"
    changed = {g["slot"] for g in second["gear_changes"]}
    assert not changed & set(level_only)
    out = _run().stdout
    assert "a different instance (link" not in out


def _relink(data: bytes, edit: Callable[[list[str]], None]) -> bytes:
    """Every item link in `data` with its payload fields edited (constructed)."""

    def one(match: re.Match[bytes]) -> bytes:
        fields = match.group(1).decode().split(":")
        edit(fields)
        return b"|Hitem:" + ":".join(fields).encode() + b"|h"

    return re.sub(rb"\|Hitem:([^|]*)\|h", one, data)


def test_constructed_only_link_level_and_spec_differ_is_no_gear_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install(tmp_path, monkeypatch)
    _put(root, _record(*R2), T1)
    _snap(root, 1)

    def character_fields(fields: list[str]) -> None:
        fields[8], fields[9] = "60", "1482"

    _put(root, _relink(_record(*R2), character_fields), T2)
    row = _json()["rows"][1]
    assert row["gear_changes"] == [] and row["gear_not_compared"] is None
    assert "  no gear or currency changes" in _run().stdout


def test_constructed_same_item_id_another_instance_names_the_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One more bonus id on every link: the same item ids, other instances."""
    root = _install(tmp_path, monkeypatch)
    _put(root, _record(*R2), T1)
    _snap(root, 1)

    def bonus(fields: list[str]) -> None:
        count = int(fields[12] or 0)
        fields[13 : 13 + count] = [*fields[13 : 13 + count], "99999"]
        fields[12] = str(count + 1)

    _put(root, _relink(_record(*R2), bonus), T2)
    changes = _json()["rows"][1]["gear_changes"]
    assert changes
    for g in changes:
        assert g["differs"] == ["bonus ids"]
        assert g["before"]["item_id"] == g["after"]["item_id"]
    out = _run().stdout
    assert "same item id, a different instance (bonus ids); before: " in out


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
    assert "4 saves, from 4 of 5 snapshots and the file in the install, oldest first:" in out
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
    # A damaged manifest is a snapshot that did not load, not a copy.
    assert "Could not read 1 copy (the others were read):" in out
    assert "1 snapshot did not load, so wowlab cannot tell whether they hold" in out


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
    assert "  currency: currency 9999999: not listed to 7" in out


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


def test_headline_says_equipped_item_level(history: dict[str, Any]) -> None:
    out = _run().stdout
    assert out.count(", equipped item level ") == 4
    assert ", item level " not in out.replace("equipped item level", "")


def test_constructed_no_average_reads_not_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install(tmp_path, monkeypatch)
    data = _lf(R2)
    assert data.count(b'["equipped"] = ') == 1
    _put(root, re.sub(rb'\["equipped"\] = [^,]*,\n', b"", data), T1)
    row = _json()["rows"][0]
    assert row["item_level"] is None
    assert "equipped item level not returned" in _run().stdout


def test_a_restored_record_names_the_save_it_repeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1, then R2, then R1's bytes again under a later time, as a restore
    leaves them (real records; the sequence constructed)."""
    root = _install(tmp_path, monkeypatch)
    _put(root, _record(*R1), T1)
    _snap(root, 1)
    _put(root, _record(*R2), T2)
    _snap(root, 2)
    _put(root, _record(*R1), T3)
    rows = _json()["rows"]
    assert [r["same_bytes_as_ns"] for r in rows] == [None, None, T1]
    out = _run().stdout
    assert out.count("likely restored") == 1
    assert "same contents as the save of " in out
    assert "likely restored, so the time is when it was put back" in out


def test_constructed_a_changed_currency_panel_is_said_on_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install(tmp_path, monkeypatch)
    data = _with_currencies(R2, {515: 3})
    _put(root, data, T1)
    _snap(root, 1)
    assert data.count(b'["headers_collapsed"] = 0,') == 1
    _put(root, data.replace(b'["headers_collapsed"] = 0,', b'["headers_collapsed"] = 2,'), T2)
    row = _json()["rows"][1]
    assert row["currency_changes"] == []
    assert row["currency_panel_changed"] == (
        "the currency panel was read with 2 collapsed headers, 0 the save before"
    )
    assert "  the currency panel was read with 2 collapsed headers" in _run().stdout


def test_constructed_spec_name_follows_the_body_type_as_char_show_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_dirs: Path
) -> None:
    """Sangraal-Sett (schema 4, sex_raw 2) and the same record with sex_raw
    3, over a constructed ChrSpecialization table whose female name differs."""
    root = _install(tmp_path, monkeypatch)
    male = _lf(R_LIVE)
    assert male.count(b'["sex_raw"] = 2,') == 1
    _put(root, male, T1)
    _snap(root, 1)
    _put(root, male.replace(b'["sex_raw"] = 2,', b'["sex_raw"] = 3,'), T2)
    build = _json()["names_build"]
    folder = user_dirs / "gamedata" / "tables" / build
    folder.mkdir(parents=True)
    (folder / "ChrSpecialization.csv").write_text(
        "ID,Name_lang,FemaleName_lang\n1490,Warlock,Warlockess\n", encoding="utf-8"
    )
    rows = _json()["rows"]
    assert [r["spec_name"] for r in rows] == ["Warlock", "Warlockess"]


def test_constructed_summary_names_the_install_file_only_when_it_gave_a_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install(tmp_path, monkeypatch)
    _put(root, _record(*R1), T1)
    _snap(root, 1)
    out = _run().stdout
    assert "1 save, from 1 of 1 snapshot and the file in the install, oldest first:" in out
    _put(root, b"WowLabCharDB = {\n", T2)
    out = _run().stdout
    assert (
        "1 save, from 1 of 1 snapshot (the install's own copy could not be read; see below), "
        "oldest first:" in out
    )
    assert "  the file in the install: " in out


def test_notes_say_names_come_from_the_installed_build(history: dict[str, Any]) -> None:
    out = " ".join(_run("--notes").stdout.split())
    assert "installed client (wowlab db2 fetch), even for older saves" in out
    assert "character's level and spec, and its name and colour are display text" in out
    assert "same instance whose item level, the client's figure, changed" in out


def _two_saves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, later: bytes) -> dict[str, Any]:
    """R2, snapshotted, then `later` as the live file; the later row."""
    root = _install(tmp_path, monkeypatch)
    _put(root, _lf(R2), T1)
    _snap(root, 1)
    _put(root, later, T2)
    row: dict[str, Any] = _json()["rows"][1]
    return row


def test_constructed_a_name_alone_is_no_gear_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every link's bracketed name rewritten, as a client language switch
    would: no gear change."""
    renamed = re.sub(rb"\|h\[[^\]]*\]\|h", b"|h[Renamed]|h", _lf(R2))
    assert renamed != _lf(R2)
    row = _two_saves(tmp_path, monkeypatch, renamed)
    assert row["gear_changes"] == [] and row["gear_not_compared"] is None
    assert "different instance" not in _run().stdout


def test_constructed_a_colour_form_alone_is_no_gear_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every named colour written in the hex form instead: no gear change."""
    hexed = re.sub(rb"\|cnIQ[0-9]:\|Hitem", b"|cff1eff00|Hitem", _lf(R2))
    assert hexed != _lf(R2)
    row = _two_saves(tmp_path, monkeypatch, hexed)
    assert row["gear_changes"] == []


def _item_levels_up(data: bytes) -> bytes:
    return re.sub(
        rb'\["item_level"\] = ([0-9]+),',
        lambda m: b'["item_level"] = %d,' % (int(m.group(1)) + 1),
        data,
    )


def test_constructed_item_level_alone_is_the_same_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _two_saves(tmp_path, monkeypatch, _item_levels_up(_lf(R2)))
    assert row["gear_changes"]
    for g in row["gear_changes"]:
        assert g["differs"] == ["item level"]
        assert g["after"]["item_level"] == g["before"]["item_level"] + 1
    out = _run().stdout
    assert "different instance" not in out
    assert ", same instance, item level 15 to 16 (the client's figure)" in out


def test_constructed_bonus_and_item_level_name_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bonus(fields: list[str]) -> None:
        count = int(fields[12] or 0)
        fields[13 : 13 + count] = [*fields[13 : 13 + count], "99999"]
        fields[12] = str(count + 1)

    later = _item_levels_up(_relink(_lf(R2), bonus))
    row = _two_saves(tmp_path, monkeypatch, later)
    assert row["gear_changes"]
    assert {tuple(g["differs"]) for g in row["gear_changes"]} == {("bonus ids", "item level")}
    out = _run().stdout
    assert "a different instance (bonus ids); before: " in out
    assert "; item level 15 to 16 (the client's figure)" in out
