# Probe from review of m12/09-char-list; reproduces `char list` leaving out, naming nowhere and exiting 0 for a character whose WowLab.lua is a symlink or a FIFO, or whose SavedVariables folder is a symlink or cannot be listed
"""§14.4: `read_all` "returns per character its label, the file's
modification time, and either the parsed record or the reason it could not
be read. One unreadable file names itself and exits 1 (as `looks show`
does) and never blocks the others."

On this branch `read_all` finds files only through `layout`'s typed file
list, which by design leaves out symbolic links (reported in
`Inventory.symlinks`, never followed), non-regular files (FIFOs), and
everything below a folder it could not list (reported in
`Inventory.errors`). So a character whose `WowLab.lua` is such a file is
simply not in the table: `char list` prints the other characters, names the
missing one nowhere (not in a row, not on stderr, not in `--json`) and exits
0. The same symlink or FIFO swapped in after the walk and before the read is
named and exits 1 today (`snapshot.read_regular_file` refuses it), so
whether the owner is told depends on timing. The branch's amendment names
only the unlistable-folder case as "not covered".

The fix is the implementer's to design (for example: one `inventory()` walk
instead of the two `read_all` makes, turning a symlink or walk error at a
character folder, its `SavedVariables/` or a `WowLab.lua` in it into an
entry; `lstat` of the expected name for a non-regular file). These tests
only ask what §14.4 asks: the character is named and the command exits 1,
in text and in `--json`, and nothing is read through a link (the linked
target's record never appears).

Positive control: the same tree with that character's regular `WowLab.lua`
cut short is named on stderr and exits 1 (passes today), and the untouched
tree exits 0 with both characters.

Constructed (L8): the `macos` capture copied into `tmp_path`, with the
second captured character's file or folder replaced by a link to a copy
outside the flavor folder, a FIFO, or a folder made unreadable. Nothing
reads or writes a real install.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder (tests may name it, L6)
ACCOUNT_DIR = "WTF/Account/90000001#6"
FIRST = "1/Labchard-Labrealmg"
SECOND = "1/Labcharb-Labrealmf"

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root / FLAVOR


def _sv_folder(flavor: Path, character: str) -> Path:
    return flavor / ACCOUNT_DIR / character / "SavedVariables"


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def _symlink(link: Path, target: Path, *, is_dir: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


def _linked_file(flavor: Path, tmp_path: Path) -> Callable[[], None]:
    lab = _sv_folder(flavor, SECOND) / "WowLab.lua"
    outside = tmp_path / "outside-WowLab.lua"
    shutil.move(lab, outside)
    _symlink(lab, outside)
    return lambda: None


def _linked_folder(flavor: Path, tmp_path: Path) -> Callable[[], None]:
    folder = _sv_folder(flavor, SECOND)
    outside = tmp_path / "outside-SavedVariables"
    shutil.move(folder, outside)
    _symlink(folder, outside, is_dir=True)
    return lambda: None


def _fifo(flavor: Path, tmp_path: Path) -> Callable[[], None]:
    if not hasattr(os, "mkfifo"):
        pytest.skip("no os.mkfifo on this platform")
    lab = _sv_folder(flavor, SECOND) / "WowLab.lua"
    lab.unlink()
    os.mkfifo(lab)
    return lambda: None


def _unlistable_folder(flavor: Path, tmp_path: Path) -> Callable[[], None]:
    if sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("POSIX permissions, and not as root")
    folder = _sv_folder(flavor, SECOND)
    folder.chmod(0)
    return lambda: folder.chmod(0o755)


CASES = {
    "WowLab.lua is a symlink": _linked_file,
    "SavedVariables is a symlink": _linked_folder,
    "WowLab.lua is a FIFO": _fifo,
    "SavedVariables cannot be listed": _unlistable_folder,
}


@pytest.mark.parametrize("case", list(CASES), ids=[f"constructed-{c}" for c in CASES])
def test_constructed_unread_lab_file_is_named_and_exits_1(
    flavor: Path, tmp_path: Path, case: str
) -> None:
    restore = CASES[case](flavor, tmp_path)
    try:
        text = run("char", "list")
        as_json = run("char", "list", "--json")
    finally:
        restore()
    # The other character is still listed with its record.
    assert FIRST in text.stdout, text.output
    report = cli.CharListReport.model_validate_json(as_json.stdout)
    by = {e.character: e for e in report.characters}
    assert by[FIRST].record is not None
    # Nothing is read through a link: the second character never has a record.
    assert SECOND not in by or by[SECOND].record is None
    # §14.4: the unreadable one names itself and the command exits 1.
    assert SECOND in text.stderr, (case, text.exit_code, text.stdout, text.stderr)
    assert text.exit_code == 1, (case, text.stdout, text.stderr)
    assert as_json.exit_code == 1, (case, as_json.stderr)


def test_constructed_positive_control_a_damaged_regular_file_is_named(flavor: Path) -> None:
    whole = run("char", "list")
    assert whole.exit_code == 0, whole.output
    assert FIRST in whole.stdout and SECOND in whole.stdout
    lab = _sv_folder(flavor, SECOND) / "WowLab.lua"
    lab.write_bytes(lab.read_bytes()[:5000])
    cut = run("char", "list")
    assert cut.exit_code == 1
    assert SECOND in cut.stderr and FIRST in cut.stdout
