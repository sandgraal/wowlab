"""`wowlab addon install|remove lab` and `wowlab_core.addoninstall` (§13.1, M11-02).

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
as `test_cli.py` builds it: nothing here reads or writes a real install, the
user data directory is redirected into `tmp_path`, and the process table is
a fake one. The addon sources are the repository's own `lab/addon/WowLab/`.

Constructed (labelled in the test ids): files planted in the installed
addon folder (an edited source, a leftover, an executable name, a link), the
lab-addon's SavedVariables, a changed `.build.info` version, and source
folders built in `tmp_path` for the refusals. No external format is parsed
from them beyond the repository's own TOC template.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import importlib
import sys
import tomllib
import zipfile
from importlib import resources
from pathlib import Path

import pytest
from test_cli import (
    ACCOUNT,
    FLAVOR,
    VERSION,
    _running,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import addoninstall, cli, guard, install, profiles
from wowlab_core.toc import parse_toc

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "lab" / "addon" / "WowLab"
LAB = "Interface/AddOns/WowLab"
SV = f"WTF/Account/{ACCOUNT}/SavedVariables"
CHAR_SV = f"WTF/Account/{ACCOUNT}/1/Labcharb-Labrealmd/SavedVariables"
INTERFACE = 16001  # 1.60.1 by the patch-number rule; tests may name it (L6)


def _tree(root: Path) -> dict[str, bytes | None]:
    """Every entry under `root` with its bytes (None for a folder)."""
    return {
        p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in sorted(root.rglob("*"))
    }


def _files_with_mtimes(root: Path, skip: str) -> dict[str, tuple[bytes, int]]:
    """Every regular file under `root` outside `skip` (root-relative), with its
    bytes and mtime: what a write confined to `skip` must keep."""
    out: dict[str, tuple[bytes, int]] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if rel == skip or rel.startswith(skip + "/") or not p.is_file() or p.is_symlink():
            continue
        out[rel] = (p.read_bytes(), p.lstat().st_mtime_ns)
    return out


def _source_lua() -> list[str]:
    return sorted(p.name for p in SOURCE.glob("*.lua"))


def _json_of[M: cli.BaseModel](model: type[M], *args: str) -> M:
    return model.model_validate_json(ok(*args, "--json").stdout)


def _plant_saved_variables(flavor: Path) -> dict[str, bytes]:
    """Constructed: the lab-addon's account and character SavedVariables."""
    planted = {
        f"{SV}/WowLab.lua": b"WowLabDB = {\n}\n",
        f"{CHAR_SV}/WowLab.lua": b'WowLabCharDB = {\n\t["probe"] = {\n\t\t["loads"] = 2,\n\t},\n}\n',
    }
    for rel, data in planted.items():
        (flavor / rel).parent.mkdir(parents=True, exist_ok=True)
        (flavor / rel).write_bytes(data)
    return planted


# ─── the template and the rule ───────────────────────────────────────────────


def test_the_toc_placeholder_line_in_the_source_is_pinned() -> None:
    """Install replaces exactly this line; the repository holds no number (ADR-0026)."""
    assert addoninstall.INTERFACE_LINE == "## Interface: @WOWLAB_INTERFACE@"
    lines = (SOURCE / "WowLab.toc").read_bytes().split(b"\n")
    assert [line.rstrip(b"\r") for line in lines].count(b"## Interface: @WOWLAB_INTERFACE@") == 1
    assert [ln for ln in lines if ln.lower().startswith(b"## interface")] == [
        b"## Interface: @WOWLAB_INTERFACE@"
    ]


@pytest.mark.parametrize(
    ("version", "expected"),
    [(VERSION, INTERFACE), ("12.1.5.65432", 120105), ("1.15.7.61582", 11507), ("11.0.2", 110002)],
)
def test_interface_version_follows_the_patch_number_rule(version: str, expected: int) -> None:
    assert addoninstall.interface_version(version) == expected


@pytest.mark.parametrize(
    "version",
    [None, "", "1.60", "1.x.1.2", "1.100.1.2", "1.60.100.2", "\uff11.60.1.2"],
    ids=["none", "empty", "short", "letter", "minor-100", "patch-100", "fullwidth-constructed"],
)
def test_interface_version_refuses_what_the_rule_cannot_express(version: str | None) -> None:
    with pytest.raises(addoninstall.AddonVersionError):
        addoninstall.interface_version(version)


def test_fill_toc_changes_only_the_placeholder() -> None:
    template = (SOURCE / "WowLab.toc").read_bytes()
    filled = addoninstall.fill_toc(template, INTERFACE)
    assert filled == template.replace(b"@WOWLAB_INTERFACE@", b"16001")
    doc = parse_toc(filled)
    assert doc.interface is not None and doc.interface.versions == (INTERFACE,)
    assert b"@WOWLAB_" not in filled


def test_fill_toc_keeps_crlf_constructed() -> None:
    template = b"## Interface: @WOWLAB_INTERFACE@\r\n## Title: x\r\nCore.lua\r\n"
    assert addoninstall.fill_toc(template, 120105) == (
        b"## Interface: 120105\r\n## Title: x\r\nCore.lua\r\n"
    )


@pytest.mark.parametrize(
    "template",
    [
        b"## Interface: 16001\nCore.lua\n",
        b"## Interface: @WOWLAB_INTERFACE@\n## Interface: @WOWLAB_INTERFACE@\n",
        b"## Interface: @WOWLAB_INTERFACE@ \n",
        b"## Interface: @WOWLAB_INTERFACE@\n## X-Other: @WOWLAB_OTHER@\n",
    ],
    ids=["no-placeholder", "twice", "trailing-space", "other-token"],
)
def test_fill_toc_refuses_a_template_that_is_not_the_contract_constructed(
    template: bytes,
) -> None:
    with pytest.raises(addoninstall.AddonSourceError):
        addoninstall.fill_toc(template, INTERFACE)


