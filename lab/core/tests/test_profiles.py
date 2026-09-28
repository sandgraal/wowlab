"""`wowlab profile` and `wowlab_core.profiles` (docs/LAB_PLAN.md §13.3, M11-08).

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
as `test_cli.py` builds it: nothing here reads or writes a real install, the
user data directory is redirected into `tmp_path`, and the process table is
a fake one.

Constructed (labelled in the test ids): the two character folders of the
copy are renamed so the pair reads as Forever writes it, a
`<digits>/<First>-<Second>/` folder and its `<Realm>/<First>/` twin sharing
the first name (the scrubbed capture gave the two different pseudonyms);
and the files changed, added or planted after a save (`.DS_Store`, a new
addon, a character folder, an executable, the lab-addon). No format is
parsed here; files are moved as bytes.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    ACCOUNT,
    FLAVOR,
    _plain,
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

from wowlab_core import cli, guard, install, layout, profiles
from wowlab_core.snapshot import SnapshotStore

ACCT = f"WTF/Account/{ACCOUNT}"
CHAR = f"{ACCT}/1/Labcharb-Labsecondb"  # a <digits>/<First>-<Second> character folder
TWIN = f"{ACCT}/Labrealmb Partb Partc Partd/Labcharb"  # its <Realm>/<First> twin (AddOns.txt)
SV = f"{ACCT}/SavedVariables"
LAB = "Interface/AddOns/WowLab"
PRESETS_TOML = Path(profiles.__file__).with_name(profiles.PRESETS_FILE)


@pytest.fixture(autouse=True)
def forever_pair(root: Path) -> None:
    """Constructed: rename the copy's character folders into a twin pair."""
    acct = root / FLAVOR / ACCT
    (acct / "1" / "Labcharb-Labrealmd").rename(acct / "1" / "Labcharb-Labsecondb")
    realm = acct / "Labrealmb Partb Partc Partd"
    (realm / "Labchard").rename(realm / "Labcharb")


def _bytes_under(root: Path) -> dict[str, bytes | None]:
    """Every entry under `root` with its bytes (None for a folder)."""
    return {
        p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }


def _json_of[M: cli.BaseModel](model: type[M], *args: str) -> M:
    result = ok(*args, "--json")
    return model.model_validate_json(result.stdout)


def _append(path: Path, text: bytes) -> None:
    path.write_bytes(path.read_bytes() + text)


def _plant_lab_addon(flavor: Path) -> None:
    (flavor / LAB).mkdir(parents=True)
    (flavor / LAB / "WowLab.toc").write_bytes(b"## Title: constructed\n")
    (flavor / LAB / "WowLab.lua").write_bytes(b"-- constructed source\n")
    (flavor / SV / "WowLab.lua").write_bytes(b"WowLabDB = {}\n")
    (flavor / CHAR / "SavedVariables").mkdir()
    (flavor / CHAR / "SavedVariables" / "WowLab.lua").write_bytes(b"WowLabCharDB = {}\n")


# ─── presets are data ────────────────────────────────────────────────────────


