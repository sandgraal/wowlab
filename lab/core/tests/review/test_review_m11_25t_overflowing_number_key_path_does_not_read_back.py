# Probe from review of m11/25-path-grammar-tests; reproduces `sv merge` printing the conflict path `Var[1e400]` and then refusing it as a `--key` ("inf is not a number"), which the M11-25T graders do not catch
"""M11-25: "make every printed path parse back to the same key, or say in
`--help` which keys cannot be addressed". `[1e400]` is a number literal
`luadata` accepts, and Lua 5.1 loads it as the key `math.huge`, a legal
table key. `sv dump` prints `Var["big"][1e400]` and `sv dump --path` reads it
back on main. `svmerge.merge` prints the same spelling as a conflict path,
then refuses it as a key: `_read_path` passes the parsed number through
`_number_text`, which raises `MergeError("inf is not a number a
SavedVariables file can hold")`. The M11-25T scratch patch routes `--path`
through the same reader, so `sv dump --path` regresses to exit 2 too, and
all 144 M11-25T graders still pass (their grammar table has no overflowing
number).

Constructed (L8, boundary): the client is not known to write such a key;
the file grammar admits it. The positive control is `[1e3]`.

Marked `xfail(strict=True)` so the graders branch stays green; M11-25
deletes the marker when both commands read the printed path back (or its
`--help` names the keys that cannot be addressed and this probe is deleted
with that ruling).
"""

from __future__ import annotations

from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, luadata, svmerge


@pytest.fixture(autouse=True)
def _user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)


def _doc(number: bytes, value: int) -> bytes:
    return b'\r\nNumDB = {\r\n["big"] = {\r\n[' + number + b"] = %d,\r\n},\r\n}\r\n" % value


def _check(tmp_path: Path, number: bytes) -> None:
    ours, theirs = luadata.parse(_doc(number, 1)), luadata.parse(_doc(number, 2))
    (conflict,) = svmerge.merge(ours, theirs).conflicts
    path = conflict.path
    # --key with the printed path: two-way, so the subtree is copied whole.
    limited = svmerge.merge(ours, theirs, keys=[path])
    assert [(t.path, t.value) for t in limited.taken] == [(path, "2")]
    f = tmp_path / "Num.lua"
    f.write_bytes(_doc(number, 1))
    again = CliRunner().invoke(cli.app, ["sv", "dump", str(f), "--path", path])
    assert (again.exit_code, again.stdout) == (0, f"{path} = 1\n"), again.stderr


def test_positive_control_finite_number_key_reads_back_constructed(tmp_path: Path) -> None:
    _check(tmp_path, b"1e3")


def test_overflowing_number_key_path_reads_back_constructed(tmp_path: Path) -> None:
    _check(tmp_path, b"1e400")
