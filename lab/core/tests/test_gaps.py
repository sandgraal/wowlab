"""The whole install and its file-map gaps (M13-22, docs/LAB_PLAN.md §15.6):
`wowlab filemap gaps`, `wowlab_core.gaps`, `FileMap.covers` and
`wowlab tree --install`.

The fixture installs are the captured trees (`fixtures/macos/`,
`fixtures/macos-70058/`) copied into `tmp_path` with their flavor folder
named as their provenance rows say (`_classic_beta_`), because discovery only
reports `_*_` folders. Their gap report is committed below and compared
whole. Nothing here reads or writes a real install: `WOWLAB_WOW_ROOT` points
at the copy and the user data directory is redirected into `tmp_path`.

Every input with `constructed` in its test name is a boundary or hostile
case built in `tmp_path` (L8): unknown files and folders, links, an
unreadable folder, a lowered walk bound, a second flavor folder.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any, cast

import platformdirs
import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from wowlab_core import cli, filemap, gaps, install
from wowlab_core.layout import Limits

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance rows' flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
POSIX_PERMISSIONS = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permissions, and not as root",
)
SYMLINKS = pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


def _install_from(capture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir(parents=True)
    shutil.copy2(FIXTURES / capture / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / capture / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _install_from("macos", tmp_path, monkeypatch)


@pytest.fixture
def flavor(root: Path) -> Path:
    return root / FLAVOR


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _json[M: BaseModel](model: type[M], *args: str) -> M:
    """Run with --json; the output validates against `model`."""
    return model.model_validate_json(ok(*args, "--json").stdout)


def _state(root: Path) -> dict[str, tuple[bytes | None, int, int]]:
    """Every entry under `root` with its bytes, mode and mtime: what L1 keeps."""
    out: dict[str, tuple[bytes | None, int, int]] = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        regular = p.is_file() and not p.is_symlink()
        out[p.relative_to(root).as_posix()] = (
            p.read_bytes() if regular else None,
            st.st_mode,
            st.st_mtime_ns,
        )
    return out


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _find(root: Path, limits: Limits | None = None) -> gaps.GapReport:
    return gaps.find_gaps(install.discover(root), limits)


def _summary(groups: list[gaps.GapGroup]) -> list[tuple[Any, ...]]:
    return [(g.parent_entry_id, g.name, g.kind, g.places, g.files, g.bytes) for g in groups]


def _places(groups: list[gaps.GapGroup]) -> list[str]:
    return [_where(p) for g in groups for p in g.gaps]


def _where(place: gaps.GapPlace) -> str:
    return "/".join(p for p in (place.flavor_folder, place.path) if p)


# ─── the fixture installs: a stable, committed gap report ────────────────────

_ADDONS = [
    {
        "base": "flavor",
        "flavor_folder": FLAVOR,
        "path": f"Interface/AddOns/{name}",
        "entry_id": "addon",
    }
    for name in ("DBM-Brawlers", "DBM-Challenges")
]
# `wowlab filemap gaps --json` on each fixture install, `install_root` left
# out (it is the test's temporary folder). Both captures have a describing
# row for every path they hold; `entries` counts what the walk `lstat`ed.
COMMITTED: dict[str, dict[str, Any]] = {
    "macos": {
        "flavor_folders": [FLAVOR],
        "groups": [],
        "files": 0,
        "bytes": 0,
        "catch_all": [],
        "catch_all_files": 0,
        "catch_all_bytes": 0,
        "not_entered": _ADDONS,
        "links": [],
        "errors": [],
        "entries": 48,
        "truncated": False,
    },
    "macos-70058": {
        "flavor_folders": [FLAVOR],
        "groups": [],
        "files": 0,
        "bytes": 0,
        "catch_all": [],
        "catch_all_files": 0,
        "catch_all_bytes": 0,
        "not_entered": [],
        "links": [],
        "errors": [],
        "entries": 10,
        "truncated": False,
    },
}
COMMITTED_TEXT = {
    "macos": [
        "File-map gaps in {root}",
        "Paths with no file-map row: none; every path the walk reached has a row",
        "Not entered, labelled by a row that covers everything inside:",
        "  [addon] 2 folders",
    ],
    "macos-70058": [
        "File-map gaps in {root}",
        "Paths with no file-map row: none; every path the walk reached has a row",
    ],
}


@pytest.mark.parametrize("capture", sorted(COMMITTED))
def test_fixture_install_gap_report_is_the_committed_one(
    capture: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install_from(capture, tmp_path, monkeypatch)
    before = _state(root)
    for _ in range(2):  # and stable from one run to the next
        result = ok("filemap", "gaps", "--json")
        report = gaps.GapReport.model_validate_json(result.stdout)
        assert report.install_root == str(root)
        payload = json.loads(result.stdout)
        del payload["install_root"]
        assert payload == COMMITTED[capture]
        text = ok("filemap", "gaps").stdout
        assert text.splitlines() == [line.format(root=root) for line in COMMITTED_TEXT[capture]]
    assert _state(root) == before


def test_every_fixture_path_has_a_row_by_classify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The committed empty reports agree with `classify` on every path, and
    no path there is matched by a catch-all row first."""
    for capture in sorted(COMMITTED):
        root = _install_from(capture, tmp_path / capture, monkeypatch)
        inst = install.discover(root)
        for p in root.rglob("*"):
            found = cli.layout.classify(p, inst)
            assert isinstance(found, cli.layout.Classified), p
            if p.is_file():  # `Logs/` itself is named by its catch-all row
                assert not found.entry.catch_all, p


