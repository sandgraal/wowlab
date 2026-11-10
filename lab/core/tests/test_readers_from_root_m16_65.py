"""The remaining install readers open from the install root (M16-65): `sv
merge`'s reads (`cli._read_sv`), the lab-addon readers (`read_char`,
`read_account`, the `char list` reader and its WTF counts) and the detail
under a plan or an undo (`cli._disk_now`, `cli._on_disk`) refuse a file
reached through a linked folder, the folder named; `sv dump` through a
shortcut into a folder of the install stays refused once a folder that
shortcut reaches is linked out.

The install is the captured tree (`fixtures/macos/`, rows in
`fixtures/README.md`, L8) copied into `tmp_path`; the links and the outside
folders are constructed (hostile input; ids and names say so). Nothing here
touches a real install.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, labaddon, layout, snapshot

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
ACCT_REL = f"WTF/Account/{ACCOUNT}"
CHAR_A = "Labchard-Labrealmg"  # M11-03's capture
CHAR_B = "Labcharb-Labrealmf"
CHAR_REL = f"{ACCT_REL}/1/{CHAR_A}/SavedVariables/WowLab.lua"
ACCOUNT_LAB_REL = f"{ACCT_REL}/SavedVariables/WowLab.lua"
CONFIG_REL = f"{ACCT_REL}/config-cache.wtf"
SV_REL = f"{ACCT_REL}/SavedVariables/Blizzard_AuctionHouseUI.lua"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need a privilege on Windows"
)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: tmp_path / "ud")
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([]))
    install_root = tmp_path / "World of Warcraft"
    install_root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", install_root / ".build.info")
    shutil.copytree(CAPTURE / "forever", install_root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(install_root))
    monkeypatch.chdir(tmp_path)
    return install_root


def _link_out(folder: Path, tmp_path: Path) -> Path:
    """Constructed: `folder` moved out of the install, a link left in its
    place. Returns the link."""
    outside = tmp_path / "Elsewhere-constructed" / folder.name
    outside.parent.mkdir(exist_ok=True)
    shutil.move(folder, outside)
    folder.symlink_to(outside, target_is_directory=True)
    return folder


def _account(root: Path) -> Path:
    return root / FLAVOR / "WTF" / "Account" / ACCOUNT


# ─── the lab-addon readers ──────────────────────────────────────────────────


def test_read_char_and_read_account_read_a_plain_install(root: Path) -> None:
    assert labaddon.read_char(root / FLAVOR / CHAR_REL) is not None
    assert labaddon.read_account(root / FLAVOR / ACCOUNT_LAB_REL) is not None
    assert labaddon.read_char(root / FLAVOR / CHAR_REL, root=root) is not None


@pytest.mark.parametrize("given_root", [False, True], ids=["root-from-path", "root-given"])
def test_constructed_read_char_refuses_a_linked_account_folder(
    root: Path, tmp_path: Path, given_root: bool
) -> None:
    link = _link_out(_account(root), tmp_path)
    kwargs: dict[str, Any] = {"root": root} if given_root else {}
    with pytest.raises(snapshot.LinkedPathError) as caught:
        labaddon.read_char(root / FLAVOR / CHAR_REL, **kwargs)
    assert caught.value.folder == link
    assert str(link) in str(caught.value)
    with pytest.raises(snapshot.LinkedPathError) as caught:
        labaddon.read_account(root / FLAVOR / ACCOUNT_LAB_REL, **kwargs)
    assert caught.value.folder == link


def test_constructed_read_char_refuses_a_linked_flavor_folder(root: Path, tmp_path: Path) -> None:
    """The walk starts above the flavor folder, so a linked one is refused too."""
    link = _link_out(root / FLAVOR, tmp_path)
    with pytest.raises(snapshot.LinkedPathError) as caught:
        labaddon.read_char(root / FLAVOR / CHAR_REL)
    assert caught.value.folder == link


def test_constructed_read_char_of_a_loose_file_checks_the_file_only(tmp_path: Path) -> None:
    """A file outside any `WTF/Account` tree has no install root in its path:
    it is read as before, only its last part checked for a link."""
    loose = tmp_path / "Loose-constructed" / "WowLab.lua"
    loose.parent.mkdir()
    shutil.copy2(CAPTURE / "forever" / CHAR_REL, loose)
    assert labaddon.read_char(loose) is not None


def test_the_install_root_of_a_path_is_the_folder_above_the_flavor(root: Path) -> None:
    assert labaddon._install_root_of(root / FLAVOR / CHAR_REL) == root
    assert labaddon._install_root_of(root / FLAVOR / "wtf" / "account" / "x") == root
    assert labaddon._install_root_of(root / "nothing.lua") is None


def test_constructed_char_show_names_the_linked_folder(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = labaddon.read_char

    def swapped(path: Path) -> Any:
        if not _account(root).is_symlink():
            _link_out(_account(root), tmp_path)
        return real(path)

    monkeypatch.setattr(labaddon, "read_char", swapped)
    done = CliRunner().invoke(cli.app, ["char", "show", "--character", CHAR_A])
    assert done.exit_code not in (0, None), done.output
    assert "linked folder" in done.stderr, done.stderr
    assert str(_account(root)) in done.stderr, done.stderr
    assert done.stdout == ""


def test_constructed_char_list_row_names_the_linked_folder(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The listing walks the account folder, then it is swapped for a link
    before each file is read: every row says so, the folder named."""
    real = labaddon._Reader.read

    def swapped(self: Any, rel: str, account: str, who: str) -> Any:
        if not _account(root).is_symlink():
            _link_out(_account(root), tmp_path)
        return real(self, rel, account, who)

    monkeypatch.setattr(labaddon._Reader, "read", swapped)
    lay = layout.Layout(root / FLAVOR, install_root=root)
    entries = labaddon.read_all(lay)
    errors = [e.error for e in entries if e.error is not None]
    assert errors, entries
    for error in errors:
        assert "linked folder" in error, error
        assert str(_account(root)) in error, error


