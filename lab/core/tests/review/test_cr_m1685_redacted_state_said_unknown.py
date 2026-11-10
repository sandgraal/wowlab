# Probe from review of m16/85-doctor-state-words; reproduces `doctor --redact` text saying a known journal state is "in a state this version of wowlab does not know" when a redacted name matches the state's text.
"""`--redact` replaces names inside `store.last_transaction.state` (it is a
REDACT field in doctorredact) before `_print_doctor` turns the state into
words, so a `--redact-name` (or a character, realm, account or host name)
equal to a word of a state name turns the state into `<NAME>` or
`rolled_<NAME>`, and the text then says the record is in a state this
version does not know: a false statement, while the problem line, computed
before redaction, still says the change did not finish.

Constructed (L8, boundary): the journal is a single record returned in place
of `guard.history`, as `test_doctor_state_words.py` does. Nothing reads or
writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401
    replayed,  # noqa: F401
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_doctor_state_words import WORDS, _journal, _store_line

UNKNOWN = "in a state this version of wowlab does not know"


def test_positive_control_redact_with_an_unrelated_name_says_the_words_constructed(
    monkeypatch: pytest.MonkeyPatch, flavor: Path
) -> None:
    _journal(monkeypatch, flavor, "open")
    line = _store_line(run("doctor", "--redact-name", "Guildname").stdout)
    assert f"({WORDS['open']};" in line, line


@pytest.mark.parametrize(
    ("state", "name"),
    [("open", "Open"), ("committed", "committed"), ("rolled_back", "back")],
)
def test_a_redacted_name_does_not_make_a_known_state_unknown_constructed(
    monkeypatch: pytest.MonkeyPatch, flavor: Path, state: str, name: str
) -> None:
    _journal(monkeypatch, flavor, state)
    line = _store_line(run("doctor", "--redact-name", name).stdout)
    assert UNKNOWN not in line, line
    assert f"({WORDS[state]};" in line, line
