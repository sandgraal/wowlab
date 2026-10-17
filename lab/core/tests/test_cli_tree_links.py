"""`tree` lists every link as a link and never walks it, by the one link test
`filemap gaps` and `explain` use (`gaps.is_link`, defined as
`layout.is_link`; M13-35): a symlink, a junction, or any other Windows
reparse point with the name-surrogate bit, such as a WCI link or a WSL
symlink made with `ln -s` under `/mnt/c`.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
with its flavor folder named as its provenance row says (`_classic_beta_`),
and `--root` pointing at the copy. Nothing reads or writes a real install.

Constructed (L8, boundary): the reparse attributes, and the `Extras` folder
at the top of the flavor folder. A Windows reparse point cannot be made on
macOS or Linux, so the `lstat` results of chosen paths are stood in with
the attribute and tag Windows reports, through the three places the
commands read them (`Path.lstat`, `layout.lstat_entry`, `gaps.lstat_entry`),
as `test_cli_tree_placeholders.py` does. A junction also answers yes to
`Path.is_junction`, as it does on Windows.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, gaps, layout, verdict
from wowlab_core.install import discover

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT_DIR = "WTF/Account/90000001#6"
SYNDICATOR = f"{ACCOUNT_DIR}/SavedVariables/Syndicator.lua"

MOUNT_POINT = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT, a junction: name surrogate
WCI_LINK = 0xA0000027  # IO_REPARSE_TAG_WCI_LINK_1: name surrogate, not a junction
LX_SYMLINK = 0xA000001D  # IO_REPARSE_TAG_LX_SYMLINK (WSL `ln -s`): name surrogate
# Windows reports a WSL symlink as a file whatever it points at: the reparse
# point carries no directory attribute.
WSL_MODE = stat.S_IFREG | 0o644
# `os.readlink` cannot read a WCI link or a WSL symlink (nor, here, a faked
# junction), so nothing says where it points (M13-35 review).
UNKNOWN_NOTE = "link (where it points is unknown), not followed"


class _ReparseStat:
    """An `lstat` result as Windows gives it for a reparse point: the real
    entry's size and identity, with the reparse attribute and tag, and the
    real mode unless `mode` stands in another."""

    def __init__(self, real: os.stat_result, tag: int, mode: int | None) -> None:
        self.st_mode = real.st_mode if mode is None else mode
        self.st_size = real.st_size
        self.st_mtime_ns = real.st_mtime_ns
        self.st_mtime = real.st_mtime
        self.st_dev = real.st_dev
        self.st_ino = real.st_ino
        self.st_file_attributes = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
        self.st_reparse_tag = tag


@pytest.fixture(autouse=True)
def idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process table with no client in it."""
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import platformdirs

    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    install = tmp_path / "wow"
    install.mkdir()
    shutil.copy2(CAPTURE / ".build.info", install / ".build.info")
    shutil.copytree(CAPTURE / "forever", install / FLAVOR)
    extras = install / FLAVOR / "Extras"  # constructed: a top-level folder of no area
    extras.mkdir()
    (extras / "notes.txt").write_bytes(b"constructed\n")
    monkeypatch.chdir(tmp_path)
    return install


def _fake(
    monkeypatch: pytest.MonkeyPatch,
    paths: Iterable[Path],
    tag: int,
    *,
    mode: int | None = None,
) -> None:
    """Every `lstat` of `paths`, by path or by listed entry, reports `tag`;
    a junction's paths also answer yes to `Path.is_junction`."""
    marked = {str(p) for p in paths}
    real_lstat = Path.lstat
    real_layout = layout.lstat_entry
    real_gaps = gaps.lstat_entry
    real_junction = Path.is_junction

    def path_lstat(self: Path) -> Any:
        st = real_lstat(self)
        return _ReparseStat(st, tag, mode) if str(self) in marked else st

    def entry_lstat(real: Any) -> Any:
        def lstat(entry: os.DirEntry[str]) -> Any:
            st = real(entry)
            return _ReparseStat(st, tag, mode) if entry.path in marked else st

        return lstat

    monkeypatch.setattr(Path, "lstat", path_lstat)
    monkeypatch.setattr(layout, "lstat_entry", entry_lstat(real_layout))
    monkeypatch.setattr(gaps, "lstat_entry", entry_lstat(real_gaps))
    if tag == MOUNT_POINT:
        monkeypatch.setattr(
            Path, "is_junction", lambda self: str(self) in marked or real_junction(self)
        )


