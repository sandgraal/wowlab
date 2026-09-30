"""`labaddon.survey` and `labaddon.read_all` (M12-09, docs/LAB_PLAN.md §14.4):
every character's `WowLab.lua` that `layout` finds, with its label, shape,
modification time, and the parsed record or the reason it could not be read,
and every place above the character folders wowlab could not look inside.
Also `labaddon.choose_lab_file`, the one rule `char show` shares, and
`Layout.wtf_walk`, the one walk the reader makes.

The captures are the committed trees, each copied into `tmp_path` with its
flavor folder named as its provenance row says: `fixtures/macos/` (M11-03,
build 70009: two characters with a `WowLab.lua`, one character folder
without one, and the retail-style `<Realm>/<First>/` twin that holds only
`AddOns.txt`) and `fixtures/macos-70058/` (M11-23: one character). Nothing
here reads or writes a real install.

Inputs labelled `constructed` (L8) are boundary and hostile cases: a real
capture cut short, given an unknown schema or another variable, made
unreadable or too large for the size bound, copied into extra character
folders (both folder shapes, a second account, names that sort differently
from their creation order), `.lua.bak` siblings, names that differ only in
case, and a file or folder replaced by a link, a FIFO, a folder, or made
unlistable. Links, FIFOs and permissions are skipped where the platform
cannot make them (Windows, root). Characters outside printable ASCII are
spelled with `chr`.
"""

from __future__ import annotations

import os
import shutil
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from wowlab_core import labaddon, luadata, snapshot
from wowlab_core.layout import Layout, Limits

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
CAPTURE_70058 = FIXTURES / "macos-70058"
FLAVOR = "_classic_beta_"  # the provenance rows' flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
ACCOUNT_DIR = f"WTF/Account/{ACCOUNT}"
FIRST = "1/Labchard-Labrealmg"
SECOND = "1/Labcharb-Labrealmf"
NO_ADDON = "1/Labcharb-Labrealmd"  # a captured character folder without the lab-addon's file
TWIN = "Labrealmb Partb Partc Partd/Labchard"  # the <Realm>/<First>/ twin: only AddOns.txt
NOT_FOLLOWED = "a link, not followed, as wowlab never follows links"
NOT_REGULAR = "not a regular file (a FIFO, socket or device), so it was not opened"

POSIX_PERMISSIONS = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permissions, and not as root",
)


