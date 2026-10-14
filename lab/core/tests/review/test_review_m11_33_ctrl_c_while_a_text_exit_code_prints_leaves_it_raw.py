# Probe from review of m11/33-cli-excepthook; reproduces Ctrl-C while `_WowlabApp.__call__` escapes a text SystemExit code making Python print that code raw
"""`_WowlabApp.__call__` (M11-33, security item B4) prints a `SystemExit`
whose code is text on one escaped line and exits 1, because CPython would
print that text raw. The print is wrapped in `contextlib.suppress(Exception)`
only. A KeyboardInterrupt raised while the text is escaped (Ctrl-C during a
long message) leaves the `except SystemExit` handler, so it skips the
`except BaseException` branch that installs `_excepthook`; the `finally`
puts back the hook from before the call. At the top of the `wowlab` script
that is Python's own hook, which prints the chain: the `SystemExit` line
with its text raw, then the KeyboardInterrupt.

`_excepthook` is guarded against exactly this ("not even on Ctrl-C while it
escapes a long message", docs/LAB_PLAN.md §6.11 amendment of 2026-09-30);
this path is not. The reviewer ran it on 2026-09-30 at 7a6cfc5: stderr held
a raw ESC and a line starting with the text after the code's line feed.

Constructed (hostile-input case, L8): the child replaces `_discover` with a
function raising `SystemExit(<text>)` and `_say_err` with one raising
KeyboardInterrupt, standing for Ctrl-C arriving mid-escape. No text-coded
exit is raised anywhere in `wowlab_core` today, so this is hardening of the
path B4 added. The child never reaches an install: `WOWLAB_WOW_ROOT` points
into `tmp_path` and the replaced function raises before anything is read.

Control: the same detector flags the raw output Python itself produces for
a text `SystemExit` raised outside the app.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ESC = chr(0x1B)
LF = chr(0x0A)
SPOOF = "wowlab: restored 1 file"
TEXT = f"exit text{ESC}[2J{LF}{SPOOF}"

_CHILD_APP = """
import sys
from wowlab_core import cli

def exit_with_text(*args, **kwargs):
    raise SystemExit(TEXT)

def interrupted(text=""):
    raise KeyboardInterrupt  # Ctrl-C while the text is being escaped

cli._discover = exit_with_text
cli._say_err = interrupted
sys.argv = ["wowlab", "install", "show"]
sys.exit(cli.app())
""".replace("TEXT", ascii(TEXT))

_CHILD_CONTROL = """
import sys
sys.exit(TEXT)
""".replace("TEXT", ascii(TEXT))


def _stderr(child_source: str, tmp_path: Path) -> bytes:
    env = {
        **os.environ,
        "WOWLAB_WOW_ROOT": str(tmp_path / "no install"),
        "PYTHONIOENCODING": "utf-8",
    }
    child = subprocess.run(
        [sys.executable, "-c", child_source],
        capture_output=True,
        cwd=tmp_path,
        env=env,
        timeout=120,
        check=False,
    )
    assert child.returncode != 0
    return child.stderr


def _raw(stderr: bytes) -> list[str]:
    """What reached stderr raw: an ESC byte, or a line starting with the
    text that followed the code's line feed."""
    found = []
    if ESC.encode() in stderr:
        found.append("raw ESC")
    lines = stderr.decode("utf-8", "backslashreplace").replace("\r\n", "\n").split("\n")
    if any(line.startswith(SPOOF) for line in lines):
        found.append("spoofed line")
    return found


def test_positive_control_the_detector_flags_pythons_raw_print(tmp_path: Path) -> None:
    assert _raw(_stderr(_CHILD_CONTROL, tmp_path)) == ["raw ESC", "spoofed line"]


def test_ctrl_c_while_a_text_exit_code_is_escaped_prints_nothing_raw_constructed(
    tmp_path: Path,
) -> None:
    raw = _raw(_stderr(_CHILD_APP, tmp_path))
    assert raw == [], f"stderr carried {raw} after Ctrl-C while the exit text was escaped"
