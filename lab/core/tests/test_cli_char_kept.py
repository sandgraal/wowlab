"""Characters kept only in a backup (M18-06): `char list --summary`'s
`kept_in_backups` and `wowlab char kept`, in JSON and text.

The install is `test_cli_char_summary.py`'s two captures side by side
(`fixtures/macos/` as `_classic_beta_`, `fixtures/macos-70888/` as
`_forever_`), and the store is `constructed` (L8, a boundary input): the
two schema-4 character folders of `fixtures/macos-70450/` (the owner's
beta characters, 2026-10-19) are copied into the beta's account, a
snapshot of the beta's `WTF/` is taken into the redirected user data
folder, and those two folders are then deleted from the install, so the
store holds records the install lacks. Nothing reads or writes a real
install; the copy in `tmp_path` is the only install touched, and only by
the test's own setup.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import test_cli_char_summary as hub_cli
from typer.testing import CliRunner

from wowlab_core import charkept, cli, snapshot

user_data = hub_cli.user_data

BETA = hub_cli.BETA
FOREVER = hub_cli.FOREVER
ACCOUNT = hub_cli.ACCOUNT
FIRST = hub_cli.FIRST
SECOND = hub_cli.SECOND
WARLOCK = hub_cli.WARLOCK
BETA_VERSION = hub_cli.BETA_VERSION
OWNER = hub_cli.FIXTURES / "macos-70450" / "forever" / "WTF" / "Account" / ACCOUNT
SETT = "1/Sangraal-Sett"
PARTICIPLE = "1/Dangling-Participle"
SV = "SavedVariables/WowLab.lua"
# The two saves, as the snapshot records them (constructed times).
SETT_NS = 1_792_400_000_000_000_000
PARTICIPLE_NS = SETT_NS - 3_600 * 10**9
SNAPPED = datetime(2026, 10, 19, 18, 0, 0, tzinfo=UTC)

runner = CliRunner()


def _account(root: Path) -> Path:
    return root / BETA / "WTF" / "Account" / ACCOUNT


def _snap(root: Path, when: datetime = SNAPPED) -> snapshot.Manifest:
    return snapshot.SnapshotStore().create(
        root, [f"{BETA}/WTF"], flavor_folder=BETA, flavor_version=BETA_VERSION, now=when
    )


@pytest.fixture
def kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    root = hub_cli.two_flavors(tmp_path / "World of Warcraft")
    monkeypatch.setenv(hub_cli.install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    for character, ns in ((SETT, SETT_NS), (PARTICIPLE, PARTICIPLE_NS)):
        shutil.copytree(OWNER / character, _account(root) / character)
        os.utime(_account(root) / character / SV, ns=(ns, ns))
    manifest = _snap(root)
    for character in (SETT, PARTICIPLE):
        shutil.rmtree(_account(root) / character)
    return {"root": root, "snapshot": manifest}


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args), terminal_width=200)


def as_json(*args: str) -> tuple[int, dict[str, Any]]:
    done = run(*args, "--json")
    return done.exit_code, json.loads(done.stdout)


def by_path(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f"{r['realm_folder']}/{r['label']}": r for r in rows}


# ─── char list --summary ─────────────────────────────────────────────────────


def test_a_record_the_install_lacks_is_listed_apart_from_the_rows_constructed(
    kept: dict[str, Any],
) -> None:
    code, report = as_json("char", "list", "--summary")
    assert code == 0
    # The live rows are as before: the two deleted folders are not among them.
    assert set(by_path(report["rows"])) == {FIRST, SECOND, hub_cli.BARE, WARLOCK}
    rows = report["kept_in_backups"]
    # Newest save first.
    assert [f"{r['realm_folder']}/{r['label']}" for r in rows] == [SETT, PARTICIPLE]
    sett, participle = rows
    manifest = kept["snapshot"]
    for row in rows:
        assert row["state"] == "recorded"
        assert row["flavor"] == BETA and row["account"] == ACCOUNT
        assert row["shape"] == "numeric_folder"
        assert row["snapshot_id"] == manifest.id
        assert row["created_at"] == manifest.created_at
        assert row["snapshot_version"] == BETA_VERSION
        assert row["snapshots_holding"] == 1
        assert row["file"] == f"WTF/Account/{ACCOUNT}/{row['realm_folder']}/{row['label']}/{SV}"
        assert row["record_schema"] == 4
    assert sett["mtime_ns"] == SETT_NS and participle["mtime_ns"] == PARTICIPLE_NS
    # What the records hold: an Undead Warlock and an Orc Mage.
    assert (sett["class_file"], sett["race_id"], sett["level"]) == ("WARLOCK", 5, 15)
    assert (participle["class_file"], participle["race_id"], participle["level"]) == (
        "MAGE",
        2,
        10,
    )
    assert isinstance(sett["item_level"], float)
    assert sett["professions"] is not None


def test_nothing_kept_when_every_character_is_in_the_install_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = hub_cli.two_flavors(tmp_path / "World of Warcraft")
    monkeypatch.setenv(hub_cli.install.ENV_ROOT, str(root))
    _snap(root)
    code, report = as_json("char", "list", "--summary")
    assert code == 0 and report["kept_in_backups"] == []


def test_a_deleted_flavor_folder_keeps_all_its_characters_constructed(
    kept: dict[str, Any],
) -> None:
    """The owner's case: the closed beta's folder is gone from the install."""
    shutil.rmtree(kept["root"] / BETA)
    code, report = as_json("char", "list", "--summary")
    assert code == 0
    assert [f["folder"] for f in report["flavors"]] == [FOREVER]
    assert set(by_path(report["rows"])) == {WARLOCK}
    assert set(by_path(report["kept_in_backups"])) == {SETT, PARTICIPLE, FIRST, SECOND}
    assert {r["flavor"] for r in report["kept_in_backups"]} == {BETA}


def test_the_newest_save_wins_across_snapshots_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = hub_cli.two_flavors(tmp_path / "World of Warcraft")
    monkeypatch.setenv(hub_cli.install.ENV_ROOT, str(root))
    lab = _account(root) / SETT / SV
    shutil.copytree(OWNER / SETT, _account(root) / SETT)
    os.utime(lab, ns=(SETT_NS, SETT_NS))
    older = _snap(root, datetime(2026, 10, 18, tzinfo=UTC))
    os.utime(lab, ns=(SETT_NS + 10**9, SETT_NS + 10**9))
    newer = _snap(root, datetime(2026, 10, 19, tzinfo=UTC))
    # A later snapshot holding the earlier save does not win over the newer save.
    os.utime(lab, ns=(SETT_NS, SETT_NS))
    _snap(root, datetime(2026, 10, 20, tzinfo=UTC))
    shutil.rmtree(_account(root) / SETT)
    (row,) = as_json("char", "list", "--summary")[1]["kept_in_backups"]
    assert row["mtime_ns"] == SETT_NS + 10**9
    assert row["snapshot_id"] == newer.id != older.id
    assert row["snapshots_holding"] == 3


def test_a_snapshot_of_another_install_or_an_import_is_not_this_ones_constructed(
    kept: dict[str, Any],
) -> None:
    store = snapshot.SnapshotStore()
    manifests = store.list_lenient().manifests
    elsewhere = kept["root"].parent / "Another"
    assert charkept.find(manifests, elsewhere) == []
    assert charkept.find(manifests, kept["root"], {kept["snapshot"].id}) == []
    assert len(charkept.find(manifests, kept["root"])) == 2


def test_flavor_and_account_narrow_the_kept_list_constructed(kept: dict[str, Any]) -> None:
    assert as_json("char", "list", "--summary", "--flavor", FOREVER)[1]["kept_in_backups"] == []
    assert len(as_json("char", "list", "--summary", "--flavor", BETA)[1]["kept_in_backups"]) == 2
    named = as_json("char", "list", "--summary", "--account", ACCOUNT)[1]
    assert len(named["kept_in_backups"]) == 2
    other = as_json("char", "list", "--summary", "--account", hub_cli.LAUNCH_ACCOUNT)[1]
    assert other["kept_in_backups"] == []


def test_a_copy_that_cannot_be_read_is_a_not_read_row_constructed(kept: dict[str, Any]) -> None:
    store = snapshot.SnapshotStore()
    entry = kept["snapshot"].entry(f"{BETA}/WTF/Account/{ACCOUNT}/{SETT}/{SV}")
    assert entry is not None and entry.sha256 is not None
    store.object_path(entry.sha256).unlink()
    code, report = as_json("char", "list", "--summary")
    assert code == 0
    sett = by_path(report["kept_in_backups"])[SETT]
    assert sett["state"] == "not_read" and "cannot be used" in sett["error"]
    assert sett["level"] is None
    assert by_path(report["kept_in_backups"])[PARTICIPLE]["state"] == "recorded"


def test_the_text_lists_kept_characters_after_the_others_constructed(
    kept: dict[str, Any],
) -> None:
    done = run("char", "list", "--summary")
    assert done.exit_code == 0, done.output
    lines = done.stdout.splitlines()
    at = lines.index("Kept only in your backups, no longer in the game folder: 2 characters")
    assert lines.index(f"  {WARLOCK}") < at
    assert lines[at + 1] == f"  {SETT} ({BETA}, account {ACCOUNT})"
    assert lines[at + 2].startswith("    level 15, class WARLOCK, race id 5, ")
    day = datetime.fromisoformat(kept["snapshot"].created_at).astimezone().strftime("%Y-%m-%d")
    assert f"    kept in the backup of {day}" in lines[at + 1 : at + 7]
    assert kept["snapshot"].id not in done.stdout


# ─── char kept ───────────────────────────────────────────────────────────────


def test_char_kept_lists_the_same_rows_constructed(kept: dict[str, Any]) -> None:
    code, report = as_json("char", "kept")
    assert code == 0
    summary = as_json("char", "list", "--summary")[1]["kept_in_backups"]
    assert report["kept"] == summary
    assert report["gear"] is None and report["history"] is None
    assert report["notes"]


def test_char_kept_reads_one_characters_gear_and_saves_from_the_store_constructed(
    kept: dict[str, Any],
) -> None:
    code, report = as_json("char", "kept", "--character", SETT)
    assert code == 0, report
    (row,) = report["kept"]
    assert row["label"] == "Sangraal-Sett"
    gear = report["gear"]
    assert gear is not None and report["gear_absent"] is None
    assert gear["flavor_folder"] == BETA
    assert gear["character"] == SETT
    assert gear["mtime_ns"] == SETT_NS
    assert gear["slots"], "the record holds gear"
    assert gear["identity"]["class_file"] == "WARLOCK"
    history = report["history"]
    assert history["character"] == SETT and history["flavor_folder"] == BETA
    (save,) = history["rows"]
    assert save["saved_at_ns"] == SETT_NS
    assert save["snapshots"] == [kept["snapshot"].id] and save["live"] is False


def test_char_kept_text_names_the_backup_and_the_gear_constructed(
    kept: dict[str, Any],
) -> None:
    done = run("char", "kept", "--character", "Sangraal-Sett")
    assert done.exit_code == 0, done.output
    lines = done.stdout.splitlines()
    assert lines[0] == f"Character: {SETT} ({BETA}, account {ACCOUNT})"
    assert any(line.startswith("  kept in the backup of ") for line in lines)
    assert any(line.startswith("Gear (") for line in lines)
    assert "Saves in your backups (1), oldest first:" in lines
    assert lines[-1].endswith("--notes shows them")


def test_char_kept_refuses_a_character_it_does_not_keep_constructed(
    kept: dict[str, Any],
) -> None:
    done = run("char", "kept", "--character", FIRST)
    assert done.exit_code == 2
    assert "no character kept only in a backup is named" in done.output


def test_char_kept_text_with_nothing_kept_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = hub_cli.two_flavors(tmp_path / "World of Warcraft")
    monkeypatch.setenv(hub_cli.install.ENV_ROOT, str(root))
    done = run("char", "kept")
    assert done.exit_code == 0, done.output
    assert done.stdout.startswith("No character is kept only in a backup")


def _state(folder: Path) -> dict[str, tuple[str, int]]:
    return {
        str(p.relative_to(folder)): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
        )
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def test_reads_only_the_install_and_the_store_stay_as_they_were_constructed(
    kept: dict[str, Any],
) -> None:
    """L1, and nothing restored: the runs change neither tree."""
    store = snapshot.SnapshotStore().path
    before = (_state(kept["root"]), _state(store))
    assert run("char", "list", "--summary", "--json").exit_code == 0
    assert run("char", "kept", "--json").exit_code == 0
    assert run("char", "kept", "--character", SETT, "--json").exit_code == 0
    assert run("char", "kept", "--character", SETT).exit_code == 0
    assert (_state(kept["root"]), _state(store)) == before
    assert not (_account(kept["root"]) / SETT).exists()
