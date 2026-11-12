# Probe from review of m16/109-doctor-generic-answer; reproduces doctor offering `undo --expect` for a change whose pre-write snapshot has lost an object, which undo then refuses.
"""M16-109 (ADR-0037): "a record whose pre-write snapshot cannot be read"
gets the one generic answer, and `doctor` does not offer a command `undo`
refuses. `_snapshot_unreadable` says it judges the snapshot "as `undo` reads
it", but it only loads the manifest (`SnapshotStore.show`). `undo` also reads
every object the journal's `before` names (`guard._object`) before it writes,
and refuses when one is missing. So with the manifest present and its object
gone, `doctor` still prints `wowlab undo --expect <id>` and that undo is
refused by the write gate ("... object ... is missing").

Constructed (L8, boundary): a real `snap restore --paths` through the gate
changes `WTF/Config.wtf` in the captured tree copied into `tmp_path`; then
the record is turned `open` and the object of its pre-write copy deleted.
Nothing reads or writes a real install.

Positive control: `undo` with the object present is not refused (the
branch's own test, `test_a_readable_pre_write_snapshot_keeps_the_undo_line`,
covers the doctor side); here the undo is checked to be refused, and the
doctor answer is expected to be the generic one.
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

from wowlab_core import guard, snapshot


def _set_open(record_id: str) -> None:
    path = snapshot.default_store_path() / "journal" / f"{record_id}.json"
    record = json.loads(path.read_text(encoding="ascii"))
    record["state"] = "open"
    path.write_text(json.dumps(record, sort_keys=True, indent=1) + "\n", encoding="ascii")


def test_a_lost_pre_write_object_gets_the_generic_answer(flavor: Path) -> None:
    ok("snap", "create")
    first = snapshot.SnapshotStore().list()[-1].id
    cfg = flavor / "WTF" / "Config.wtf"
    cfg.write_bytes(cfg.read_bytes() + b'SET constructedM16109 "1"\n')
    ok("snap", "restore", first, "--paths", "WTF/Config.wtf", "--yes")
    last = guard.history()[-1]
    befores = [p.before for p in last.paths if p.before]
    assert befores, "positive control: the change replaced a file, so undo needs its object"
    _set_open(last.id)
    store = snapshot.SnapshotStore()
    store.show(last.snapshot_id)  # the manifest still loads
    for digest in befores:
        store.object_path(digest).unlink()

    refused = run("undo", "--expect", last.id, "--yes")
    assert refused.exit_code != 0, "positive control: undo refuses this change"

    result = run("doctor", "--json")
    report = json.loads(result.stdout)
    assert result.exit_code == 1
    assert not any("undo --expect" in p for p in report["problems"]), (
        f"doctor offers an undo the gate refuses: {report['problems']}"
    )
    assert report.get("cannot_work_out", {}).get("cause") == "snapshot_unreadable", report