# ─── grouping and ranking ────────────────────────────────────────────────────


def test_gaps_are_grouped_by_parent_row_and_name_constructed(root: Path, flavor: Path) -> None:
    _write(root / "notes-constructed.txt", b"abc")
    _write(root / "_nope_" / "a" / "x.txt", b"12345")  # `_*_` without .flavor.info
    _write(flavor / "Unknown-constructed" / "deep" / "y.bin", b"1234567")
    _write(flavor / "Unknown-constructed" / "z.bin", b"1")
    _write(flavor / "Unknown-constructed" / ".DS_Store", b"os")  # has a row: not a gap
    _write(flavor / "WTF" / "Account" / ACCOUNT / "strange-constructed.txt", b"zz")
    _write(flavor / "stray-constructed.txt", b"0123456789")
    report = _find(root)

    assert _summary(report.groups) == [
        ("flavor-folder", "Unknown-constructed", "folder", 1, 2, 8),
        ("flavor-folder", "stray-constructed.txt", "file", 1, 1, 10),
        (None, "_nope_", "folder", 1, 1, 5),
        (None, "notes-constructed.txt", "file", 1, 1, 3),
        ("account-folder", "strange-constructed.txt", "file", 1, 1, 2),
    ]
    unknown = report.groups[0]
    assert (unknown.base, unknown.folders, unknown.entry_id, unknown.note) == (
        "flavor",
        1,
        None,
        None,
    )
    (place,) = unknown.gaps
    assert (place.flavor_folder, place.parent, place.path) == (FLAVOR, "", "Unknown-constructed")
    # in walk order: a folder's files before what is in its sub-folders
    assert place.examples == ["Unknown-constructed/z.bin", "Unknown-constructed/deep/y.bin"]
    nope = report.groups[2]
    assert (nope.base, nope.note) == ("root", gaps.NOT_A_FLAVOR)
    assert nope.gaps[0].flavor_folder is None
    (strange,) = report.groups[4].gaps
    assert strange.parent == f"WTF/Account/{ACCOUNT}"
    assert (report.files, report.bytes) == (6, 28)
    assert report.errors == [] and report.links == [] and not report.truncated
    assert report.catch_all == []


