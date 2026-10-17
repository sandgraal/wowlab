"""`tree --explain` judges a path at or under a Windows reparse point that is
not a link the way `explain` and the write gate do (M13-33): a folder or file
there is not restorable and carries the placeholder label.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
with its flavor folder named as its provenance row says (`_classic_beta_`),
and `--root` pointing at the copy. Nothing reads or writes a real install.

Constructed (L8, boundary): the reparse attributes. A Windows reparse point
cannot be made on macOS or Linux, so the `lstat` results of chosen paths are
stood in with the attribute and tag Windows reports, through the three
places the commands read them (`Path.lstat`, `layout.lstat_entry`,
`gaps.lstat_entry`), as `test_cli_explain_detail.py` does for `explain`.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, gaps, layout, verdict

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT_DIR = "WTF/Account/90000001#6"
SV_DIR = f"{ACCOUNT_DIR}/SavedVariables"  # an ancestor in `tree`: no item of its own
SYNDICATOR = f"{SV_DIR}/Syndicator.lua"

CLOUD_PLACEHOLDER = 0x9000001A  # IO_REPARSE_TAG_CLOUD_A: no name-surrogate bit
DEDUPLICATED = 0x80000013  # IO_REPARSE_TAG_DEDUP: no name-surrogate bit
MOUNT_POINT = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT, a junction: name surrogate
WCI = 0x80000018  # IO_REPARSE_TAG_WCI, no name-surrogate bit
WCI_LINK = 0xA0000027  # IO_REPARSE_TAG_WCI_LINK_1: name surrogate, not a junction


class _ReparseStat:
    """An `lstat` result as Windows gives it for a reparse point: the real
    entry's mode, size and identity, with the reparse attribute and tag."""

    def __init__(self, real: os.stat_result, tag: int) -> None:
        self.st_mode = real.st_mode
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
    monkeypatch.chdir(tmp_path)
    return install


def _fake(monkeypatch: pytest.MonkeyPatch, paths: Iterable[Path], tag: int) -> None:
    """Every `lstat` of `paths`, by path or by listed entry, reports `tag`."""
    marked = {str(p) for p in paths}
    real_lstat = Path.lstat
    real_layout = layout.lstat_entry
    real_gaps = gaps.lstat_entry

    def path_lstat(self: Path) -> Any:
        st = real_lstat(self)
        return _ReparseStat(st, tag) if str(self) in marked else st

    def entry_lstat(real: Any) -> Any:
        def lstat(entry: os.DirEntry[str]) -> Any:
            st = real(entry)
            return _ReparseStat(st, tag) if entry.path in marked else st

        return lstat

    monkeypatch.setattr(Path, "lstat", path_lstat)
    monkeypatch.setattr(layout, "lstat_entry", entry_lstat(real_layout))
    monkeypatch.setattr(gaps, "lstat_entry", entry_lstat(real_gaps))


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


@pytest.mark.parametrize("install", [False, True], ids=["tree", "tree-install"])
@pytest.mark.parametrize(
    "tag",
    [
        pytest.param(CLOUD_PLACEHOLDER, id="constructed-onedrive-placeholder"),
        pytest.param(DEDUPLICATED, id="constructed-deduplicated"),
        pytest.param(WCI, id="constructed-wci"),
    ],
)
def test_a_placeholder_folder_and_what_is_under_it_are_not_restorable(
    root: Path, monkeypatch: pytest.MonkeyPatch, install: bool, tag: int
) -> None:
    flavor = root / FLAVOR
    _fake(monkeypatch, [flavor / ACCOUNT_DIR], tag)
    judged = _judged(root, install=install)
    for rel in (ACCOUNT_DIR, SV_DIR, SYNDICATOR):
        item = judged[_key(rel, install)]
        v = item["verdict"]
        assert v["placeholder"] is True, rel
        assert v["link"] is False, rel
        assert v["label"] == verdict.LABELS["no"] == "wowlab won't touch", rel
        assert v["restorable"] is (None if item["kind"] == "folder" else False), rel
        # The same verdict `explain` gives for the same path, word for word.
        assert v == _explain(root, flavor / rel), rel
        assert item["explained"] is None or item["explained"]["verdict"] == v
    file = judged[_key(SYNDICATOR, install)]["verdict"]
    assert file["level"] == "gate"
    assert file["restorable"] is False
    # Outside the placeholder nothing moves.
    beside = judged[_key("WTF/Config.wtf", install)]["verdict"]
    assert beside["placeholder"] is False
    assert beside["restorable"] is True
    assert beside == _explain(root, flavor / "WTF/Config.wtf")


