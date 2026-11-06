# Probe from review of m16/36-coach-inputs; reproduces `binds coach --character X` choosing the log without X's name when X has no record.
"""§16.2 (amended 2026-11-04) and M16-36: the combat log is chosen by the
caster name that matches the chosen character. With `--character` naming a
folder that has no WowLab.lua, the character is still known (the casts are
still counted, the record error is reported), but `_coach_pick_log` gets
`who=None` from the missing target: the log is the newest regardless of
caster, `caster_match` is "no character", and no warning is given although
the newest log's casts are someone else's while an older log holds the
named character's casts.

The install is the captured tree of `test_cli_binds_coach.py` with its real
log (caster `Labchard-…-`); NAMESAKE's WowLab.lua is removed, and a newer
constructed log by another caster is added. Nothing reads or writes a real
install.
"""

# ruff: noqa: F811  (fixtures imported from the coach tests are requested by name)
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli_binds_coach import (
    LOG_NAME,
    SCHEMA_3,
    cast,
    flavor,  # noqa: F401
    report,
    root,  # noqa: F401
    user_dirs,  # noqa: F401
)
from test_cli_binds_coach_inputs import NAMESAKE, NAMESAKE_RECORD, caster_log


def _newer_log_by_someone_else(flavor: Path) -> None:
    caster_log(
        flavor,
        "Someone-Realm-",
        cast(686, "Shadow Bolt"),
        name="WoWCombatLog-060126_000000.txt",
        offset=5,
    )


def test_positive_control_with_a_record(flavor: Path) -> None:
    shutil.copy2(SCHEMA_3, flavor / NAMESAKE_RECORD)
    _newer_log_by_someone_else(flavor)
    rep = report("--character", NAMESAKE)
    assert rep.log.file == f"Logs/{LOG_NAME}"
    assert (rep.log.chosen_by, rep.log.caster_match) == ("caster name", "matches")


def test_character_named_without_a_record_still_chooses_the_log_by_name(flavor: Path) -> None:
    (flavor / NAMESAKE_RECORD).unlink()
    _newer_log_by_someone_else(flavor)
    rep = report("--character", NAMESAKE)
    assert rep.record_error is not None  # the precondition: no record read
    # Expected: the log with the named character's casts, by caster name.
    # Got: the newest log, chosen_by "newest", caster_match "no character",
    # character_name None, no warning.
    assert rep.log.character_name == NAMESAKE.split("-", 1)[0]
    assert rep.log.file == f"Logs/{LOG_NAME}"
    assert (rep.log.chosen_by, rep.log.caster_match) == ("caster name", "matches")