def test_one_unknown_file_in_every_character_folder_is_one_group_constructed(
    root: Path, flavor: Path
) -> None:
    """A row is a pattern, so a gap that recurs across characters is one
    row's worth of work: one group, ranked by its total, and the text names
    no account, realm or character folder."""
    chars = [p for p in (flavor / "WTF" / "Account" / ACCOUNT / "1").iterdir() if p.is_dir()]
    assert len(chars) == 3
    for c in chars:
        _write(c / "new-cache-constructed.txt", b"abcd")
    _write(flavor / "single-constructed.bin", b"x" * 10)
    report = _find(root)
    assert _summary(report.groups) == [
        ("second-name-character-folder", "new-cache-constructed.txt", "file", 3, 3, 12),
        ("flavor-folder", "single-constructed.bin", "file", 1, 1, 10),
    ]
    assert sorted(p.parent for p in report.groups[0].gaps) == sorted(
        f"WTF/Account/{ACCOUNT}/1/{c.name}" for c in chars
    )
    text = ok("filemap", "gaps").stdout
    assert (
        "  [second-name-character-folder] new-cache-constructed.txt  12 bytes, in 3 places\n"
        in text
    )
    for name in (ACCOUNT, *(c.name for c in chars)):
        assert name not in text


def test_ranking_is_by_files_then_bytes_then_name_constructed(root: Path, flavor: Path) -> None:
    _write(flavor / "b-constructed.bin", b"xx")
    _write(flavor / "a-constructed.bin", b"xx")
    _write(flavor / "big-constructed.bin", b"x" * 100)
    _write(flavor / "many-constructed" / "1", b"")
    _write(flavor / "many-constructed" / "2", b"")
    assert [g.name for g in _find(root).groups] == [
        "many-constructed",
        "big-constructed.bin",
        "a-constructed.bin",
        "b-constructed.bin",
    ]


def test_a_row_inside_a_gap_starts_a_group_of_its_own_constructed(root: Path, flavor: Path) -> None:
    """`Interface/<x>/` has a row (interface-override); `Data/**` covers."""
    _write(flavor / "Interface" / "Over-constructed" / "a.blp", b"a")
    _write(root / "Data" / "extra-constructed.bin", b"a")
    assert _find(root).groups == []


def test_the_install_root_and_outside_are_never_gaps_constructed(
    root: Path, tmp_path: Path
) -> None:
    _write(tmp_path / "outside-constructed.txt", b"x")
    report = _find(root)
    assert report.groups == [] and report.catch_all == []


def test_flavor_folders_come_from_discovery_constructed(root: Path) -> None:
    """A second `_*_` folder with a `.flavor.info` is a flavor (its unknown
    file groups under its flavor-folder row); one without is an unknown root
    folder with the note (L6)."""
    second = root / "_second_constructed_"
    _write(second / ".flavor.info", (root / FLAVOR / ".flavor.info").read_bytes())
    _write(second / "odd-constructed.txt", b"x")
    _write(root / "_third_constructed_" / "odd.txt", b"y")
    report = _find(root)
    assert report.flavor_folders == sorted([FLAVOR, "_second_constructed_"])
    assert _places(report.groups) == [  # equal counts: by name
        "_third_constructed_",
        "_second_constructed_/odd-constructed.txt",
    ]
    assert [g.note for g in report.groups] == [gaps.NOT_A_FLAVOR, None]
    assert report.groups[1].parent_entry_id == "flavor-folder"
    text = ok("filemap", "gaps").stdout
    assert f"  (install root) _third_constructed_/  1 file, 1 byte, {gaps.NOT_A_FLAVOR}\n" in text


@SYMLINKS
def test_a_symlinked_flavor_folder_gets_the_link_note_constructed(
    root: Path, tmp_path: Path
) -> None:
    """A `_*_` link to a folder with a regular `.flavor.info`: discovery
    refuses it because it is a link, so the note must not say a file is
    missing (domain review round 2 of #203)."""
    elsewhere = tmp_path / "elsewhere-constructed"
    shutil.copytree(root / FLAVOR, elsewhere)
    assert (elsewhere / ".flavor.info").is_file()
    (root / "_linked_constructed_").symlink_to(elsewhere, target_is_directory=True)
    report = _find(root)
    (group,) = report.groups
    assert (group.name, group.kind, group.note) == (
        "_linked_constructed_",
        "link",
        gaps.NOT_A_FLAVOR_LINK,
    )
    text = ok("filemap", "gaps").stdout
    assert (
        f"(install root) _linked_constructed_  link, not followed, {gaps.NOT_A_FLAVOR_LINK}\n"
        in text
    )
    assert gaps.NOT_A_FLAVOR not in text
    note = {i.path: i.note for i in _json(cli.InstallTreeReport, "tree", "--install").items}
    assert (note["_linked_constructed_"] or "").endswith(f"; {gaps.NOT_A_FLAVOR_LINK}")
    assert gaps.NOT_A_FLAVOR not in (note["_linked_constructed_"] or "")