@pytest.mark.parametrize("install", [False, True], ids=["tree", "tree-install"])
def test_a_placeholder_file_alone(
    root: Path, monkeypatch: pytest.MonkeyPatch, install: bool
) -> None:
    flavor = root / FLAVOR
    _fake(monkeypatch, [flavor / SYNDICATOR], CLOUD_PLACEHOLDER)
    judged = _judged(root, install=install)
    v = judged[_key(SYNDICATOR, install)]["verdict"]
    assert (v["placeholder"], v["restorable"], v["label"]) == (True, False, "wowlab won't touch")
    assert v == _explain(root, flavor / SYNDICATOR)
    assert judged[_key(SV_DIR, install)]["verdict"]["placeholder"] is False


@pytest.mark.parametrize("install", [False, True], ids=["tree", "tree-install"])
def test_a_placeholder_flavor_folder_covers_everything_in_it(
    root: Path, monkeypatch: pytest.MonkeyPatch, install: bool
) -> None:
    flavor = root / FLAVOR
    _fake(monkeypatch, [flavor], CLOUD_PLACEHOLDER)
    report = _tree(root, "--install", "--explain") if install else _tree(root, "--explain")
    judged = _by_path(report, install=install)
    for rel in ("WTF/Config.wtf", SYNDICATOR, "WTF/Account"):
        v = judged[_key(rel, install)]["verdict"]
        assert v["placeholder"] is True, rel
        assert v == _explain(root, flavor / rel), rel
    if install:
        assert judged[FLAVOR]["verdict"] == _explain(root, flavor)
        assert judged[FLAVOR]["verdict"]["placeholder"] is True
    else:
        assert report["flavor_folder_verdict"]["placeholder"] is True
        assert report["flavor_folder_verdict"] == _explain(root, flavor)


def test_a_placeholder_entry_of_the_install_root(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake(monkeypatch, [root / ".build.info"], CLOUD_PLACEHOLDER)
    v = _judged(root, install=True)[".build.info"]["verdict"]
    assert (v["placeholder"], v["restorable"], v["label"]) == (True, False, "wowlab won't touch")
    assert v == _explain(root, root / ".build.info")


@pytest.mark.parametrize("install", [False, True], ids=["tree", "tree-install"])
def test_a_name_surrogate_that_layout_walks_is_judged_a_link_as_explain_does(
    root: Path, monkeypatch: pytest.MonkeyPatch, install: bool
) -> None:
    """Constructed: a name-surrogate reparse point that is no junction (a
    WCI link). `layout` walks it, but `explain` and the gate call it a link."""
    flavor = root / FLAVOR
    _fake(monkeypatch, [flavor / ACCOUNT_DIR], WCI_LINK)
    judged = _judged(root, install=install)
    for rel in (ACCOUNT_DIR, SV_DIR, SYNDICATOR):
        v = judged[_key(rel, install)]["verdict"]
        assert (v["link"], v["placeholder"], v["restorable"]) == (True, False, None), rel
        assert v["label"] == verdict.LINK_LABEL
        assert v == _explain(root, flavor / rel), rel


@pytest.mark.parametrize("install", [False, True], ids=["tree", "tree-install"])
def test_a_junction_is_unchanged(
    root: Path, monkeypatch: pytest.MonkeyPatch, install: bool
) -> None:
    """Constructed: the account folder is a junction (the mount-point tag,
    and `Path.is_junction` answering yes for it): a link, not followed, and
    not a placeholder, exactly as before M13-33."""
    flavor = root / FLAVOR
    account = flavor / ACCOUNT_DIR
    before = _judged(root, install=install)
    _fake(monkeypatch, [account], MOUNT_POINT)
    real_junction = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda self: self == account or real_junction(self))
    judged = _judged(root, install=install)
    item = judged[_key(ACCOUNT_DIR, install)]
    assert item["kind"] == "symlink"
    assert (item["verdict"]["link"], item["verdict"]["placeholder"]) == (True, False)
    assert _key(SYNDICATOR, install) not in judged  # never followed
    assert {k: v for k, v in judged.items() if not k.endswith(ACCOUNT_DIR)} == {
        k: v for k, v in before.items() if not k.startswith(_key(ACCOUNT_DIR, install))
    }


@pytest.mark.parametrize("install", [False, True], ids=["tree", "tree-install"])
def test_a_symlink_is_unchanged(root: Path, install: bool) -> None:
    flavor = root / FLAVOR
    (flavor / "WTF" / "linked").symlink_to(flavor / ACCOUNT_DIR, target_is_directory=True)
    judged = _judged(root, install=install)
    item = judged[_key("WTF/linked", install)]
    assert item["kind"] == "symlink"
    v = item["verdict"]
    assert (v["link"], v["placeholder"], v["restorable"]) == (True, False, None)
    assert v["label"] == verdict.LINK_LABEL
    assert not any(v["verdict"]["placeholder"] for v in judged.values())


