# Probe from review of m22/08-addon-lookups; reproduces `lookup --notes` saying "2 entrys of QuestieDB ... were" and "1 entry ... were"
"""The standing note `wowlab lookup` prints (and `--json` carries in
`notes`) when QuestieDB rows could not be decoded is built with `_plural`,
which adds a bare `s`: two rows read "2 entrys of QuestieDB could not be
read and were left out.", one row "1 entry ... and were left out.". The
positive control is that the note is there at all, for one and for two."""

from __future__ import annotations

from wowlab_core.cli._cmd_lookup import _notes
from wowlab_core.lookup import QuestieState


def _note(n: int) -> str:
    notes = [
        x for x in _notes(QuestieState(state="read", unreadable_rows=n)) if "could not be read" in x
    ]
    assert len(notes) == 1, notes  # positive control: the note is printed
    return notes[0]


def test_the_note_is_there_for_one_and_for_two() -> None:
    assert _note(1).startswith("1 ")
    assert _note(2).startswith("2 ")


def test_two_unreadable_rows_are_not_entrys() -> None:
    assert "entrys" not in _note(2), _note(2)


def test_one_unreadable_row_does_not_take_were() -> None:
    assert " were " not in _note(1), _note(1)
