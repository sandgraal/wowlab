"""Grades for M13-53: `check_wiki` never quotes a bare account number.

Every input is `constructed`: the small passing wiki of `test_check_wiki.py`
with one invented line added. The account number `123456789` is invented.

Two gaps, both pre-existing on `main` before M13-53:

- `[x](123456789#1)` split into a page part `123456789` (no matcher fires on
  it alone) and an anchor, so the "page does not exist" line quoted the
  number into the public log, though the line was flagged personal (exit 3).
- `_ACCOUNT_FOLDER`'s lookbehind excluded `#`, so `#123456789#1` and
  `Home#123456789#1` passed the gate and the anchor message printed them.
  `_BATTLETAG` had the same lookbehind (`#Brakka#1234`, round 1 of review),
  and both treated `_` as a word character, so `Page_123456789#1` passed.
  Both now refuse only a letter or a digit before the match.

The BattleTag `Brakka#1234` is invented, as in `test_check_wiki.py`.
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
TAG_NAME, TAG_DIGITS = "Brakka", "1234"
TAG = f"{TAG_NAME}#{TAG_DIGITS}"
TAG_LINE = f"Extra.md:3: a BattleTag {cw.PERSONAL_SUFFIX}"
PAGE_MISSING = "link to a page that does not exist (target withheld)"
ANCHOR_MISSING = "link to an anchor that does not exist (anchor withheld)"


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
        (f"[x]({ACCOUNT})", PAGE_MISSING),
        (f"[x](#{ACCOUNT})", ANCHOR_MISSING),
        (f"[x](Home#{ACCOUNT})", ANCHOR_MISSING),
        (f"[x](Page_{ACCOUNT})", PAGE_MISSING),
    ],
    ids=["page-part", "same-page-anchor", "other-page-anchor", "after-an-underscore"],
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
    [f"#{ACCOUNT}", f"Home#{ACCOUNT}", f"See #{ACCOUNT} here.", f"Page_{ACCOUNT}"],
    ids=["bare", "after-a-page-name", "in-prose", "after-an-underscore"],
)
def test_constructed_an_account_folder_after_a_hash_is_personal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], line: str
) -> None:
    root = _build(tmp_path / "wiki", Extra=f"# Extra\n\n{line}\n")
    code, out = _main(root, capsys)
    assert code == cw.EXIT_PERSONAL
    assert out == [ACCOUNT_LINE]
    _no_digits_of_the_number(out)


def _no_part_of_the_tag(lines: list[str]) -> None:
    """No message quotes the BattleTag or either half of it."""
    for line in lines:
        message = re.sub(r"^Extra\.md:\d+: ", "", line)
        assert not re.search(r"\d", message), line
        assert TAG_NAME not in line, line


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (f"[x]({TAG})", PAGE_MISSING),
        (f"[x](#{TAG})", ANCHOR_MISSING),
        (f"[x](Home#{TAG})", ANCHOR_MISSING),
        (f"[[Home#{TAG}]]", ANCHOR_MISSING),
        (f"[r]: #{TAG}", ANCHOR_MISSING),
        (f"[x](Page_{TAG})", PAGE_MISSING),
    ],
    ids=[
        "page-part",
        "same-page-anchor",
        "other-page-anchor",
        "wikilink",
        "reference-definition",
        "after-an-underscore",
    ],
)
def test_constructed_a_link_to_a_battletag_is_withheld_and_exits_personal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], line: str, message: str
) -> None:
    root = _build(tmp_path / "wiki", Extra=f"# Extra\n\n{line}\n")
    code, out = _main(root, capsys)
    assert code == cw.EXIT_PERSONAL
    assert sorted(out) == sorted([f"Extra.md:3: {message}", TAG_LINE])
    _no_part_of_the_tag(out)


@pytest.mark.parametrize(
    "line",
    [f"#{TAG}", f"Home#{TAG}", f"see #{TAG} here", f"Page_{TAG}"],
    ids=["bare", "after-a-page-name", "in-prose", "after-an-underscore"],
)
def test_constructed_a_battletag_after_a_hash_is_personal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], line: str
) -> None:
    root = _build(tmp_path / "wiki", Extra=f"# Extra\n\n{line}\n")
    code, out = _main(root, capsys)
    assert code == cw.EXIT_PERSONAL
    assert out == [TAG_LINE]
    _no_part_of_the_tag(out)


def test_constructed_the_lookbehind_refuses_only_a_letter_or_a_digit() -> None:
    folder, tag = ["a numeric account folder"], ["a BattleTag"]
    # `#` and `_` before the identifier no longer hide it.
    assert cw.personal_kinds(f"#{ACCOUNT}") == folder
    assert cw.personal_kinds(f"x#{ACCOUNT}") == folder
    assert cw.personal_kinds(f"_{ACCOUNT}") == folder
    assert cw.personal_kinds(f"#{TAG}") == tag
    assert cw.personal_kinds(f"x#{TAG}") == tag
    # A letter right before the number still means it is not an account folder.
    assert cw.personal_kinds(f"x{ACCOUNT}") == []
    # A leading digit matches only because the number is then 10 digits long,
    # and the match starts at that digit, not after it.
    assert cw.personal_kinds(f"0{ACCOUNT}") == folder


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


@pytest.mark.parametrize(
    ("text", "secret", "kind"),
    [
        (f"#{ACCOUNT}", NUMBER, "a numeric account folder"),
        (f"see Home#{ACCOUNT} now", NUMBER, "a numeric account folder"),
        (f"see Page_{ACCOUNT} now", NUMBER, "a numeric account folder"),
        (f"#{TAG}", TAG_NAME, "a BattleTag"),
        (f"see Home#{TAG} now", TAG_NAME, "a BattleTag"),
        (f"see Page_{TAG} now", TAG_NAME, "a BattleTag"),
    ],
)
def test_constructed_the_diagnosis_redactor_withholds_an_identifier_after_a_hash_or_underscore(
    text: str, secret: str, kind: str
) -> None:
    out = wd.Redactor().line(text)
    assert secret not in out
    assert kind in out
