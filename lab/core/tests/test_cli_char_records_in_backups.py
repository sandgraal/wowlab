"""A character folder with no lab record whose record a backup holds
(M18-11): `char list --summary`'s `records_in_backups`, and
`wowlab char kept --character` reading it.

The install is `test_cli_char_kept.py`'s (two real captures side by side),
and the store is `constructed` (L8, a boundary input): the owner's beta
character `1/Sangraal-Sett` of `fixtures/macos-70450/` is copied into the
beta's account, a snapshot of the beta's `WTF/` is taken into the
redirected user data folder, and then only its `WowLab.lua` is deleted, so
the folder stays in the install with no lab record while the store holds
one. Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
import test_cli_char_kept as kept_cli
import test_cli_char_summary as hub_cli

user_data = kept_cli.user_data

BETA = kept_cli.BETA
ACCOUNT = kept_cli.ACCOUNT
SETT = kept_cli.SETT
PARTICIPLE = kept_cli.PARTICIPLE
SV = kept_cli.SV
run = kept_cli.run


@pytest.fixture
def held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    root = hub_cli.two_flavors(tmp_path / "World of Warcraft")
    monkeypatch.setenv(hub_cli.install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    account = kept_cli._account(root)
    for character, ns in ((SETT, kept_cli.SETT_NS), (PARTICIPLE, kept_cli.PARTICIPLE_NS)):
        shutil.copytree(kept_cli.OWNER / character, account / character)
        os.utime(account / character / SV, ns=(ns, ns))
    manifest = kept_cli._snap(root)
    # Sett's folder stays, its record gone; Participle keeps its record.
    (account / SETT / SV).unlink()
    return {"root": root, "snapshot": manifest}


def _summary() -> dict[str, Any]:
    result = run("char", "list", "--summary", "--json", "--flavor", BETA)
    report: dict[str, Any] = json.loads(result.stdout)
    return report


def test_a_folder_with_no_record_lists_the_backups_record_constructed(
    held: dict[str, Any],
) -> None:
    report = _summary()
    (row,) = [r for r in report["rows"] if r["label"] == "Sangraal-Sett"]
    assert row["state"] == "no_lab_record"
    (record,) = report["records_in_backups"]
    assert (record["flavor"], record["account"], record["realm_folder"], record["label"]) == (
        row["flavor"],
        row["account"],
        row["realm_folder"],
        row["label"],
    )
    assert record["state"] == "recorded"
    assert record["level"] == 15
    assert record["snapshot_id"] == held["snapshot"].id
    assert record["created_at"] == held["snapshot"].created_at
    assert record["mtime_ns"] == kept_cli.SETT_NS
    # Its folder is in the game folder: it is not a character kept only in a backup.
    assert report["kept_in_backups"] == []


def test_a_folder_with_a_record_lists_nothing_constructed(held: dict[str, Any]) -> None:
    report = _summary()
    labels = [r["label"] for r in report["records_in_backups"]]
    assert "Dangling-Participle" not in labels
    # The capture's own folder with no record: no backup holds one.
    assert labels == ["Sangraal-Sett"]


def test_no_backup_lists_nothing_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_data: Path
) -> None:
    root = hub_cli.two_flavors(tmp_path / "World of Warcraft")
    monkeypatch.setenv(hub_cli.install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    assert _summary()["records_in_backups"] == []


def test_a_snapshot_of_another_install_lists_nothing_constructed(
    held: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same folders in a second install: the first install's snapshot is
    another game folder's, and its record is not this one's."""
    other = hub_cli.two_flavors(tmp_path / "Other" / "World of Warcraft")
    account = kept_cli._account(other)
    shutil.copytree(kept_cli.OWNER / SETT, account / SETT)
    (account / SETT / SV).unlink()
    monkeypatch.setenv(hub_cli.install.ENV_ROOT, str(other))
    assert _summary()["records_in_backups"] == []


def test_char_kept_reads_the_backups_record_of_the_folder_constructed(
    held: dict[str, Any],
) -> None:
    result = run(
        "char", "kept", "--character", SETT, "--account", ACCOUNT, "--flavor", BETA, "--json"
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    (row,) = report["kept"]
    assert (row["realm_folder"], row["label"], row["level"]) == ("1", "Sangraal-Sett", 15)
    assert row["snapshot_id"] == held["snapshot"].id
    assert report["gear"] is not None and report["gear"]["slots"]
    assert report["history"]["snapshots_holding"] == 1
    # Nothing was restored: the folder still holds no record.
    assert not (kept_cli._account(held["root"]) / SETT / SV).exists()


def test_char_kept_lists_only_characters_gone_from_the_folder_constructed(
    held: dict[str, Any],
) -> None:
    """Without --character the list is the kept-only one: Sett's folder is
    still in the game folder."""
    result = run("char", "kept", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["kept"] == []


def test_char_kept_refuses_a_folder_that_has_its_record_constructed(
    held: dict[str, Any],
) -> None:
    result = run("char", "kept", "--character", PARTICIPLE, "--account", ACCOUNT, "--flavor", BETA)
    assert result.exit_code == 2, result.output
    assert "no character kept only in a backup is named" in result.output