def test_presets_are_the_four_of_13_3_and_name_no_flavor() -> None:
    found = profiles.presets()
    assert set(found) == {"ui", "bindings", "macros", "addons"}
    assert found["ui"].flavor == ("WTF/Config.wtf",)
    assert set(found["ui"].account) == {"config-cache.wtf", "edit-mode-cache-account.txt"}
    assert set(found["ui"].character) == {
        "config-cache.wtf",
        "edit-mode-cache-character.txt",
        "layout-local.txt",
        "chat-cache.txt",
    }
    assert found["bindings"].account == ("bindings-cache.wtf",)
    assert set(found["bindings"].character) == {"bindings-cache.wtf", "click-bindings-cache.txt"}
    assert found["macros"].account == found["macros"].character == ("macros-cache.txt",)
    assert found["addons"].flavor == ("Interface/AddOns",)
    assert set(found["addons"].character) == {"AddOns.txt", "SavedVariables"}
    assert found["ui"].summary.startswith("Config.wtf (machine-wide: also graphics, sound,")
    assert "check your bars after applying" in found["macros"].summary
    assert found["addons"].summary == (
        "Interface/AddOns/ as saved (addons installed since are removed, updates since are "
        "undone), AddOns.txt, addon SavedVariables; never the lab-addon "
        "(Interface/AddOns/WowLab/, WowLab.lua)"
    )
    # L6: no flavor folder (`_x_`), product code or interface/build number in the data.
    data = "\n".join(
        line
        for line in PRESETS_TOML.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert not re.search(r"(?<![A-Za-z0-9])_[a-z_]+_(?![A-Za-z0-9])", data)
    assert not re.search(r"\bwow(_[a-z_]*)?\b", data)  # product codes: wow, wow_classic_beta…
    assert not re.search(r"\d{4,}", data)


def test_the_lab_addon_is_excluded_from_every_profile_by_data() -> None:
    exclude = profiles.always_excluded()
    assert exclude.flavor == (LAB,)
    for scope in (exclude.account, exclude.character):
        assert set(scope) == {"SavedVariables/WowLab.lua", "SavedVariables/WowLab.lua.bak"}
    assert profiles.is_always_excluded(f"{LAB}/WowLab.toc")
    assert profiles.is_always_excluded(f"{SV}/WowLab.lua")
    assert profiles.is_always_excluded(f"{CHAR}/SavedVariables/WowLab.lua.bak")
    assert not profiles.is_always_excluded("Interface/AddOns/WowLabExtra/x.lua")
    assert not profiles.is_always_excluded(f"{SV}/Syndicator.lua")


@pytest.mark.parametrize(
    "bad",
    ['flavor = ["../x"]', 'account = ["/abs"]', 'character = ["a//b"]', 'flavor = ["a\\\\b"]'],
    ids=[
        "dotdot-constructed",
        "absolute-constructed",
        "empty-part-constructed",
        "backslash-constructed",
    ],
)
def test_a_preset_path_must_be_plain_and_relative_constructed(bad: str) -> None:
    with pytest.raises(ValueError, match="plain relative path"):
        profiles.parse_presets(f'[presets.x]\nsummary = "s"\n{bad}\n')


def test_ui_selection_joins_names_to_every_account_and_character_constructed(
    flavor: Path,
) -> None:
    lay = layout.Layout(flavor)
    sel = profiles.select(lay, preset_names=["ui"])
    assert "WTF/Config.wtf" in sel.subtrees
    assert f"{ACCT}/config-cache.wtf" in sel.subtrees
    assert f"{ACCT}/edit-mode-cache-account.txt" in sel.subtrees
    for folder in (CHAR, TWIN):  # both shapes, present or not
        for name in ("config-cache.wtf", "layout-local.txt", "chat-cache.txt"):
            assert f"{folder}/{name}" in sel.subtrees
    assert f"{ACCT}/bindings-cache.wtf" not in sel.subtrees
    assert {LAB, f"{SV}/WowLab.lua", f"{CHAR}/SavedVariables/WowLab.lua"} <= set(sel.excluded)


def test_addons_selection_constructed(flavor: Path) -> None:
    sel = profiles.select(layout.Layout(flavor), preset_names=["addons"])
    assert {"Interface/AddOns", SV, f"{TWIN}/AddOns.txt", f"{CHAR}/SavedVariables"} <= set(
        sel.subtrees
    )
    assert {LAB, f"{SV}/WowLab.lua", f"{SV}/WowLab.lua.bak"} <= set(sel.excluded)


# ─── save, list, show, delete ────────────────────────────────────────────────


def test_save_reads_only_and_json_validates_constructed(root: Path, flavor: Path) -> None:
    before = _state(root)
    report = _json_of(cli.ProfileReport, "profile", "save", "look", "--preset", "ui")
    assert _state(root) == before, "save wrote into the install (L1)"
    assert report.profile.name == "look"
    assert report.profile.presets == ["ui"]
    paths = {e.path for e in report.entries}
    assert f"{FLAVOR}/WTF/Config.wtf" in paths
    assert f"{FLAVOR}/{CHAR}/chat-cache.txt" in paths
    assert f"{FLAVOR}/{ACCT}/bindings-cache.wtf" not in paths
    assert profiles.SERVER_SIDE_NOTE in report.notes
    store = SnapshotStore()
    manifest = store.show(report.profile.snapshot_id)
    assert manifest.label == "profile:look presets=ui"
    assert manifest.purpose == "profile"
    raw = (store.manifests_dir / f"{manifest.id}.json").read_bytes()
    assert b'"purpose":"profile"' in raw


def test_list_show_delete_and_json_constructed(root: Path) -> None:
    ok("profile", "save", "a", "--preset", "bindings")
    ok("profile", "save", "b", "--subtree", f"{ACCT}/macros-cache.txt")
    listed = _json_of(cli.ProfileListReport, "profile", "list")
    assert [p.name for p in listed.profiles] == ["a", "b"]
    assert listed.profiles[1].presets == []
    shown = _json_of(cli.ProfileReport, "profile", "show", "b")
    assert [e.path for e in shown.entries] == [f"{FLAVOR}/{ACCT}/macros-cache.txt"]
    text = _plain(ok("profile", "show", "b").stdout)
    assert "Profile b" in text and "macros-cache.txt" in text
    deleted = _json_of(cli.ProfileDeleteReport, "profile", "delete", "a")
    (gone,) = deleted.relabelled
    assert gone.label == "deleted-profile:a presets=bindings"
    assert [p.name for p in _json_of(cli.ProfileListReport, "profile", "list").profiles] == ["b"]
    # The snapshot stays in the store, relabelled; it is no longer a profile.
    assert SnapshotStore().show(gone.id).label == gone.label
    missing = run("profile", "show", "a")
    assert missing.exit_code == 1 and "no profile named 'a'" in missing.stderr
    assert run("profile", "delete", "a").exit_code == 1


@pytest.mark.parametrize("label", ["profile:a", "deleted-profile:a", "profile:"])
def test_snap_create_refuses_profile_labels_constructed(root: Path, label: str) -> None:
    before = _state(root)
    result = run("snap", "create", "-m", label)
    assert result.exit_code == 2, (result.stdout, result.stderr)
    assert "belong to `wowlab profile`" in result.stderr
    assert _state(root) == before
    assert SnapshotStore().list_lenient().manifests == ()
    ok("snap", "create", "-m", "a profile: not reserved")  # only the prefix is reserved


def test_a_snapshot_is_a_profile_only_with_the_marker_constructed(root: Path) -> None:
    """A snapshot labelled `profile:a` without `purpose="profile"` (made by a
    library caller) is not a profile; an ordinary manifest has no marker."""
    store = SnapshotStore()
    plain = store.create(root, [f"{FLAVOR}/WTF"], label="profile:a", flavor_folder=FLAVOR)
    assert plain.purpose is None
    raw = (store.manifests_dir / f"{plain.id}.json").read_bytes()
    assert b"purpose" not in raw, "a manifest without a purpose is written as before M11-08"
    assert profiles.listing(store).profiles == ()
    assert run("profile", "show", "a").exit_code == 1
    ok("profile", "save", "a", "--preset", "macros")
    assert profiles.find(store, "a").manifest.purpose == "profile"


def test_two_profile_snapshots_with_one_name_constructed(root: Path) -> None:
    """`save` never makes a second one; a library caller can. `find` refuses
    the ambiguity and `delete` relabels both."""
    ok("profile", "save", "a", "--preset", "macros")
    SnapshotStore().create(
        root, [f"{FLAVOR}/WTF"], label="profile:a", flavor_folder=FLAVOR, purpose="profile"
    )
    shown = run("profile", "show", "a")
    assert shown.exit_code == 1 and "2 snapshots are labelled as profile 'a'" in shown.stderr
    deleted = _json_of(cli.ProfileDeleteReport, "profile", "delete", "a")
    assert len(deleted.relabelled) == 2
    assert profiles.listing(SnapshotStore()).profiles == ()


def test_save_refuses_a_name_in_use_constructed(root: Path) -> None:
    ok("profile", "save", "a", "--preset", "macros")
    again = run("profile", "save", "a", "--preset", "ui")
    assert again.exit_code == 1 and "a profile named 'a' exists" in again.stderr
    assert len(profiles.listing(SnapshotStore()).profiles) == 1


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--preset", "ui", "--subtree", "WTF"], "not both"),
        ([], "not both"),
        (["--preset", "nope"], "no preset 'nope'"),
        (["--subtree", "Cache"], "outside"),
        (["--subtree", "WTF/../Data"], "plain relative path"),
        (["--subtree", "WTF"], "snap restore"),
        (["--subtree", "wtf/Account/"], "snap restore"),
        (["--subtree", "Interface"], "snap restore"),
        (["--subtree", "Fonts"], "snap restore"),
        (["--subtree", LAB], "the lab-addon"),
        (["--subtree", f"{SV}/WowLab.lua"], "the lab-addon"),
    ],
    ids=[
        "both-constructed",
        "neither-constructed",
        "unknown-preset-constructed",
        "outside-subtrees-constructed",
        "dotdot-constructed",
        "wtf-constructed",
        "wtf-account-constructed",
        "interface-constructed",
        "fonts-constructed",
        "lab-addon-code-constructed",
        "lab-addon-sv-constructed",
    ],
)
def test_save_usage_errors_exit_2(root: Path, args: list[str], message: str) -> None:
    result = run("profile", "save", "x", *args)
    assert result.exit_code == 2, (result.stdout, result.stderr)
    assert message in result.stderr
    assert profiles.listing(SnapshotStore()).profiles == ()


