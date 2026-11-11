# Probe from review of m16/86-undo-plan-rollback; reproduces the UI's killed-run note telling the player to look for "state open" in undo's plan, which no longer prints the state's name
"""The page's sentence beside a confirmed run that was killed (`ui._KILLED_NOTE`,
§16.1 as amended 2026-10-19) tells the player to show `undo`'s plan and
confirm it only if the record it names is this command's, "(state open)".
M16-86 changed the plan's header to say the state in words ("did not
finish"), so the word the note sends the player to find is not in the plan.

Constructed (boundary): an `addon install` record whose state is edited to
`open` in the journal, as `test_m16_13_sec_interrupted_probes.py` does, to
stand for a run killed mid-change. No real install is read or written.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401
    ok,
    replayed,  # noqa: F401
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard, snapshot, ui


def _make_last_record_open() -> str:
    last = guard.history()[-1]
    path = snapshot.default_store_path() / "journal" / f"{last.id}.json"
    record = json.loads(path.read_text(encoding="ascii"))
    record["state"] = "open"
    path.write_text(json.dumps(record), encoding="ascii")
    return str(last.id)


def test_the_killed_note_names_a_state_the_undo_plan_shows_constructed(flavor: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    record_id = _make_last_record_open()
    shown = run("undo", input="n\n")
    assert shown.exit_code == 1, shown.output
    header = shown.stdout.splitlines()[0]
    # Positive controls: the plan names the record, the note's first test,
    # and says its state in these words.
    assert header.startswith(f"Undo {record_id} ("), header
    words = cli._journal_state_words("open")
    assert words in header, header
    # The note's second test must be one the plan lets the player make.
    assert words in ui._KILLED_NOTE, (words, ui._KILLED_NOTE)
