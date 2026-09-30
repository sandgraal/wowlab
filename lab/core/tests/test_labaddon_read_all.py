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
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

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
NOT_FOLLOWED = "a link, not followed: wowlab does not follow links"
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
    def broken(path: Path) -> labaddon.CharDB:
        raise RuntimeError("a bug, not a file problem")

    monkeypatch.setattr(labaddon, "read_char", broken)
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
    assert by[SECOND].error == NOT_FOLLOWED
    assert by[SECOND].file == f"{ACCOUNT_DIR}/{SECOND}/SavedVariables/WowLab.lua"
    assert by[FIRST].record is not None


def test_constructed_linked_saved_variables_folder_is_named(flavor: Path, tmp_path: Path) -> None:
    _move_and_link(_lab_file(flavor, SECOND).parent, tmp_path, is_dir=True)
    by = _by(flavor)
    assert by[SECOND].record is None and by[SECOND].mtime_ns is None
    assert by[SECOND].error == f"its SavedVariables folder: {NOT_FOLLOWED}"
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
    assert entry.error == f"the character folder: {NOT_FOLLOWED}"
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
    assert by[SECOND].error == "its SavedVariables folder: Permission denied"
    assert by[SECOND].file == f"{ACCOUNT_DIR}/{SECOND}/SavedVariables"
    assert by[FIRST].record is not None


@POSIX_PERMISSIONS
def test_constructed_unlistable_character_folder_is_named(flavor: Path) -> None:
    with _unlistable(flavor / ACCOUNT_DIR / SECOND):
        by = _by(flavor)
    assert by[SECOND].record is None
    assert by[SECOND].error == "the character folder: Permission denied"
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
    assert found.not_looked_at == [labaddon.NotLookedAt(path=folder, reason="Permission denied")]


@pytest.mark.parametrize(
    "folder",
    [
        pytest.param("WTF", id="constructed-wtf"),
        pytest.param("WTF/Account", id="constructed-accounts-folder"),
        pytest.param(ACCOUNT_DIR, id="constructed-account"),
        pytest.param(f"{ACCOUNT_DIR}/1", id="constructed-digits-folder"),
    ],
)
def test_constructed_linked_folder_above_the_characters_is_named(
    flavor: Path, tmp_path: Path, folder: str
) -> None:
    _move_and_link(flavor / folder, tmp_path, is_dir=True)
    found = labaddon.survey(Layout(flavor))
    assert found.characters == []
    assert found.not_looked_at == [labaddon.NotLookedAt(path=folder, reason=NOT_FOLLOWED)]


def test_constructed_truncated_walk_is_named(flavor: Path) -> None:
    found = labaddon.survey(Layout(flavor, limits=Limits(max_entries=10)))
    assert found.not_looked_at == [
        labaddon.NotLookedAt(
            path="WTF",
            reason=(
                "the walk stopped at its bound (10 entries, 24 folders deep): what lies past "
                "it was not looked at"
            ),
        )
    ]


def test_constructed_links_and_fifos_that_hide_no_lab_file_are_not_named(
    flavor: Path, tmp_path: Path
) -> None:
    """A linked account-level file the file map names, the account's own
    SavedVariables/, another addon's file, a twin's AddOns.txt and a FIFO
    with another name cannot hide a character's WowLab.lua."""
    _move_and_link(flavor / ACCOUNT_DIR / "config-cache.wtf", tmp_path, is_dir=False)
    _move_and_link(flavor / ACCOUNT_DIR / "SavedVariables", tmp_path, is_dir=True)
    _move_and_link(flavor / ACCOUNT_DIR / TWIN / "AddOns.txt", tmp_path, is_dir=False)
    other = _lab_file(flavor, FIRST).with_name("Syndicator.lua")
    other.write_bytes(b"x = 1\n")
    _move_and_link(other, tmp_path, is_dir=False)
    if hasattr(os, "mkfifo"):
        os.mkfifo(_lab_file(flavor, FIRST).with_name("Other.lua"))
    found = labaddon.survey(Layout(flavor))
    assert found.not_looked_at == []
    assert [(e.character, e.error) for e in found.characters] == [(SECOND, None), (FIRST, None)]


def test_constructed_places_in_another_account_are_left_out_with_account(
    flavor: Path, tmp_path: Path
) -> None:
    other = "WTF/Account/90000002#1"
    _plant(flavor, "2/Other-Char", b"broken =\n", other)
    (flavor / other / "4").mkdir()
    _move_and_link(flavor / other / "4", tmp_path, is_dir=True)
    everyone = labaddon.survey(Layout(flavor))
    assert everyone.not_looked_at == [labaddon.NotLookedAt(path=f"{other}/4", reason=NOT_FOLLOWED)]
    assert [e.character for e in everyone.characters] == [SECOND, FIRST, "2/Other-Char"]
    mine = labaddon.survey(Layout(flavor), account=ACCOUNT)
    assert mine.not_looked_at == []
    assert [e.character for e in mine.characters] == [SECOND, FIRST]


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


def test_constructed_an_entry_holds_a_record_or_an_error(flavor: Path) -> None:
    record = labaddon.read_char(_lab_file(flavor, FIRST))
    with pytest.raises(ValidationError, match="either a record or an error"):
        labaddon.CharacterFile.model_validate(_entry())
    with pytest.raises(ValidationError, match="either a record or an error"):
        labaddon.CharacterFile.model_validate(_entry(record=record, error="both"))
    assert labaddon.CharacterFile.model_validate(_entry(record=record)).record == record
    assert labaddon.CharacterFile.model_validate(_entry(error="why")).error == "why"
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