def test_a_bad_name_exits_2_constructed(root: Path) -> None:
    result = run("profile", "save", "has space", "--preset", "ui")
    assert result.exit_code == 2 and "not a profile name" in result.stderr


# ─── apply ───────────────────────────────────────────────────────────────────


def test_apply_returns_chosen_subtrees_to_saved_bytes_and_leaves_others_constructed(
    root: Path, flavor: Path
) -> None:
    ok("profile", "save", "look", "--preset", "ui")
    saved = _bytes_under(flavor)
    # Change files in the profile, files outside it, and add some.
    _append(flavor / "WTF/Config.wtf", b'SET constructedCVar "1"\n')
    _append(flavor / ACCT / "config-cache.wtf", b'SET constructedCVar "2"\n')
    (flavor / CHAR / "layout-local.txt").write_bytes(b"constructed\n")
    (flavor / TWIN / "config-cache.wtf").write_bytes(b"added since save\n")  # in a subtree
    _append(flavor / ACCT / "bindings-cache.wtf", b"bind CTRL-X constructed\n")  # not in ui
    _append(flavor / SV / "Syndicator.lua", b"\n-- constructed\n")  # not in ui
    new_char = flavor / ACCT / "1" / "Newchar-Labsecondc"  # created after the save
    new_char.mkdir()
    (new_char / "config-cache.wtf").write_bytes(b"new character\n")
    outside = _bytes_under(flavor)

    report = _json_of(cli.ProfileApplyReport, "profile", "apply", "look", "--yes")
    assert report.applied and report.transaction is not None
    assert report.added == [f"{TWIN}/config-cache.wtf"]
    assert profiles.PRESET_SCOPE_NOTE in report.notes

    after = _bytes_under(flavor)
    in_profile = {"WTF/Config.wtf", f"{ACCT}/config-cache.wtf", f"{CHAR}/layout-local.txt"}
    for path in in_profile:
        assert after[path] == saved[path], path
    assert f"{TWIN}/config-cache.wtf" not in after, "a file added in a subtree is removed"
    for path, data in outside.items():
        if path in in_profile or path == f"{TWIN}/config-cache.wtf":
            continue
        assert after.get(path) == data, f"{path} is outside the profile and must be left alone"
    assert after[f"{ACCT}/1/Newchar-Labsecondc/config-cache.wtf"] == b"new character\n"


