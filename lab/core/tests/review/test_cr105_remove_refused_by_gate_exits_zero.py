# Probe from review of m11/02-addon-install; reproduces `addon remove lab` exiting 0 with "Nothing to remove" when the gate refused every delete.
"""On a case-insensitive volume, an addon folder spelled `wowlab` is found by
`_installed()` under the name `WowLab`; every delete the dry run plans for
its files is refused by the gate (a case collision), `_deletes` turns each
refusal into a "left alone" row, and `remove` then prints "Nothing to
remove: no lab-addon files" and exits 0. §6.11: exit 3 means refused by the
gate, and `install` in the same tree does exit 3 (the positive control).

Skipped on a case-sensitive volume, where `WowLab` and `wowlab` are two
folders and "nothing to remove" is true.

Constructed: a `wowlab` folder with one file in the captured tree copied
into `tmp_path`. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)


def _case_variant(flavor: Path) -> Path:
    folder = flavor / "Interface" / "AddOns" / "wowlab"
    folder.mkdir()
    (folder / "WowLab.toc").write_bytes(b"## Interface: 16001\n")
    if not (flavor / "Interface" / "AddOns" / "WowLab").exists():
        pytest.skip("case-sensitive volume: WowLab and wowlab are different folders")
    return folder


def test_positive_control_install_into_a_case_variant_is_refused_constructed(
    flavor: Path,
) -> None:
    _case_variant(flavor)
    result = run("addon", "install", "lab", "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)


def test_remove_of_a_case_variant_is_not_reported_as_nothing_to_remove_constructed(
    flavor: Path,
) -> None:
    folder = _case_variant(flavor)
    result = run("addon", "remove", "lab", "--yes")
    assert (folder / "WowLab.toc").exists()
    assert result.exit_code == 3, (result.exit_code, result.stdout, result.stderr)
