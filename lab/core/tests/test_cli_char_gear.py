"""`wowlab char gear` (M14-05, docs/LAB_PLAN.md §16.2) through Typer's runner.

Each install is a captured tree copied into `tmp_path` with its flavor
folder named as its provenance row says (`_classic_beta_`): `macos/` (two
characters, 70009), `macos-70058/`, and `macos-70425/`, whose rows say its
files are classified inside the install root borrowed from `macos/` (its
`.build.info` and `.flavor.info`). The text and `--json` of each character
are compared with snapshot files under `tests/snapshots/char_gear/`; the
file times are set to one fixed instant, and the text's local time and the
two slot-table paths are put back as placeholders before the comparison.
`WOWLAB_UPDATE_SNAPSHOTS=1` rewrites the snapshots instead.

Inputs labelled `constructed` are boundary cases (L8): a record with `gear`
switched off, one without `gear`, one of an unknown schema, a user slot
table. Nothing here reads or writes a real install: `WOWLAB_WOW_ROOT`
points at the copy and the user data and user config directories are
redirected into `tmp_path`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, gear, install, labaddon

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SNAPSHOTS = Path(__file__).resolve().parent / "snapshots" / "char_gear"
UPDATE = os.environ.get("WOWLAB_UPDATE_SNAPSHOTS") == "1"
FLAVOR = "_classic_beta_"  # the provenance rows' flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
ACCOUNT_DIR = f"WTF/Account/{ACCOUNT}"
MTIME_NS = 1_700_000_000_000_000_000
# (fixture tree, character folder): every character with a gear record.
CASES = [
    ("macos", "1/Labchard-Labrealmg"),
    ("macos", "1/Labcharb-Labrealmf"),
    ("macos-70058", "1/Labchard-Labrealmg"),
    ("macos-70425", "1/Labchard-Labrealme"),
]
_WRITTEN = re.compile(r"^Written: .* \(the file's modification time\)$", re.MULTILINE)

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "userdata" / "wowlab"
    config = tmp_path / "userconfig" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config)
    return config


def _install(tree: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    flavor = root / FLAVOR
    if tree == "macos-70425":  # no .build.info or .flavor.info of its own (fixture index)
        shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
        shutil.copytree(FIXTURES / tree / "forever", flavor)
        shutil.copy2(FIXTURES / "macos" / "forever" / ".flavor.info", flavor / ".flavor.info")
    else:
        shutil.copy2(FIXTURES / tree / ".build.info", root / ".build.info")
        shutil.copytree(FIXTURES / tree / "forever", flavor)
    for p in flavor.rglob("WowLab.lua"):
        os.utime(p, ns=(MTIME_NS, MTIME_NS))
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return flavor


def _lab_file(flavor: Path, character: str) -> Path:
    return flavor / ACCOUNT_DIR / character / "SavedVariables" / "WowLab.lua"


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _placeholders(text: str, user_config: Path) -> str:
    text = _WRITTEN.sub("Written: <local time> (the file's modification time)", text)
    text = text.replace(gear.shipped_slots_path(), "<shipped gear_slots.toml>")
    return text.replace(str(user_config / gear.SLOTS_FILE), "<user gear_slots.toml>")


def _snapshot(name: str, actual: str) -> None:
    path = SNAPSHOTS / name
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert path.is_file(), f"no snapshot {path.name}; run with WOWLAB_UPDATE_SNAPSHOTS=1"
    assert actual == path.read_text(encoding="utf-8")


def _case_id(case: tuple[str, str]) -> str:
    return f"{case[0]}-{case[1].split('/')[-1]}"


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_char_gear_text_matches_its_snapshot(
    case: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_dirs: Path
) -> None:
    tree, character = case
    _install(tree, tmp_path, monkeypatch)
    out = ok("char", "gear", "--character", character).stdout
    _snapshot(f"{_case_id(case)}.txt", _placeholders(out, user_dirs))


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_char_gear_json_matches_its_snapshot_and_validates(
    case: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree, character = case
    flavor = _install(tree, tmp_path, monkeypatch)
    out = ok("char", "gear", "--character", character, "--json").stdout
    _snapshot(f"{_case_id(case)}.json", out)
    report = cli.CharGearReport.model_validate_json(out)
    assert json.loads(report.model_dump_json()) == json.loads(out)
    record = labaddon.read_char(_lab_file(flavor, character))
    assert isinstance(record.gear, labaddon.Gear)
    assert report.character == character and report.mtime_ns == MTIME_NS
    assert report.file == f"{ACCOUNT_DIR}/{character}/SavedVariables/WowLab.lua"
    # Every slot the record holds, in the client's order, its link kept whole.
    assert [s.slot for s in report.slots] == sorted(s.slot for s in record.gear.slots)
    assert [s.raw_link for s in report.slots] == [
        s.link for s in sorted(record.gear.slots, key=lambda s: s.slot)
    ]
    filled = {s.slot for s in report.slots}
    assert [e.slot for e in report.empty_slots] == [n for n in range(1, 20) if n not in filled]
    assert all(s.slot_name for s in report.slots) and all(e.slot_name for e in report.empty_slots)
    assert all(s.quality for s in report.slots)
    # Names are the bracketed text; the levels and averages are the record's.
    for s in report.slots:
        assert s.raw_link.endswith(f"|h[{s.name}]|h|r")
    assert [s.item_level for s in report.slots] == [
        s.item_level for s in sorted(record.gear.slots, key=lambda s: s.slot)
    ]
    assert isinstance(record.gear.average, labaddon.GearAverage)
    assert report.average.equipped == record.gear.average.equipped
    assert isinstance(record.spec, labaddon.Spec) and report.spec.id == record.spec.id
    assert all(s.spec_id == record.spec.id for s in report.slots)
    assert any("shipped default" in n and "[verify]" in n for n in report.notes)
    assert "nothing is fetched" in " ".join(report.notes)


def test_char_gear_json_keys_are_the_plans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§16.2's `CharGearReport` field list, in that order."""
    _install("macos-70425", tmp_path, monkeypatch)
    data = json.loads(ok("char", "gear", "--json").stdout)
    assert list(data) == [
        "character",
        "file",
        "mtime_ns",
        "spec",
        "average",
        "slots",
        "empty_slots",
        "notes",
    ]
    assert list(data["slots"][0]) == [
        "slot",
        "slot_name",
        "raw_link",
        "item_id",
        "name",
        "colour",
        "quality",
        "item_level",
        "item_level_api",
        "enchant_id",
        "gem_ids",
        "suffix_id",
        "spec_id",
        "bonus_ids",
        "upgrade",
        "crafter_removed",
    ]
    cloak = next(s for s in data["slots"] if s["slot"] == 15)
    assert cloak["crafter_removed"] is True and cloak["slot_name"] == "Back"