def test_an_explicit_subtree_deletes_files_added_anywhere_under_it_constructed(
    root: Path, flavor: Path
) -> None:
    ok("profile", "save", "acct", "--subtree", f"{ACCT}/1")
    new_char = flavor / ACCT / "1" / "Newchar-Labsecondc"
    new_char.mkdir()
    (new_char / "macros-cache.txt").write_bytes(b"constructed\n")
    dry = _json_of(cli.ProfileApplyReport, "profile", "apply", "acct", "--dry-run")
    assert dry.added == [f"{ACCT}/1/Newchar-Labsecondc/macros-cache.txt"]
    assert profiles.SUBTREE_SCOPE_NOTE in dry.notes
    assert profiles.PRESET_SCOPE_NOTE not in dry.notes
    assert profiles.SUBTREE_SCOPE_NOTE == (
        "An explicit subtree: files added anywhere under it since the save are deleted, "
        "including every file in a character folder created under it since."
    )
    lines = _plain(ok("profile", "apply", "acct", "--dry-run").stdout).splitlines()
    assert lines[0].startswith("Apply profile acct")
    assert lines[1] == f"  {profiles.SUBTREE_SCOPE_NOTE}", "the scope note is under the header"
    shown = _json_of(cli.ProfileReport, "profile", "show", "acct")
    assert shown.notes[0] == profiles.SUBTREE_SCOPE_NOTE


