"""M13-63: the personal-data matchers also see percent-decoded text.

Every input is `constructed`: the small passing wiki of `test_check_wiki.py`
with one invented line added, or an invented string handed to the matcher
or the diagnosis redactor. The account number `123456789`, the BattleTag
`Brakka#1234` and the address `someone@example.org` are invented.

Before M13-63 `check_privacy` and `wiki_diagnose.Redactor.line` read raw
text and `_resolve` decoded a link target once, for the withhold decision
only: `[x](123456789%231)` was withheld but exited 1, `[x](123456789%25231)`
printed the digits, and `123456789%231` as plain text exited 0.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_check_wiki import _build, _load, cw

wd = _load("wiki_diagnose")

NUMBER = "123456789"
ONCE = f"{NUMBER}%231"  # `#` encoded once
TWICE = f"{NUMBER}%25231"  # and twice
ACCOUNT_LINE = f"Extra.md:3: a numeric account folder {cw.PERSONAL_SUFFIX}"
PAGE_MISSING = "Extra.md:3: link to a page that does not exist (target withheld)"
ANCHOR_MISSING = "Extra.md:3: link to an anchor that does not exist (anchor withheld)"


def _encode(text: str, times: int) -> str:
    """`text` with every `#` percent-encoded `times` times."""
    return text.replace("#", "%" + "25" * (times - 1) + "23")


def _main(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str]]:
    code = cw.main([str(root)])
    return code, capsys.readouterr().out.splitlines()


def _no_digits(lines: list[str]) -> None:
    for line in lines:
        message = re.sub(r"^Extra\.md:\d+: ", "", line)
        assert not re.search(r"\d", message), line
        assert NUMBER not in line, line


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (f"[x]({ONCE})", [PAGE_MISSING, ACCOUNT_LINE]),
        (f"[x]({TWICE})", [PAGE_MISSING, ACCOUNT_LINE]),
        (f"[x](Home#{_encode('#' + NUMBER + '#1', 2)})", [ANCHOR_MISSING, ACCOUNT_LINE]),
        (ONCE, [ACCOUNT_LINE]),
        (TWICE, [ACCOUNT_LINE]),
        (f"See `{TWICE}` here.", [ACCOUNT_LINE]),
        (f"{NUMBER}\\%231", [ACCOUNT_LINE]),
        (f"{NUMBER}%2523%31", [ACCOUNT_LINE]),
    ],
    ids=[
        "link-once",
        "link-twice",
        "anchor-twice",
        "text-once",
        "text-twice",
        "code-twice",
        "markdown-escaped-percent",
        "mixed-depths",
    ],
)
def test_constructed_an_encoded_account_folder_exits_personal_without_quoting_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], line: str, expected: list[str]
) -> None:
    root = _build(tmp_path / "wiki", Extra=f"# Extra\n\n{line}\n")
    code, out = _main(root, capsys)
    assert code == cw.EXIT_PERSONAL
    assert sorted(out) == sorted(expected)
    _no_digits(out)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("someone%40example.org", "an email address"),
        ("someone%2540example.org", "an email address"),
        ("Brakka%231234", "a BattleTag"),
        ("Brakka%25231234", "a BattleTag"),
        ("%2FUsers%2Fsomeone%2FGames", "an absolute home path"),
        ("WTF%2FAccount%252F123456789", "a numeric account folder"),
        # FULLWIDTH NUMBER SIGN (U+FF03) encoded as UTF-8 bytes: NFKC after decoding.
        (f"{NUMBER}%EF%BC%831", "a numeric account folder"),
    ],
)
def test_constructed_each_matcher_sees_the_decoded_text(text: str, kind: str) -> None:
    assert cw.personal_kinds(text) == [kind]


@pytest.mark.parametrize("times", [1, 2, 3, cw.MAX_PERCENT_DECODES])
def test_constructed_decoding_repeats_up_to_the_cap(times: int) -> None:
    assert cw.personal_kinds(_encode(f"{NUMBER}#1", times)) == ["a numeric account folder"]


def test_constructed_decoding_stops_at_the_cap() -> None:
    """One level past the cap is not decoded: the cap is real, and bounds the work."""
    assert cw.personal_kinds(_encode(f"{NUMBER}#1", cw.MAX_PERCENT_DECODES + 1)) == []
    # A long, deeply nested line returns (linear in the cap, not the depth).
    deep = "%" + "25" * 50_000 + "23"
    assert cw.personal_kinds(deep) == []


def test_constructed_a_match_counts_once_across_the_decoded_forms() -> None:
    assert cw.personal_kinds(f"{ONCE} and {NUMBER}#1") == ["a numeric account folder"] * 2


@pytest.mark.parametrize(
    "line",
    [
        "[Getting Started](Getting%20Started#install)",
        "About 50% of runs; 100%.",
        "A stray %zz and %2 and %%.",
        "`/path/to/World%20of%20Warcraft` is a placeholder.",
    ],
)
def test_constructed_ordinary_percent_text_still_passes(tmp_path: Path, line: str) -> None:
    root = _build(tmp_path / "wiki", Extra=f"# Extra\n\n{line}\n")
    assert [str(f) for f in cw.check(root).failures] == []
    assert cw.main([str(root)]) == cw.EXIT_OK


@pytest.mark.parametrize("token", [ONCE, TWICE])
def test_constructed_the_diagnosis_redactor_withholds_an_encoded_account(token: str) -> None:
    out = wd.Redactor().line(f"the model wrote {token} into a page")
    assert NUMBER not in out
    assert "[withheld: a numeric account folder]" in out
    assert out.startswith("the model wrote ")