def test_folder_groups_print_their_examples_constructed(root: Path, flavor: Path) -> None:
    """Text names the files a folder group counts, relative to the place's
    parent, so a gap in a character folder shows no account or character."""
    char = flavor / "WTF" / "Account" / ACCOUNT / "1" / "Labcharb-Labrealmd"
    _write(char / "NewFolder-constructed" / "a.txt", b"a")
    _write(char / "NewFolder-constructed" / "sub" / "b.txt", b"b")
    _write(flavor / "Cache" / "Odd-constructed" / "c.bin", b"c")
    text = ok("filemap", "gaps").stdout.splitlines()
    i = text.index(
        "  [second-name-character-folder] NewFolder-constructed/  2 files, 2 bytes, 1 folder"
    )
    assert text[i + 1 : i + 3] == [
        "      NewFolder-constructed/a.txt",
        "      NewFolder-constructed/sub/b.txt",
    ]
    j = text.index("  [flavor-folder] Cache/  1 file, 1 byte, 1 folder, row cache-other")
    assert text[j + 1] == "      Cache/Odd-constructed/c.bin"
    for name in (ACCOUNT, "Labcharb", "Labrealmd"):
        assert not any(name in line for line in text[1:])


def test_a_place_deeper_than_its_parent_keeps_its_own_path_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catch-all-only path inside a gap folder (no row in the shipped map
    produces this): its place keeps its real path and name, and the nearest
    describing parent."""
    fm = filemap.load()
    extra = filemap.FileMapEntry(
        id="deep-catch-all",
        section="flavor",
        base="flavor",
        doc_path="x",
        what="x, not described here yet",
        written_by="x",
        edit="no",
        tier="—",
        module="none",
        match=("Unknown-constructed/Inner/", "Unknown-constructed/Inner/**"),
        catch_all=True,
    )
    patched = filemap.FileMap(sections=fm.sections, entries=(*fm.entries, extra))
    monkeypatch.setattr(filemap, "load", lambda: patched)
    _write(flavor / "Unknown-constructed" / "Inner" / "x.bin", b"xyz")
    _write(flavor / "Unknown-constructed" / "y.bin", b"1")
    report = _find(root)
    (gap,) = report.groups
    assert (gap.name, gap.files) == ("Unknown-constructed", 1)
    (loose,) = report.catch_all
    assert (loose.parent_entry_id, loose.name, loose.entry_id) == (
        "flavor-folder",
        "Inner",
        "deep-catch-all",
    )
    (place,) = loose.gaps
    assert (place.parent, place.path, place.examples) == (
        "",
        "Unknown-constructed/Inner",
        ["Unknown-constructed/Inner/x.bin"],
    )


def test_a_covered_folder_is_not_entered_and_named_constructed(root: Path) -> None:
    before = _find(root)
    for i in range(20):
        _write(root / "Data" / "data" / f"data.{i:03}", b"x")
    report = _find(root)
    assert report.entries == before.entries + 1  # `Data` itself, not what is in it
    assert report.groups == []
    assert report.not_entered[0] == gaps.NotEntered(
        base="root", flavor_folder=None, path="Data", entry_id="data-other"
    )
    assert "  [data-other] Data/\n" in ok("filemap", "gaps").stdout


# ─── catch-all rows ──────────────────────────────────────────────────────────


def test_catch_all_rows_are_marked() -> None:
    """The rows that say they do not describe what they match are exactly
    the ones marked `catch_all`."""
    fm = filemap.load()
    marked = {e.id for e in fm.entries if e.catch_all}
    said = {e.id for e in fm.entries if "not described here yet" in e.what}
    assert marked == said == {"cache-other", "logs-other"}


def test_paths_only_a_catch_all_row_matches_are_their_own_section_constructed(
    root: Path, flavor: Path
) -> None:
    _write(flavor / "Cache" / "ADB" / "enUS" / "DBCache.bin", b"described")
    _write(flavor / "Cache" / "ADB" / "enUS" / "odd-constructed.bin", b"123")
    _write(flavor / "Cache" / "New-constructed" / "x.dat", b"12345")
    _write(flavor / "Logs" / "odd-constructed.txt", b"1")
    report = _find(root)
    assert report.groups == []
    assert [
        (g.parent_entry_id, g.name, g.kind, g.entry_id, g.files, g.bytes) for g in report.catch_all
    ] == [
        ("flavor-folder", "Cache", "folder", "cache-other", 2, 8),
        ("flavor-folder", "Logs", "folder", "logs-other", 1, 1),
    ]
    assert report.catch_all[0].gaps[0].examples == [
        "Cache/ADB/enUS/odd-constructed.bin",
        "Cache/New-constructed/x.dat",
    ]
    assert (report.catch_all_files, report.catch_all_bytes) == (3, 9)
    assert not any(n.path in ("Cache", "Logs") for n in report.not_entered)
    text = ok("filemap", "gaps").stdout
    assert "Described only by a catch-all row: 3 files, 9 bytes\n" in text
    assert "  [flavor-folder] Cache/  2 files, 8 bytes, 3 folders, row cache-other\n" in text


def test_a_catch_all_folder_holding_only_described_files_is_quiet_constructed(
    root: Path, flavor: Path
) -> None:
    _write(flavor / "Cache" / "WDB" / "enUS" / "creaturecache.wdb", b"x")
    assert _find(root).catch_all == []


# ─── Windows reparse points ──────────────────────────────────────────────────

CLOUD_PLACEHOLDER = 0x9000001A  # IO_REPARSE_TAG_CLOUD_A: no name-surrogate bit
MOUNT_POINT = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT, a junction: name surrogate


class _ReparseStat:
    """An `lstat` result as Windows gives it for a reparse point: the real
    entry's mode and size, with `st_file_attributes` and `st_reparse_tag`."""

    def __init__(self, real: os.stat_result, tag: int) -> None:
        self.st_mode = real.st_mode
        self.st_size = real.st_size
        self.st_mtime_ns = real.st_mtime_ns
        self.st_file_attributes = stat.FILE_ATTRIBUTE_REPARSE_POINT
        self.st_reparse_tag = tag