def test_save_and_show_state_the_preset_scope_constructed(root: Path) -> None:
    saved = _json_of(cli.ProfileReport, "profile", "save", "look", "--preset", "ui")
    assert saved.notes[0] == profiles.PRESET_SCOPE_NOTE
    assert _json_of(cli.ProfileReport, "profile", "show", "look").notes[0] == (
        profiles.PRESET_SCOPE_NOTE
    )
    assert profiles.PRESET_SCOPE_NOTE in " ".join(ok("profile", "show", "look").stdout.split())


def test_subtree_help_names_character_folders_created_since() -> None:
    text = " ".join(_plain(ok("profile", "save", "--help").stdout).split())
    assert "including in character folders created since, are deleted by an apply" in text


def test_apply_lists_every_cache_file_with_the_note_constructed(root: Path, flavor: Path) -> None:
    ok("profile", "save", "look", "--preset", "ui")
    _append(flavor / "WTF/Config.wtf", b'SET constructedCVar "1"\n')
    _append(flavor / ACCT / "config-cache.wtf", b'SET constructedCVar "2"\n')
    _append(flavor / ACCT / "edit-mode-cache-account.txt", b"constructed")
    _append(flavor / CHAR / "chat-cache.txt", b"constructed\n")
    (flavor / TWIN / "config-cache.wtf").write_bytes(b"added since save\n")

    dry = _json_of(cli.ProfileApplyReport, "profile", "apply", "look", "--dry-run")
    expected = {
        f"{ACCT}/config-cache.wtf": ("write", profiles.SERVER_NOTE),
        f"{ACCT}/edit-mode-cache-account.txt": ("write", profiles.SERVER_NOTE),
        f"{CHAR}/chat-cache.txt": ("write", profiles.SERVER_NOTE),
        f"{TWIN}/config-cache.wtf": ("delete", profiles.SERVER_DELETE_NOTE),
    }
    assert {c.path: (c.action, c.note) for c in dry.cache_files} == expected
    assert profiles.SERVER_NOTE == (
        "the server may replace this at your next login (synchronize* CVars; see `wowlab doctor`)"
    )
    assert profiles.SERVER_DELETE_NOTE == (
        "the server may write this file again at your next login (synchronize* CVars; "
        "see `wowlab doctor`)"
    )
    assert "WTF/Config.wtf" in {i.path for i in dry.plan}
    assert "WTF/Config.wtf" not in {c.path for c in dry.cache_files}
    assert profiles.MACROS_NOTE not in dry.notes
    assert not dry.applied

    text = _plain(ok("profile", "apply", "look", "--yes").stdout)
    for path, (_, note) in expected.items():
        assert f"{path}: {note}" in text
    assert "proven only by starting the client and logging in" in text
    assert f"delete   {TWIN}/config-cache.wtf  (added since the profile was saved)" in text


def test_macros_apply_carries_the_macros_note_constructed(root: Path, flavor: Path) -> None:
    ok("profile", "save", "m", "--preset", "macros")
    _append(flavor / CHAR / "macros-cache.txt", b"constructed\n")
    dry = _json_of(cli.ProfileApplyReport, "profile", "apply", "m", "--dry-run")
    assert profiles.MACROS_NOTE in dry.notes
    assert profiles.MACROS_NOTE.startswith("Action buttons are kept on the server and may point")
    text = " ".join(_plain(ok("profile", "apply", "m", "--yes").stdout).split())
    assert profiles.MACROS_NOTE in text