def test_char_gear_without_character_takes_the_latest_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flavor = _install("macos", tmp_path, monkeypatch)
    later = MTIME_NS + 10**9
    os.utime(_lab_file(flavor, "1/Labcharb-Labrealmf"), ns=(later, later))
    out = ok("char", "gear").stdout
    assert out.startswith("Character: 1/Labcharb-Labrealmf in account 90000001#6 (the WowLab.lua")
    report = cli.CharGearReport.model_validate_json(ok("char", "gear", "--json").stdout)
    assert report.character == "1/Labcharb-Labrealmf"
    assert any("newest modification time" in n for n in report.notes)


def _write_record(flavor: Path, character: str, body: str) -> None:
    path = _lab_file(flavor, character)
    path.write_bytes(f"\nWowLabCharDB = {{\n{body}}}\n".encode())


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            '["schema"] = 1,\n["gear"] = {\n["absent"] = "switched off by the owner",\n},\n',
            "the lab-addon recorded it as absent (switched off by the owner; /wowlab unskip gear",
        ),
        ('["schema"] = 1,\n', f"no gear in {ACCOUNT_DIR}/1/Labchard-Labrealmg/"),
        ('["schema"] = 99,\n', "is schema 99, and this reader knows schema"),
    ],
    ids=["constructed-gear-switched-off", "constructed-no-gear-key", "constructed-unknown-schema"],
)
def test_constructed_a_record_without_gear_exits_1_with_the_reason(
    body: str, reason: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flavor = _install("macos", tmp_path, monkeypatch)
    _write_record(flavor, "1/Labchard-Labrealmg", body)
    for extra in ([], ["--json"]):
        result = run("char", "gear", "--character", "1/Labchard-Labrealmg", *extra)
        assert result.exit_code == 1, (result.stdout, result.stderr)
        assert result.stdout == ""
        assert reason in " ".join(result.stderr.split())


def test_constructed_no_gear_key_says_not_in_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flavor = _install("macos", tmp_path, monkeypatch)
    _write_record(flavor, "1/Labchard-Labrealmg", '["schema"] = 1,\n')
    result = run("char", "gear", "--character", "Labchard-Labrealmg")
    assert result.exit_code == 1
    assert "not in the file" in " ".join(result.stderr.split())


def test_constructed_a_user_slot_table_is_used_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_dirs: Path
) -> None:
    _install("macos-70425", tmp_path, monkeypatch)
    user_dirs.mkdir(parents=True)
    (user_dirs / gear.SLOTS_FILE).write_text(
        '[slots]\n18 = "Wand"\n[quality]\n"nIQ2:" = "Green"\n', encoding="utf-8"
    )
    out = ok("char", "gear").stdout
    assert f"your table {user_dirs / gear.SLOTS_FILE}, over the shipped default" in out
    assert "slot 18 Wand" in out and "Shadow Wand: Green" in out
    report = cli.CharGearReport.model_validate_json(ok("char", "gear", "--json").stdout)
    assert any("your gear_slots.toml" in n for n in report.notes)
    assert not any("the shipped default gear_slots.toml" in n for n in report.notes)


def test_constructed_a_bad_user_slot_table_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_dirs: Path
) -> None:
    _install("macos-70425", tmp_path, monkeypatch)
    user_dirs.mkdir(parents=True)
    (user_dirs / gear.SLOTS_FILE).write_text("[slots]\n1 = ''\n", encoding="utf-8")
    result = run("char", "gear")
    assert result.exit_code == 1
    assert "the slot table cannot be used" in " ".join(result.stderr.split())


def test_char_gear_writes_nothing_into_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flavor = _install("macos", tmp_path, monkeypatch)
    root = flavor.parent

    def state() -> dict[str, tuple[bytes | None, int]]:
        return {
            p.relative_to(root).as_posix(): (
                p.read_bytes() if p.is_file() else None,
                p.lstat().st_mtime_ns,
            )
            for p in sorted(root.rglob("*"))
        }

    before = state()
    ok("char", "gear")
    ok("char", "gear", "--json")
    assert state() == before