def _reparse_points(monkeypatch: pytest.MonkeyPatch, tags: dict[str, int]) -> None:
    """Entries named in `tags` look like Windows reparse points with that tag."""
    real = gaps.lstat_entry

    def fake(entry: os.DirEntry[str]) -> os.stat_result:
        st = real(entry)
        tag = tags.get(entry.name)
        return st if tag is None else cast(os.stat_result, _ReparseStat(st, tag))

    monkeypatch.setattr(gaps, "lstat_entry", fake)


def test_a_cloud_placeholder_is_walked_and_a_junction_is_not_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An install in a OneDrive-synced folder: the flavor folder and `WTF/`
    are cloud placeholders (a reparse point without the name-surrogate
    bit), which hold real content and are walked, as discovery takes them
    (M13-04). A junction (name surrogate) is never followed. Constructed:
    the attributes are stood in, so this runs on any system."""
    _write(flavor / "WTF" / "Account" / ACCOUNT / "strange-constructed.txt", b"zz")
    _write(flavor / "Junction-constructed" / "inside.txt", b"never counted")
    _write(root / "RootJunction-constructed" / "inside.txt", b"never counted")
    _reparse_points(
        monkeypatch,
        {
            FLAVOR: CLOUD_PLACEHOLDER,
            "WTF": CLOUD_PLACEHOLDER,
            "Junction-constructed": MOUNT_POINT,
            "RootJunction-constructed": MOUNT_POINT,
        },
    )
    report = _find(root)
    assert report.links == ["RootJunction-constructed", f"{FLAVOR}/Junction-constructed"]
    assert _summary(report.groups) == [
        ("account-folder", "strange-constructed.txt", "file", 1, 1, 2),
        ("flavor-folder", "Junction-constructed", "link", 1, 0, 0),
        (None, "RootJunction-constructed", "link", 1, 0, 0),
    ]
    tree = _json(cli.InstallTreeReport, "tree", "--install")
    kinds = {(i.base, i.path): i.kind for i in tree.items}
    assert kinds[("flavor", "")] == "folder"  # the placeholder flavor folder
    assert kinds[("root", "RootJunction-constructed")] == "symlink"
    assert ("flavor", "WTF/Config.wtf") in kinds


# ─── links, unreadable folders, the bound ────────────────────────────────────


@SYMLINKS
def test_a_link_is_never_followed_constructed(root: Path, flavor: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside-constructed"
    for i in range(5):
        _write(outside / f"f{i}.txt", b"secret")
    (flavor / "Linked-constructed").symlink_to(outside, target_is_directory=True)
    (flavor / "Interface" / "AddOns" / "Dev-constructed").symlink_to(outside)  # has a row
    _write(flavor / "Unknown-constructed" / "a.txt", b"a")
    (flavor / "Unknown-constructed" / "inner-link").symlink_to(outside)
    (root / "root-link-constructed").symlink_to(flavor / "WTF")  # into the install
    before = _state(root)

    report = _find(root)
    assert report.links == [
        f"{FLAVOR}/Interface/AddOns/Dev-constructed",
        f"{FLAVOR}/Linked-constructed",
        f"{FLAVOR}/Unknown-constructed/inner-link",
        "root-link-constructed",
    ]
    assert [(g.name, g.kind, g.files, g.bytes, g.links) for g in report.groups] == [
        ("Unknown-constructed", "folder", 1, 1, 1),
        ("Linked-constructed", "link", 0, 0, 1),
        ("root-link-constructed", "link", 0, 0, 1),
    ]
    assert report.files == 1  # nothing behind a link is counted
    text = ok("filemap", "gaps").stdout
    assert "  [flavor-folder] Linked-constructed  link, not followed\n" in text
    assert "Links met and not followed:\n" in text
    for link in report.links:
        assert f"\n  {link}\n" in text
    assert _state(root) == before


@SYMLINKS
def test_a_linked_wtf_says_it_was_not_checked_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    """A synced `WTF/` linked in has a row (wtf-folder), so it is no gap;
    the "none" line must not read as "everything was looked at"."""
    shutil.move(flavor / "WTF", tmp_path / "synced-WTF-constructed")
    (flavor / "WTF").symlink_to(tmp_path / "synced-WTF-constructed", target_is_directory=True)
    report = _find(root)
    assert report.groups == [] and report.links == [f"{FLAVOR}/WTF"]
    lines = ok("filemap", "gaps").stdout.splitlines()
    assert lines[1] == (
        "Paths with no file-map row: none; every path the walk reached has a row; "
        "1 link met and not followed (listed below), so what they point at, "
        "a linked WTF/ for one, was not checked"
    )
    assert lines[-2:] == ["Links met and not followed:", f"  {FLAVOR}/WTF"]


@POSIX_PERMISSIONS
def test_an_unreadable_folder_is_reported_not_fatal_constructed(root: Path, flavor: Path) -> None:
    locked = flavor / "Locked-constructed"
    _write(locked / "a.txt", b"a")
    sv = flavor / "WTF" / "Account" / ACCOUNT / "SavedVariables"
    _write(flavor / "after-constructed.txt", b"b")
    locked.chmod(0)
    sv.chmod(0)
    try:
        report = _find(root)
        result = run("filemap", "gaps", "--json")
        text = run("filemap", "gaps")
    finally:
        locked.chmod(0o755)
        sv.chmod(0o755)
    assert {e.path for e in report.errors} == {
        f"{FLAVOR}/Locked-constructed",
        f"{FLAVOR}/WTF/Account/{ACCOUNT}/SavedVariables",
    }
    assert all(e.error for e in report.errors)
    assert [(g.name, g.kind, g.files) for g in report.groups] == [
        ("after-constructed.txt", "file", 1),
        ("Locked-constructed", "folder", 0),
    ]
    assert not report.truncated
    assert result.exit_code == 0
    assert gaps.GapReport.model_validate_json(result.stdout) == report
    assert text.exit_code == 0
    assert f"could not read {FLAVOR}/Locked-constructed: " in text.stderr


def test_the_entry_bound_truncates_constructed(root: Path, flavor: Path) -> None:
    for i in range(10):
        _write(flavor / "zz-constructed" / f"{i}.txt", b"x")
    full = _find(root)
    assert not full.truncated
    assert full.groups[0].files == 10
    cut = _find(root, Limits(max_entries=full.entries - 3))
    assert cut.truncated
    assert cut.entries == full.entries - 3
    assert cut.groups[0].files == 7


def test_the_depth_bound_truncates_constructed(root: Path, flavor: Path) -> None:
    _write(flavor / "a-constructed" / "b" / "c" / "d" / "e.txt", b"x")
    assert _find(root).groups[0].files == 1
    cut = _find(root, Limits(max_depth=3))  # root/flavor/a/b listed; b/c is not
    assert cut.truncated
    (group,) = cut.groups
    assert (group.name, group.files, group.folders) == ("a-constructed", 0, 2)


def test_the_cli_says_when_the_bound_is_hit_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(flavor / "zz-constructed" / "1.txt", b"x")
    real = gaps.find_gaps
    monkeypatch.setattr(gaps, "find_gaps", lambda inst: real(inst, Limits(max_entries=3)))
    result = ok("filemap", "gaps", "--json")
    assert gaps.GapReport.model_validate_json(result.stdout).truncated
    assert "The walk hit a bound; there may be gaps it did not reach." in result.stderr


def test_gaps_json_keeps_undecodable_file_names_constructed(root: Path, flavor: Path) -> None:
    name = os.fsdecode(b"bad-\xff-constructed.txt")
    try:
        _write(flavor / name, b"x")
    except (OSError, UnicodeEncodeError):
        pytest.skip("this volume refuses a file name that is not UTF-8")
    report = _json(gaps.GapReport, "filemap", "gaps")
    assert report.groups[0].name == name
    assert report.groups[0].gaps[0].path == name


def test_gaps_reads_only_constructed(root: Path, flavor: Path) -> None:
    _write(flavor / "Unknown-constructed" / "a.txt", b"a")
    _write(flavor / "Cache" / "x-constructed.bin", b"a")
    before = _state(root)
    ok("filemap", "gaps")
    ok("filemap", "gaps", "--json")
    ok("tree", "--install", "--explain")
    assert _state(root) == before


# ─── FileMap.covers ──────────────────────────────────────────────────────────

COVERED = [
    ("root", ("Data",)),
    ("root", ("Data", "data")),
    ("root", ("World of Warcraft Launcher.app",)),
    ("flavor", ("Interface", "AddOns", "SomeAddon")),
    ("flavor", ("Interface", "AddOns", "Blizzard_Foo")),
    ("flavor", ("World of Warcraft Classic.app",)),
]
NOT_COVERED = [
    ("flavor", ("Cache",)),  # catch-all rows never cover
    ("flavor", ("Logs", "x")),
    ("root", ()),
    ("root", ("Interface",)),
    ("flavor", ()),
    ("flavor", ("WTF",)),
    ("flavor", ("WTF", "Account", "X")),
    ("flavor", ("Interface",)),
    ("flavor", ("Interface", "AddOns")),
]


@pytest.mark.parametrize(("base", "parts"), COVERED)
def test_covers_says_yes_only_when_every_descendant_has_a_row(
    base: str, parts: tuple[str, ...]
) -> None:
    fm = filemap.load()
    assert fm.covers(parts, base)  # type: ignore[arg-type]
    tails = [("x",), ("12345",), ("Blizzard_X", "y.lua"), ("a", "b", "c.txt"), (".DS_Store",)]
    for tail in tails:
        for kind in ("file", "dir", "any"):
            assert fm.classify((*parts, *tail), base, kind) is not None, (parts, tail, kind)  # type: ignore[arg-type]


@pytest.mark.parametrize(("base", "parts"), NOT_COVERED)
def test_covers_says_no_where_a_descendant_may_have_no_row(
    base: str, parts: tuple[str, ...]
) -> None:
    assert not filemap.load().covers(parts, base)  # type: ignore[arg-type]


def test_covers_respects_an_exclude_constructed() -> None:
    fm = filemap.parse(
        """
