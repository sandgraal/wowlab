# Probe from review of m13/40-check-wiki-crash; reproduces a crash after the new try block in check_wiki.main exiting 1 with personal data found
"""M13-40: "a crash reads as 'no personal data'". The fix wraps `Checker.run()`
in `try/except Exception`, but `main` then sorts the failures with `_order`,
outside that block. `_order` takes the text after the last `:` of a
failure's location as a line number when `str.isdigit()` holds, and
`isdigit()` is true for characters `int()` rejects, such as `²`. A file named
`x:²` in the wiki is reported as an "unexpected file" at location `x:²`
(`quote` lets the name through), so sorting raises `ValueError`, Python exits
1, and exit 1 is one of the two codes that lets pages out, here with an email
address on `Extra.md` already found. The script's docstring says a crash
exits 2, or 3 after personal data.

Positive control: the same wiki without the extra file exits 3.

Constructed: the small passing wiki of `tests/scripts/test_check_wiki.py`
with one invented page and one invented file name; the address is invented.
A file name holding `:` cannot be created on Windows, so the probe is skipped
there.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "check_wiki.py"
TESTS = ROOT / "tests" / "scripts"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_check_wiki import _build  # noqa: E402

PERSONAL = "# Extra\n\nWrite to jane.doe@example.org.\n"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="':' is not allowed in a Windows file name"
)


def _exit_code(root: Path) -> int:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, check=False
    )
    return result.returncode


def test_positive_control_personal_data_exits_3(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki", Extra=PERSONAL)
    assert _exit_code(root) == 3


def test_a_file_name_ending_in_a_superscript_digit_still_exits_3(tmp_path: Path) -> None:
    root = _build(tmp_path / "wiki", Extra=PERSONAL)
    (root / "x:\u00b2").write_text("constructed\n", encoding="utf-8")
    # Expected 3 (personal data found), or at least 2 (a crash). Exits 1.
    assert _exit_code(root) == 3