def test_addon_files_are_the_toc_and_the_lua_it_lists() -> None:
    files = addoninstall.addon_files(INTERFACE)
    names = [rel.removeprefix(LAB + "/") for rel in files]
    assert names[0] == "WowLab.toc"
    assert sorted(names[1:]) == _source_lua()
    assert all(rel.startswith(LAB + "/") and rel.count("/") == 3 for rel in files)
    assert "README.md" not in names and "selene.toml" not in names
    for rel, data in list(files.items())[1:]:
        assert data == (SOURCE / rel.removeprefix(LAB + "/")).read_bytes()


def test_find_source_is_the_repository_addon_folder() -> None:
    assert addoninstall.find_source() == SOURCE.resolve()


def _empty_package(tmp_path: Path) -> Path:
    """Constructed: an installed `wowlab_core` with no bundled copy, so a
    test never depends on which `wowlab_core` this run imported."""
    package = tmp_path / "empty-install" / "wowlab_core"
    package.mkdir(parents=True, exist_ok=True)
    return package


def test_find_source_outside_a_checkout_says_so(tmp_path: Path) -> None:
    with pytest.raises(addoninstall.AddonSourceError, match="not running from a checkout"):
        addoninstall.find_source(tmp_path, package=_empty_package(tmp_path))


def _source_copy(tmp_path: Path) -> Path:
    folder = tmp_path / "src" / "WowLab"
    folder.mkdir(parents=True)
    for p in SOURCE.iterdir():
        if p.name == "WowLab.toc" or p.suffix == ".lua":
            (folder / p.name).write_bytes(p.read_bytes())
    return folder


def test_an_unlisted_lua_file_in_the_sources_is_refused_constructed(tmp_path: Path) -> None:
    folder = _source_copy(tmp_path)
    (folder / "Extra.lua").write_bytes(b"-- constructed\n")
    with pytest.raises(addoninstall.AddonSourceError, match="must list every"):
        addoninstall.addon_files(INTERFACE, folder)


def test_a_listed_file_missing_from_the_sources_is_refused_constructed(tmp_path: Path) -> None:
    folder = _source_copy(tmp_path)
    (folder / "Gear.lua").unlink()
    with pytest.raises(addoninstall.AddonSourceError, match="must list every"):
        addoninstall.addon_files(INTERFACE, folder)


def test_the_install_folder_is_the_one_profiles_exclude() -> None:
    assert profiles.always_excluded().flavor == (addoninstall.ADDON_FOLDER,)
    for rel in addoninstall.addon_files(INTERFACE):
        assert profiles.is_always_excluded(rel)


# ─── install ─────────────────────────────────────────────────────────────────


def test_install_writes_the_addon_with_the_discovered_interface(flavor: Path) -> None:
    result = ok("addon", "install", "lab", "--yes")
    assert "## Interface: 16001" in result.stdout
    assert "wowlab undo" in result.stdout
    installed = flavor / LAB
    assert sorted(p.name for p in installed.iterdir()) == sorted(["WowLab.toc", *_source_lua()])
    toc = (installed / "WowLab.toc").read_bytes()
    assert toc == (SOURCE / "WowLab.toc").read_bytes().replace(b"@WOWLAB_INTERFACE@", b"16001")
    for name in _source_lua():
        assert (installed / name).read_bytes() == (SOURCE / name).read_bytes()
    (record,) = guard.history()
    assert record.state == "committed"
    assert record.label == "addon install lab (## Interface: 16001)"


def test_the_interface_follows_build_info_constructed(root: Path, flavor: Path) -> None:
    info = root / ".build.info"
    info.write_bytes(info.read_bytes().replace(VERSION.encode(), b"1.61.2.70001"))
    report = _json_of(cli.AddonInstallReport, "addon", "install", "lab", "--yes")
    assert report.interface == 16102 and report.flavor_version == "1.61.2.70001"
    doc = parse_toc((flavor / LAB / "WowLab.toc").read_bytes())
    assert doc.interface is not None and doc.interface.versions == (16102,)


def test_install_then_undo_restores_the_tree_byte_for_byte(root: Path) -> None:
    before = _tree(root)
    ok("addon", "install", "lab", "--yes")
    assert _tree(root) != before
    ok("undo", "--yes")
    assert _tree(root) == before


def test_install_writes_nothing_outside_the_addon_folder(root: Path, flavor: Path) -> None:
    skip = f"{FLAVOR}/{LAB}"
    files_before = _files_with_mtimes(root, skip)
    entries_before = {k for k in _tree(root) if k != skip and not k.startswith(skip + "/")}
    ok("addon", "install", "lab", "--yes")
    assert _files_with_mtimes(root, skip) == files_before
    assert {k for k in _tree(root) if k != skip and not k.startswith(skip + "/")} == entries_before
    (record,) = guard.history()
    assert record.paths and all(p.path.startswith(LAB + "/") for p in record.paths)
    assert record.created_dirs == (LAB,)
    assert not list(flavor.rglob(".wowlab-*"))