def test_constructed_char_list_counts_refuse_a_linked_folder(root: Path, tmp_path: Path) -> None:
    """The WTF counts read the account's `config-cache.wtf` from the root."""
    _link_out(_account(root), tmp_path)
    counter = labaddon._Counter(root / FLAVOR, root)
    found = [
        layout.WtfFile(
            path=CONFIG_REL,
            name="config-cache.wtf",
            scope="account",
            account=ACCOUNT,
            realm_folder=None,
            character_folder=None,
            size=10,
            mtime_ns=0,
            entry_id=None,
        )
    ]
    count, why = counter.count(found, "config-cache.wtf")
    assert count is None
    assert why is not None and "linked folder" in why, why
    assert str(_account(root)) in why, why


def test_the_reader_still_refuses_a_file_replaced_after_its_lstat(root: Path) -> None:
    """`expect` holds with the walk from the root: a file that is not the one
    the reader `lstat`ed is refused."""
    other = root / FLAVOR / SV_REL
    with pytest.raises(snapshot.UnsafeReadError, match="replaced"):
        snapshot.read_regular_file(root / FLAVOR / CHAR_REL, root=root, expect=other.lstat())
    path = root / FLAVOR / CHAR_REL
    data = snapshot.read_regular_file(path, root=root, expect=path.lstat(), limit=1 << 20)
    assert data == path.read_bytes()


# ─── sv merge ───────────────────────────────────────────────────────────────


def test_constructed_read_sv_refuses_a_linked_account_folder(root: Path, tmp_path: Path) -> None:
    lay = layout.Layout(root / FLAVOR, install_root=root)
    assert cli._read_sv(lay, CHAR_REL)
    link = _link_out(_account(root), tmp_path)
    with pytest.raises(snapshot.LinkedPathError) as caught:
        cli._read_sv(lay, CHAR_REL)
    assert caught.value.folder == link


def test_constructed_sv_merge_names_the_linked_folder(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The account folder swapped for a link after the merge listed the
    characters, before it reads their files: refused, the folder named,
    nothing written."""
    real = cli._read_sv

    def swapped(lay: layout.Layout, rel: str) -> bytes:
        if not _account(root).is_symlink():
            _link_out(_account(root), tmp_path)
        return real(lay, rel)

    monkeypatch.setattr(cli, "_read_sv", swapped)
    done = CliRunner().invoke(
        cli.app, ["sv", "merge", "WowLab.lua", "--from", CHAR_B, "--into", CHAR_A, "--yes"]
    )
    assert done.exit_code not in (0, None), done.output
    assert "linked folder" in done.stderr, done.stderr
    assert str(_account(root)) in done.stderr, done.stderr


# ─── the detail under a plan or an undo ─────────────────────────────────────


def test_constructed_detail_reads_name_the_linked_folder(root: Path, tmp_path: Path) -> None:
    flavor = root / FLAVOR
    data = (flavor / CONFIG_REL).read_bytes()
    assert cli._disk_now(flavor, CONFIG_REL) == data
    link = _link_out(_account(root), tmp_path)
    now = cli._disk_now(flavor, CONFIG_REL)
    assert isinstance(now, cli._Unread)
    assert now.why is not None and str(link) in now.why and "linked folder" in now.why
    store = snapshot.SnapshotStore(tmp_path / "store-constructed")
    before = cli._on_disk(store, flavor, CONFIG_REL, hashlib.sha256(data).hexdigest())
    assert isinstance(before, cli._Unread), "the file behind the link was read"
    assert before.why is not None and str(link) in before.why
    lines = cli._detail_lines(CONFIG_REL, now, b"", limit=20)
    assert len(lines) == 1 and "not compared" in lines[0] and str(link) in lines[0], lines


def test_detail_of_an_unreadable_side_keeps_its_general_words() -> None:
    lines = cli._detail_lines("WTF/Config.wtf", cli._UNREAD, b"", limit=20)
    assert lines == [
        f"{cli._DETAIL_INDENT}(not compared: wowlab could not read both versions back)"
    ]


# ─── sv dump through a shortcut into the install ────────────────────────────


def test_constructed_folders_passed_through_resolve_each_link(root: Path, tmp_path: Path) -> None:
    shortcut = tmp_path / "MySV-constructed"
    shortcut.symlink_to(_account(root), target_is_directory=True)
    _link_out(_account(root), tmp_path)
    seen = cli._folders_passed_through(shortcut / "SavedVariables" / "x.lua")
    assert root in seen
    assert cli._really_inside(shortcut / "SavedVariables" / "x.lua", root)


def test_constructed_folders_passed_through_stop_at_a_link_loop(tmp_path: Path) -> None:
    loop = tmp_path / "loop-constructed"
    loop.symlink_to(loop)
    seen = cli._folders_passed_through(loop / "a" / "b.lua")
    assert tmp_path in seen


def test_constructed_sv_dump_through_a_shortcut_with_a_folder_linked_out_is_refused(
    root: Path, tmp_path: Path
) -> None:
    shortcut = tmp_path / "MySV-constructed"
    shortcut.symlink_to(_account(root), target_is_directory=True)
    _link_out(_account(root), tmp_path)
    done = CliRunner().invoke(
        cli.app, ["sv", "dump", str(shortcut / "SavedVariables" / "Blizzard_AuctionHouseUI.lua")]
    )
    assert done.exit_code == cli.EXIT_REFUSED, done.output
    assert "not spelled under" in done.stderr, done.stderr
    assert done.stdout == ""
