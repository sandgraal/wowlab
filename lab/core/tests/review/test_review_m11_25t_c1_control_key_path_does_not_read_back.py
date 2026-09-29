# Probe from review of m11/25-path-grammar-tests; reproduces a key holding a C1 control character (U+0085, U+009B) printed by `sv dump` as a path `--path` cannot read back, which the M11-25T graders do not catch
"""M11-25: "make every printed path parse back to the same key ... a
round-trip test ... over labelled constructed keys with quotes, backslashes
and control characters". The M11-25T graders count only C0 and DEL as
control characters. The CLI's own text output also treats C1 (U+0080 to
U+009F) as control: `cli._safe` rewrites each one as the four characters
`\\xNN` so a CSI (U+009B) never reaches the terminal. A string key whose
bytes are the UTF-8 of a C1 character is therefore printed as
`Var["nel\\x85x"]`, and `\\x` is an escape the path grammar refuses
(`docs/LAB_FORMATS.md` 2026-09-22 amendment). The M11-25T scratch patch
(`m1125t-scratch-impl.patch`) passes all 144 graders and still fails this
probe: `cannot read --path 'C1DB["nel\\\\x85x"]' at column 11`.

Constructed (L8, boundary): C1 characters are ordinary text to the client
and valid UTF-8, so a key holding one is a string the client can write. The
positive controls are an ASCII key and a non-control non-ASCII key, which
already read back.

Marked `xfail(strict=True)` so the graders branch stays green; M11-25
deletes the marker when the printed path reads back (for example by
spelling C1 bytes as `\\194\\133`).
"""

from __future__ import annotations

from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli

DOC = (
    b"\r\nC1DB = {\r\n"
    b'["plain"] = 11,\r\n'
    b'["e\xc3\xa9"] = 12,\r\n'  # e + U+00E9 as raw UTF-8, not a control character
    b'["nel\\194\\133x"] = 13,\r\n'  # U+0085 NEL
    b'["csi\\194\\155x"] = 14,\r\n'  # U+009B CSI
    b"}\r\n"
)


@pytest.fixture(autouse=True)
def _user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)


def _round_trip(tmp_path: Path, value: str) -> tuple[str, int, str, str]:
    """(printed path, exit, stdout, stderr) of pasting the path `sv dump`
    prints for the leaf holding `value` back into `--path`."""
    f = tmp_path / "C1.lua"
    f.write_bytes(DOC)
    runner = CliRunner()
    dump = runner.invoke(cli.app, ["sv", "dump", str(f)])
    assert dump.exit_code == 0, dump.stderr
    (line,) = [ln for ln in dump.stdout.split("\n") if ln.endswith(f" = {value}")]
    path = line[: -len(f" = {value}")]
    again = runner.invoke(cli.app, ["sv", "dump", str(f), "--path", path])
    return path, again.exit_code, again.stdout, again.stderr


@pytest.mark.parametrize("value", ["11", "12"], ids=["ascii", "non-ascii-letter"])
def test_positive_control_printed_path_reads_back_constructed(tmp_path: Path, value: str) -> None:
    path, code, out, err = _round_trip(tmp_path, value)
    assert (code, out) == (0, f"{path} = {value}\n"), err


@pytest.mark.xfail(strict=True, reason="M11-25: a C1 key's printed path does not read back")
@pytest.mark.parametrize("value", ["13", "14"], ids=["nel-u0085", "csi-u009b"])
def test_c1_control_key_printed_path_reads_back_constructed(tmp_path: Path, value: str) -> None:
    path, code, out, err = _round_trip(tmp_path, value)
    assert (code, out) == (0, f"{path} = {value}\n"), (path, err)
