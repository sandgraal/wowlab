"""`wowlab profile` and `wowlab_core.profiles` (docs/LAB_PLAN.md §13.3, M11-08).

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
as `test_cli.py` builds it: nothing here reads or writes a real install, the
user data directory is redirected into `tmp_path`, and the process table is
a fake one. Files changed, added or planted after a save (`.DS_Store`, a new
addon, a character folder, `WowLab.lua`) are constructed inputs to the
synthetic install, labelled `constructed` in the test ids; the formats are
not parsed here, only moved as bytes.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import re
from pathlib import Path

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
CHAR = f"{ACCT}/1/Labcharb-Labrealmd"  # the <digits>/<First>-<Second> character folder
TWIN = f"{ACCT}/Labrealmb Partb Partc Partd/Labchard"  # its <Realm>/<First> twin
SV = f"{ACCT}/SavedVariables"
PRESETS_TOML = Path(profiles.__file__).with_name(profiles.PRESETS_FILE)


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
    assert "SavedVariables/WowLab.lua" in found["addons"].exclude.account
    assert "SavedVariables/WowLab.lua" in found["addons"].exclude.character
    # L6: no flavor folder (`_x_`), product code or interface/build number in the data.
    data = "\n".join(
        line
        for line in PRESETS_TOML.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    assert not re.search(r"(?<![A-Za-z0-9])_[a-z_]+_(?![A-Za-z0-9])", data)
    assert not re.search(r"\bwow(_[a-z_]*)?\b", data)  # product codes: wow, wow_classic_beta…
    assert not re.search(r"\d{4,}", data)


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


def test_ui_selection_joins_names_to_every_account_and_character(flavor: Path) -> None:
    lay = layout.Layout(flavor)
    sel = profiles.select(lay, preset_names=["ui"])
    assert "WTF/Config.wtf" in sel.subtrees
    assert f"{ACCT}/config-cache.wtf" in sel.subtrees
    assert f"{ACCT}/edit-mode-cache-account.txt" in sel.subtrees
    for folder in (CHAR, TWIN):  # both shapes, present or not
        for name in ("config-cache.wtf", "layout-local.txt", "chat-cache.txt"):
            assert f"{folder}/{name}" in sel.subtrees
    assert f"{ACCT}/bindings-cache.wtf" not in sel.subtrees
    assert sel.excluded == ()


def test_addons_selection_excludes_the_lab_addons_own_file(flavor: Path) -> None:
    sel = profiles.select(layout.Layout(flavor), preset_names=["addons"])
    assert {"Interface/AddOns", SV, f"{TWIN}/AddOns.txt", f"{CHAR}/SavedVariables"} <= set(
        sel.subtrees
    )
    assert f"{SV}/WowLab.lua" in sel.excluded
    assert f"{CHAR}/SavedVariables/WowLab.lua" in sel.excluded


# ─── save, list, show, delete ────────────────────────────────────────────────


def test_save_reads_only_and_json_validates(root: Path, flavor: Path) -> None:
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
    manifest = SnapshotStore().show(report.profile.snapshot_id)
    assert manifest.label == "profile:look presets=ui"


def test_list_show_delete_and_json(root: Path) -> None:
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


def test_two_snapshots_labelled_as_one_profile_constructed(root: Path) -> None:
    """`snap create -m profile:<name>` can make a second one; `find` refuses
    the ambiguity and `delete` relabels both."""
    ok("profile", "save", "a", "--preset", "macros")
    ok("snap", "create", "-m", "profile:a")
    shown = run("profile", "show", "a")
    assert shown.exit_code == 1 and "2 snapshots are labelled as profile 'a'" in shown.stderr
    deleted = _json_of(cli.ProfileDeleteReport, "profile", "delete", "a")
    assert len(deleted.relabelled) == 2
    assert profiles.listing(SnapshotStore()).profiles == ()


def test_save_refuses_a_name_in_use(root: Path) -> None:
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
    ],
    ids=["both", "neither", "unknown-preset", "outside-subtrees", "dotdot-constructed"],
)
def test_save_usage_errors_exit_2(root: Path, args: list[str], message: str) -> None:
    result = run("profile", "save", "x", *args)
    assert result.exit_code == 2, (result.stdout, result.stderr)
    assert message in result.stderr


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
    new_char = flavor / ACCT / "1" / "Newchar-Labrealmd"  # created after the save
    new_char.mkdir()
    (new_char / "config-cache.wtf").write_bytes(b"new character\n")
    outside = _bytes_under(flavor)

    report = _json_of(cli.ProfileApplyReport, "profile", "apply", "look", "--yes")
    assert report.applied and report.transaction is not None
    assert report.added == [f"{TWIN}/config-cache.wtf"]

    after = _bytes_under(flavor)
    in_profile = {"WTF/Config.wtf", f"{ACCT}/config-cache.wtf", f"{CHAR}/layout-local.txt"}
    for path in in_profile:
        assert after[path] == saved[path], path
    assert f"{TWIN}/config-cache.wtf" not in after, "a file added in a subtree is removed"
    for path, data in outside.items():
        if path in in_profile or path == f"{TWIN}/config-cache.wtf":
            continue
        assert after.get(path) == data, f"{path} is outside the profile and must be left alone"
    assert after[f"{ACCT}/1/Newchar-Labrealmd/config-cache.wtf"] == b"new character\n"


def test_apply_lists_every_cache_file_with_the_note(root: Path, flavor: Path) -> None:
    ok("profile", "save", "look", "--preset", "ui")
    _append(flavor / "WTF/Config.wtf", b'SET constructedCVar "1"\n')
    _append(flavor / ACCT / "config-cache.wtf", b'SET constructedCVar "2"\n')
    _append(flavor / ACCT / "edit-mode-cache-account.txt", b"constructed")
    _append(flavor / CHAR / "chat-cache.txt", b"constructed\n")
    (flavor / TWIN / "config-cache.wtf").write_bytes(b"added since save\n")

    dry = _json_of(cli.ProfileApplyReport, "profile", "apply", "look", "--dry-run")
    expected = {
        f"{ACCT}/config-cache.wtf": "write",
        f"{ACCT}/edit-mode-cache-account.txt": "write",
        f"{CHAR}/chat-cache.txt": "write",
        f"{TWIN}/config-cache.wtf": "delete",
    }
    assert {c.path: c.action for c in dry.cache_files} == expected
    assert all(c.note == profiles.SERVER_NOTE for c in dry.cache_files)
    assert profiles.SERVER_NOTE == (
        "the server may replace this at your next login (synchronize* CVars; see `wowlab doctor`)"
    )
    assert "WTF/Config.wtf" in {i.path for i in dry.plan}
    assert "WTF/Config.wtf" not in {c.path for c in dry.cache_files}
    assert not dry.applied

    text = _plain(ok("profile", "apply", "look", "--yes").stdout)
    for path in expected:
        assert f"{path}: {profiles.SERVER_NOTE}" in text
    assert "proven only by logging in" in text
    assert f"delete   {TWIN}/config-cache.wtf  (added since the profile was saved)" in text


def test_apply_skips_edit_no_files_constructed(root: Path, flavor: Path) -> None:
    (flavor / SV / "WowLab.lua").write_bytes(b"WowLabDB = {}\n")
    ok("profile", "save", "mods", "--preset", "addons")
    manifest = profiles.find(SnapshotStore(), "mods").manifest
    assert manifest.entry(f"{FLAVOR}/{SV}/WowLab.lua") is None, "WowLab.lua is excluded"
    bak = flavor / SV / "RareScanner.lua.bak"  # file map: savedvariables-backup, Edit no
    lua = flavor / SV / "RareScanner.lua"
    saved_lua = lua.read_bytes()
    _append(bak, b"\n-- constructed\n")
    _append(lua, b"\n-- constructed\n")
    (flavor / SV / "WowLab.lua").write_bytes(b"WowLabDB = { constructed = true }\n")
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
    assert (flavor / SV / "WowLab.lua").read_bytes() == b"WowLabDB = { constructed = true }\n"
    text = _plain(ok("profile", "apply", "mods", "--dry-run").stdout)
    assert 'Skipped 3 file(s) wowlab leaves alone (file-map Edit "no"' in text


def test_undo_reverses_an_apply(root: Path, flavor: Path) -> None:
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


def test_apply_refused_by_the_gate_exits_3(
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


def test_declining_the_prompt_changes_nothing(root: Path, flavor: Path) -> None:
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


def test_apply_json_validates_and_notes_server_side(root: Path, flavor: Path) -> None:
    ok("profile", "save", "look", "--preset", "ui")
    _append(flavor / ACCT / "config-cache.wtf", b'SET constructedCVar "2"\n')
    result = ok("profile", "apply", "look", "--yes", "--json")
    report = cli.ProfileApplyReport.model_validate_json(result.stdout)
    assert json.loads(result.stdout)["profile"] == "look"
    assert profiles.SERVER_SIDE_NOTE in report.notes
    assert profiles.LOGIN_NOTE in report.notes


def test_help_says_action_bars_and_talents_are_server_side() -> None:
    for args in (["profile", "--help"], ["profile", "apply", "--help"]):
        text = " ".join(_plain(ok(*args).stdout).split())
        assert "Action-bar contents and talents are kept on the server" in text


# ─── snapshot.list_tree ──────────────────────────────────────────────────────


def test_list_tree_reads_only_and_honours_excludes(tmp_path: Path) -> None:
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
