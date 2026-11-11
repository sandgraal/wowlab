"""The player wording M16-69 adds to `undo` and `doctor` (LAB_PLAN §6.10,
amendments of 2026-11-11 (a) and (a1)): what `undo` says after it closes a
change that stopped before it changed any file, and how `doctor` names such
a change. Constructed records only (boundary: the words, not the gate; the
gate's behaviour is graded in `test_guard_cleanup_crash_m16_93.py`)."""

from __future__ import annotations

from wowlab_core import cli, guard

D = "00000001-0123abcd"
K = "00000002-4567cdef"


def _record(record_id: str, *, undoes: str | None = None, state: str = "rolled_back") -> object:
    return guard.HistoryRecord(
        id=record_id,
        created_at="2026-11-11T00:00:00.000000Z",
        label="constructed",
        snapshot_id="",
        flavor_path="/constructed/World of Warcraft/_flavor_",
        state=state,  # type: ignore[arg-type]
        rolled_back=state == "rolled_back",
        paths=(),
        created_dirs=(),
        temps_removed=("WTF/.wowlab-0123456789abcdef0123456789abcdef.tmp",),
        temps_left=(),
        undoes=undoes,
    )


def test_constructed_closing_a_stopped_change_says_nothing_was_put_back() -> None:
    k = _record(K)
    line = cli._closed_unsnapshotted(k, k)  # type: ignore[arg-type]
    assert line.startswith("Closed; that change stopped before it changed any file")
    assert "wowlab undo --expect" not in line


def test_constructed_closing_a_stopped_undo_names_the_change_it_was_undoing() -> None:
    k = _record(K, undoes=D)
    d = _record(D, state="open")
    line = cli._closed_unsnapshotted(k, d)  # type: ignore[arg-type]
    assert line.startswith("Closed; that undo stopped before it changed any file.")
    assert line.endswith(f"wowlab undo --expect {D}"), "the command stays last"


def test_constructed_doctor_names_a_stopped_change_and_the_undo_that_stopped() -> None:
    stopped = cli._stopped_before_any_file(K)
    assert "stopped before it changed any file" in stopped
    assert stopped.endswith(f"wowlab undo --expect {K}")
    assert stopped.replace(K, "<id>") != cli._interrupted(K).replace(K, "<id>")

    named = cli._interrupted(D, [K])
    assert K in named and named.endswith(f"wowlab undo --expect {D}")
    assert f"wowlab undo --expect {K}" not in named
    assert cli._interrupted(D) == cli._interrupted(D, ()), "no stopped undo: the wording as before"
