# Probe from review of m15/06-casc-stat-cat; reproduces `casc cat --out` writing into the
# default `files/` namespace, so a later default `casc cat` of the same build is refused.
"""`--out` may name any path inside the build folder, including `files`
itself (a regular file where the default subfolder must be) or another
file's default name under `files/`. Either one makes a later plain
`wowlab casc cat --fdid N` refuse, the first with a false "reached through
a link". Constructed installs only (the suite's `_casc_build`), user data
directory redirected into `tmp_path`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_cli_casc as casc_suite

IDS = casc_suite.IDS
Env = casc_suite.Env
env = casc_suite.env  # the suite's fixture: constructed install, redirected user data


def test_positive_control_default_cat_writes_constructed(env: Env) -> None:
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]))
    assert result.exit_code == 0, result.output
    assert env.default().is_file()


def test_out_files_does_not_block_the_default_place_constructed(env: Env) -> None:
    env.run("casc", "cat", "--fdid", str(IDS["local"]), "--out", "files")
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]))
    assert result.exit_code == 0, result.output
    assert env.default().is_file()


def test_out_cannot_take_another_files_default_name_constructed(env: Env) -> None:
    # File 10's bytes at the name file 11 would get for a content key
    # starting deadbeef: a later `casc cat --fdid 11` of that file is refused.
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--out", "files/11-deadbeef.bin")
    assert result.exit_code == 1, result.output
    assert not (env.folder() / "files" / "11-deadbeef.bin").exists()
