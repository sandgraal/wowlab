# Probe from review of m10/02-lab-capture-followup-4; reproduces another player's name
# surviving in combat-log game text, file written, when the name is a vocabulary word.
"""One defect found by running `process` with `--pseudonymise-other-players` at 3b92084.

Rule 3 (`docs/handoffs/M10-03.md`, "Capture"): the log is refused if one of
another player's real names appears anywhere outside their unit fields.
`OtherUnits.leaks` drops every name part that is in RESERVED_WORDS or
VOCABULARY_PARTNERS before it searches, so a player called `Neutral`,
`Default`, `True`, `Bind`, ... is never searched for: an emote that names
them is written as is, with no refusal and no note. A name that cannot be
searched for safely (`nil`, `player` stand in every log) should refuse the
log, not pass it.

The invented name `Zorvinth` is the positive control: it refuses today.
Constructed boundary input in the docs/LAB_FORMATS.md §8 shape, invented
names only; no install (ADR-0012).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path, PurePosixPath
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "lab_capture.py"


def _load() -> ModuleType:
    name = "lab_capture_review_probe_followup4_vocabulary_name"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()

TS = b"1/1/2026 00:00:00.000-4  "
DUMMY = b'Creature-0-1-2-3-4-0000000000,"Training Dummy",0x10a48,0x0'


def _problems(name: str, tmp_path: Path) -> tuple[list[str], bytes]:
    unit = b'Player-1-00C0FFEE,"' + name.encode() + b'-KestrelHollow-",0x512,0x0'
    emote = f"The Grave Warden glares at {name}!".encode()
    log = (
        TS
        + b"COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1\n"
        + TS
        + b"SWING_DAMAGE,"
        + unit
        + b","
        + DUMMY
        + b",1,-1\n"
        + TS
        + b'EMOTE,Creature-0-1-2-3-4-0000000000,"Grave Warden",0000000000000000,nil,"'
        + emote
        + b'"\n'
    )
    source = tmp_path / "WoWCombatLog-010126_000000.txt"
    source.write_bytes(log)
    rel = PurePosixPath("_classic_beta_/Logs/WoWCombatLog-010126_000000.txt")
    item = lab_capture.Item(source, rel, "_classic_beta_", "1", "combatlog")
    identity = lab_capture.Identity(
        characters=["Orlavin"], realms=["Kestrel Hollow"], guids=["Player-1-0000ABCD"]
    )
    outcome = lab_capture.process(item, identity, None, lab_capture.OtherPlayers())
    return list(outcome.problems), emote


@pytest.mark.parametrize("name", ["Zorvinth", "Neutral", "Default", "Bind", "True"])
def test_constructed_other_players_name_in_game_text_refuses_whatever_the_word(
    name: str, tmp_path: Path
) -> None:
    problems, _emote = _problems(name, tmp_path)
    assert problems, f"a log whose emote names the other player {name!r} was written"
