"""The world PvP reader on the owner's own newest combat log (M20-18,
docs/LAB_PLAN.md §21.3). Never run in CI (ADR-0012); the owner runs it
after a fight in the open world with `/combatlog` on:

    WOWLAB_WOW_ROOT="/path/to/World of Warcraft" uv run pytest -m live lab/core/tests/test_crew_world_live.py -s -p no:cacheprovider

For every flavor folder with a `Logs/` folder and every game account whose
characters have a game id in the lab-addon's saved file, it reads the
newest combat log with `worldpvp` (read-only; no database is opened, nothing
is written anywhere) and prints counts only: lines, fights, fights outside
the open world, encounters, players per side, deaths and killing blows.
Never a name, a key, a GUID, a zone, an account folder, a flavor folder or
a path. Nothing is asserted about the counts; the test fails only when the
log cannot be read, or the read does not end at a line boundary.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from wowlab_core import combatlog, install, labaddon, layout, worldpvp
from wowlab_core.cli._cmd_log import _logs_dir


def _owners(lay: layout.Layout, account: str) -> dict[str, str | None]:
    owners: dict[str, str | None] = {}
    for c in labaddon.read_all(lay, account=account):
        record = c.record
        if isinstance(record, labaddon.CharDBV5) and isinstance(
            record.identity, labaddon.IdentityV5
        ):
            guid = record.identity.guid
            if isinstance(guid, str):
                owners[guid] = record.identity.realm
    return owners


@pytest.mark.live
def test_live_the_newest_log_reads_as_world_encounters() -> None:
    value = os.environ.get(install.ENV_ROOT, "")
    if not value.strip():
        pytest.skip(f"set {install.ENV_ROOT} to the install to read its newest combat log")
    inst = install.discover(Path(value))
    read = 0
    for f_number, flavor in enumerate(inst.flavors, 1):
        lay = layout.Layout.for_flavor(flavor, inst.root)
        logs = _logs_dir(lay)
        newest = combatlog.newest_log(logs) if logs is not None else None
        if newest is None:
            print(f"flavor#{f_number}: no combat log")
            continue
        size, _ = combatlog.head(newest)
        for a_number, acct in enumerate(lay.wtf_walk().accounts, 1):
            owners = _owners(lay, acct.folder)
            label = f"flavor#{f_number} account#{a_number}"
            if not owners:
                print(f"{label}: no character with a stored game id")
                continue
            started = time.perf_counter()
            scan = worldpvp.read_world(newest, owners)
            took = time.perf_counter() - started
            read += 1
            assert 0 <= scan.end <= size
            foes = [sum(p.side == "foe" for p in e.players) for e in scan.encounters]
            allies = [sum(p.side == "ally" for p in e.players) for e in scan.encounters]
            print(
                f"{label}: {len(owners)} characters with an id; log {size:,} bytes read in "
                f"{took:.1f} s; lines {scan.lines}, unparsed {scan.unparsed}, fights "
                f"{scan.fights}, outside the open world {scan.outside_world}, before a zone "
                f"line {scan.no_zone}, no time {scan.no_time}; encounters "
                f"{len(scan.encounters)} (left out {scan.left_out}), foes per encounter "
                f"{foes}, allies per encounter {allies}, deaths "
                f"{sum(e.died for e in scan.encounters)}, killing blows "
                f"{sum(p.killed_by_you for e in scan.encounters for p in e.players)}, "
                f"zone known {scan.state.zone is not None}, difficulty {scan.state.difficulty}"
            )
    if not read:
        pytest.skip("no combat log and lab-addon game id to read together")