def test_apply_skips_edit_no_files_constructed(root: Path, flavor: Path) -> None:
    ok("profile", "save", "mods", "--preset", "addons")
    bak = flavor / SV / "RareScanner.lua.bak"  # file map: savedvariables-backup, Edit no
    lua = flavor / SV / "RareScanner.lua"
    saved_lua = lua.read_bytes()
    _append(bak, b"\n-- constructed\n")
    _append(lua, b"\n-- constructed\n")
    addons = flavor / "Interface/AddOns"
    (addons / ".DS_Store").write_bytes(b"constructed")  # os-metadata, Edit no
    (addons / "Blizzard_Constructed").mkdir()
    (addons / "Blizzard_Constructed" / "x.lua").write_bytes(b"-- constructed\n")
    (addons / "NewAddon").mkdir()
    (addons / "NewAddon" / "NewAddon.toc").write_bytes(b"## Title: constructed\n")
    changed_bak = bak.read_bytes()

    report = _json_of(cli.ProfileApplyReport, "profile", "apply", "mods", "--yes")
    skipped = {s.path: s.action for s in report.skipped}
    assert skipped == {
        f"{SV}/RareScanner.lua.bak": "write",
        "Interface/AddOns/.DS_Store": "delete",
        "Interface/AddOns/Blizzard_Constructed/x.lua": "delete",
    }
    assert {s.entry_id for s in report.skipped} >= {"savedvariables-backup", "os-metadata"}
    assert bak.read_bytes() == changed_bak, "an Edit no file is never written"
    assert (addons / ".DS_Store").exists() and (addons / "Blizzard_Constructed/x.lua").exists()
    assert lua.read_bytes() == saved_lua
    assert not (addons / "NewAddon" / "NewAddon.toc").exists(), "an added addon is removed"
    assert report.added == ["Interface/AddOns/NewAddon/NewAddon.toc"]
    text = _plain(ok("profile", "apply", "mods", "--dry-run").stdout)
    assert 'Skipped 3 file(s) wowlab leaves alone (file-map Edit "no"' in text


def test_the_lab_addon_is_never_saved_restored_or_deleted_constructed(
    root: Path, flavor: Path
) -> None:
    _plant_lab_addon(flavor)
    ok("profile", "save", "mods", "--preset", "addons")
    ok("profile", "save", "sv", "--subtree", SV)
    for name in ("mods", "sv"):
        manifest = profiles.find(SnapshotStore(), name).manifest
        held = [e.path for e in manifest.entries if "WowLab" in e.path]
        assert held == [], f"{name} holds the lab-addon: {held}"
    # Change and add lab-addon files; an apply must leave every one alone.
    (flavor / LAB / "WowLab.lua").write_bytes(b"-- constructed update\n")
    (flavor / LAB / "New.lua").write_bytes(b"-- constructed\n")
    (flavor / SV / "WowLab.lua").write_bytes(b"WowLabDB = { loads = 2 }\n")
    (flavor / SV / "WowLab.lua.bak").write_bytes(b"WowLabDB = {}\n")
    lab_before = {k: v for k, v in _bytes_under(flavor).items() if "WowLab" in k and v is not None}
    for name in ("mods", "sv"):
        ok("profile", "apply", name, "--yes")
    lab_after = {k: v for k, v in _bytes_under(flavor).items() if "WowLab" in k and v is not None}
    assert lab_after == lab_before


def test_explicit_subtree_leaves_a_new_characters_lab_addon_file_constructed(
    root: Path, flavor: Path
) -> None:
    ok("profile", "save", "acct", "--subtree", f"{ACCT}/1")
    sv = flavor / ACCT / "1" / "Newchar-Labsecondc" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "WowLab.lua").write_bytes(b"WowLabCharDB = {}\n")
    (sv / "Other.lua").write_bytes(b"Other = {}\n")
    report = _json_of(cli.ProfileApplyReport, "profile", "apply", "acct", "--yes")
    lab = f"{ACCT}/1/Newchar-Labsecondc/SavedVariables/WowLab.lua"
    assert report.added == [f"{ACCT}/1/Newchar-Labsecondc/SavedVariables/Other.lua"]
    assert {lp.path: lp.reason for lp in report.left} == {lab: profiles.LAB_ADDON_REASON}
    assert (sv / "WowLab.lua").exists() and not (sv / "Other.lua").exists()


