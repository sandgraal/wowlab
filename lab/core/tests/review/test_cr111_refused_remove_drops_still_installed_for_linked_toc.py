# Probe from review of m11/17-addon-looks-followups; reproduces a refused `addon remove lab` dropping STILL_INSTALLED_NOTE when WowLab.toc is found as a link.
"""M11-17 keeps "The lab-addon is still installed ..." unless no `WowLab.toc`
is among the files found. A `WowLab.toc` that is a symlink is found: the
folder listing reports it (as left alone, never followed), and the client,
which opens files through the link, can still load the addon from it. The
non-refused remove in the same tree says so with `TOC_LEFT_NOTE` ("the
client may still find an addon there"). The refused remove, whose message
names only the refused paths, drops the sentence because `_toc_found` looks
only at the regular files, so the user is told nothing about the addon
still loading.

Positive control: the same tree with a regular `WowLab.toc` carries the
sentence.

Constructed: a folder holding `helper.dll` (which the gate refuses to
delete) and a `WowLab.toc`, in the captured tree copied into `tmp_path`.
Skipped on Windows, where a file symlink needs a privilege. Nothing reads
or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    FLAVOR,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import addoninstall

LAB = "Interface/AddOns/WowLab"


def _refused_stderr(root: Path, tmp_path: Path, *, linked: bool) -> str:
    folder = root / FLAVOR / LAB
    folder.mkdir(parents=True)
    toc = folder / "WowLab.toc"
    if linked:
        real = tmp_path / "real-WowLab.toc"
        real.write_bytes(b"## Interface: 11500\n")
        toc.symlink_to(real)
    else:
        toc.write_bytes(b"## Interface: 11500\n")
    (folder / "helper.dll").write_bytes(b"constructed, not an executable")
    result = run("addon", "remove", "lab", "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    return " ".join(result.stderr.split())


def test_positive_control_regular_toc_keeps_the_sentence(root: Path, tmp_path: Path) -> None:
    err = _refused_stderr(root, tmp_path, linked=False)
    assert " ".join(addoninstall.STILL_INSTALLED_NOTE.split()) in err


@pytest.mark.skipif(sys.platform == "win32", reason="file symlinks need a privilege on Windows")
def test_a_linked_toc_keeps_the_sentence_constructed(root: Path, tmp_path: Path) -> None:
    err = _refused_stderr(root, tmp_path, linked=True)
    assert " ".join(addoninstall.STILL_INSTALLED_NOTE.split()) in err, err