[[section]]
id = "s"
title = "S"

[[entry]]
id = "e"
section = "s"
base = "flavor"
doc_path = "x"
what = "x"
written_by = "x"
edit = "no"
tier = "—"
module = "none"
match = ["A/**"]
exclude = ["A/B/**"]
"""
    )
    assert not fm.covers(("A",), "flavor")
    assert not fm.covers(("A", "B"), "flavor")
    assert fm.covers(("A", "C"), "flavor")


# ─── tree --install ──────────────────────────────────────────────────────────


def test_tree_install_covers_the_root_and_every_flavor_constructed(
    root: Path, flavor: Path
) -> None:
    second = root / "_second_constructed_"
    _write(second / ".flavor.info", (flavor / ".flavor.info").read_bytes())
    _write(second / "WTF" / "Config.wtf", b'SET a "1"\n')
    _write(root / "notes-constructed.txt", b"abc")
    report = _json(cli.InstallTreeReport, "tree", "--install")
    assert report.install_root == str(root)
    assert report.flavor_folders == sorted([FLAVOR, "_second_constructed_"])
    keyed = {(i.base, i.flavor_folder, i.path): i for i in report.items}

    build = keyed[("root", None, ".build.info")]
    assert (build.kind, build.size, build.verdict.level) == ("file", 701, "no")
    notes = keyed[("root", None, "notes-constructed.txt")]
    assert notes.verdict.level == "unclassified"
    for folder in report.flavor_folders:
        item = keyed[("flavor", folder, "")]
        assert (item.kind, item.note, item.verdict.level) == ("folder", "flavor folder", "folder")
        assert keyed[("flavor", folder, "WTF/Config.wtf")].verdict.level == "gate"
    assert keyed[("flavor", FLAVOR, f"WTF/Account/{ACCOUNT}/SavedVariables/Syndicator.lua")]
    # Root entries first, in name order; then each flavor's, as `tree` lists them.
    bases = [i.base for i in report.items if i.path]
    assert bases == sorted(bases, key=lambda b: b != "root")
    single = _json(cli.TreeReport, "tree", "--flavor", FLAVOR)
    assert [i for i in report.items if i.flavor_folder == FLAVOR and i.path] == single.items


def test_tree_install_text_and_explain(root: Path) -> None:
    out = ok("tree", "--install", "--explain").stdout.splitlines()
    assert out[0] == f"Install root {root}"
    assert "  .build.info  701 bytes" in out
    assert "      [build-info] Installed products, versions, build keys" in out
    assert f"  {FLAVOR}/  (flavor folder)" in out
    assert f"{FLAVOR} in {root}" in out
    assert out.index(f"{FLAVOR} in {root}") > out.index("  .build.info  701 bytes")
    assert "  WTF/Config.wtf  3,755 bytes" in out


def test_tree_install_path_is_relative_to_the_install_root(root: Path) -> None:
    report = _json(cli.InstallTreeReport, "tree", "--install", f"{FLAVOR}/WTF/Config.wtf")
    assert [(i.base, i.flavor_folder, i.path) for i in report.items] == [
        ("flavor", FLAVOR, "WTF/Config.wtf")
    ]
    report = _json(cli.InstallTreeReport, "tree", "--install", ".build.info")
    assert [(i.base, i.path) for i in report.items] == [("root", ".build.info")]


def test_tree_install_refuses_flavor(root: Path) -> None:
    result = run("tree", "--install", "--flavor", FLAVOR)
    assert result.exit_code == 2
    assert "--install covers every flavor folder" in result.stderr


@SYMLINKS
def test_tree_install_names_a_root_link_without_following_it_constructed(
    root: Path, tmp_path: Path
) -> None:
    (tmp_path / "elsewhere-constructed").mkdir()
    (root / "linked-constructed").symlink_to(tmp_path / "elsewhere-constructed")
    report = _json(cli.InstallTreeReport, "tree", "--install")
    (item,) = [i for i in report.items if i.path == "linked-constructed"]
    assert item.kind == "symlink"
    assert item.verdict.restorable is None
    assert item.note is not None and item.note.endswith(", not followed")


def test_tree_items_carry_base_and_flavor_folder(root: Path) -> None:
    """Without --install, every item is in the chosen flavor folder."""
    report = _json(cli.TreeReport, "tree")
    assert report.items
    assert {(i.base, i.flavor_folder) for i in report.items} == {("flavor", FLAVOR)}