def test_an_entry_behind_a_symlinked_folder_is_never_read_constructed(
    root: Path, flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the gate refuses the whole restore (a changed executable), the
    entries are compared with the disk by `profiles` itself. An addon folder
    replaced by a symlink to a folder outside the install must not be read
    through: its entry is left alone, and nothing outside is opened."""
    addons = flavor / "Interface/AddOns"
    for folder, name in (("Tool", "helper.sh"), ("Linked", "a.lua")):
        (addons / folder).mkdir()
        (addons / folder / name).write_bytes(b"-- saved\n")
    ok("profile", "save", "mods", "--preset", "addons")
    (addons / "Tool" / "helper.sh").write_bytes(b"-- changed\n")  # forces the fallback
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.lua").write_bytes(b"-- outside the install\n")
    (addons / "Linked" / "a.lua").unlink()
    (addons / "Linked").rmdir()
    try:
        (addons / "Linked").symlink_to(outside, target_is_directory=True)
    except OSError:  # Windows without the symlink privilege
        pytest.skip("cannot create a symlink here")
    opened: list[str] = []
    real_open = profiles.os.open

    def spy(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        opened.append(str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(profiles.os, "open", spy)
    report = _json_of(cli.ProfileApplyReport, "profile", "apply", "mods", "--dry-run")
    left = {lp.path: lp.reason for lp in report.left}
    assert left["Interface/AddOns/Linked/a.lua"] == profiles.BEHIND_LINK_REASON
    assert "is an executable" in left["Interface/AddOns/Tool/helper.sh"]
    assert not [p for p in opened if str(outside) in p or "Linked" in p], opened
    assert all("Linked" not in i.path for i in report.plan)
    assert (outside / "a.lua").read_bytes() == b"-- outside the install\n"


def test_an_executable_the_gate_refuses_is_left_and_the_rest_applied_constructed(
    root: Path, flavor: Path
) -> None:
    tool = flavor / "Interface/AddOns/Tool"
    tool.mkdir()
    (tool / "Tool.toc").write_bytes(b"## Title: constructed\n")
    (tool / "helper.sh").write_bytes(b"#!/bin/sh\n")
    (tool / "gone.dll").write_bytes(b"MZ constructed")
    ok("profile", "save", "mods", "--preset", "addons")
    lua = flavor / SV / "RareScanner.lua"
    saved_lua = lua.read_bytes()
    _append(lua, b"\n-- constructed\n")
    (tool / "helper.sh").write_bytes(b"#!/bin/sh\necho changed\n")  # changed
    (tool / "gone.dll").unlink()  # removed
    (tool / "added.so").write_bytes(b"constructed")  # added
    report = _json_of(cli.ProfileApplyReport, "profile", "apply", "mods", "--yes")
    assert report.applied
    left = {lp.path: lp.reason for lp in report.left}
    for rel in ("helper.sh", "gone.dll", "added.so"):
        assert "is an executable; guard never writes one" in left[f"Interface/AddOns/Tool/{rel}"]
    assert lua.read_bytes() == saved_lua, "the rest of the profile is applied"
    assert (tool / "helper.sh").read_bytes() == b"#!/bin/sh\necho changed\n"
    assert not (tool / "gone.dll").exists() and (tool / "added.so").exists()
    assert all("Tool/" not in i.path or i.path.endswith(".toc") for i in report.plan)


def test_too_many_deletes_are_refused_with_the_count_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profiles, "MAX_DELETES", 2)
    ok("profile", "save", "mods", "--preset", "addons")
    new = flavor / "Interface/AddOns/NewAddon"
    new.mkdir()
    for i in range(3):
        (new / f"f{i}.lua").write_bytes(b"-- constructed\n")
    before = _state(root)
    result = run("profile", "apply", "mods", "--yes")
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert "would delete 3 files added since it was saved, more than 2" in result.stderr
    assert "wowlab snap restore" in result.stderr
    assert _state(root) == before
    assert guard.history() == ()


def test_undo_reverses_an_apply_constructed(root: Path, flavor: Path) -> None:
    ok("profile", "save", "keys", "--preset", "bindings")
    _append(flavor / ACCT / "bindings-cache.wtf", b"bind CTRL-X constructed\n")
    (flavor / CHAR / "bindings-cache.wtf").write_bytes(b"bind CTRL-Y constructed\n")
    before = _bytes_under(flavor)
    report = _json_of(cli.ProfileApplyReport, "profile", "apply", "keys", "--yes")
    assert report.added == [f"{CHAR}/bindings-cache.wtf"]
    assert _bytes_under(flavor) != before
    undone = _json_of(cli.UndoReport, "undo", "--yes")
    assert undone.undone == report.transaction
    assert _bytes_under(flavor) == before


def test_apply_refused_by_the_gate_exits_3_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok("profile", "save", "look", "--preset", "ui")
    _append(flavor / "WTF/Config.wtf", b'SET constructedCVar "1"\n')
    before = _state(root)
    _running(monkeypatch, flavor)
    for args in (["--yes"], ["--dry-run"], ["--yes", "--json"]):
        result = run("profile", "apply", "look", *args)
        assert result.exit_code == 3, (args, result.stdout, result.stderr)
        assert "refused by the write gate" in result.stderr
    assert _state(root) == before


def test_declining_the_prompt_changes_nothing_constructed(root: Path, flavor: Path) -> None:
    ok("profile", "save", "look", "--preset", "macros")
    _append(flavor / ACCT / "macros-cache.txt", b"constructed\n")
    before = _state(root)
    result = run("profile", "apply", "look", input="n\n")
    assert result.exit_code == 1 and "Nothing was changed." in result.stderr
    assert "Apply profile look" in result.stdout
    assert _state(root) == before
    as_json = run("profile", "apply", "look", "--json", input="n\n")
    assert as_json.exit_code == 1
    assert "Apply profile look" in as_json.stderr, "the plan goes to stderr before the prompt"
    # The runner echoes the typed answer onto stdout; the JSON follows it.
    json_text = as_json.stdout[as_json.stdout.index("{") :]
    assert not cli.ProfileApplyReport.model_validate_json(json_text).applied


def test_apply_with_nothing_to_do(root: Path) -> None:
    ok("profile", "save", "look", "--preset", "ui")
    assert "Nothing to apply" in ok("profile", "apply", "look", "--yes").stdout
    report = _json_of(cli.ProfileApplyReport, "profile", "apply", "look", "--yes")
    assert report.plan == [] and not report.applied
    assert guard.history() == ()


def test_apply_rolls_back_when_the_files_change_after_the_plan_constructed(
    root: Path, flavor: Path
) -> None:
    ok("profile", "save", "look", "--preset", "macros")
    target = flavor / ACCT / "macros-cache.txt"
    _append(target, b"constructed\n")
    store = SnapshotStore()
    profile = profiles.find(store, "look")
    (chosen,) = install.discover(root).flavors
    plan = profiles.plan_apply(profile, chosen, root, store=store)
    _append(target, b"changed again\n")
    changed = target.read_bytes()
    with pytest.raises(profiles.ProfileChangedError):
        profiles.apply(plan, chosen, store=store)
    assert target.read_bytes() == changed
    assert guard.history()[-1].rolled_back


def test_apply_json_validates_and_carries_the_notes_constructed(root: Path, flavor: Path) -> None:
    ok("profile", "save", "look", "--preset", "ui")
    _append(flavor / ACCT / "config-cache.wtf", b'SET constructedCVar "2"\n')
    result = ok("profile", "apply", "look", "--yes", "--json")
    report = cli.ProfileApplyReport.model_validate_json(result.stdout)
    assert json.loads(result.stdout)["profile"] == "look"
    assert profiles.SERVER_SIDE_NOTE in report.notes
    assert profiles.LOGIN_NOTE in report.notes
    assert profiles.PRESET_SCOPE_NOTE in report.notes


def test_help_states_scope_and_server_side() -> None:
    for args in (
        ["profile", "--help"],
        ["profile", "apply", "--help"],
        ["profile", "save", "--help"],
    ):
        text = " ".join(_plain(ok(*args).stdout).split())
        assert "Action-bar contents and talents are not in these files" in text, args
        assert "every account and every character folder" in text, args
    apply_help = " ".join(_plain(ok("profile", "apply", "--help").stdout).split())
    assert "Addon updates made since are undone." in apply_help
    assert "not only the character you play" in apply_help


# ─── snapshot.list_tree ──────────────────────────────────────────────────────


def test_list_tree_reads_only_and_honours_excludes_constructed(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "a" / "b").mkdir(parents=True)
    (tree / "a" / "b" / "f.txt").write_bytes(b"x")
    (tree / "a" / "skip.txt").write_bytes(b"y")
    try:
        (tree / "a" / "link").symlink_to("b", target_is_directory=True)
    except OSError:  # Windows without the symlink privilege
        pytest.skip("cannot create a symlink here")
    store = SnapshotStore(tmp_path / "store")
    found = store.list_tree(tree, ["a", "missing"], exclude=["a/skip.txt"])
    assert found == (("a/b/f.txt", "file"), ("a/link", "symlink"))
    assert not store.path.exists(), "list_tree creates nothing"
