# Probe from review of m13/49-scrub-keeps-names; reproduces the by-eye note "Name-Realm-shaped string that is not a pseudonym pair" firing on the owner's own, now public, character-realm pair.
"""Since M13-49 (ADR-0019 amendment, 2026-10-17) character and realm names are
not identifiers and the scrub no longer pseudonymises them. The by-eye note in
`Identity.inspect` still asks the owner to look at every quoted `Name-Realm`
string "that is not a pseudonym pair". No pair is a pseudonym pair any more, so
the note now fires on every such string, the owner's own included, and its
label names a rule that was withdrawn.

Expected: a file whose only names are the owner's own character and realm is
written with no `look` line about a Name-Realm string.

CONSTRUCTED INPUT, invented names, built in `tmp_path` with the tree helper of
tests/scripts/test_lab_capture_names_kept.py. No real install (ADR-0012).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tests" / "scripts"))
from test_lab_capture import _write, capture, outputs  # noqa: E402
from test_lab_capture_names_kept import ACCOUNT, CHARACTER, REALM, _tree  # noqa: E402

BODY = f'\nPairs = {{\n\t["{CHARACTER} - {REALM}"] = 1,\n\t["acct"] = "{ACCOUNT}",\n}}\n'


def _run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, bytes], str]:
    root = tmp_path / "World of Warcraft"
    retail, _forever = _tree(root)
    _write(retail / REALM / CHARACTER / "SavedVariables" / "Pairs.lua", BODY)
    out = tmp_path / "incoming"
    rc = capture(root, out, "--flavor", "_retail_", "--sv", "Pairs.lua")
    return rc, outputs(out), capsys.readouterr().out


def _notes_for(stdout: str, suffix: str) -> list[str]:
    """The indented lines printed under the `wrote` line of the file ending in `suffix`."""
    lines = stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("wrote") and suffix in line)
    notes = []
    for line in lines[start + 1 :]:
        if not line.startswith("  "):
            break
        notes.append(line)
    return notes


def test_constructed_positive_control_the_pair_is_kept_and_the_account_rewritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, written, _stdout = _run(tmp_path, capsys)
    pairs = [data for dest, data in written.items() if dest.endswith("/Pairs.lua")]
    assert rc == 0
    assert pairs == [BODY.replace(ACCOUNT, "90000001#1").encode()]


def test_constructed_own_public_pair_draws_no_name_realm_look_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _rc, _written, stdout = _run(tmp_path, capsys)
    notes = _notes_for(stdout, "/Pairs.lua (")
    assert not [n for n in notes if "Name-Realm" in n], notes