def _run(*args: str) -> str:
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == 0, (args, result.output, result.exception)
    return result.stdout


def _tree(root: Path, *more: str) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(_run("tree", "--root", str(root), "--json", *more))
    return out


def _by_path(report: dict[str, Any], *, install: bool) -> dict[str, dict[str, Any]]:
    """Every item and ancestor, keyed by its path relative to the flavor
    folder (with --install: relative to the install root)."""
    out = {}
    for item in [*report["items"], *report["ancestors"]]:
        path = item["path"]
        if install and item["base"] == "flavor":
            path = f"{item['flavor_folder']}/{path}" if path else item["flavor_folder"]
        out[path] = item
    return out


def _explain(root: Path, path: Path) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(_run("explain", str(path), "--root", str(root), "--json"))
    return out["verdict"]


def _judged(root: Path, *, install: bool) -> dict[str, dict[str, Any]]:
    flags = ("--install", "--explain") if install else ("--explain",)
    return _by_path(_tree(root, *flags), install=install)


def _key(rel: str, install: bool) -> str:
    return f"{FLAVOR}/{rel}" if install else rel


def _assert_a_link_not_walked(
    root: Path, judged: dict[str, dict[str, Any]], rel: str, install: bool
) -> None:
    key = _key(rel, install)
    item = judged[key]
    assert item["kind"] == "symlink", rel
    assert item["size"] is None, rel  # nothing read through it
    assert item["note"].startswith("link") and item["note"].endswith("not followed"), item
    v = item["verdict"]
    assert (v["link"], v["placeholder"], v["restorable"]) == (True, False, None), rel
    assert v["label"] == verdict.LINK_LABEL, rel
    assert v == _explain(root, root / FLAVOR / rel), rel  # word for word
    assert item["explained"] is None or item["explained"]["verdict"] == v
    assert [k for k in judged if k.startswith(key + "/")] == [], rel  # never walked


def _links_in_gaps(root: Path) -> list[str]:
    out: dict[str, Any] = json.loads(_run("filemap", "gaps", "--root", str(root), "--json"))
    links: list[str] = out["links"]
    return links


LINKS_UNDER_ACCOUNT = [
    pytest.param(WCI_LINK, None, id="constructed-wci-link"),
    pytest.param(LX_SYMLINK, WSL_MODE, id="constructed-wsl-symlink"),
]


@pytest.mark.parametrize("install", [False, True], ids=["tree", "tree-install"])
@pytest.mark.parametrize(("tag", "mode"), LINKS_UNDER_ACCOUNT)
def test_a_name_surrogate_under_wtf_account_is_a_link_never_walked(
    root: Path, monkeypatch: pytest.MonkeyPatch, install: bool, tag: int, mode: int | None
) -> None:
    flavor = root / FLAVOR
    before = _judged(root, install=install)
    assert _key(SYNDICATOR, install) in before  # walked while it is a plain folder
    _fake(monkeypatch, [flavor / ACCOUNT_DIR], tag, mode=mode)
    judged = _judged(root, install=install)
    _assert_a_link_not_walked(root, judged, ACCOUNT_DIR, install)
    assert judged[_key(ACCOUNT_DIR, install)]["note"] == UNKNOWN_NOTE
    # Everything outside it is as it was.
    inside = _key(ACCOUNT_DIR, install)
    assert {k: v for k, v in judged.items() if k != inside} == {
        k: v for k, v in before.items() if k != inside and not k.startswith(inside + "/")
    }
    # The walk reports it as a link, and `filemap gaps` names the same one.
    inv = layout.Layout(flavor, install_root=root).inventory()
    links = {s.path: s for s in inv.symlinks}
    assert (links[ACCOUNT_DIR].target, links[ACCOUNT_DIR].inside_install) == (None, None)
    assert inv.reparse_points == ()
    assert not any(sv.path.startswith(ACCOUNT_DIR + "/") for sv in inv.saved_variables)
    assert f"{FLAVOR}/{ACCOUNT_DIR}" in _links_in_gaps(root)