def test_install_refused_while_the_client_runs(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _tree(root)
    _running(monkeypatch, flavor)
    result = run("addon", "install", "lab", "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "refused by the write gate" in result.stderr
    assert _tree(root) == before
    assert guard.history() == ()


def test_install_dry_run_changes_nothing(root: Path) -> None:
    before = _tree(root)
    result = ok("addon", "install", "lab", "--dry-run")
    assert "Dry run: nothing was changed." in result.stdout
    assert "create   Interface/AddOns/WowLab/WowLab.toc" in result.stdout
    assert _tree(root) == before
    assert guard.history() == ()


def test_install_declined_changes_nothing(root: Path) -> None:
    before = _tree(root)
    result = run("addon", "install", "lab", input="n\n")
    assert result.exit_code == 1
    assert "Nothing was changed." in result.stderr
    assert _tree(root) == before


def test_install_json_validates_and_lists_every_file(flavor: Path) -> None:
    report = _json_of(cli.AddonInstallReport, "addon", "install", "lab", "--yes")
    assert report.applied and report.transaction == guard.history()[-1].id
    assert report.interface == INTERFACE and report.flavor_version == VERSION
    assert [i.path for i in report.plan] == report.files
    assert all(i.before is None for i in report.plan)
    assert report.unchanged == [] and report.left == []
    assert report.flavor_path == str(flavor)


def test_install_json_dry_run_is_not_applied(root: Path) -> None:
    report = _json_of(cli.AddonInstallReport, "addon", "install", "lab", "--dry-run")
    assert not report.applied and report.dry_run and report.transaction is None
    assert len(report.plan) == len(report.files)


def test_reinstall_plans_only_changed_files_and_removes_leftovers_constructed(
    flavor: Path,
) -> None:
    ok("addon", "install", "lab", "--yes")
    core = flavor / LAB / "Core.lua"
    core.write_bytes(core.read_bytes() + b"-- constructed local edit\n")
    (flavor / LAB / "Old.lua").write_bytes(b"-- constructed leftover of an older version\n")
    text = ok("addon", "install", "lab", "--dry-run").stdout
    assert "replace  Interface/AddOns/WowLab/Core.lua" in text
    assert "delete   Interface/AddOns/WowLab/Old.lua  (not part of the lab-addon's sources)" in text
    assert f"{len(_source_lua())} file(s) already up to date." in text
    report = _json_of(cli.AddonInstallReport, "addon", "install", "lab", "--yes")
    assert [(i.path, i.after is None) for i in report.plan] == [
        (f"{LAB}/Core.lua", False),
        (f"{LAB}/Old.lua", True),
    ]
    assert core.read_bytes() == (SOURCE / "Core.lua").read_bytes()
    assert not (flavor / LAB / "Old.lua").exists()
    ok("undo", "--yes")
    assert core.read_bytes().endswith(b"-- constructed local edit\n")
    assert (flavor / LAB / "Old.lua").exists()


def test_reinstall_when_up_to_date_writes_nothing(root: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    before = _tree(root)
    result = ok("addon", "install", "lab", "--yes")
    assert "Nothing to install" in result.stdout
    assert _tree(root) == before
    assert len(guard.history()) == 1


def test_an_executable_name_in_the_folder_is_left_alone_by_install_constructed(
    flavor: Path,
) -> None:
    ok("addon", "install", "lab", "--yes")
    planted = flavor / LAB / "helper.dll"
    planted.write_bytes(b"constructed, not an executable")
    report = _json_of(cli.AddonInstallReport, "addon", "install", "lab", "--yes")
    assert report.plan == [] and [lp.path for lp in report.left] == [f"{LAB}/helper.dll"]
    assert planted.read_bytes() == b"constructed, not an executable"


def test_remove_refused_by_the_gate_exits_3_with_its_reason_constructed(root: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    for name in ("helper.dll", "tool.exe"):
        (root / FLAVOR / LAB / name).write_bytes(b"constructed, not an executable")
    before = _tree(root)
    result = run("addon", "remove", "lab", "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    err = " ".join(result.stderr.split())
    assert "refused by the write gate" in err
    assert f"'{LAB}/helper.dll' is an executable" in err  # every refused path is named
    assert f"'{LAB}/tool.exe' is an executable" in err
    assert " ".join(addoninstall.REMOVE_REFUSED_NOTE.split()) in err
    assert "the path(s) named above are not ones wowlab will delete" in err
    assert "this path is not one" not in err
    # WowLab.toc is among the files found: the addon still loads.
    assert " ".join(addoninstall.STILL_INSTALLED_NOTE.split()) in err
    assert "Nothing to remove" not in result.stdout
    assert _tree(root) == before
    assert len(guard.history()) == 1


def test_a_refused_remove_prints_one_line_per_refused_path_then_the_notes_constructed(
    root: Path,
) -> None:
    """M11-30: the refusal keeps its line structure (the note speaks of "the
    path(s) named above"): the error line names the first refused path, and
    each further refused path and the notes follow on lines of their own,
    as the library carries them as notes on the exception."""
    ok("addon", "install", "lab", "--yes")
    for name in ("helper.dll", "tool.exe"):
        (root / FLAVOR / LAB / name).write_bytes(b"constructed, not an executable")
    result = run("addon", "remove", "lab", "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert result.stderr.split("\n") == [
        f"wowlab: refused by the write gate: '{LAB}/helper.dll' is an executable; "
        "guard never writes one",
        f"'{LAB}/tool.exe' is an executable; guard never writes one",
        f"{addoninstall.REMOVE_REFUSED_NOTE} {addoninstall.STILL_INSTALLED_NOTE}",
        "",
    ]


@pytest.mark.parametrize(
    ("names", "still_installed"),
    [
        (("helper.dll",), False),
        (("helper.dll", "Notes.txt"), False),
        (("helper.dll", "wowlab.TOC"), True),
    ],
    ids=["no-toc-constructed", "other-files-constructed", "case-variant-toc-constructed"],
)
def test_a_refused_remove_says_still_installed_only_with_a_toc_constructed(
    root: Path, names: tuple[str, ...], still_installed: bool
) -> None:
    """M11-17: without a WowLab.toc among the files found, nothing claims the
    lab-addon is installed. A case variant of the name counts, as a
    case-insensitive volume would load it."""
    folder = root / FLAVOR / LAB
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).write_bytes(b"constructed, not an executable")
    before = _tree(root)
    result = run("addon", "remove", "lab", "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    err = " ".join(result.stderr.split())
    assert f"'{LAB}/helper.dll' is an executable" in err
    assert " ".join(addoninstall.REMOVE_REFUSED_NOTE.split()) in err
    assert ("The lab-addon is still installed" in err) is still_installed
    assert (" ".join(addoninstall.STILL_INSTALLED_NOTE.split()) in err) is still_installed
    assert _tree(root) == before
    assert len(guard.history()) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="file symlinks need a privilege on Windows")
def test_a_refused_remove_says_still_installed_with_a_linked_toc_constructed(
    root: Path, tmp_path: Path
) -> None:
    """A `WowLab.toc` found as a link is left alone, never followed, but the
    client opens it through the link, so the addon still loads."""
    folder = root / FLAVOR / LAB
    folder.mkdir(parents=True)
    real = tmp_path / "linked-toc-target"
    real.write_bytes(b"## Interface: 16001\n")
    (folder / "WowLab.toc").symlink_to(real)
    (folder / "helper.dll").write_bytes(b"constructed, not an executable")
    result = run("addon", "remove", "lab", "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    err = " ".join(result.stderr.split())
    assert " ".join(addoninstall.STILL_INSTALLED_NOTE.split()) in err
    assert (folder / "WowLab.toc").is_symlink() and real.read_bytes() == b"## Interface: 16001\n"


def test_the_note_texts_are_pinned() -> None:
    """M11-17: the exact wording the #105 review asked for."""
    assert addoninstall.TOC_LEFT_NOTE == (
        "The folder Interface/AddOns/WowLab/ stays and still holds a .toc listed above as left "
        "alone, so the client may still find an addon there."
    )
    assert addoninstall.REMOVE_REFUSED_NOTE == (
        "Nothing was deleted: `wowlab addon remove lab` removes the folder's files all together "
        "or not at all, and the path(s) named above are not ones wowlab will delete."
    )
    assert addoninstall.STILL_INSTALLED_NOTE.startswith("The lab-addon is still installed")
    assert "still installed" not in addoninstall.REMOVE_REFUSED_NOTE


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
def test_a_linked_addon_folder_is_refused_constructed(root: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere-addon"
    elsewhere.mkdir()
    (root / FLAVOR / LAB).symlink_to(elsewhere, target_is_directory=True)
    before = _tree(root)
    for command in ("install", "remove"):
        result = run("addon", command, "lab", "--yes")
        assert result.exit_code == 1
        assert "is a link" in result.stderr
    assert _tree(root) == before
    assert list(elsewhere.iterdir()) == []


def test_only_the_lab_addon_is_known(root: Path) -> None:
    for command in ("install", "remove"):
        result = run("addon", command, "Details", "--yes")
        assert result.exit_code == 2
        assert "`lab`" in result.stderr


def test_install_outside_a_checkout_fails_clearly(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(addoninstall, "_HERE", tmp_path / "site-packages" / "wowlab_core")
    monkeypatch.setattr(addoninstall, "_package", lambda: _empty_package(tmp_path))
    before = _tree(root)
    result = run("addon", "install", "lab", "--yes")
    assert result.exit_code == 1
    assert "were not found" in result.stderr and "checkout" in result.stderr
    assert _tree(root) == before
    assert guard.history() == ()


def test_install_without_a_flavor_version_fails_clearly_constructed(root: Path) -> None:
    info = root / ".build.info"
    info.write_bytes(info.read_bytes().replace(b"|wow_classic_beta", b"|wow_other"))
    assert install.discover(root).flavors[0].version is None
    before = _tree(root)
    result = run("addon", "install", "lab", "--yes")
    assert result.exit_code == 1
    assert "no version" in result.stderr
    assert _tree(root) == before


# ─── remove ──────────────────────────────────────────────────────────────────


def test_remove_deletes_the_code_and_leaves_saved_variables(root: Path, flavor: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    planted = _plant_saved_variables(flavor)
    result = ok("addon", "remove", "lab", "--yes")
    assert "SavedVariables are left alone" in result.stdout
    assert "WowLab.lua" in result.stdout
    assert addoninstall.FOLDER_STAYS_NOTE in result.stdout
    assert addoninstall.FOLDER_LEFT_NOTE not in result.stdout
    assert list((flavor / LAB).iterdir()) == []
    for rel, data in planted.items():
        assert (flavor / rel).read_bytes() == data
    record = guard.history()[-1]
    assert record.label == "addon remove lab"
    assert {p.path for p in record.paths} == set(addoninstall.addon_files(INTERFACE))
    assert all(p.after is None for p in record.paths)


def test_remove_then_undo_puts_the_files_back(root: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    installed = _tree(root)
    ok("addon", "remove", "lab", "--yes")
    ok("undo", "--yes")
    assert _tree(root) == installed


def test_remove_refused_while_the_client_runs(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok("addon", "install", "lab", "--yes")
    before = _tree(root)
    _running(monkeypatch, flavor)
    result = run("addon", "remove", "lab", "--yes")
    assert result.exit_code == 3
    assert _tree(root) == before
    assert len(guard.history()) == 1


def test_remove_when_not_installed(root: Path, flavor: Path) -> None:
    _plant_saved_variables(flavor)
    before = _tree(root)
    result = ok("addon", "remove", "lab", "--yes")
    assert "Nothing to remove" in result.stdout
    assert "SavedVariables are left alone" in result.stdout
    assert _tree(root) == before
    assert guard.history() == ()


def test_remove_json_validates_and_names_the_saved_variables(flavor: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    dry = _json_of(cli.AddonRemoveReport, "addon", "remove", "lab", "--dry-run")
    assert not dry.applied and len(dry.plan) == len(_source_lua()) + 1
    done = _json_of(cli.AddonRemoveReport, "addon", "remove", "lab", "--yes")
    assert done.applied and done.transaction == guard.history()[-1].id
    assert done.notes[0] == addoninstall.SAVED_VARIABLES_NOTE
    assert addoninstall.FOLDER_STAYS_NOTE in done.notes


def test_remove_declined_changes_nothing(root: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    before = _tree(root)
    result = run("addon", "remove", "lab", input="n\n")
    assert result.exit_code == 1
    assert _tree(root) == before


# ─── fix round 1 (PR #105 reviews) ───────────────────────────────────────────


def _chosen(root: Path) -> install.Flavor:
    return next(f for f in install.discover(root).flavors if f.folder == FLAVOR)


def test_a_leftover_added_after_the_plan_rolls_back_constructed(root: Path, flavor: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    (flavor / LAB / "Gear.lua").write_bytes(b"-- stale\n")
    chosen = _chosen(root)
    plan = addoninstall.plan_install(chosen)
    (flavor / LAB / "New.lua").write_bytes(b"-- constructed, planted after the plan\n")
    with pytest.raises(addoninstall.AddonChangedError):
        addoninstall.install(plan, chosen)
    assert (flavor / LAB / "Gear.lua").read_bytes() == b"-- stale\n"
    assert guard.history()[-1].state == "rolled_back"


def test_remove_lists_the_folder_again_under_the_locks_constructed(
    root: Path, flavor: Path
) -> None:
    ok("addon", "install", "lab", "--yes")
    chosen = _chosen(root)
    plan = addoninstall.plan_remove(chosen)
    (flavor / LAB / "New.lua").write_bytes(b"-- constructed, planted after the plan\n")
    with pytest.raises(addoninstall.AddonChangedError):
        addoninstall.remove(plan, chosen)
    assert (flavor / LAB / "WowLab.toc").exists()


def test_a_change_between_the_plan_and_the_confirm_exits_1_cleanly_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok("addon", "install", "lab", "--yes")
    (flavor / LAB / "Gear.lua").write_bytes(b"-- stale\n")
    core = flavor / LAB / "Core.lua"

    def confirm_after_an_edit(question: str, yes: bool) -> None:
        core.write_bytes(b"-- constructed, edited at the prompt\n")

    monkeypatch.setattr(cli, "_confirm", confirm_after_an_edit)
    result = run("addon", "install", "lab")
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "changed after the plan" in result.stderr
    assert core.read_bytes() == b"-- constructed, edited at the prompt\n"
    assert (flavor / LAB / "Gear.lua").read_bytes() == b"-- stale\n"


@pytest.mark.parametrize("which", ["install", "remove"])
def test_a_plan_naming_a_path_outside_the_folder_is_refused_constructed(
    root: Path, which: str
) -> None:
    ok("addon", "install", "lab", "--yes")
    chosen = _chosen(root)
    before = _tree(root)
    records = len(guard.history())
    outside = guard.PlanItem(
        path=f"WTF/Account/{ACCOUNT}/macros-cache.txt", before="0" * 64, after=None, size=None
    )
    with pytest.raises(addoninstall.AddonError, match="is not under"):
        if which == "install":
            plan = addoninstall.plan_install(chosen)
            addoninstall.install(plan.model_copy(update={"plan": (outside,)}), chosen)
        else:
            rplan = addoninstall.plan_remove(chosen)
            addoninstall.remove(rplan.model_copy(update={"plan": (outside,)}), chosen)
    assert _tree(root) == before
    assert len(guard.history()) == records


def test_a_write_that_is_not_an_addon_file_is_refused_constructed(root: Path) -> None:
    chosen = _chosen(root)
    plan = addoninstall.plan_install(chosen)
    extra = guard.PlanItem(path=f"{LAB}/Extra.lua", before=None, after="0" * 64, size=1)
    before = _tree(root)
    with pytest.raises(addoninstall.AddonChangedError):
        addoninstall.install(plan.model_copy(update={"plan": (*plan.plan, extra)}), chosen)
    assert _tree(root) == before


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
@pytest.mark.parametrize("component", ["lab", "addon", "WowLab"])
def test_a_link_in_the_source_path_is_refused_constructed(tmp_path: Path, component: str) -> None:
    parts = list(addoninstall.SOURCE_PARTS)
    cut = parts.index(component)
    ws = tmp_path / "ws"
    ws.joinpath(*parts[:cut]).mkdir(parents=True)
    (ws / ".git").mkdir()
    real = tmp_path / "real"
    leaf = real.joinpath(*parts[cut:])
    leaf.mkdir(parents=True)
    (leaf / "WowLab.toc").write_bytes((SOURCE / "WowLab.toc").read_bytes())
    ws.joinpath(*parts[: cut + 1]).symlink_to(real / component, target_is_directory=True)
    with pytest.raises(addoninstall.AddonSourceError, match="is a link"):
        addoninstall._candidate(ws)
    if component == "lab":
        return  # the package's own folder would resolve through the link
    package = ws.joinpath(*addoninstall.CHECKOUT_PACKAGE_PARTS)
    package.mkdir(parents=True)
    with pytest.raises(addoninstall.AddonSourceError, match="is a link"):
        addoninstall.find_source(package, package=_empty_package(tmp_path))


def _root_marker(folder: Path, marker: str) -> None:
    """Constructed: make `folder` a workspace root by `.git` (an empty folder,
    or a worktree's file) or by a uv-workspace `pyproject.toml`."""
    folder.mkdir(parents=True, exist_ok=True)
    if marker == "git":
        (folder / ".git").mkdir()
    elif marker == "git-file":
        (folder / ".git").write_text("gitdir: elsewhere\n")
    else:
        (folder / "pyproject.toml").write_text('[tool.uv.workspace]\nmembers = ["lab/*"]\n')


def _sources_in(root: Path) -> Path:
    """Constructed: a `lab/addon/WowLab/` under `root` that is a plausible
    lab-addon (the repository's TOC) plus a file that must never be installed."""
    folder = root.joinpath(*addoninstall.SOURCE_PARTS)
    folder.mkdir(parents=True)
    (folder / "WowLab.toc").write_bytes((SOURCE / "WowLab.toc").read_bytes())
    (folder / "Evil.lua").write_bytes(b"-- constructed, must never be installed\n")
    return folder


@pytest.mark.parametrize("marker", ["git", "git-file", "pyproject"])
def test_a_package_outside_the_checkout_layout_never_uses_a_root_above_it_constructed(
    tmp_path: Path, marker: str
) -> None:
    """A workspace root right above the package holds `lab/addon/WowLab/`,
    but the package is not that root's `lab/core/src/wowlab_core`: it is
    not a checkout, and with no bundle the sources are not found."""
    ws = tmp_path / "ws"
    _root_marker(ws, marker)
    _sources_in(ws)
    (ws / "pkg").mkdir()
    with pytest.raises(addoninstall.AddonSourceError, match="not running from a checkout"):
        addoninstall.find_source(ws / "pkg", package=_empty_package(tmp_path))


def test_an_installed_package_never_uses_a_lab_folder_above_it_constructed(
    tmp_path: Path,
) -> None:
    """A package under site-packages with no workspace root above it, no copy
    bundled in it, and a hostile `lab/addon/WowLab/` higher up: refused, never
    used. (M13-14 changed the rule from M11-02's "a checkout and nowhere else"
    to "a checkout, else the copy bundled in the installed package, nothing
    else"; this package bundles nothing, so it is still refused.)"""
    hostile = tmp_path.joinpath(*addoninstall.SOURCE_PARTS)
    hostile.mkdir(parents=True)
    (hostile / "WowLab.toc").write_bytes(b"## Interface: @WOWLAB_INTERFACE@\n")
    (hostile / "Evil.lua").write_bytes(b"-- constructed, must never be installed\n")
    package = _site_packages(tmp_path)
    package.mkdir(parents=True)
    with pytest.raises(addoninstall.AddonSourceError, match="not running from a checkout"):
        addoninstall.find_source(package, package=package)


# ─── the copy bundled in an installed package (M13-14) ───────────────────────


def _site_packages(tmp_path: Path) -> Path:
    return tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "wowlab_core"


def _bundled_package(tmp_path: Path) -> Path:
    """Constructed: an installed `wowlab_core` laid out as the wheel lays it
    out, with the repository's TOC and `.lua` files under `_addon/WowLab/`."""
    package = _site_packages(tmp_path)
    bundle = package.joinpath(*addoninstall.BUNDLE_PARTS)
    bundle.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"")
    for p in SOURCE.iterdir():
        if p.name == "WowLab.toc" or p.suffix == ".lua":
            (bundle / p.name).write_bytes(p.read_bytes())
    return package


def _files_from(package: Path) -> dict[str, bytes]:
    return addoninstall.addon_files(INTERFACE, addoninstall.find_source(package, package=package))


def test_the_sdist_bundles_the_addon_where_find_source_looks() -> None:
    """The build copies `lab/addon/WowLab/` to the folder `BUNDLE_PARTS` names."""
    config = tomllib.loads((REPO / "lab" / "core" / "pyproject.toml").read_text("utf-8"))
    build = config["tool"]["hatch"]["build"]["targets"]
    assert build["wheel"]["packages"] == ["src/wowlab_core"]
    target = "src/wowlab_core/" + "/".join(addoninstall.BUNDLE_PARTS)
    assert build["sdist"]["force-include"]["../addon/WowLab"] == target
    assert "force-include" not in build["wheel"]


def test_an_installed_package_uses_its_bundled_copy_constructed(tmp_path: Path) -> None:
    """No workspace root above the package, a hostile `lab/addon/WowLab/` higher
    up: the bundled copy is used, the hostile folder never is."""
    hostile = tmp_path.joinpath(*addoninstall.SOURCE_PARTS)
    hostile.mkdir(parents=True)
    (hostile / "WowLab.toc").write_bytes((SOURCE / "WowLab.toc").read_bytes())
    package = _bundled_package(tmp_path)
    found = addoninstall.find_source(package, package=package)
    assert found == package.resolve().joinpath(*addoninstall.BUNDLE_PARTS)
    assert addoninstall.addon_files(INTERFACE, found) == addoninstall.addon_files(INTERFACE)


def test_a_workspace_root_without_sources_falls_back_to_the_bundle_constructed(
    tmp_path: Path,
) -> None:
    """A venv inside some other git repository: not a checkout, so the
    bundled copy is used; nothing above the package is searched."""
    hostile = tmp_path.joinpath(*addoninstall.SOURCE_PARTS)
    hostile.mkdir(parents=True)
    (hostile / "WowLab.toc").write_bytes((SOURCE / "WowLab.toc").read_bytes())
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    package = _bundled_package(project)
    assert addoninstall.find_source(package, package=package) == package.resolve().joinpath(
        *addoninstall.BUNDLE_PARTS
    )


@pytest.mark.parametrize("marker", ["git", "git-file", "pyproject"])
def test_an_installed_package_under_a_root_with_sources_uses_its_bundle_constructed(
    tmp_path: Path, marker: str
) -> None:
    """Security finding 1 on PR #182: an installed wowlab inside any folder
    holding `.git` (even an empty one) or a uv-workspace `pyproject.toml`,
    and `lab/addon/WowLab/` beside it. That folder's Lua is never used; the
    package's bundled copy is."""
    project = tmp_path / "project"
    _root_marker(project, marker)
    _sources_in(project)
    package = _bundled_package(project)
    bundle = package.resolve().joinpath(*addoninstall.BUNDLE_PARTS)
    assert addoninstall.find_source(package, package=package) == bundle
    files = addoninstall.addon_files(INTERFACE, bundle)
    assert not any(rel.endswith("/Evil.lua") for rel in files)


def test_a_lab_folder_in_a_second_root_further_up_is_ignored_constructed(
    tmp_path: Path,
) -> None:
    """Two roots: the near one holds the package in the checkout layout but
    no sources, the far one holds `lab/addon/WowLab/`. The far one is never
    used; the package's bundled copy is."""
    outer = tmp_path / "outer"
    _root_marker(outer, "git")
    _sources_in(outer)
    inner = outer / "inner"
    _root_marker(inner, "git")
    package = inner.joinpath(*addoninstall.CHECKOUT_PACKAGE_PARTS)
    bundle = package.joinpath(*addoninstall.BUNDLE_PARTS)
    bundle.mkdir(parents=True)
    for p in SOURCE.iterdir():
        if p.name == "WowLab.toc" or p.suffix == ".lua":
            (bundle / p.name).write_bytes(p.read_bytes())
    assert addoninstall.find_source(package, package=package) == bundle.resolve()
    with pytest.raises(addoninstall.AddonSourceError, match=r"checkout .* holds no lab-addon"):
        addoninstall.find_source(package, package=_empty_package(tmp_path))


@pytest.mark.parametrize("marker", ["git", "git-file", "pyproject"])
def test_the_checkout_wins_over_the_bundled_copy_constructed(tmp_path: Path, marker: str) -> None:
    """The real checkout layout: the package's folder is exactly
    `<root>/lab/core/src/wowlab_core` and `<root>` is the workspace root, so
    `<root>/lab/addon/WowLab/` is used even when a bundled copy exists."""
    ws = tmp_path / "ws"
    _root_marker(ws, marker)
    checkout = ws.joinpath(*addoninstall.SOURCE_PARTS)
    checkout.mkdir(parents=True)
    (checkout / "WowLab.toc").write_bytes((SOURCE / "WowLab.toc").read_bytes())
    package = ws.joinpath(*addoninstall.CHECKOUT_PACKAGE_PARTS)
    package.joinpath(*addoninstall.BUNDLE_PARTS).mkdir(parents=True)
    (package.joinpath(*addoninstall.BUNDLE_PARTS) / "WowLab.toc").write_bytes(
        (SOURCE / "WowLab.toc").read_bytes()
    )
    assert addoninstall.find_source(package, package=package) == checkout.resolve()


def test_the_checkout_layout_needs_a_workspace_root_constructed(tmp_path: Path) -> None:
    """`lab/core/src/wowlab_core` under a folder with no `.git` and no
    uv-workspace `pyproject.toml` is not a checkout."""
    _sources_in(tmp_path / "ws")
    package = (tmp_path / "ws").joinpath(*addoninstall.CHECKOUT_PACKAGE_PARTS)
    package.mkdir(parents=True)
    with pytest.raises(addoninstall.AddonSourceError, match="not running from a checkout"):
        addoninstall.find_source(package, package=_empty_package(tmp_path))


def test_a_zip_imported_package_is_refused_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real zipimport: `importlib.resources` hands back a traversable that is
    not a folder on disk, and its bundled copy is never read."""
    archive = tmp_path / "zipped.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("zipped_wowlab/__init__.py", "")
        zf.writestr("zipped_wowlab/_addon/WowLab/WowLab.toc", (SOURCE / "WowLab.toc").read_bytes())
    monkeypatch.syspath_prepend(str(archive))
    monkeypatch.delitem(sys.modules, "zipped_wowlab", raising=False)
    importlib.import_module("zipped_wowlab")
    try:
        package = resources.files("zipped_wowlab")
        assert not isinstance(package, Path)
        with pytest.raises(addoninstall.AddonSourceError, match="not a real folder on disk"):
            addoninstall.find_source(tmp_path / "elsewhere", package=package)
    finally:
        sys.modules.pop("zipped_wowlab", None)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
@pytest.mark.parametrize(
    ("linked", "match"),
    [
        ("_addon", "is a link"),
        ("_addon/WowLab", "is a link"),
        ("_addon/WowLab/WowLab.toc", "not a regular file"),
        ("_addon/WowLab/Core.lua", "not a regular file"),
    ],
    ids=["addon-folder", "wowlab-folder", "toc", "lua"],
)
def test_a_link_in_the_bundled_copy_is_refused_constructed(
    tmp_path: Path, linked: str, match: str
) -> None:
    package = _bundled_package(tmp_path / "inst")
    target = package / linked
    moved = tmp_path / "elsewhere" / target.name
    moved.parent.mkdir()
    target.rename(moved)
    target.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(addoninstall.AddonSourceError, match=match):
        _files_from(package)


def test_install_from_an_installed_package_uses_the_bundled_copy_constructed(
    root: Path, flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI end to end, wowlab running from a constructed installed package."""
    package = _bundled_package(tmp_path / "inst")
    monkeypatch.setattr(addoninstall, "_HERE", package)
    monkeypatch.setattr(addoninstall, "_package", lambda: package)
    bundle = package.resolve().joinpath(*addoninstall.BUNDLE_PARTS)
    report = _json_of(cli.AddonInstallReport, "addon", "install", "lab", "--yes")
    assert report.applied and report.source == str(bundle)
    assert sorted(p.name for p in (flavor / LAB).iterdir()) == sorted(
        ["WowLab.toc", *_source_lua()]
    )
    for name in _source_lua():
        assert (flavor / LAB / name).read_bytes() == (SOURCE / name).read_bytes()
    ok("addon", "remove", "lab", "--yes")
    assert list((flavor / LAB).iterdir()) == []


def test_a_member_pyproject_is_not_the_workspace_root_constructed(tmp_path: Path) -> None:
    found = tmp_path.joinpath(*addoninstall.SOURCE_PARTS)
    found.mkdir(parents=True)
    (found / "WowLab.toc").write_bytes((SOURCE / "WowLab.toc").read_bytes())
    (tmp_path / ".git").mkdir()
    member = tmp_path / "lab" / "core"
    package = tmp_path.joinpath(*addoninstall.CHECKOUT_PACKAGE_PARTS)
    package.mkdir(parents=True)
    (member / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert addoninstall.find_source(package, package=_empty_package(tmp_path)) == found


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
def test_a_linked_source_file_is_refused_constructed(tmp_path: Path) -> None:
    folder = _source_copy(tmp_path)
    elsewhere = tmp_path / "elsewhere.lua"
    elsewhere.write_bytes((folder / "Core.lua").read_bytes())
    (folder / "Core.lua").unlink()
    (folder / "Core.lua").symlink_to(elsewhere)
    with pytest.raises(addoninstall.AddonSourceError, match="not a regular file"):
        addoninstall.addon_files(INTERFACE, folder)


@pytest.mark.parametrize(
    "version",
    ["1.60.1.1234567890", "1234567890.1.1.2", "1.60.0000000001.2"],
    ids=["build-10-digits", "major-10-digits", "patch-10-digits"],
)
def test_a_version_part_over_nine_digits_is_refused_constructed(version: str) -> None:
    with pytest.raises(addoninstall.AddonVersionError, match="at most 9 digits"):
        addoninstall.interface_version(version)


PATCH_TEXT = (
    "The TOC says ## Interface: 16001, derived from the client version 1.60.1.69913 in "
    ".build.info (flavor folder _classic_beta_). When a client update changes the first three "
    "numbers of that version, the AddOns list may mark WowLab out of date: close the client "
    "and run `wowlab addon install lab` again. An update that changes only the build number "
    "needs no reinstall."
)


def test_install_notes_are_the_same_in_text_and_json_on_every_path(root: Path) -> None:
    """M11-17: the load note, in the text and in `--json` alike, only after an
    install that was applied."""
    assert "If it is marked out of date now," in addoninstall.LOAD_NOTE
    dry = ok("addon", "install", "lab", "--dry-run").stdout
    assert PATCH_TEXT in dry and addoninstall.LOAD_NOTE not in dry
    assert _json_of(cli.AddonInstallReport, "addon", "install", "lab", "--dry-run").notes == [
        PATCH_TEXT
    ]
    declined = run("addon", "install", "lab", "--json", input="n\n")
    assert declined.exit_code == 1
    # The test runner echoes typed input onto stdout; a terminal does not.
    body = declined.stdout[declined.stdout.index("{") :]
    assert cli.AddonInstallReport.model_validate_json(body).notes == [PATCH_TEXT]
    done = ok("addon", "install", "lab", "--yes").stdout
    installed = done.index("Installed the lab-addon")
    assert done.index(PATCH_TEXT) < installed < done.index(addoninstall.LOAD_NOTE)
    again = ok("addon", "install", "lab", "--yes").stdout
    assert "Nothing to install" in again and PATCH_TEXT in again
    assert addoninstall.LOAD_NOTE not in again
    assert _json_of(cli.AddonInstallReport, "addon", "install", "lab").notes == [PATCH_TEXT]


def test_install_json_carries_the_load_note_only_when_applied(root: Path) -> None:
    assert not (root / FLAVOR / LAB).exists()
    applied = _json_of(cli.AddonInstallReport, "addon", "install", "lab", "--yes")
    assert applied.applied
    assert applied.notes == [PATCH_TEXT, addoninstall.LOAD_NOTE]
    (root / FLAVOR / LAB / "Old.lua").write_bytes(b"-- constructed leftover\n")
    dry = _json_of(cli.AddonInstallReport, "addon", "install", "lab", "--yes", "--dry-run")
    assert dry.plan and not dry.applied and dry.notes == [PATCH_TEXT]


def test_remove_notes_are_printed_on_every_path(root: Path) -> None:
    nothing = ok("addon", "remove", "lab", "--yes").stdout
    assert addoninstall.SAVED_VARIABLES_NOTE in nothing
    assert addoninstall.FOLDER_STAYS_NOTE not in nothing  # there is no folder
    ok("addon", "install", "lab", "--yes")
    dry = ok("addon", "remove", "lab", "--dry-run").stdout
    assert addoninstall.SAVED_VARIABLES_NOTE in dry and addoninstall.FOLDER_STAYS_NOTE in dry
    report = _json_of(cli.AddonRemoveReport, "addon", "remove", "lab", "--dry-run")
    assert report.notes == [addoninstall.SAVED_VARIABLES_NOTE, addoninstall.FOLDER_STAYS_NOTE]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
@pytest.mark.parametrize(
    ("name", "folder_note"),
    [
        ("Linked.toc", addoninstall.TOC_LEFT_NOTE),
        (
            "linked-notes",
            addoninstall.FOLDER_STAYS_NOTE + " " + addoninstall.FOLDER_LEFT_NOTE,
        ),
    ],
    ids=["toc-left-constructed", "other-left-constructed"],
)
def test_the_folder_note_follows_what_is_left_alone(
    root: Path, flavor: Path, tmp_path: Path, name: str, folder_note: str
) -> None:
    ok("addon", "install", "lab", "--yes")
    target = tmp_path / "link-target"
    target.write_bytes(b"constructed")
    (flavor / LAB / name).symlink_to(target)
    report = _json_of(cli.AddonRemoveReport, "addon", "remove", "lab", "--yes")
    assert [lp.path for lp in report.left] == [f"{LAB}/{name}"]
    assert report.notes == [addoninstall.SAVED_VARIABLES_NOTE, folder_note]
    assert (flavor / LAB / name).is_symlink()


@pytest.mark.parametrize(
    "rel",
    [
        f"{LAB}/../../../WTF/Config.wtf",
        f"{LAB}/./Core.lua",
        f"{LAB}//Core.lua",
        f"{LAB}/",
        f"{LAB}/sub/..",
    ],
    ids=["dotdot", "dot", "empty-segment", "trailing-slash", "dotdot-last"],
)
def test_in_folder_refuses_dot_and_empty_components_constructed(rel: str) -> None:
    with pytest.raises(addoninstall.AddonError, match="is not under"):
        addoninstall._in_folder(rel)
    addoninstall._in_folder(f"{LAB}/Core.lua")  # the ordinary case passes
