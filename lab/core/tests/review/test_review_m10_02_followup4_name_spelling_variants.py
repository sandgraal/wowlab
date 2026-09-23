# Probe from review of m10/02-lab-capture-followup-4; reproduces rule 3 missing another
# player's name or realm in a full-case-folded or spaced spelling.
"""Two gaps found by running `process` with `--pseudonymise-other-players` at 3b92084.

`OtherUnits.leaks` promises "Any casing, NFC", but it searches with
`re.IGNORECASE`, which is simple case folding: `Weißbart` does not match
`WEISSBART` (`str.upper` of it; `casefold` makes both `weissbart`). The rest
of the tool compares with `_fold` (NFC + casefold).

An unknown realm is searched only in its unit-name spelling (`GloamSpire`);
the tool's own realms are also searched spaced (REALM_TRANSFORMS,
`_split_camel`), but another player's `Gloam Spire` in game text passes.

`Zorvinth` and the unspaced realm are the positive controls: they refuse
today. Constructed boundary input in the docs/LAB_FORMATS.md §8 shape,
invented names only; no install (ADR-0012).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path, PurePosixPath
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "lab_capture.py"


def _load() -> ModuleType:
    name = "lab_capture_review_probe_followup4_spelling_variants"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()

TS = b"1/1/2026 00:00:00.000-4  "
DUMMY = b'Creature-0-1-2-3-4-0000000000,"Training Dummy",0x10a48,0x0'


def _problems(unit_name: str, text: str, tmp_path: Path) -> list[str]:
    unit = b'Player-77-0DEADBEE,"' + unit_name.encode() + b'",0x548,0x0'
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
        + text.encode()
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
    return list(outcome.problems)


@pytest.mark.parametrize(
    ("unit_name", "text"),
    [
        ("Zorvinth-GloamSpire-US", "The Warden glares at ZORVINTH!"),  # control
        ("Weißbart-GloamSpire-US", "The Warden glares at WEISSBART!"),
        ("Zorvinth-GloamSpire-US", "The Warden of GloamSpire wakes."),  # control
        ("Zorvinth-GloamSpire-US", "The Warden of Gloam Spire wakes."),
    ],
    ids=["control-name", "sharp-s-upper", "control-realm", "realm-spaced"],
)
def test_constructed_other_player_spelling_variant_in_game_text_refuses(
    unit_name: str, text: str, tmp_path: Path
) -> None:
    assert _problems(unit_name, text, tmp_path), "a log naming the other player was written"
