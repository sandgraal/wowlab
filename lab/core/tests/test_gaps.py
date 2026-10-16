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
import sys
from pathlib import Path
from typing import Any

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


def _group(report: gaps.GapReport, flavor_folder: str | None, parent: str) -> gaps.GapGroup:
    (found,) = [g for g in report.groups if g.flavor_folder == flavor_folder and g.parent == parent]
    return found


# ─── the fixture installs: a stable, committed gap report ────────────────────

# `wowlab filemap gaps --json` on each fixture install, `install_root` left
# out (it is the test's temporary folder). Both captures have a row for every
# path they hold; `entries` counts what the walk `lstat`ed.
COMMITTED = {
    "macos": {
        "flavor_folders": [FLAVOR],
        "groups": [],
        "files": 0,
        "bytes": 0,
        "links": [],
        "errors": [],
        "entries": 47,
        "truncated": False,
    },
    "macos-70058": {
        "flavor_folders": [FLAVOR],
        "groups": [],
        "files": 0,
        "bytes": 0,
        "links": [],
        "errors": [],
        "entries": 10,
        "truncated": False,
    },
}
COMMITTED_TEXT = [
    "File-map gaps in {root}",
    "  none: every path the walk reached has a file-map row",
]


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
        assert text.splitlines() == [line.format(root=root) for line in COMMITTED_TEXT]
    assert _state(root) == before