@pytest.mark.parametrize("install", [False, True], ids=["tree", "tree-install"])
def test_a_wsl_symlink_file_is_a_link_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch, install: bool
) -> None:
    """Constructed: a SavedVariables file that is a WSL symlink is named as a
    link, with no size read through it, and not as a SavedVariables file."""
    flavor = root / FLAVOR
    _fake(monkeypatch, [flavor / SYNDICATOR], LX_SYMLINK, mode=WSL_MODE)
    judged = _judged(root, install=install)
    _assert_a_link_not_walked(root, judged, SYNDICATOR, install)
    assert judged[_key(SYNDICATOR, install)]["note"] == UNKNOWN_NOTE


@pytest.mark.parametrize("install", [False, True], ids=["tree", "tree-install"])
@pytest.mark.parametrize("folder", ["Extras", "Logs"])
@pytest.mark.parametrize(
    "tag",
    [
        pytest.param(MOUNT_POINT, id="constructed-junction"),
        pytest.param(WCI_LINK, id="constructed-wci-link"),
    ],
)
def test_a_link_at_the_top_of_the_flavor_folder_is_a_link_never_walked(
    root: Path, monkeypatch: pytest.MonkeyPatch, install: bool, folder: str, tag: int
) -> None:
    """A junction is no `DirEntry.is_symlink()` on Windows; the top-level
    scan judges it by the walk's link test all the same."""
    flavor = root / FLAVOR
    before = _judged(root, install=install)
    assert before[_key(folder, install)]["kind"] == "folder"
    _fake(monkeypatch, [flavor / folder], tag)
    judged = _judged(root, install=install)
    _assert_a_link_not_walked(root, judged, folder, install)
    assert f"{FLAVOR}/{folder}" in _links_in_gaps(root)
    same = _key(folder, install)
    assert {k: v for k, v in judged.items() if k != same} == {
        k: v for k, v in before.items() if k != same and not k.startswith(same + "/")
    }


def test_a_link_at_the_top_has_one_item_in_text_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake(monkeypatch, [root / FLAVOR / "Extras"], MOUNT_POINT)
    lines = _run("tree", "--root", str(root)).splitlines()
    named = [line for line in lines if line.strip().startswith("Extras")]
    assert len(named) == 1, named
    assert named[0].startswith("  Extras  (link ") and named[0].endswith("not followed)")


FLAVOR_LINKS = [
    pytest.param(WCI_LINK, None, id="constructed-wci-link"),
    pytest.param(LX_SYMLINK, WSL_MODE, id="constructed-wsl-symlink"),
]


