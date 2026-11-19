# Probe from review of m19/10-addon-crew-book; reproduces the printed-field scan passing a stored note printed raw as plural()'s count
"""CONSTRUCTED INPUT: a mutated copy of the real `Crew.lua`, written under
`tmp_path`; the tree is not changed and no Lua is run (L3).

`test_crew_book_source_scan_every_printed_field_is_flattened` says every chat
line is built only from literals, numbers and `shown(...)`. Its walker skips
the whole argument list of `plural(...)` and checks only that the two words
are literals, so `plural(entry.note, "time", "times")` (which prints
`count .. " " .. many`, the note unflattened) passes the scan. The positive
control (the note printed without `shown`) is refused, as it should be.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[4]
ADDON_TESTS = ROOT / "tests" / "addon"

GROUPED = 'plural(met, "time", "times") .. "."\n    else'
NOTE_SHOWN = '" Your note: " .. shown(entry.note)'


def _scan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, old: str, new: str) -> None:
    monkeypatch.syspath_prepend(str(ADDON_TESTS))
    mod: ModuleType = importlib.import_module("test_crew_book")
    src = mod.CREW.read_text(encoding="utf-8")
    assert src.count(old) == 1, old
    mutated = tmp_path / "Crew.lua"
    mutated.write_text(src.replace(old, new), encoding="utf-8")
    monkeypatch.setattr(mod, "CREW", mutated)
    mod.test_crew_book_source_scan_every_printed_field_is_flattened()


def test_unmutated_copy_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _scan(monkeypatch, tmp_path, GROUPED, GROUPED)


def test_positive_control_a_raw_note_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(AssertionError):
        _scan(monkeypatch, tmp_path, NOTE_SHOWN, '" Your note: " .. entry.note')


def test_a_note_printed_as_plurals_count_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(AssertionError):
        _scan(
            monkeypatch,
            tmp_path,
            GROUPED,
            'plural(entry.note, "time", "times") .. "."\n    else',
        )
