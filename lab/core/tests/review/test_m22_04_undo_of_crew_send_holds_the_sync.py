# Probe from review of m22/04-tooltip-record; reproduces `wowlab undo` of a `crew send` holding every later crew sync as a put-back book
"""Constructed (L8: the crew book, matches and Spy file of
`test_crew_send.py`, all invented; the write gate writes into a copy of the
captured install under `tmp_path` only).

`crew send --help` says "`wowlab undo` takes the records out again". The
send's own journal record is exempt from the sync's put-back check
(`crewdb._CREW_WRITE` now lists `send`), because the send leaves the crew
book as it was. Its undo is not: `crewdb._wrote_book` counts every undo
record (`record.undoes is not None`), so after the advertised undo, which
only removes `WowLabDeskDB` and leaves the book byte for byte as the sync
last saw it, the next sync is held as `put_back` ("the book may be older
than the database"), `crew list` reads a held book, and `crew send` itself
refuses until the player runs `crew sync` by hand.

Positive control: the same bytes written under a label that is not a crew
write command's (a merge) are a put-back, so the check itself still works.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_crew_send import _built, _send
from test_crewdb import make  # noqa: F401 - the fixture


def test_undo_of_a_send_leaves_the_next_sync_unheld(make: Any) -> None:  # noqa: F811
    env = _built(make)
    before = env.data()
    done = _send(env)
    result = env.run_bare("undo", "--yes", f"--expect={done.transaction}")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert env.data() == before  # the book is exactly as the sync last saw it
    out = env.sync()
    assert out.held == [], out.words
    result = env.run("crew", "send", "--yes", "--json")
    assert result.exit_code == 0, result.stderr


def test_positive_control_another_label_is_a_put_back(make: Any) -> None:  # noqa: F811
    env = _built(make)
    env.gate("sv merge (1 file)", env.data() + b"\r\n")
    assert env.sync().held == ["put_back"]