def _copy(capture: Path, tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(capture / ".build.info", root / ".build.info")
    shutil.copytree(capture / "forever", root / FLAVOR)
    return root / FLAVOR


@pytest.fixture
def flavor(tmp_path: Path) -> Path:
    return _copy(CAPTURE, tmp_path)


def _lab_file(flavor: Path, character: str, account_dir: str = ACCOUNT_DIR) -> Path:
    return flavor / account_dir / character / "SavedVariables" / "WowLab.lua"


def _plant(flavor: Path, character: str, data: bytes, account_dir: str = ACCOUNT_DIR) -> Path:
    """A constructed character folder holding `data` as its WowLab.lua."""
    target = _lab_file(flavor, character, account_dir)
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    return target


def _state(root: Path) -> dict[str, tuple[bytes | None, int, int]]:
    out: dict[str, tuple[bytes | None, int, int]] = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[p.relative_to(root).as_posix()] = (
            p.read_bytes() if p.is_file() else None,
            st.st_mtime_ns,
            st.st_mode,
        )
    return out


def _case_sensitive(tmp_path: Path) -> bool:
    probe = tmp_path / "m12-09-case-probe"
    probe.mkdir()
    (probe / "a").write_bytes(b"")
    return not (probe / "A").exists()


def _symlink(link: Path, target: Path, *, is_dir: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


def _move_and_link(path: Path, tmp_path: Path, *, is_dir: bool) -> None:
    """`path` moved outside the flavor folder and replaced by a link to it."""
    outside = tmp_path / f"outside-{path.name}"
    shutil.move(path, outside)
    _symlink(path, outside, is_dir=is_dir)


@contextmanager
def _unlistable(folder: Path) -> Iterator[None]:
    folder.chmod(0)
    try:
        yield
    finally:
        folder.chmod(0o755)


def _by(flavor: Path) -> dict[str, labaddon.CharacterFile]:
    return {e.character: e for e in labaddon.read_all(Layout(flavor))}


# ─── the captures ────────────────────────────────────────────────────────────


def test_each_captured_character_with_a_lab_file_is_listed_once(flavor: Path) -> None:
    found = labaddon.survey(Layout(flavor))
    assert found.not_looked_at == []
    entries = found.characters
    assert entries == labaddon.read_all(Layout(flavor))
    assert [e.character for e in entries] == [SECOND, FIRST]
    assert [e.label for e in entries] == ["Labcharb-Labrealmf", "Labchard-Labrealmg"]
    for entry, character in zip(entries, [SECOND, FIRST], strict=True):
        path = _lab_file(flavor, character)
        assert entry.account == ACCOUNT
        assert entry.realm_folder == "1"
        assert entry.shape == "numeric_folder"  # a digits folder, not a realm name
        assert entry.file == f"{ACCOUNT_DIR}/{character}/SavedVariables/WowLab.lua"
        assert entry.mtime_ns == path.lstat().st_mtime_ns
        assert entry.error is None
        assert entry.record == labaddon.read_char(path)
        assert entry.record is not None and entry.record.schema_ == 1


def test_the_twin_holding_only_addons_txt_is_walked_and_not_listed(flavor: Path) -> None:
    """The layout finds the twin and the folder without the addon's file (so
    the walk covers them); neither holds a WowLab.lua, so neither is listed."""
    (account,) = Layout(flavor).accounts()
    found = {f"{c.realm_folder}/{c.folder}": c for r in account.realms for c in r.characters}
    assert set(found) == {FIRST, SECOND, NO_ADDON, TWIN}
    assert found[TWIN].shape == "realm_name"
    assert found[TWIN].files == ("AddOns.txt",) and found[TWIN].folders == ()
    assert "SavedVariables" not in found[NO_ADDON].folders
    listed = {e.character for e in labaddon.read_all(Layout(flavor))}
    assert TWIN not in listed and NO_ADDON not in listed
    assert all(e.label != "Labchard" for e in labaddon.read_all(Layout(flavor)))


def test_the_70058_capture_is_listed(tmp_path: Path) -> None:
    flavor = _copy(CAPTURE_70058, tmp_path)
    (entry,) = labaddon.read_all(Layout(flavor))
    assert entry.character == FIRST and entry.account == ACCOUNT
    assert entry.record == labaddon.read_char(_lab_file(flavor, FIRST))
    assert entry.record is not None
    assert labaddon.summary(entry.record) == (
        "schema 1, saved by client 1.60.1.70058, spec id 1490"
    )


def test_summary_of_the_captures(flavor: Path) -> None:
    """The build is the client's that saved the file, not the install's:
    this capture's `.build.info` says 1.60.1.69913."""
    rows = {e.character: e.record for e in labaddon.read_all(Layout(flavor))}
    assert {c: labaddon.summary(r) for c, r in rows.items() if r is not None} == {
        SECOND: "schema 1, saved by client 1.60.1.70009, spec id 1482",
        FIRST: "schema 1, saved by client 1.60.1.70009, spec id 1490",
    }
    assert b"1.60.1.69913" in (CAPTURE / ".build.info").read_bytes()


def test_read_all_writes_nothing(flavor: Path) -> None:
    root = flavor.parent
    before = _state(root)
    labaddon.read_all(Layout(flavor))
    labaddon.survey(Layout(flavor), account=ACCOUNT)
    assert _state(root) == before


def test_entries_round_trip_through_json(flavor: Path) -> None:
    found = labaddon.survey(Layout(flavor))
    assert labaddon.AllCharacters.model_validate_json(found.model_dump_json()) == found
    for entry in found.characters:
        assert labaddon.CharacterFile.model_validate_json(entry.model_dump_json()) == entry


def test_wtf_walk_is_one_walk_of_what_the_listings_give(flavor: Path) -> None:
    lay = Layout(flavor)
    walk = lay.wtf_walk()
    assert walk.accounts == lay.accounts()
    assert walk.saved_variables == lay.saved_variables()
    assert walk.wtf_files == lay.wtf_files()
    assert walk.symlinks == () and walk.errors == () and walk.not_regular == ()
    assert not walk.truncated
    assert f"{ACCOUNT_DIR}/{FIRST}/SavedVariables" in walk.folders
    assert f"{ACCOUNT_DIR}/{TWIN}" in walk.folders
    assert list(walk.folders) == sorted(walk.folders)
    assert all(f.startswith("WTF/") for f in walk.folders)


# ─── unreadable files ────────────────────────────────────────────────────────


def test_constructed_damaged_file_is_named_and_the_rest_still_read(flavor: Path) -> None:
    target = _lab_file(flavor, FIRST)
    data = target.read_bytes()
    target.write_bytes(data[: len(data) // 2])
    first, second = sorted(labaddon.read_all(Layout(flavor)), key=lambda e: e.label)
    assert second.character == FIRST and second.record is None
    assert second.error is not None
    assert second.error.startswith("not SavedVariables data the parser accepts: line ")
    assert second.file == f"{ACCOUNT_DIR}/{FIRST}/SavedVariables/WowLab.lua"
    assert second.mtime_ns == target.lstat().st_mtime_ns
    assert first.character == SECOND and first.error is None
    assert first.record == labaddon.read_char(_lab_file(flavor, SECOND))


def test_constructed_unknown_schema_gives_the_readers_reason(flavor: Path) -> None:
    _lab_file(flavor, FIRST).write_bytes(b'\r\nWowLabCharDB = {\r\n["schema"] = 3,\r\n}\r\n')
    by = _by(flavor)
    assert by[FIRST].record is None
    assert by[FIRST].error == (
        "WowLabCharDB is schema 3, and this reader knows schema 1, 2 only: it was written by "
        "another version of the lab-addon; nothing was read"
    )
    assert by[SECOND].record is not None


def test_constructed_another_variable_gives_the_readers_reason(flavor: Path) -> None:
    _lab_file(flavor, FIRST).write_bytes(b"\r\nSomethingElse = {\r\n}\r\n")
    by = _by(flavor)
    assert by[FIRST].error == "the file assigns no WowLabCharDB (it assigns 1 names: SomethingElse)"
    assert by[SECOND].record is not None


def test_constructed_value_that_does_not_fit_the_model_is_named(flavor: Path) -> None:
    target = _lab_file(flavor, FIRST)
    data = target.read_bytes()
    assert data.count(b'["loads"] = 4,') == 1
    target.write_bytes(data.replace(b'["loads"] = 4,', b'["loads"] = "four",'))
    by = _by(flavor)
    error = by[FIRST].error
    assert error is not None and error.startswith("WowLabCharDB does not fit the schema-1 model")
    assert "probe.loads" in error and "four" not in error  # the value is never echoed
    assert by[SECOND].record is not None


def test_constructed_file_over_the_size_bound_names_no_absolute_path(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    size = _lab_file(flavor, FIRST).stat().st_size
    bound = min(size, _lab_file(flavor, SECOND).stat().st_size) - 1
    monkeypatch.setattr(luadata, "MAX_FILE_BYTES", bound)
    entries = labaddon.read_all(Layout(flavor))
    assert [e.record for e in entries] == [None, None]
    by = {e.character: e for e in entries}
    assert by[FIRST].error == f"the file holds {size} bytes, more than {bound}"
    for entry in entries:
        assert entry.error is not None and str(flavor) not in entry.error


def test_constructed_os_error_gives_the_systems_words(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = snapshot.read_regular_file
    refused = _lab_file(flavor, FIRST)

    def read(path: Path, **kwargs: object) -> bytes:
        if Path(path) == refused:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(snapshot, "read_regular_file", read)
    by = _by(flavor)
    assert by[FIRST].error == "Permission denied"
    assert by[SECOND].record is not None


@POSIX_PERMISSIONS
def test_constructed_unreadable_file_on_disk_is_named(flavor: Path) -> None:
    target = _lab_file(flavor, FIRST)
    target.chmod(0)
    try:
        by = _by(flavor)
    finally:
        target.chmod(0o644)
    assert by[FIRST].record is None
    assert by[FIRST].error == "Permission denied"
    assert by[SECOND].record is not None


def test_an_unexpected_exception_is_not_swallowed_constructed(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(data: bytes) -> labaddon.CharDB:
        raise RuntimeError("a bug, not a file problem")

    monkeypatch.setattr(labaddon, "parse_char", broken)
    with pytest.raises(RuntimeError, match="a bug"):
        labaddon.read_all(Layout(flavor))


# ─── what wowlab could not look at ───────────────────────────────────────────


def test_constructed_linked_lab_file_is_named_and_not_followed(
    flavor: Path, tmp_path: Path
) -> None:
    lab = _lab_file(flavor, SECOND)
    _move_and_link(lab, tmp_path, is_dir=False)
    found = labaddon.survey(Layout(flavor))
    assert found.not_looked_at == []
    by = {e.character: e for e in found.characters}
    assert by[SECOND].record is None and by[SECOND].mtime_ns is None
    assert by[SECOND].error == NOT_FOLLOWED and by[SECOND].place == "file"
    assert by[SECOND].file == f"{ACCOUNT_DIR}/{SECOND}/SavedVariables/WowLab.lua"
    assert by[FIRST].record is not None


def test_constructed_linked_saved_variables_folder_is_named(flavor: Path, tmp_path: Path) -> None:
    _move_and_link(_lab_file(flavor, SECOND).parent, tmp_path, is_dir=True)
    by = _by(flavor)
    assert by[SECOND].record is None and by[SECOND].mtime_ns is None
    assert by[SECOND].error == NOT_FOLLOWED
    assert by[SECOND].place == "saved_variables_folder"
    assert by[SECOND].file == f"{ACCOUNT_DIR}/{SECOND}/SavedVariables"
    assert by[FIRST].record is not None


def test_constructed_linked_character_folder_is_named(flavor: Path, tmp_path: Path) -> None:
    _move_and_link(flavor / ACCOUNT_DIR / SECOND, tmp_path, is_dir=True)
    found = labaddon.survey(Layout(flavor))
    assert found.not_looked_at == []
    by = {e.character: e for e in found.characters}
    entry = by[SECOND]
    assert entry.label == "Labcharb-Labrealmf" and entry.shape == "numeric_folder"
    assert entry.record is None and entry.mtime_ns is None
    assert entry.error == NOT_FOLLOWED and entry.place == "character_folder"
    assert entry.file == f"{ACCOUNT_DIR}/{SECOND}"
    assert by[FIRST].record is not None


def test_constructed_fifo_named_like_the_lab_file_is_never_opened(flavor: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("no os.mkfifo on this platform")
    lab = _lab_file(flavor, SECOND)
    lab.unlink()
    os.mkfifo(lab)  # opening it for reading would block
    by = _by(flavor)
    assert by[SECOND].record is None and by[SECOND].error == NOT_REGULAR
    assert by[SECOND].mtime_ns is None
    assert by[FIRST].record is not None


def test_constructed_folder_named_like_the_lab_file_is_named(flavor: Path) -> None:
    lab = _lab_file(flavor, SECOND)
    lab.unlink()
    lab.mkdir()
    by = _by(flavor)
    assert by[SECOND].record is None and by[SECOND].error == "a folder, not a file"
    assert by[FIRST].record is not None


@POSIX_PERMISSIONS
def test_constructed_unlistable_saved_variables_folder_is_named(flavor: Path) -> None:
    with _unlistable(_lab_file(flavor, SECOND).parent):
        by = _by(flavor)
    assert by[SECOND].record is None
    assert by[SECOND].error == "Permission denied"
    assert by[SECOND].place == "saved_variables_folder"
    assert by[SECOND].file == f"{ACCOUNT_DIR}/{SECOND}/SavedVariables"
    assert by[FIRST].record is not None


@POSIX_PERMISSIONS
def test_constructed_unlistable_character_folder_is_named(flavor: Path) -> None:
    with _unlistable(flavor / ACCOUNT_DIR / SECOND):
        by = _by(flavor)
    assert by[SECOND].record is None
    assert by[SECOND].error == "Permission denied" and by[SECOND].place == "character_folder"
    assert by[SECOND].file == f"{ACCOUNT_DIR}/{SECOND}"
    assert by[FIRST].record is not None


@POSIX_PERMISSIONS
@pytest.mark.parametrize(
    "folder",
    [
        pytest.param(ACCOUNT_DIR, id="constructed-account"),
        pytest.param(f"{ACCOUNT_DIR}/1", id="constructed-digits-folder"),
    ],
)
def test_constructed_unlistable_folder_above_the_characters_is_named(
    flavor: Path, folder: str
) -> None:
    with _unlistable(flavor / folder):
        found = labaddon.survey(Layout(flavor))
    assert found.characters == []
    assert found.not_looked_at == [
        labaddon.NotLookedAt(path=folder, reason="Permission denied", account=ACCOUNT)
    ]


@pytest.mark.parametrize(
    ("folder", "account"),
    [
        pytest.param("WTF", None, id="constructed-wtf"),
        pytest.param("WTF/Account", None, id="constructed-accounts-folder"),
        pytest.param(ACCOUNT_DIR, ACCOUNT, id="constructed-account"),
        pytest.param(f"{ACCOUNT_DIR}/1", ACCOUNT, id="constructed-digits-folder"),
    ],
)
def test_constructed_linked_folder_above_the_characters_is_named(
    flavor: Path, tmp_path: Path, folder: str, account: str | None
) -> None:
    _move_and_link(flavor / folder, tmp_path, is_dir=True)
    found = labaddon.survey(Layout(flavor))
    assert found.characters == []
    assert found.not_looked_at == [
        labaddon.NotLookedAt(path=folder, reason=NOT_FOLLOWED, account=account)
    ]


@POSIX_PERMISSIONS
def test_constructed_flavor_folder_that_cannot_be_listed_is_named_by_its_name(
    flavor: Path,
) -> None:
    """Entered but not listed (0o111): `WTF/` is never found. The flavor
    folder is named by its own name, never by an absolute path."""
    flavor.chmod(0o111)
    try:
        found = labaddon.survey(Layout(flavor))
    finally:
        flavor.chmod(0o755)
    assert found.characters == []
    assert found.not_looked_at == [
        labaddon.NotLookedAt(path=FLAVOR, reason="Permission denied", account=None)
    ]


def test_constructed_truncated_walk_is_named(flavor: Path) -> None:
    found = labaddon.survey(Layout(flavor, limits=Limits(max_entries=10)))
    assert found.not_looked_at == [
        labaddon.NotLookedAt(
            path="WTF",
            reason=(
                "the walk stopped at its bound (10 entries, 24 folders deep), so what lies "
                "past it was not looked at"
            ),
        )
    ]


def test_constructed_links_and_fifos_that_hide_no_lab_file_are_not_named(
    flavor: Path, tmp_path: Path
) -> None:
    """A linked account-level file the file map names, a linked file, a FIFO
    and a linked `WowLab.lua` in the account's own SavedVariables/, another
    addon's linked file in a character's SavedVariables/, a twin's linked
    AddOns.txt, a FIFO with another name and a FIFO in a digits folder
    cannot hide a character's WowLab.lua: none is named, and the FIFO is
    not taken for a character folder."""
    _move_and_link(flavor / ACCOUNT_DIR / "config-cache.wtf", tmp_path, is_dir=False)
    account_sv = flavor / ACCOUNT_DIR / "SavedVariables"
    _move_and_link(account_sv / "RareScanner.lua", tmp_path, is_dir=False)
    _move_and_link(account_sv / "WowLab.lua", tmp_path, is_dir=False)
    _move_and_link(flavor / ACCOUNT_DIR / TWIN / "AddOns.txt", tmp_path, is_dir=False)
    other = _lab_file(flavor, FIRST).with_name("Syndicator.lua")
    other.write_bytes(b"x = 1\n")
    _move_and_link(other, tmp_path, is_dir=False)
    if hasattr(os, "mkfifo"):
        os.mkfifo(_lab_file(flavor, FIRST).with_name("Other.lua"))
        os.mkfifo(account_sv / "WeakAuras.lua")
        os.mkfifo(flavor / ACCOUNT_DIR / "1" / "Not-A-Folder")
    found = labaddon.survey(Layout(flavor))
    assert found.not_looked_at == []
    assert [(e.character, e.error) for e in found.characters] == [(SECOND, None), (FIRST, None)]


def test_constructed_linked_account_saved_variables_folder_is_not_named(
    flavor: Path, tmp_path: Path
) -> None:
    _move_and_link(flavor / ACCOUNT_DIR / "SavedVariables", tmp_path, is_dir=True)
    found = labaddon.survey(Layout(flavor))
    assert found.not_looked_at == []
    assert [e.character for e in found.characters] == [SECOND, FIRST]


def test_constructed_places_in_another_account_are_left_out_with_account(
    flavor: Path, tmp_path: Path
) -> None:
    other = "WTF/Account/90000002#1"
    _plant(flavor, "2/Other-Char", b"broken =\n", other)
    (flavor / other / "4").mkdir()
    _move_and_link(flavor / other / "4", tmp_path, is_dir=True)
    everyone = labaddon.survey(Layout(flavor))
    assert everyone.not_looked_at == [
        labaddon.NotLookedAt(path=f"{other}/4", reason=NOT_FOLLOWED, account="90000002#1")
    ]
    assert [e.character for e in everyone.characters] == [SECOND, FIRST, "2/Other-Char"]
    mine = labaddon.survey(Layout(flavor), account=ACCOUNT)
    assert mine.not_looked_at == []
    assert [e.character for e in mine.characters] == [SECOND, FIRST]


# ─── the total bound ─────────────────────────────────────────────────────────


TINY = b'\r\nWowLabCharDB = {\r\n["schema"] = 1,\r\n}\r\n'  # constructed: a valid, tiny file


def _bound(budget_words: str, who: str, account: str = ACCOUNT) -> str:
    return (
        "the listing's total size bound was reached (wowlab reads at most "
        f"{budget_words} of WowLab.lua files in one listing, and the files before this one "
        f"used it up); `wowlab char show --account {account} --character {who}` reads this "
        "one on its own"
    )


def _size(flavor: Path, character: str) -> int:
    return _lab_file(flavor, character).stat().st_size


def _results(found: labaddon.AllCharacters) -> list[tuple[str, bool, str | None]]:
    return [(e.character, e.summary is not None, e.error) for e in found.characters]


def test_constructed_total_bound_stops_reading_and_names_every_file_after_it(
    flavor: Path,
) -> None:
    """A budget injected small (real files never reach `MAX_SURVEY_BYTES`):
    the first file fits, the second would pass the bound, so it and every
    file after it in the order get the bound's reason and are not read,
    even a tiny one that would fit in what is left (the bound stays
    reached)."""
    donor = _lab_file(flavor, FIRST).read_bytes()
    _plant(flavor, "1/Xa-A", donor)
    _plant(flavor, "1/Zz-Tiny", TINY)
    budget = _size(flavor, SECOND) + len(TINY) + 10
    found = labaddon.survey(Layout(flavor), budget=budget)
    words = f"{budget:,} bytes"
    assert _results(found) == [
        (SECOND, True, None),
        (FIRST, False, _bound(words, FIRST)),
        ("1/Xa-A", False, _bound(words, "1/Xa-A")),
        ("1/Zz-Tiny", False, _bound(words, "1/Zz-Tiny")),
    ]


def test_the_total_bound_is_64_mib_and_says_so() -> None:
    assert labaddon.MAX_SURVEY_BYTES == 64 * 1024 * 1024
    assert labaddon._size_words(labaddon.MAX_SURVEY_BYTES) == "64 MiB"
    assert labaddon._size_words(23_019) == "23,019 bytes"


def test_constructed_a_file_exactly_at_the_bound_is_read_and_the_next_is_not(
    flavor: Path,
) -> None:
    budget = _size(flavor, SECOND)
    found = labaddon.survey(Layout(flavor), budget=budget)
    assert _results(found) == [
        (SECOND, True, None),
        (FIRST, False, _bound(f"{budget:,} bytes", FIRST)),
    ]


def test_constructed_a_budget_of_zero_opens_no_file(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = _plant(flavor, "1/Aa-Empty", b"")

    def never(path: Path, **kwargs: object) -> bytes:
        raise AssertionError(f"read {path}")

    monkeypatch.setattr(snapshot, "read_regular_file", never)
    found = labaddon.survey(Layout(flavor), budget=0)
    assert [e.summary for e in found.characters] == [None, None, None]
    assert all("total size bound was reached" in (e.error or "") for e in found.characters)
    assert empty.stat().st_size == 0  # even an empty file is not opened


def test_constructed_bytes_read_are_charged_not_the_size_seen_first(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first file grows by 5 bytes between its lstat and its read: the
    run is charged what it read, so the second file no longer fits."""
    grown = _lab_file(flavor, SECOND)
    budget = _size(flavor, SECOND) + 5 + _size(flavor, FIRST) - 1
    real = snapshot.read_regular_file

    def read(path: Path, **kwargs: Any) -> bytes:
        if Path(path) == grown and grown.stat().st_size < budget:
            with grown.open("ab") as fh:
                fh.write(b"\r\n\r\n\n")
        return real(path, **kwargs)

    monkeypatch.setattr(snapshot, "read_regular_file", read)
    found = labaddon.survey(Layout(flavor), budget=budget)
    assert _results(found) == [
        (SECOND, True, None),
        (FIRST, False, _bound(f"{budget:,} bytes", FIRST)),
    ]


def test_constructed_file_swapped_between_lstat_and_open_is_refused(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The open is checked against the lstat: a file replaced in between
    (another inode) is refused, never read in the first one's place."""
    swapped = _lab_file(flavor, SECOND)
    real = snapshot.read_regular_file

    def read(path: Path, **kwargs: Any) -> bytes:
        if Path(path) == swapped:
            fresh = swapped.with_name("m12-09-fresh")
            fresh.write_bytes(swapped.read_bytes())
            fresh.replace(swapped)
        return real(path, **kwargs)

    monkeypatch.setattr(snapshot, "read_regular_file", read)
    by = _by(flavor)
    assert by[SECOND].summary is None
    assert by[SECOND].error == "the file was replaced before it was opened"
    assert by[FIRST].summary is not None


def test_constructed_inode_zero_is_never_taken_for_a_hard_link(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file system that reports inode 0 for every file (some do): two
    different files are both read, not taken for one."""

    def lstat_ino_0(path: Any) -> os.stat_result:
        st = os.lstat(path)
        fields = (st.st_mode, 0, st.st_dev, st.st_nlink, st.st_uid, st.st_gid, st.st_size)
        return os.stat_result((*fields, int(st.st_atime), int(st.st_mtime), int(st.st_ctime)))

    real = snapshot.read_regular_file
    monkeypatch.setattr(labaddon, "os", types.SimpleNamespace(lstat=lstat_ino_0))
    monkeypatch.setattr(
        snapshot, "read_regular_file", lambda path, **kw: real(path, limit=kw["limit"])
    )
    found = labaddon.survey(Layout(flavor))
    assert _results(found) == [(SECOND, True, None), (FIRST, True, None)]
    assert all(e.same_as is None for e in found.characters)


def _hard_link(flavor: Path, source: str, character: str, account_dir: str = ACCOUNT_DIR) -> None:
    extra = _lab_file(flavor, character, account_dir)
    extra.parent.mkdir(parents=True)
    try:
        os.link(_lab_file(flavor, source), extra)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot make a hard link here: {exc}")


def test_constructed_hard_linked_copy_is_read_once(flavor: Path) -> None:
    _hard_link(flavor, FIRST, "1/Linked-Copy")
    other = "WTF/Account/90000002#1"
    _hard_link(flavor, FIRST, "2/Far-Copy", other)
    found = labaddon.survey(Layout(flavor))
    by = {(e.account, e.character): e for e in found.characters}
    twin = by[(ACCOUNT, "1/Linked-Copy")]
    assert twin.record is None and twin.summary is None and twin.same_as == FIRST
    assert twin.error == (
        f"the same file as {FIRST} (a hard link), read once, on that character's row"
    )
    far = by[("90000002#1", "2/Far-Copy")]
    assert far.same_as == f"{FIRST} in account {ACCOUNT}"
    assert far.error == (
        f"the same file as {FIRST} in account {ACCOUNT} (a hard link), read once, on that "
        "character's row"
    )
    assert by[(ACCOUNT, FIRST)].record is not None and by[(ACCOUNT, SECOND)].record is not None


def test_constructed_a_hard_link_to_a_file_that_failed_is_read_on_its_own(
    flavor: Path,
) -> None:
    """Only a file read and parsed stands for its twins: a twin of a file
    that failed reports its own error, never "read once"."""
    _lab_file(flavor, FIRST).write_bytes(b"not = a lab file\n")
    _hard_link(flavor, FIRST, "1/Linked-Copy")
    by = _by(flavor)
    assert by[FIRST].error is not None and by[FIRST].error.startswith("not SavedVariables")
    assert by["1/Linked-Copy"].same_as is None
    assert by["1/Linked-Copy"].error == by[FIRST].error


@POSIX_PERMISSIONS
def test_constructed_a_hard_link_to_an_unreadable_file_reports_its_own_error(
    flavor: Path,
) -> None:
    _hard_link(flavor, FIRST, "1/Linked-Copy")
    target = _lab_file(flavor, FIRST)
    target.chmod(0)
    try:
        by = _by(flavor)
    finally:
        target.chmod(0o644)
    assert by[FIRST].error == "Permission denied"
    assert by["1/Linked-Copy"].error == "Permission denied"
    assert by["1/Linked-Copy"].same_as is None


def test_constructed_a_file_over_the_per_file_bound_is_not_charged(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file refused on its own bound (checked first, nothing read) leaves
    the total untouched: the files after it are read."""
    monkeypatch.setattr(luadata, "MAX_FILE_BYTES", _size(flavor, SECOND) - 1)
    budget = _size(flavor, SECOND) + 10
    _plant(flavor, "1/Zz-Tiny", TINY)
    found = labaddon.survey(Layout(flavor), budget=budget)
    size = _size(flavor, SECOND)
    assert _results(found)[0] == (
        SECOND,
        False,
        f"the file holds {size} bytes, more than {size - 1}",
    )
    assert _results(found)[2] == ("1/Zz-Tiny", True, None)


def test_constructed_summaries_without_records_when_asked(flavor: Path) -> None:
    kept = labaddon.survey(Layout(flavor))
    lean = labaddon.survey(Layout(flavor), keep_records=False)
    assert [e.record for e in lean.characters] == [None, None]
    assert [e.summary for e in lean.characters] == [e.summary for e in kept.characters]
    assert [e.summary for e in kept.characters] == [
        labaddon.summary(e.record) for e in kept.characters if e.record is not None
    ]


# ─── choosing the file ───────────────────────────────────────────────────────


SV = f"{ACCOUNT_DIR}/{FIRST}/SavedVariables"


def test_choose_lab_file_prefers_the_exact_whole_name() -> None:
    choose = labaddon.choose_lab_file
    assert choose([f"{SV}/WowLab.LUA", f"{SV}/WowLab.lua"]) == f"{SV}/WowLab.lua"
    assert choose([f"{SV}/wowlab.lua", f"{SV}/WowLab.lua", f"{SV}/WOWLAB.LUA"]) == (
        f"{SV}/WowLab.lua"
    )
    assert choose([f"{SV}/wowlab.lua"]) == f"{SV}/wowlab.lua"
    assert choose([f"{SV}/WowLab.lua.bak", f"{SV}/Syndicator.lua"]) is None
    assert choose([]) is None


def test_constructed_choose_lab_file_refuses_several_without_one_choice() -> None:
    with pytest.raises(labaddon.LabAddonError) as refused:
        labaddon.choose_lab_file([f"{SV}/wowlab.lua", f"{SV}/WOWLAB.lua"])
    assert str(refused.value) == (
        "the character folder holds 2 files named like WowLab.lua (SavedVariables/WOWLAB.lua, "
        "SavedVariables/wowlab.lua, none spelled exactly WowLab.lua); which one the client "
        "reads is not known, so none was read"
    )
    with pytest.raises(labaddon.LabAddonError) as twice:
        labaddon.choose_lab_file(
            [f"{SV}/WowLab.lua", f"{ACCOUNT_DIR}/{FIRST}/savedvariables/WowLab.lua"]
        )
    assert str(twice.value).startswith(
        "the character folder holds 2 files named like WowLab.lua "
        "(SavedVariables/WowLab.lua, savedvariables/WowLab.lua); "
    )


def _with_listed(lay: Layout, monkeypatch: pytest.MonkeyPatch, spellings: dict[str, str]) -> None:
    """The walk lists, beside a character's real WowLab.lua, another spelling
    of it (character -> spelling): names a case-insensitive volume cannot
    hold twice."""
    walk = lay.wtf_walk()
    extra = []
    for f in walk.saved_variables:
        for character, spelling in spellings.items():
            if f.path == f"{ACCOUNT_DIR}/{character}/SavedVariables/WowLab.lua":
                path = f.path[: -len("WowLab.lua")] + spelling
                extra.append(f.model_copy(update={"path": path, "addon": spelling[:-4]}))
    changed = walk.model_copy(update={"saved_variables": (*walk.saved_variables, *extra)})
    monkeypatch.setattr(lay, "wtf_walk", lambda: changed)


def test_constructed_exact_name_wins_over_listed_case_variants(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lay = Layout(flavor)
    _with_listed(lay, monkeypatch, {FIRST: "WowLab.LUA", SECOND: "wowlab.lua"})
    by = {e.character: e for e in labaddon.read_all(lay)}
    for character in (FIRST, SECOND):
        assert by[character].file.endswith("/SavedVariables/WowLab.lua")
        assert by[character].record is not None


def test_constructed_two_case_variants_without_the_exact_name_are_refused(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    donor = _lab_file(flavor, FIRST).read_bytes()
    sv = flavor / ACCOUNT_DIR / "1/Two-Variants/SavedVariables"
    sv.mkdir(parents=True)
    (sv / "wowlab.lua").write_bytes(donor)
    lay = Layout(flavor)
    walk = lay.wtf_walk()
    (real,) = [
        f for f in walk.saved_variables if f.path.endswith("Two-Variants/SavedVariables/wowlab.lua")
    ]
    variant = real.model_copy(update={"path": real.path[: -len("wowlab.lua")] + "WOWLAB.lua"})
    changed = walk.model_copy(update={"saved_variables": (*walk.saved_variables, variant)})
    monkeypatch.setattr(lay, "wtf_walk", lambda: changed)
    by = {e.character: e for e in labaddon.read_all(lay)}
    entry = by["1/Two-Variants"]
    assert entry.record is None
    assert entry.error == (
        "the character folder holds 2 files named like WowLab.lua (SavedVariables/WOWLAB.lua, "
        "SavedVariables/wowlab.lua, none spelled exactly WowLab.lua); which one the client "
        "reads is not known, so none was read"
    )
    assert entry.file.endswith("/SavedVariables/WOWLAB.lua")
    assert by[FIRST].record is not None and by[SECOND].record is not None


def test_constructed_case_variants_on_a_case_sensitive_volume(flavor: Path, tmp_path: Path) -> None:
    if not _case_sensitive(tmp_path):
        pytest.skip("needs a case-sensitive volume to hold names that differ only in case")
    donor = _lab_file(flavor, FIRST).read_bytes()
    sv = flavor / ACCOUNT_DIR / "1/Two-Variants/SavedVariables"
    sv.mkdir(parents=True)
    (sv / "wowlab.lua").write_bytes(donor)
    (sv / "WOWLAB.lua").write_bytes(donor)
    _lab_file(flavor, FIRST).with_name("wowlab.lua").write_bytes(b"not lua at all")
    _lab_file(flavor, SECOND).with_name("WowLab.LUA").write_bytes(b"not lua at all")
    by = _by(flavor)
    assert by["1/Two-Variants"].record is None
    assert "none spelled exactly WowLab.lua" in (by["1/Two-Variants"].error or "")
    for character in (FIRST, SECOND):
        assert by[character].file.endswith("/WowLab.lua") and by[character].record is not None


def test_constructed_a_case_variant_alone_is_read(flavor: Path) -> None:
    donor = _lab_file(flavor, FIRST).read_bytes()
    sv = flavor / ACCOUNT_DIR / "1/Lower-Case/SavedVariables"
    sv.mkdir(parents=True)
    (sv / "wowlab.lua").write_bytes(donor)
    entry = _by(flavor)["1/Lower-Case"]
    assert entry.file.endswith("/SavedVariables/wowlab.lua")
    assert entry.record is not None and entry.error is None


def test_constructed_backups_are_never_read(flavor: Path) -> None:
    donor = _lab_file(flavor, FIRST).read_bytes()
    shutil.copy2(_lab_file(flavor, FIRST), _lab_file(flavor, FIRST).with_name("WowLab.lua.bak"))
    only_backup = flavor / ACCOUNT_DIR / "1/Backup-Only/SavedVariables"
    only_backup.mkdir(parents=True)
    (only_backup / "WowLab.lua.bak").write_bytes(donor)
    entries = labaddon.read_all(Layout(flavor))
    assert [e.character for e in entries] == [SECOND, FIRST]
    assert all(e.file.endswith("/WowLab.lua") for e in entries)


def test_constructed_other_addon_files_are_not_read(flavor: Path) -> None:
    sv = flavor / ACCOUNT_DIR / "1/Other-Addon/SavedVariables"
    sv.mkdir(parents=True)
    (sv / "WowLabExtra.lua").write_bytes(b"WowLabCharDB = {}\n")
    (sv / "Syndicator.lua").write_bytes(b"x = 1\n")
    assert [e.character for e in labaddon.read_all(Layout(flavor))] == [SECOND, FIRST]


def test_constructed_character_named_like_the_saved_variables_folder_is_not_invented(
    flavor: Path,
) -> None:
    """A `SavedVariables/` folder where a character folder would be is not a
    character (layout's rule): its files are not read as one."""
    donor = _lab_file(flavor, FIRST).read_bytes()
    odd = flavor / ACCOUNT_DIR / "1/SavedVariables/SavedVariables"
    odd.mkdir(parents=True)
    (odd / "WowLab.lua").write_bytes(donor)
    assert [e.character for e in labaddon.read_all(Layout(flavor))] == [SECOND, FIRST]


# ─── folders, accounts and order ─────────────────────────────────────────────


def test_constructed_both_folder_shapes_are_listed_in_a_stable_order(flavor: Path) -> None:
    donor = _lab_file(flavor, FIRST).read_bytes()
    # Created in an order unlike the sorted one, with names that sort
    # differently with case folded than as spelled.
    for character in ("1/zeta-A", "Realm Name/Gamma", "1/Alpha-B", "1/beta-C"):
        _plant(flavor, character, donor)
    expected = ["1/Alpha-B", "1/beta-C", SECOND, FIRST, "1/zeta-A", "Realm Name/Gamma"]
    first = labaddon.read_all(Layout(flavor))
    assert [e.character for e in first] == expected
    assert all(e.error is None for e in first)
    shapes = {e.character: e.shape for e in first}
    assert shapes["Realm Name/Gamma"] == "realm_name" and shapes[FIRST] == "numeric_folder"
    # Newest first, then oldest first: the order never follows the time.
    for step, character in enumerate(expected):
        stamp = 1_000_000_000_000_000_000 + step * 1_000_000_000
        os.utime(_lab_file(flavor, character), ns=(stamp, stamp))
    assert [e.character for e in labaddon.read_all(Layout(flavor))] == expected
    for step, character in enumerate(reversed(expected)):
        stamp = 1_000_000_000_000_000_000 + step * 1_000_000_000
        os.utime(_lab_file(flavor, character), ns=(stamp, stamp))
    again = labaddon.read_all(Layout(flavor))
    assert [e.character for e in again] == expected
    assert [e.record for e in again] == [e.record for e in first]


def test_constructed_every_account_unless_one_is_named(flavor: Path) -> None:
    donor = _lab_file(flavor, FIRST).read_bytes()
    other = "WTF/Account/90000002#1"
    _plant(flavor, "2/Other-Char", donor, other)
    everyone = labaddon.read_all(Layout(flavor))
    assert [(e.account, e.character) for e in everyone] == [
        (ACCOUNT, SECOND),
        (ACCOUNT, FIRST),
        ("90000002#1", "2/Other-Char"),
    ]
    only = labaddon.read_all(Layout(flavor), account="90000002#1")
    assert [(e.account, e.character) for e in only] == [("90000002#1", "2/Other-Char")]
    assert [e.character for e in labaddon.read_all(Layout(flavor), account=ACCOUNT)] == [
        SECOND,
        FIRST,
    ]
    assert labaddon.read_all(Layout(flavor), account="nobody") == []


# ─── the model ───────────────────────────────────────────────────────────────


def _entry(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "label": "Labchard-Labrealmg",
        "account": ACCOUNT,
        "realm_folder": "1",
        "shape": "numeric_folder",
        "file": f"{ACCOUNT_DIR}/{FIRST}/SavedVariables/WowLab.lua",
        "mtime_ns": 1,
    }
    return {**base, **changes}


def test_constructed_an_entry_holds_a_summary_or_an_error(flavor: Path) -> None:
    record = labaddon.read_char(_lab_file(flavor, FIRST))
    words = labaddon.summary(record)
    with pytest.raises(ValidationError, match="either a summary"):
        labaddon.CharacterFile.model_validate(_entry())
    with pytest.raises(ValidationError, match="either a summary"):
        labaddon.CharacterFile.model_validate(_entry(summary=words, error="both"))
    with pytest.raises(ValidationError, match="either a summary"):
        labaddon.CharacterFile.model_validate(_entry(record=record))
    with pytest.raises(ValidationError, match="with an error holds no record"):
        labaddon.CharacterFile.model_validate(_entry(record=record, summary=None, error="x"))
    kept = labaddon.CharacterFile.model_validate(_entry(record=record, summary=words))
    assert kept.record == record and kept.place == "file"
    assert labaddon.CharacterFile.model_validate(_entry(summary=words)).record is None
    assert labaddon.CharacterFile.model_validate(_entry(error="why")).error == "why"
    with pytest.raises(ValidationError):
        labaddon.CharacterFile.model_validate(_entry(error="why", place="somewhere"))
    assert (
        labaddon.CharacterFile.model_validate(_entry(error="why", mtime_ns=None)).mtime_ns is None
    )
    with pytest.raises(ValidationError):
        labaddon.CharacterFile.model_validate(_entry(error="why", shape="realm"))


def test_constructed_a_name_that_is_not_utf8_round_trips_through_json() -> None:
    label = "Bad" + chr(0xDCFF) + "-Name"  # the byte 0xFF, carried by surrogateescape
    entry = labaddon.CharacterFile.model_validate(_entry(label=label, error="why"))
    text = entry.model_dump_json()
    assert chr(0xDCFF) not in text
    assert labaddon.CharacterFile.model_validate_json(text) == entry


def test_constructed_summary_words_absent_and_missing_sections() -> None:
    absent = labaddon.load_char(
        {"schema": 2, "client": {"absent": "no GetBuildInfo"}, "spec": {"absent": "no API"}}
    )
    assert labaddon.summary(absent) == (
        "schema 2, client absent (the reason is in char show), "
        "spec absent (the reason is in char show)"
    )
    missing = labaddon.load_char({"schema": 1})
    assert labaddon.summary(missing) == "schema 1, client not in the file, spec not in the file"
    unreturned = labaddon.load_char(
        {"schema": 1, "client": {"version": "1.60.1"}, "spec": {"api": "C_SpecializationInfo"}}
    )
    assert labaddon.summary(unreturned) == (
        "schema 1, saved by client version 1.60.1, build not returned, spec id not returned"
    )
