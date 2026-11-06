# Probe from review of m16/16-notes-flag; reproduces the goldens' normaliser turning a backslash a Linux run printed after the sandbox folder into "/" (`_AFTER_PLACEHOLDER`).
"""Constructed strings only (hostile/boundary): no install, no fixture.

`test_cli_notes._placeholders` puts each known path in `/` before its
placeholder goes in, which is exact. Its extra rule, `_AFTER_PLACEHOLDER`,
then rewrites any `\\` right after `<tmp>` or `<package>` that does not look
like an escape. On Linux no known path holds a backslash, so a `\\` there is
one the run printed: output `/tmp/.../t0\\World of Warcraft` (a separator
that is wrong on this platform) compares equal to the right output. The
snapshots prove the text and `--json` (M16-16 acceptance); the normaliser
must not turn a printed byte into another.

Positive control: an escape right after the folder (`\\n`) is kept.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_cli_notes as notes

TMP = Path("/nowhere")


def test_positive_control_an_escape_after_the_folder_is_kept() -> None:
    assert notes._placeholders("/nowhere\\new", TMP, []) == "<tmp>\\new"


def test_a_printed_backslash_after_the_folder_is_kept_constructed() -> None:
    text = "Install: /nowhere\\World of Warcraft (x)"
    assert notes._placeholders(text, TMP, []) == "Install: <tmp>\\World of Warcraft (x)"