def test_every_fixture_path_has_a_row_by_classify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The committed empty reports agree with `classify` on every path."""
    for capture in sorted(COMMITTED):
        root = _install_from(capture, tmp_path / capture, monkeypatch)
        inst = install.discover(root)
        for p in root.rglob("*"):
            found = cli.layout.classify(p, inst)
            assert found.status == "classified", p


# ─── grouping and ranking ────────────────────────────────────────────────────


def test_gaps_are_grouped_under_the_nearest_classified_parent_constructed(
    root: Path, flavor: Path
) -> None:
    _write(root / "notes-constructed.txt", b"abc")
    _write(root / "_nope_" / "a" / "x.txt", b"12345")  # `_*_` without .flavor.info
    _write(flavor / "Unknown-constructed" / "deep" / "y.bin", b"1234567")
    _write(flavor / "Unknown-constructed" / "z.bin", b"1")
    _write(flavor / "Unknown-constructed" / ".DS_Store", b"os")  # has a row: not a gap
    _write(flavor / "WTF" / "Account" / ACCOUNT / "strange-constructed.txt", b"zz")
    _write(flavor / "stray-constructed.txt", b"0123456789")
    report = _find(root)

    assert [(g.flavor_folder, g.parent) for g in report.groups] == [
        (FLAVOR, ""),
        (None, ""),
        (FLAVOR, f"WTF/Account/{ACCOUNT}"),
    ]
    top = _group(report, FLAVOR, "")
    assert (top.base, top.entry_id, top.files, top.bytes) == ("flavor", "flavor-folder", 3, 18)
    assert [(g.path, g.kind, g.files, g.bytes, g.folders) for g in top.gaps] == [
        ("Unknown-constructed", "folder", 2, 8, 1),
        ("stray-constructed.txt", "file", 1, 10, 0),
    ]
    at_root = _group(report, None, "")
    assert (at_root.base, at_root.entry_id) == ("root", None)
    assert [(g.path, g.kind, g.files, g.bytes) for g in at_root.gaps] == [
        ("_nope_", "folder", 1, 5),
        ("notes-constructed.txt", "file", 1, 3),
    ]
    account = _group(report, FLAVOR, f"WTF/Account/{ACCOUNT}")
    assert account.entry_id == "account-folder"
    assert [g.path for g in account.gaps] == [f"WTF/Account/{ACCOUNT}/strange-constructed.txt"]
    assert (report.files, report.bytes) == (6, 28)
    assert report.errors == [] and report.links == [] and not report.truncated


def test_ranking_is_by_files_then_bytes_then_path_constructed(root: Path, flavor: Path) -> None:
    _write(flavor / "b-constructed.bin", b"xx")
    _write(flavor / "a-constructed.bin", b"xx")
    _write(flavor / "big-constructed.bin", b"x" * 100)
    _write(flavor / "many-constructed" / "1", b"")
    _write(flavor / "many-constructed" / "2", b"")
    (top,) = _find(root).groups
    assert [g.path for g in top.gaps] == [
        "many-constructed",
        "big-constructed.bin",
        "a-constructed.bin",
        "b-constructed.bin",
    ]


def test_a_row_inside_a_gap_starts_a_group_of_its_own_constructed(root: Path, flavor: Path) -> None:
    """`Interface/<x>/` has a row (interface-override); an unknown folder
    above a classified one is not counted through it."""
    _write(flavor / "Interface" / "Over-constructed" / "a.blp", b"a")
    _write(root / "Data" / "extra-constructed.bin", b"a")  # Data/** has a row
    assert _find(root).groups == []


def test_the_install_root_and_outside_are_never_gaps_constructed(
    root: Path, tmp_path: Path
) -> None:
    _write(tmp_path / "outside-constructed.txt", b"x")
    report = _find(root)
    assert report.groups == []
    for g in report.groups:
        for gap in g.gaps:
            assert gap.path


def test_flavor_folders_come_from_discovery_constructed(root: Path) -> None:
    """A second `_*_` folder with a `.flavor.info` is a flavor (its unknown
    file groups under it); one without is an unknown root folder (L6)."""
    second = root / "_second_constructed_"
    _write(second / ".flavor.info", (root / FLAVOR / ".flavor.info").read_bytes())
    _write(second / "odd-constructed.txt", b"x")
    _write(root / "_third_constructed_" / "odd.txt", b"y")
    report = _find(root)
    assert report.flavor_folders == sorted([FLAVOR, "_second_constructed_"])
    assert _group(report, "_second_constructed_", "").gaps[0].path == "odd-constructed.txt"
    assert _group(report, None, "").gaps[0].path == "_third_constructed_"


def test_a_covered_folder_is_not_walked_constructed(root: Path) -> None:
    before = _find(root).entries
    for i in range(20):
        _write(root / "Data" / "data" / f"data.{i:03}", b"x")
    report = _find(root)
    assert report.entries == before + 1  # `Data` itself, not what is in it
    assert report.groups == []


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
    top = _group(report, FLAVOR, "")
    assert [(g.path, g.kind, g.files, g.bytes, g.links) for g in top.gaps] == [
        ("Unknown-constructed", "folder", 1, 1, 1),
        ("Linked-constructed", "link", 0, 0, 1),
    ]
    assert [(g.path, g.kind) for g in _group(report, None, "").gaps] == [
        ("root-link-constructed", "link")
    ]
    assert report.files == 1  # nothing behind a link is counted
    text = ok("filemap", "gaps").stdout
    assert "  Linked-constructed  link, not followed\n" in text
    assert "Links met and not followed: 4\n" in text
    assert _state(root) == before


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
    top = _group(report, FLAVOR, "")
    assert [(g.path, g.kind, g.files) for g in top.gaps] == [
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
    assert _group(full, FLAVOR, "").gaps[0].files == 10
    cut = _find(root, Limits(max_entries=full.entries - 3))
    assert cut.truncated
    assert cut.entries == full.entries - 3
    assert _group(cut, FLAVOR, "").gaps[0].files == 7


def test_the_depth_bound_truncates_constructed(root: Path, flavor: Path) -> None:
    _write(flavor / "a-constructed" / "b" / "c" / "d" / "e.txt", b"x")
    assert _group(_find(root), FLAVOR, "").gaps[0].files == 1
    cut = _find(root, Limits(max_depth=3))  # root/flavor/a/b listed; b/c is not
    assert cut.truncated
    (gap,) = _group(cut, FLAVOR, "").gaps
    assert (gap.path, gap.files, gap.folders) == ("a-constructed", 0, 2)


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
    assert _group(report, FLAVOR, "").gaps[0].path == name


def test_gaps_reads_only_constructed(root: Path, flavor: Path) -> None:
    _write(flavor / "Unknown-constructed" / "a.txt", b"a")
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
    ("flavor", ("Cache",)),
    ("flavor", ("Logs", "x")),
    ("flavor", ("Interface", "AddOns", "SomeAddon")),
    ("flavor", ("Interface", "AddOns", "Blizzard_Foo")),
    ("flavor", ("World of Warcraft Classic.app",)),
]
NOT_COVERED = [
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