def test_install_tree_lists_the_folders_above_its_items(root: Path) -> None:
    """M13-33: `tree --install --json` carries `ancestors` as `tree --json`
    does, so an unlisted folder such as `WTF/Account` has a verdict."""
    single = cli.TreeReport.model_validate(_tree(root, "--explain"))
    whole = cli.InstallTreeReport.model_validate(_tree(root, "--install", "--explain"))
    assert [a.path for a in whole.ancestors] == [a.path for a in single.ancestors]
    assert whole.ancestors == single.ancestors
    paths = {a.path for a in whole.ancestors}
    assert {"WTF/Account", "Interface/AddOns"} <= paths
    assert all(a.base == "flavor" and a.flavor_folder == FLAVOR for a in whole.ancestors)
    scoped = _tree(root, "--install", f"{FLAVOR}/WTF/Account/90000001#6")
    assert scoped["ancestors"]
    for a in scoped["ancestors"]:
        assert a["path"].startswith("WTF/Account/90000001#6/"), a["path"]
    assert _tree(root, "--install", ".build.info")["ancestors"] == []


# ─── the folders `Layout._sub` looks up (code review note 1) ─────────────────
# `WTF/Account` and `Interface/AddOns` are found by name with `Path.lstat`
# (`Layout._sub`), not only listed through `layout.lstat_entry`
# (`Layout._stat`). These fake `Path.lstat` alone, so the reparse point is
# seen through `_sub` and nowhere else in the walk.

SUB_FOLDERS = [
    pytest.param("WTF/Account", SYNDICATOR, id="wtf-account"),
    pytest.param("Interface/AddOns", None, id="interface-addons"),
]


def _fake_path_lstat_only(monkeypatch: pytest.MonkeyPatch, path: Path, tag: int) -> None:
    real_lstat = Path.lstat

    def path_lstat(self: Path) -> Any:
        st = real_lstat(self)
        return _ReparseStat(st, tag) if self == path else st

    monkeypatch.setattr(Path, "lstat", path_lstat)


def _under(judged: dict[str, dict[str, Any]], key: str) -> list[str]:
    return [k for k in judged if k.startswith(key + "/")]


@pytest.mark.parametrize("install", [False, True], ids=["tree", "tree-install"])
@pytest.mark.parametrize(("folder", "inside"), SUB_FOLDERS)
def test_a_placeholder_seen_by_sub_lookup(
    root: Path, monkeypatch: pytest.MonkeyPatch, install: bool, folder: str, inside: str | None
) -> None:
    """Constructed: a cloud placeholder on a folder `_sub` looks up, seen
    only through `Path.lstat`."""
    flavor = root / FLAVOR
    _fake_path_lstat_only(monkeypatch, flavor / folder, CLOUD_PLACEHOLDER)
    lay = layout.Layout(flavor, install_root=root)
    assert [r.path for r in lay.inventory().reparse_points] == [folder]
    judged = _judged(root, install=install)
    key = _key(folder, install)
    below = _under(judged, key)
    assert below, folder  # still walked: a placeholder holds real content
    for k in [key, *below]:
        assert judged[k]["verdict"]["placeholder"] is True, k
    assert judged[key]["verdict"] == _explain(root, flavor / folder)
    if inside is not None:
        assert judged[_key(inside, install)]["verdict"] == _explain(root, flavor / inside)
        assert judged[_key(inside, install)]["verdict"]["restorable"] is False


@pytest.mark.parametrize("install", [False, True], ids=["tree", "tree-install"])
@pytest.mark.parametrize(("folder", "inside"), SUB_FOLDERS)
def test_a_junction_seen_by_sub_lookup_is_unchanged(
    root: Path, monkeypatch: pytest.MonkeyPatch, install: bool, folder: str, inside: str | None
) -> None:
    """Constructed: a junction on a folder `_sub` looks up (the mount-point
    tag through `Path.lstat`, `Path.is_junction` answering yes): reported as
    a link, never walked, and no placeholder anywhere."""
    flavor = root / FLAVOR
    target = flavor / folder
    _fake_path_lstat_only(monkeypatch, target, MOUNT_POINT)
    real_junction = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda self: self == target or real_junction(self))
    inv = layout.Layout(flavor, install_root=root).inventory()
    assert inv.reparse_points == ()
    assert folder in {s.path for s in inv.symlinks}
    judged = _judged(root, install=install)
    key = _key(folder, install)
    assert judged[key]["kind"] == "symlink"
    assert (judged[key]["verdict"]["link"], judged[key]["verdict"]["placeholder"]) == (True, False)
    assert _under(judged, key) == []  # never followed
    assert not any(v["verdict"]["placeholder"] for v in judged.values())