@pytest.mark.parametrize(("tag", "mode"), FLAVOR_LINKS)
def test_a_flavor_folder_that_is_a_link_is_no_flavor_and_never_walked(
    root: Path, monkeypatch: pytest.MonkeyPatch, tag: int, mode: int | None
) -> None:
    """Constructed: discovery judges the flavor folder by the same link test
    (M13-37), so a WCI link or a WSL symlink is named in `other_dirs`, not
    taken as a flavor. `tree --install` lists it as a link that is not a
    flavor folder, word for word as `filemap gaps` and `explain` say it, and
    nothing in it is walked; a command that needs a flavor finds none."""
    flavor = root / FLAVOR
    assert [f.folder for f in discover(root).flavors] == [FLAVOR]
    _fake(monkeypatch, [flavor], tag, mode=mode)
    found = discover(root)
    assert (found.flavors, found.other_dirs) == ((), (FLAVOR,))

    def no_walk(self: layout.Layout) -> layout.Inventory:
        raise AssertionError(f"walked {self.flavor_path}")

    monkeypatch.setattr(layout.Layout, "inventory", no_walk)
    expected = _explain(root, flavor)
    assert (expected["link"], expected["placeholder"]) == (True, False)
    report = _tree(root, "--install", "--explain")
    judged = _by_path(report, install=True)
    item = judged[FLAVOR]
    assert (item["kind"], item["base"], item["flavor_folder"]) == ("symlink", "root", None)
    assert item["size"] is None
    assert item["note"] == f"{UNKNOWN_NOTE}; {gaps.NOT_A_FLAVOR_LINK}"
    assert item["verdict"] == expected
    assert [i for i in report["items"] if i["flavor_folder"] is not None] == []
    assert ".build.info" in judged  # the install root is still listed
    gap = json.loads(_run("filemap", "gaps", "--root", str(root), "--json"))
    assert (gap["flavor_folders"], gap["links"]) == ([], [FLAVOR])
    named = [g for g in gap["groups"] if g["name"] == FLAVOR]
    assert [(g["base"], g["kind"], g["note"]) for g in named] == [
        ("root", "link", gaps.NOT_A_FLAVOR_LINK)
    ]
    result = CliRunner().invoke(cli.app, ["tree", "--root", str(root)])
    assert result.exit_code != 0
    assert "has no flavor folders" in result.stderr


@pytest.mark.parametrize("install", [False, True], ids=["tree", "tree-install"])
def test_a_flavor_folder_that_became_a_link_after_discovery_is_never_walked_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch, install: bool
) -> None:
    """Constructed: discovery took the folder as a flavor (here by standing
    in a link test that says no, as when the folder is replaced by a WCI link
    between discovery and the walk). `tree` judges it again by the walk's
    link test, as `explain` does, and never walks it: `Layout.inventory` is
    never called (M13-35)."""
    from wowlab_core import install as install_mod

    flavor = root / FLAVOR
    _fake(monkeypatch, [flavor], WCI_LINK)
    monkeypatch.setattr(install_mod, "is_link", lambda path, st: False)
    assert [f.folder for f in discover(root).flavors] == [FLAVOR]

    def no_walk(self: layout.Layout) -> layout.Inventory:
        raise AssertionError(f"walked {self.flavor_path}")

    monkeypatch.setattr(layout.Layout, "inventory", no_walk)
    expected = _explain(root, flavor)
    assert (expected["link"], expected["placeholder"]) == (True, False)
    if install:
        report = _tree(root, "--install", "--explain")
        judged = _by_path(report, install=True)
        item = judged[FLAVOR]
        assert item["kind"] == "symlink"
        assert item["note"] == f"flavor folder; {UNKNOWN_NOTE}; nothing in it is listed"
        assert item["verdict"] == expected
        assert [i for i in report["items"] if i["flavor_folder"] == FLAVOR and i["path"]] == []
        assert report["ancestors"] == []
        assert ".build.info" in judged  # the install root is still listed
    else:
        report = _tree(root, "--explain")
        assert report["items"] == [] and report["ancestors"] == []
        assert report["flavor_folder_verdict"] == expected
        assert report["errors"] == [] and report["truncated"] is False


def test_a_flavor_folder_that_became_a_link_says_so_in_text_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wowlab_core import install as install_mod

    _fake(monkeypatch, [root / FLAVOR], WCI_LINK)
    monkeypatch.setattr(install_mod, "is_link", lambda path, st: False)
    result = CliRunner().invoke(cli.app, ["tree", "--root", str(root)])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [f"{FLAVOR} in {root}"]
    assert "is a link; wowlab does not follow it" in result.stderr


