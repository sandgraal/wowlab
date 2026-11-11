# Probe from review of m16/45-addon-schema5; reproduces a schema-5 bindings record judged incomplete in a debug session and complete outside it, for the same gather, because the reader still counts `issecretvalue` errors that the addon's `dropped` leaves out.
"""Schema 5 (round 2) writes `dropped` with `droppedCount`, which leaves out
the `issecretvalue` probe's own errors and the CLICK frame's `GetAttribute`
probe: when `issecretvalue` raises on a value, `clean` already counts that
value as secret on the asking API's probe, so the error would count it twice;
and a CLICK frame that fails is counted in `clicks_left_out`, not dropped.

In a debug session the record also carries `probes`, and
`labaddon.dropped_words` reads those instead of `dropped`. It leaves out
`GetAttribute` but still counts `issecretvalue` errors. So one CLICK frame
whose `type` attribute `issecretvalue` could not test (Actions.lua:
`clean(isSecret, secret, attributeProbe, kind)` adds 1 to the
`issecretvalue` probe's errors and 1 to `GetAttribute`'s secret, and the
command goes to clicks_left_out) gives:

- outside debug: `dropped = 0`, so `dropped_words` is empty (complete);
- in debug: "1 call(s) raised an error", so the bindings record is
  incomplete and `binds coach` cannot judge.

The record is the owner's real schema-4 capture (`macos-70450/`) moved to
schema 5 the way the branch's own `test_labaddon_schema_5_m16_45.py` does;
the probe counts are constructed (boundary input, L8). The positive control
is the same gather outside a debug session.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from wowlab_core import labaddon, luadata

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SCHEMA_4 = (
    FIXTURES
    / "macos-70450/forever/WTF/Account/90000001#6/1/Sangraal-Sett/SavedVariables/WowLab.lua"
)


def _schema_5(*, debug: bool) -> dict[str, Any]:
    values = luadata.parse(SCHEMA_4.read_bytes()).to_python()
    raw = copy.deepcopy(values[labaddon.CHAR_VARIABLE])
    raw["schema"] = 5
    for key in ("spellbook", "actionbars", "bindings"):
        if not debug:
            raw[key].pop("probes", None)
        raw[key]["dropped"] = 0  # droppedCount: issecretvalue and GetAttribute left out
    binds = raw["bindings"]
    # One CLICK command, left out because its frame's type was untestable.
    binds["commands"] += 1
    binds["commands_left_out"] += 1
    binds["clicks_left_out"] = 1
    raw["announced"] = 5
    if debug:
        raw["debug"] = True
        probes = binds["probes"]
        for p in probes:
            if p["api"] == "issecretvalue":
                p["calls"] += 1
                p["errors"] += 1
        probes.insert(
            -2,
            {
                "api": "GetAttribute",
                "present": True,
                "calls": 1,
                "errors": 0,
                "answered": 0,
                "secret": 1,
            },
        )
    return raw


def _bindings(debug: bool) -> labaddon.Bindings:
    record = labaddon.load_char(_schema_5(debug=debug))
    assert isinstance(record, labaddon.CharDBV5)
    assert isinstance(record.bindings, labaddon.BindingsV5)
    return record.bindings


def test_positive_control_outside_debug_nothing_dropped() -> None:
    assert labaddon.dropped_words(_bindings(debug=False)) == []


def test_debug_session_judges_the_same_gather_alike() -> None:
    # Fails on the branch: ['1 call(s) raised an error and 0 value(s) were
    # dropped as secret or untestable'].
    assert labaddon.dropped_words(_bindings(debug=True)) == []
