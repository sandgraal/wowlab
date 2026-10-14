# Probe from review of m12/13-command-reference; reproduces the printed page failing (exit 1, UnicodeEncodeError) when stdout's encoding is not UTF-8
"""With neither `--out` nor `--check`, `scripts/gen_command_reference.py`
prints the page with `sys.stdout.write(rendered)`, so the bytes depend on
the process's stdout encoding and newline translation, not on the page. The
page's own intro holds a non-ASCII character (U+2026 in "uv run wowlab …"),
so under a non-UTF-8 stdout (PYTHONIOENCODING=ascii here; a C/POSIX locale
on older Pythons, a legacy console code page) the script dies with
UnicodeEncodeError, and on Windows text-mode stdout turns every LF into
CRLF. `--out` writes UTF-8 bytes and is unaffected; the printed form should
be the same bytes (`sys.stdout.buffer.write(rendered.encode("utf-8"))`), as
the docstring's "the page is deterministic" implies. `--check`'s diff goes
through the same text stream.

Positive control: with PYTHONIOENCODING=utf-8, the printed bytes are the
page `--out` writes.

Real: runs the script on the real app in a subprocess. Nothing reads or
writes an install.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "gen_command_reference.py"


def _run(encoding: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "PYTHONIOENCODING": encoding}
    env.pop("PYTHONUTF8", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, env=env, check=False
    )


def _page(tmp_path: Path) -> bytes:
    out = tmp_path / "Command-Reference.md"
    done = _run("utf-8", "--out", str(out))
    assert done.returncode == 0, done.stderr
    return out.read_bytes()


def test_positive_control_utf8_stdout_prints_the_page(tmp_path: Path) -> None:
    page = _page(tmp_path)
    done = _run("utf-8")
    assert done.returncode == 0, done.stderr
    assert done.stdout == page


def test_printed_page_does_not_depend_on_stdout_encoding(tmp_path: Path) -> None:
    page = _page(tmp_path)
    done = _run("ascii")
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-300:]
    assert done.stdout == page