def test_gaps_uses_the_walks_link_test() -> None:
    """One link test (M13-35): `gaps.is_link` is `layout.is_link`."""
    assert gaps.is_link is layout.is_link


def test_an_entry_that_cannot_be_stated_is_listed_as_before(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed (code review, M13-35): a plain top-level folder whose
    `lstat` fails is still listed from the listing alone, and the walk names
    the error, as on main before this ticket."""
    extras = root / FLAVOR / "Extras"
    real = layout.lstat_entry

    def refuse(entry: os.DirEntry[str]) -> os.stat_result:
        if entry.path == str(extras):
            raise PermissionError(13, "Permission denied")
        return real(entry)

    monkeypatch.setattr(layout, "lstat_entry", refuse)
    report = _tree(root)
    items = {i["path"]: i for i in report["items"]}
    assert items["Extras"]["kind"] == "folder"
    assert items["Extras"]["verdict"]["link"] is False
    assert "Extras: Permission denied" in report["errors"]
    lines = _run("tree", "--root", str(root)).splitlines()
    assert "  Extras/" in lines


@pytest.mark.parametrize(
    ("target", "where"),
    [
        pytest.param("outside", "outside the install", id="outside"),
        pytest.param("inside", "inside the install", id="inside"),
    ],
)
def test_a_link_at_the_install_root_reads_as_one_in_a_flavor_folder(
    root: Path, tmp_path: Path, target: str, where: str
) -> None:
    """One wording for a link at both levels (M13-35 review): a real symlink
    at the install root and one in the flavor folder both say where they
    point, inside or outside the install."""
    to = tmp_path / "elsewhere" if target == "outside" else root / FLAVOR / "Logs"
    to.mkdir(exist_ok=True)
    (root / "rootlink").symlink_to(to, target_is_directory=True)
    (root / FLAVOR / "WTF" / "flavorlink").symlink_to(to, target_is_directory=True)
    judged = _by_path(_tree(root, "--install"), install=True)
    expected = f"link to {to} ({where}), not followed"
    assert judged["rootlink"]["note"] == expected
    assert judged[f"{FLAVOR}/WTF/flavorlink"]["note"] == expected


def test_a_two_link_loop_is_where_it_points_unknown_constructed(root: Path) -> None:
    """Constructed (POSIX, M13-35 round 2): two real symlinks in `WTF/` that
    name each other. Their link text reads, but resolving them loops, so
    nothing says where they point: neither is called inside or outside."""
    wtf = root / FLAVOR / "WTF"
    try:
        (wtf / "loop-a").symlink_to("loop-b")
        (wtf / "loop-b").symlink_to("loop-a")
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")
    links = {
        s.path: s for s in layout.Layout(root / FLAVOR, install_root=root).inventory().symlinks
    }
    assert (links["WTF/loop-a"].target, links["WTF/loop-a"].inside_install) == ("loop-b", None)
    assert (links["WTF/loop-b"].target, links["WTF/loop-b"].inside_install) == ("loop-a", None)
    items = {i["path"]: i for i in _tree(root)["items"]}
    assert (
        items["WTF/loop-a"]["note"] == "link to loop-b (where it points is unknown), not followed"
    )
    assert (
        items["WTF/loop-b"]["note"] == "link to loop-a (where it points is unknown), not followed"
    )
    assert items["WTF/loop-a"]["kind"] == "symlink"


def test_link_facts_of_a_link_whose_text_cannot_be_read_constructed(tmp_path: Path) -> None:
    """Constructed: a folder stands in for a WCI link, whose link text
    `os.readlink` refuses: no target, and where it points is unknown."""
    folder = tmp_path / "wci"
    folder.mkdir()
    assert layout.link_facts(folder, tmp_path.resolve()) == (None, None)
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "plain").symlink_to(real, target_is_directory=True)
    assert layout.link_facts(tmp_path / "plain", tmp_path.resolve()) == (str(real), True)
