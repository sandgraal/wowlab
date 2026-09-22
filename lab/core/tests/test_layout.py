"""`layout` on synthetic trees (constructed; L1, L6, L8).

The captured tree (`parser/test_layout_fixtures.py`) has no SavedVariables,
no retail-style twin, no second account shape, no overrides, no caches, no
symlinks and no non-ASCII names (pseudonyms are ASCII). Those shapes are
built here in `tmp_path` from the folder layout in `docs/LAB_FILE_MAP.md`
and `docs/GLOSSARY.md`; every test id carries `constructed`. Where a file's
bytes matter (a TOC), the real captured bytes are used.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from wowlab_core import layout as layout_module
from wowlab_core.install import read_install
from wowlab_core.layout import (
    Character,
    Classified,
    Inventory,
    Layout,
    Limits,
    Symlink,
    Unclassified,
    classify,
    layouts,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REAL_TOC = (
    FIXTURES / "macos/forever/Interface/AddOns/DBM-Challenges/DBM-Challenges.toc"
).read_bytes()
REAL_BUILD_INFO = (FIXTURES / "macos/.build.info").read_bytes()
REAL_FLAVOR_INFO = (FIXTURES / "macos/forever/.flavor.info").read_bytes()
FLAVOR = "_flavor_"  # any `_*_` name; the library never looks at it


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        path = root / rel
        if rel.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def _install(tmp_path: Path, files: dict[str, bytes]) -> tuple[Path, Path]:
    root = tmp_path / "World of Warcraft"
    _tree(root, {".build.info": REAL_BUILD_INFO, f"{FLAVOR}/.flavor.info": REAL_FLAVOR_INFO})
    _tree(root / FLAVOR, files)
    return root, root / FLAVOR


def _symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create symlinks here: {exc}")


A = "WTF/Account/ACCOUNT1"


# ─── accounts, realms, characters ───────────────────────────────────────────


def test_both_account_shapes_and_twins_constructed(tmp_path: Path) -> None:
    _, flavor = _install(
        tmp_path,
        {
            f"{A}/config-cache.wtf": b"",
            f"{A}/7/Ann-Bee/config-cache.wtf": b"",
            f"{A}/7/Ann-Bee/SavedVariables/Foo.lua": b"x",
            f"{A}/7/Ann-Cee/macros-cache.txt": b"",
            f"{A}/7/Solo/chat-cache.txt": b"",
            f"{A}/Realm Name/Ann/AddOns.txt": b"Foo: enabled\n",
            f"{A}/Realm Name/Dan/config-cache.wtf": b"",
            f"{A}/SavedVariables/Foo.lua": b"x",
        },
    )
    (account,) = Layout(flavor).accounts()
    assert account.folder == "ACCOUNT1"
    assert account.has_saved_variables
    assert account.files == ("config-cache.wtf",)
    realms = {r.folder: r for r in account.realms}
    assert set(realms) == {"7", "Realm Name"}, "SavedVariables is not a realm"
    assert realms["7"].kind == "numeric"
    assert realms["Realm Name"].kind == "name"

    chars = {c.path.removeprefix(A + "/"): c for r in account.realms for c in r.characters}
    bee = chars["7/Ann-Bee"]
    assert (bee.shape, bee.first_name, bee.second_name) == ("numeric_folder", "Ann", "Bee")
    assert bee.realm_folder == "7", "the digits folder is kept as found, not as a realm name"
    assert bee.folders == ("SavedVariables",)
    assert chars["7/Ann-Cee"].second_name == "Cee"
    assert (chars["7/Solo"].first_name, chars["7/Solo"].second_name) == ("Solo", None)
    twin = chars["Realm Name/Ann"]
    assert (twin.shape, twin.first_name, twin.second_name) == ("realm_name", "Ann", None)
    assert twin.files == ("AddOns.txt",)
    assert twin.twins == (f"{A}/7/Ann-Bee", f"{A}/7/Ann-Cee")
    assert bee.twins == (f"{A}/Realm Name/Ann",)
    assert chars["Realm Name/Dan"].twins == ()
    assert chars["7/Solo"].twins == ()

    lay = Layout(flavor)
    ids = {
        rel: lay.classify(rel)
        for rel in (f"{A}/7", f"{A}/Realm Name", f"{A}/7/Ann-Bee", f"{A}/Realm Name/Ann")
    }
    assert {k: v.entry.id for k, v in ids.items() if isinstance(v, Classified)} == {
        f"{A}/7": "numeric-folder",
        f"{A}/Realm Name": "realm-folder",
        f"{A}/7/Ann-Bee": "second-name-character-folder",
        f"{A}/Realm Name/Ann": "character-folder",
    }


def test_hyphen_in_a_realm_name_folder_is_not_split_constructed(tmp_path: Path) -> None:
    _, flavor = _install(tmp_path, {f"{A}/Azjol-Nerub/Some-Thing/chat-cache.txt": b""})
    (account,) = Layout(flavor).accounts()
    (realm,) = account.realms
    (char,) = realm.characters
    assert (realm.kind, char.shape) == ("name", "realm_name")
    assert (char.first_name, char.second_name) == ("Some-Thing", None)


def test_non_ascii_names_constructed(tmp_path: Path) -> None:
    """The corpus has only ASCII pseudonyms (fixtures README), so non-ASCII
    folder and character names are covered here."""
    names = {
        "WTF/Account/ÄCCÖUNT#1/Mal'Ganis/Ærwyn/config-cache.wtf": b"",
        "WTF/Account/ÄCCÖUNT#1/Кровь/Ноктюрн/AddOns.txt": b"",
        "WTF/Account/ÄCCÖUNT#1/42/Ælf-Ündine/SavedVariables/Addön.lua": b"x",
        "WTF/Account/ÄCCÖUNT#1/SavedVariables/魔兽.lua": b"x",
        "Interface/AddOns/Añadido/Añadido.toc": REAL_TOC,
    }
    root, flavor = _install(tmp_path, names)
    inv = Layout(flavor).inventory()
    (account,) = inv.accounts
    assert account.folder == "ÄCCÖUNT#1"
    chars = {c.folder: c for r in account.realms for c in r.characters}
    assert set(chars) == {"Ærwyn", "Ноктюрн", "Ælf-Ündine"}
    assert (chars["Ælf-Ündine"].first_name, chars["Ælf-Ündine"].second_name) == ("Ælf", "Ündine")
    assert {sv.addon for sv in inv.saved_variables} == {"Addön", "魔兽"}
    assert [a.name for a in inv.addons] == ["Añadido"]
    assert inv.addons[0].selection == "single"
    assert Inventory.model_validate_json(inv.model_dump_json()) == inv
    install = read_install(root)
    for rel in names:
        got = classify(f"{FLAVOR}/{rel}", install)
        assert isinstance(got, Classified), rel


# ─── SavedVariables ─────────────────────────────────────────────────────────


def test_saved_variables_scope_owner_and_flags_constructed(tmp_path: Path) -> None:
    _, flavor = _install(
        tmp_path,
        {
            f"{A}/SavedVariables.lua": b"a",
            f"{A}/SavedVariables.lua.bak": b"ab",
            f"{A}/SavedVariables/Details.lua": b"abc",
            f"{A}/SavedVariables/Details.lua.bak": b"abcd",
            f"{A}/SavedVariables/notes.txt": b"",
            f"{A}/Realm/Char/SavedVariables/Details.lua": b"abcde",
            f"{A}/9/First-Second/SavedVariables/WeakAuras.lua": b"abcdef",
            f"{A}/9/First-Second/SavedVariables/WeakAuras.lua.bak": b"",
        },
    )
    lay = Layout(flavor)
    by_path = {sv.path.removeprefix(A + "/"): sv for sv in lay.saved_variables()}
    assert set(by_path) == {
        "SavedVariables.lua",
        "SavedVariables.lua.bak",
        "SavedVariables/Details.lua",
        "SavedVariables/Details.lua.bak",
        "Realm/Char/SavedVariables/Details.lua",
        "9/First-Second/SavedVariables/WeakAuras.lua",
        "9/First-Second/SavedVariables/WeakAuras.lua.bak",
    }
    blizzard = by_path["SavedVariables.lua"]
    assert (blizzard.scope, blizzard.addon, blizzard.blizzard, blizzard.backup) == (
        "account",
        None,
        True,
        False,
    )
    assert by_path["SavedVariables.lua.bak"].backup is True
    assert by_path["SavedVariables.lua.bak"].blizzard is True
    details = by_path["SavedVariables/Details.lua"]
    assert (details.scope, details.addon, details.size) == ("account", "Details", 3)
    assert details.entry_id == "account-savedvariables"
    bak = by_path["SavedVariables/Details.lua.bak"]
    assert (bak.addon, bak.backup, bak.entry_id) == ("Details", True, "savedvariables-backup")
    char = by_path["Realm/Char/SavedVariables/Details.lua"]
    assert (char.scope, char.realm_folder, char.character_folder) == ("character", "Realm", "Char")
    assert char.entry_id == "character-savedvariables"
    forever = by_path["9/First-Second/SavedVariables/WeakAuras.lua"]
    assert (forever.scope, forever.addon, forever.realm_folder) == ("character", "WeakAuras", "9")
    assert forever.character_folder == "First-Second"
    assert all(sv.mtime_ns > 0 and sv.account == "ACCOUNT1" for sv in by_path.values())

    assert {sv.scope for sv in lay.saved_variables("account")} == {"account"}
    assert len(lay.saved_variables("character")) == 3
    stray = [w for w in lay.wtf_files() if w.name == "notes.txt"]
    assert [(w.scope, w.account, w.entry_id) for w in stray] == [("other", "ACCOUNT1", None)]


# ─── WTF files ──────────────────────────────────────────────────────────────


def test_wtf_file_scopes_constructed(tmp_path: Path) -> None:
    _, flavor = _install(
        tmp_path,
        {
            "WTF/Config.wtf": (FIXTURES / "macos/forever/WTF/Config.wtf").read_bytes(),
            "WTF/Launcher.wtf": b"",
            f"{A}/bindings-cache.wtf": b"",
            f"{A}/Realm/Char/AddOns.txt": b"",
            f"{A}/Realm/Char/layout-local.txt": b"",
            f"{A}/Realm/loose.txt": b"",
        },
    )
    got = {w.path: (w.scope, w.entry_id) for w in Layout(flavor).wtf_files()}
    assert got == {
        "WTF/Config.wtf": ("machine", "config-wtf"),
        "WTF/Launcher.wtf": ("machine", None),
        f"{A}/bindings-cache.wtf": ("account", "account-bindings-cache"),
        f"{A}/Realm/Char/AddOns.txt": ("character", "character-addons-txt"),
        f"{A}/Realm/Char/layout-local.txt": ("character", "character-layout-local"),
        f"{A}/Realm/loose.txt": ("other", None),
    }


def test_folder_names_are_found_case_insensitively_constructed(tmp_path: Path) -> None:
    _, flavor = _install(
        tmp_path,
        {
            "wtf/account/ACC/savedvariables/Foo.lua": b"",
            "interface/addons/Foo/foo.TOC": REAL_TOC,
        },
    )
    lay = Layout(flavor)
    assert [sv.addon for sv in lay.saved_variables()] == ["Foo"]
    (addon,) = lay.addons()
    assert (addon.selection, addon.selected_toc) == ("single", "foo.TOC")


# ─── addons ─────────────────────────────────────────────────────────────────


def test_addons_tocs_and_selection_constructed(tmp_path: Path) -> None:
    _, flavor = _install(
        tmp_path,
        {
            "Interface/AddOns/Foo/Foo.toc": REAL_TOC,
            "Interface/AddOns/Foo/Foo.lua": b"",
            "Interface/AddOns/Bar/Bar.toc": REAL_TOC,
            "Interface/AddOns/Bar/Bar_Mainline.toc": REAL_TOC,
            "Interface/AddOns/Bar/Bar-Classic.toc": REAL_TOC,
            "Interface/AddOns/Baz/Baz_Vanilla.toc": REAL_TOC,
            "Interface/AddOns/Qux/Qux.lua": b"",
            "Interface/AddOns/Odd/Other.toc": REAL_TOC,
            "Interface/AddOns/Odd/Odd_.toc": REAL_TOC,
            "Interface/AddOns/Blizzard_Thing/Blizzard_Thing.toc": REAL_TOC,
            "Interface/AddOns/Empty/": b"",
            "Interface/AddOns/readme.txt": b"",
        },
    )
    addons = {a.name: a for a in Layout(flavor).addons()}
    assert list(addons) == sorted(addons), "sorted by folder name"
    assert set(addons) == {"Foo", "Bar", "Baz", "Qux", "Odd", "Blizzard_Thing", "Empty"}
    assert (addons["Foo"].selection, addons["Foo"].selected_toc) == ("single", "Foo.toc")
    bar = addons["Bar"]
    assert [(t.file, t.suffix, t.matches_folder) for t in bar.tocs] == [
        ("Bar-Classic.toc", "Classic", True),
        ("Bar.toc", None, True),
        ("Bar_Mainline.toc", "Mainline", True),
    ]
    assert (bar.selection, bar.selected_toc) == ("depends_on_game_type", None)
    assert all(t.document is not None and t.document.to_bytes() == REAL_TOC for t in bar.tocs)
    assert addons["Baz"].selection == "depends_on_game_type", "no guess at a suffix (L6)"
    assert (addons["Qux"].selection, addons["Qux"].tocs) == ("no_toc", ())
    odd = addons["Odd"]
    assert [(t.file, t.matches_folder) for t in odd.tocs] == [
        ("Odd_.toc", False),
        ("Other.toc", False),
    ]
    assert odd.selection == "no_toc"
    assert addons["Blizzard_Thing"].blizzard is True
    assert not any(a.blizzard for n, a in addons.items() if n != "Blizzard_Thing")
    assert addons["Empty"].selection == "no_toc"


def test_oversized_toc_is_reported_not_read_constructed(tmp_path: Path) -> None:
    _, flavor = _install(tmp_path, {"Interface/AddOns/Foo/Foo.toc": REAL_TOC})
    (addon,) = Layout(flavor, limits=Limits(max_toc_bytes=100)).addons()
    (toc,) = addon.tocs
    assert toc.document is None
    assert toc.error is not None and "larger than 100 bytes" in toc.error
    assert toc.size == len(REAL_TOC)


# ─── other areas and overrides ──────────────────────────────────────────────


def test_other_areas_and_loose_overrides_constructed(tmp_path: Path) -> None:
    _, flavor = _install(
        tmp_path,
        {
            "Interface/Icons/Foo.blp": b"12",
            "Interface/Loose.blp": b"1",
            "Interface/AddOns/Foo/Foo.toc": REAL_TOC,
            "Fonts/FRIZQT__.TTF": b"123",
            "Cache/WDB/enUS/creaturecache.wdb": b"1234",
            "Cache/ADB/enUS/DBCache.bin": b"12345",
            "Logs/Client.log": b"",
            "Screenshots/WoWScrnShot_092226_120000.jpg": b"123456",
        },
    )
    other = Layout(flavor).other()
    areas = {a.name: a for a in other.areas}
    assert [a.name for a in other.areas] == [
        "Cache",
        "Logs",
        "Screenshots",
        "Errors",
        "Fonts",
        "BlizzardInterfaceCode",
        "BlizzardInterfaceArt",
    ]
    cache = areas["Cache"]
    assert (cache.present, cache.files, cache.folders, cache.bytes) == (True, 2, 4, 9)
    assert (areas["Logs"].files, areas["Screenshots"].bytes) == (1, 6)
    assert areas["Errors"].present is False and areas["Errors"].path is None
    overrides = {o.path: (o.area, o.size, o.entry_id) for o in other.overrides}
    assert overrides == {
        "Fonts/FRIZQT__.TTF": ("fonts", 3, "fonts"),
        "Interface/Icons/Foo.blp": ("interface", 2, "interface-override"),
        "Interface/Loose.blp": ("interface", 1, "interface-override"),
    }


def test_os_metadata_is_listed_apart_from_everything_else_constructed(tmp_path: Path) -> None:
    _, flavor = _install(
        tmp_path,
        {
            "Interface/.DS_Store": b"",
            "Interface/Icons/Foo.blp": b"1",
            "Interface/AddOns/Foo/Foo.toc": REAL_TOC,
            "Interface/AddOns/Foo/._Foo.toc": b"\x00\x05\x16\x07",
            "Fonts/.DS_Store": b"",
            "Fonts/FRIZQT__.TTF": b"123",
            f"{A}/SavedVariables/._Foo.lua": b"",
            f"{A}/SavedVariables/Foo.lua": b"x",
            f"{A}/Realm/Char/desktop.ini": b"",
            f"{A}/Realm/Char/AddOns.txt": b"",
            "WTF/.DS_Store": b"",
            "WTF/Config.wtf": b"",
        },
    )
    inv = Layout(flavor).inventory()
    assert inv.os_metadata == tuple(
        sorted(
            [
                "Fonts/.DS_Store",
                "Interface/.DS_Store",
                "Interface/AddOns/Foo/._Foo.toc",
                f"{A}/Realm/Char/desktop.ini",
                f"{A}/SavedVariables/._Foo.lua",
                "WTF/.DS_Store",
            ]
        )
    )
    assert [o.path for o in inv.other.overrides] == [
        "Fonts/FRIZQT__.TTF",
        "Interface/Icons/Foo.blp",
    ]
    assert [sv.addon for sv in inv.saved_variables] == ["Foo"]
    assert sorted(w.path for w in inv.wtf_files) == [f"{A}/Realm/Char/AddOns.txt", "WTF/Config.wtf"]
    assert [t.file for t in inv.addons[0].tocs] == ["Foo.toc"]
    assert inv.addons[0].selection == "single"
    fonts = next(a for a in inv.other.areas if a.name == "Fonts")
    assert (fonts.files, fonts.bytes) == (1, 3), "area counts leave OS metadata out"
    (char,) = inv.accounts[0].realms[0].characters
    assert char.files == ("AddOns.txt",)
    assert Inventory.model_validate_json(inv.model_dump_json()) == inv


def test_os_metadata_at_the_flavor_root_and_in_addons_folder_constructed(tmp_path: Path) -> None:
    _, flavor = _install(
        tmp_path,
        {
            ".DS_Store": b"",
            "desktop.ini": b"",
            "Interface/AddOns/.DS_Store": b"",
            "Interface/AddOns/Foo/Foo.toc": REAL_TOC,
            "Interface/AddOns/Foo/Libs/.DS_Store": b"",  # deeper: not walked
        },
    )
    inv = Layout(flavor).inventory()
    assert inv.os_metadata == (".DS_Store", "Interface/AddOns/.DS_Store", "desktop.ini")
    assert [a.name for a in inv.addons] == ["Foo"]
    assert inv.other.overrides == ()


def test_savedvariables_folder_at_realm_depth_is_not_a_character_constructed(
    tmp_path: Path,
) -> None:
    _, flavor = _install(tmp_path, {f"{A}/Realm/SavedVariables/Foo.lua": b"x"})
    lay = Layout(flavor)
    (realm,) = lay.accounts()[0].realms
    assert realm.characters == ()
    (stray,) = lay.wtf_files()
    assert (stray.scope, stray.character_folder, stray.entry_id) == ("other", None, None)
    assert lay.saved_variables() == ()
    got = lay.classify(f"{A}/Realm/SavedVariables")
    assert isinstance(got, Unclassified)


# ─── symlinks ───────────────────────────────────────────────────────────────


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every path the layout module lists or opens."""
    seen: list[str] = []
    real_scandir = os.scandir
    real_open = Path.open

    def scandir(path: object) -> object:
        seen.append(os.fspath(path))  # type: ignore[call-overload]
        return real_scandir(path)  # type: ignore[call-overload]

    def open_(self: Path, *args: object, **kwargs: object) -> object:
        seen.append(str(self))
        return real_open(self, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(layout_module.os, "scandir", scandir)
    monkeypatch.setattr(Path, "open", open_)
    yield seen


def test_symlinks_outside_the_install_are_reported_not_followed_constructed(
    tmp_path: Path, spy: list[str]
) -> None:
    root, flavor = _install(
        tmp_path,
        {
            "Interface/AddOns/Real/Real.toc": REAL_TOC,
            f"{A}/SavedVariables/Mine.lua": b"x",
        },
    )
    outside = _tree(
        tmp_path / "outside",
        {
            "AddonSrc/Linked.toc": b"## Title: OUTSIDE-MARKER\n",
            "secret.toc": b"## Title: OUTSIDE-MARKER\n",
            "Foo.lua": b"OUTSIDE-MARKER",
            "fonts/x.ttf": b"OUTSIDE-MARKER",
            "wtf/Config.wtf": b"OUTSIDE-MARKER",
        },
    )
    _symlink(flavor / "Interface/AddOns/Linked", outside / "AddonSrc", directory=True)
    _symlink(flavor / "Interface/AddOns/Real/Other.toc", outside / "secret.toc")
    _symlink(flavor / f"{A}/SavedVariables/Foo.lua", outside / "Foo.lua")
    _symlink(flavor / "Fonts", outside / "fonts", directory=True)
    _symlink(flavor / "Interface/Skin.blp", outside / "Foo.lua")

    spy.clear()  # the tree above was written through Path.open
    inv = Layout(flavor).inventory()

    links = {s.path: s for s in inv.symlinks}
    assert set(links) == {
        "Interface/AddOns/Linked",
        "Interface/AddOns/Real/Other.toc",
        f"{A}/SavedVariables/Foo.lua",
        "Fonts",
        "Interface/Skin.blp",
    }
    assert not any(s.inside_install for s in links.values())
    assert links["Fonts"].target is not None
    assert Path(links["Fonts"].target).name == "fonts", "the link text, as stored"
    assert links["Interface/AddOns/Linked"].entry_id == "addon"
    assert [a.name for a in inv.addons] == ["Real"], "a linked addon folder is not walked"
    assert [t.file for t in inv.addons[0].tocs] == ["Real.toc"]
    assert [sv.addon for sv in inv.saved_variables] == ["Mine"]
    fonts = next(a for a in inv.other.areas if a.name == "Fonts")
    assert (fonts.present, fonts.symlink, fonts.files) == (True, True, 0)
    assert [o.path for o in inv.other.overrides] == []
    assert "OUTSIDE-MARKER" not in inv.model_dump_json()
    assert spy, "the spy saw the walk"
    assert not [p for p in spy if p.startswith(str(outside))], "nothing outside was read"
    assert read_install(root).flavors, "the install is still an install"


def test_symlinks_inside_the_install_are_reported_not_followed_constructed(
    tmp_path: Path,
) -> None:
    _, flavor = _install(tmp_path, {"Interface/AddOns/Real/Real.toc": REAL_TOC})
    _symlink(flavor / "Interface/AddOns/Alias", flavor / "Interface/AddOns/Real", directory=True)
    _symlink(flavor / "WTF", flavor / "Interface", directory=True)
    inv = Layout(flavor).inventory()
    links = {s.path: s for s in inv.symlinks}
    assert set(links) == {"Interface/AddOns/Alias", "WTF"}
    assert all(s.inside_install for s in links.values())
    assert [a.name for a in inv.addons] == ["Real"]
    assert inv.accounts == ()
    link_class = Layout(flavor).classify("Interface/AddOns/Alias")
    assert isinstance(link_class, Classified) and link_class.entry.id == "addon"


def test_symlink_loop_terminates_constructed(tmp_path: Path) -> None:
    _, flavor = _install(tmp_path, {"Interface/AddOns/": b""})
    _symlink(flavor / "Interface/AddOns/Loop", flavor / "Interface/AddOns/Loop", directory=True)
    inv = Layout(flavor).inventory()
    assert [s.path for s in inv.symlinks] == ["Interface/AddOns/Loop"]
    assert inv.addons == ()


# ─── reads never write ──────────────────────────────────────────────────────


def _state(root: Path) -> list[tuple[str, int, int, int]]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            p = Path(dirpath, name)
            st = p.lstat()
            out.append((str(p), st.st_size, st.st_mtime_ns, st.st_mode))
    return sorted(out)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_reads_never_write_constructed(tmp_path: Path) -> None:
    root, flavor = _install(
        tmp_path,
        {
            "WTF/Config.wtf": b'SET a "1"\n',
            f"{A}/SavedVariables/Foo.lua": b"x",
            f"{A}/9/F-S/config-cache.wtf": b"",
            f"{A}/Realm/F/AddOns.txt": b"",
            "Interface/AddOns/Foo/Foo.toc": REAL_TOC,
            "Interface/Icons/x.blp": b"",
            "Fonts/a.ttf": b"",
            "Cache/WDB/enUS/a.wdb": b"",
        },
    )
    before = _state(root)
    paths = [root, *(Path(d) for d, _, _ in os.walk(root))]
    for p in paths:
        p.chmod(0o555)
    try:
        inv = Layout(flavor).inventory()
        install = read_install(root)
        for p in sorted(root.rglob("*")):
            classify(p, install)
        for lay in layouts(install):
            lay.accounts(), lay.addons(), lay.wtf_files(), lay.other(), lay.saved_variables()
    finally:
        for p in paths:
            p.chmod(0o755)
    assert _state(root) == before, "nothing created, changed or touched"
    assert inv.errors == ()
    assert len(inv.saved_variables) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads anything")
def test_an_unreadable_folder_is_reported_constructed(tmp_path: Path) -> None:
    _, flavor = _install(tmp_path, {f"{A}/SavedVariables/Foo.lua": b"x"})
    locked = flavor / A / "SavedVariables"
    locked.chmod(0)
    try:
        inv = Layout(flavor).inventory()
    finally:
        locked.chmod(0o755)
    assert [e.path for e in inv.errors] == [f"{A}/SavedVariables"]
    assert inv.saved_variables == ()


# ─── bounds ─────────────────────────────────────────────────────────────────


def test_depth_and_entry_bounds_truncate_and_say_so_constructed(tmp_path: Path) -> None:
    _, flavor = _install(
        tmp_path,
        {
            f"{A}/SavedVariables/Foo.lua": b"x",
            "Interface/AddOns/A/A.toc": REAL_TOC,
            "Interface/AddOns/B/B.toc": REAL_TOC,
        },
    )
    shallow = Layout(flavor, limits=Limits(max_depth=1)).inventory()
    assert shallow.truncated is True
    assert shallow.saved_variables == ()
    few = Layout(flavor, limits=Limits(max_entries=3)).inventory()
    assert few.truncated is True
    whole = Layout(flavor).inventory()
    assert whole.truncated is False
    assert [a.name for a in whole.addons] == ["A", "B"]


# ─── classify ───────────────────────────────────────────────────────────────


def test_classify_outside_root_other_dirs_and_hints_constructed(tmp_path: Path) -> None:
    root, flavor = _install(tmp_path, {"WTF/Config.wtf": b"", "Interface/AddOns/": b""})
    (root / "_nofile_").mkdir()
    install = read_install(root)
    assert install.other_dirs == ("_nofile_",)

    outside = classify(tmp_path / "elsewhere.txt", install)
    assert isinstance(outside, Unclassified)
    assert (outside.base, outside.reason) == (None, "not inside the install root")
    escaped = classify(f"{FLAVOR}/../../elsewhere.txt", install)
    assert isinstance(escaped, Unclassified) and escaped.base is None
    itself = classify(root, install)
    assert isinstance(itself, Unclassified) and itself.reason == "the install root itself"
    other_dir = classify("_nofile_", install)
    assert isinstance(other_dir, Unclassified) and other_dir.base == "root"

    dotted = classify(f"{FLAVOR}/WTF/./Account/../Config.wtf", install)
    assert isinstance(dotted, Classified) and dotted.entry.id == "config-wtf"
    assert dotted.path == "WTF/Config.wtf" and dotted.flavor_folder == FLAVOR
    missing_dir = classify(f"{FLAVOR}/WTF/Account/ACC/", install)
    assert isinstance(missing_dir, Classified) and missing_dir.entry.id == "account-folder"
    missing_any = classify(f"{FLAVOR}/WTF/Account/ACC/9", install)
    assert isinstance(missing_any, Classified) and missing_any.entry.id == "numeric-folder"
    forced = classify(f"{FLAVOR}/WTF/Account/ACC/9", install, is_dir=False)
    assert isinstance(forced, Unclassified) and forced.reason == "no file-map row matches"
    build = classify(".build.info", install)
    assert isinstance(build, Classified) and (build.base, build.path) == ("root", ".build.info")

    lay = Layout(flavor)
    assert lay.classify("WTF/Config.wtf") == dotted
    assert lay.classify(root / ".build.info") == build
    assert lay.classify(flavor).entry.id == "flavor-folder"  # type: ignore[union-attr]


def test_layouts_one_per_discovered_flavor_constructed(tmp_path: Path) -> None:
    root = _tree(
        tmp_path / "WoW",
        {
            ".build.info": REAL_BUILD_INFO,
            "_one_/.flavor.info": REAL_FLAVOR_INFO,
            "_one_/WTF/Config.wtf": b"",
            "_two_/.flavor.info": REAL_FLAVOR_INFO,
            "_two_/Interface/AddOns/Foo/Foo.toc": REAL_TOC,
        },
    )
    install = read_install(root)
    one, two = layouts(install)
    assert (one.flavor_folder, two.flavor_folder) == ("_one_", "_two_")
    assert one.install_root == two.install_root == root
    assert [w.path for w in one.wtf_files()] == ["WTF/Config.wtf"]
    assert [a.name for a in two.addons()] == ["Foo"]
    got = classify("_two_/Interface/AddOns/Foo/Foo.toc", install)
    assert isinstance(got, Classified) and got.flavor_folder == "_two_"


def test_layout_refuses_a_flavor_outside_its_root_constructed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is not inside"):
        Layout(tmp_path / "a" / "_x_", install_root=tmp_path / "b")


def test_a_missing_flavor_folder_inventories_empty_constructed(tmp_path: Path) -> None:
    inv = Layout(tmp_path / "gone").inventory()
    assert (inv.accounts, inv.addons, inv.wtf_files, inv.errors) == ((), (), (), ())


# ─── JSON ───────────────────────────────────────────────────────────────────


def test_file_name_strings_with_lone_surrogates_survive_json_constructed() -> None:
    """POSIX hands back undecodable name bytes as lone surrogates; APFS will
    not create such a name, so the models are built directly."""
    name = b"Caf\xe9".decode("utf-8", "surrogateescape")
    link = Symlink(
        path=f"Interface/{name}", target=f"/x/{name}", inside_install=False, entry_id=None
    )
    assert Symlink.model_validate_json(link.model_dump_json()) == link
    char = Character(
        folder=name,
        path=f"{A}/1/{name}",
        realm_folder="1",
        shape="numeric_folder",
        first_name=name,
        second_name=None,
        files=(name,),
        folders=(),
        twins=(),
    )
    assert Character.model_validate_json(char.model_dump_json()) == char
    assert "\\u0000DCE9" in char.model_dump_json()
    json.loads(char.model_dump_json())
    nul = Symlink(path="a\x00b", target=None, inside_install=True, entry_id=None)
    assert Symlink.model_validate_json(nul.model_dump_json()) == nul


@pytest.mark.skipif(sys.platform in ("darwin", "win32"), reason="needs byte file names")
def test_undecodable_file_names_on_disk_constructed(tmp_path: Path) -> None:
    _, flavor = _install(tmp_path, {f"{A}/SavedVariables/": b""})
    name = os.fsdecode(b"Caf\xe9.lua")  # a lone surrogate where the byte was
    try:
        (flavor / A / "SavedVariables" / name).write_bytes(b"x")
    except (OSError, UnicodeEncodeError) as exc:
        pytest.skip(f"file system refuses non-UTF-8 names: {exc}")
    inv = Layout(flavor).inventory()
    (sv,) = inv.saved_variables
    assert os.fsencode(sv.addon or "") == b"Caf\xe9"
    assert Inventory.model_validate_json(inv.model_dump_json()) == inv


# ─── performance (LAB_PLAN §6.2) ────────────────────────────────────────────


def test_400_addon_folders_inventory_under_two_seconds_constructed(tmp_path: Path) -> None:
    files: dict[str, bytes] = {}
    for i in range(400):
        name = f"Addon{i:03d}"
        files[f"Interface/AddOns/{name}/{name}.toc"] = REAL_TOC
        if i % 4 == 0:
            files[f"Interface/AddOns/{name}/{name}_Mainline.toc"] = REAL_TOC
        for j in range(5):
            files[f"Interface/AddOns/{name}/file{j}.lua"] = b"-- lua\n"
        files[f"Interface/AddOns/{name}/Libs/lib.lua"] = b"-- lua\n"
    _, flavor = _install(tmp_path, files)
    lay = Layout(flavor)

    start = time.perf_counter()
    addons = lay.addons()
    addons_seconds = time.perf_counter() - start
    start = time.perf_counter()
    inv = lay.inventory()
    inventory_seconds = time.perf_counter() - start

    print(f"\n400 addons: addons() {addons_seconds:.3f}s, inventory() {inventory_seconds:.3f}s")
    assert len(addons) == len(inv.addons) == 400
    assert sum(len(a.tocs) for a in addons) == 500
    assert all(t.document is not None for a in addons for t in a.tocs)
    assert addons_seconds < 2.0
    assert inventory_seconds < 2.0


# ─── L6 ─────────────────────────────────────────────────────────────────────


def test_no_flavor_constants_in_the_new_modules() -> None:
    src = Path(layout_module.__file__).parent
    forbidden = re.compile(
        r"_(retail|classic|ptr|xptr|beta|anniversary)\w*_|wow_classic|\bwowt\b|\bwow_beta\b"
        r"|\b\d{5,6}\b",
        re.IGNORECASE,
    )
    for name in ("layout.py", "toc.py", "filemap.py"):
        text = (src / name).read_text(encoding="utf-8")
        assert not forbidden.search(text), f"{name}: {forbidden.search(text)}"
