"""Grades for M13-53: `check_wiki` never quotes a bare account number.

Every input is `constructed`: the small passing wiki of `test_check_wiki.py`
with one invented line added. The account number `123456789` is invented.

Two gaps, both pre-existing on `main` before M13-53:

- `[x](123456789#1)` split into a page part `123456789` (no matcher fires on
  it alone) and an anchor, so the "page does not exist" line quoted the
  number into the public log, though the line was flagged personal (exit 3).
- `_ACCOUNT_FOLDER`'s lookbehind excluded `#`, so `#123456789#1` and
  `Home#123456789#1` passed the gate and the anchor message printed them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_check_wiki import _build, _load, cw

wd = _load("wiki_diagnose")

NUMBER = "123456789"
ACCOUNT = f"{NUMBER}#1"
ACCOUNT_LINE = f"Extra.md:3: a numeric account folder {cw.PERSONAL_SUFFIX}"


def _main(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str]]:
    code = cw.main([str(root)])
    return code, capsys.readouterr().out.splitlines()


def _no_digits_of_the_number(lines: list[str]) -> None:
    """No message quotes the number, whole or in part: no digit after `where`."""
    for line in lines:
        message = re.sub(r"^Extra\.md:\d+: ", "", line)
        assert not re.search(r"\d", message), line
        assert NUMBER not in line, line


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (f"[x]({ACCOUNT})", "link to a page that does not exist (target withheld)"),
        (f"[x](#{ACCOUNT})", "link to an anchor that does not exist (anchor withheld)"),
        (f"[x](Home#{ACCOUNT})", "link to an anchor that does not exist (anchor withheld)"),
    ],
    ids=["page-part", "same-page-anchor", "other-page-anchor"],
)
def test_constructed_a_link_to_an_account_folder_is_withheld_and_exits_personal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], line: str, message: str
) -> None:
    root = _build(tmp_path / "wiki", Extra=f"# Extra\n\n{line}\n")
    code, out = _main(root, capsys)
    assert code == cw.EXIT_PERSONAL
    assert sorted(out) == sorted([f"Extra.md:3: {message}", ACCOUNT_LINE])
    _no_digits_of_the_number(out)


@pytest.mark.parametrize(
    "line",
    [f"#{ACCOUNT}", f"Home#{ACCOUNT}", f"See #{ACCOUNT} here."],
    ids=["bare", "after-a-page-name", "in-prose"],
)
def test_constructed_an_account_folder_after_a_hash_is_personal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], line: str
) -> None:
    root = _build(tmp_path / "wiki", Extra=f"# Extra\n\n{line}\n")
    code, out = _main(root, capsys)
    assert code == cw.EXIT_PERSONAL
    assert out == [ACCOUNT_LINE]
    _no_digits_of_the_number(out)


def test_constructed_the_hash_lookbehind_keeps_a_word_boundary() -> None:
    # A digit or letter right before the number still means it is not an
    # account folder on its own; `#` no longer does.
    assert cw.personal_kinds(f"#{ACCOUNT}") == ["a numeric account folder"]
    assert cw.personal_kinds(f"x#{ACCOUNT}") == ["a numeric account folder"]
    assert cw.personal_kinds(f"x{ACCOUNT}") == []
    assert cw.personal_kinds(f"0{ACCOUNT}") == ["a numeric account folder"]  # still 5+ digits


def test_constructed_the_exit_codes_are_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = _build(tmp_path / "clean")
    assert cw.main([str(clean)]) == cw.EXIT_OK == 0
    broken = _build(tmp_path / "broken", Extra="# Extra\n\n[x](Nope)\n")
    assert cw.main([str(broken)]) == cw.EXIT_FAILURES == 1
    assert cw.main([str(tmp_path / "missing")]) == cw.EXIT_ERROR == 2
    personal = _build(tmp_path / "personal", Extra=f"# Extra\n\n#{ACCOUNT}\n")
    assert cw.main([str(personal)]) == cw.EXIT_PERSONAL == 3
    capsys.readouterr()


@pytest.mark.parametrize("text", [f"#{ACCOUNT}", f"see Home#{ACCOUNT} now"])
def test_constructed_the_diagnosis_redactor_withholds_an_account_folder_after_a_hash(
    text: str,
) -> None:
    out = wd.Redactor().line(text)
    assert NUMBER not in out
    assert "a numeric account folder" in out
