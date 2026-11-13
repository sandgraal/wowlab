# Probe from review of m16/126-capture-refuses-chat; reproduces another player's plain-text chat-channel line passing the capture when the speaker carries a realm with a space.
"""Review probe for `scripts/lab_capture.py` (M16-126).

CONSTRUCTED INPUT (L8, hostile-input case). Names, realms and messages are
invented; no test needs or touches a real install (ADR-0012).

CHANNEL_SPEAKER_RE takes the plain-text speaker after a `|Hchannel:` link as
`[^\\s:|\\[\\]]+` and then needs `]?:` at once. A cross-realm speaker is
written `Name-Realm`, and many realms hold a space (`Area 52`, the realm the
capture tests themselves use). The name stops at the space, the `:` is not
next, and the line is not counted: someone else's words are captured.
The positive controls (same line, realm without a space) pass today.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "lab_capture.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lab_capture_cr_m16126_probe", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()


def _chat(data: bytes) -> list[str]:
    identity = lab_capture.Identity(own_characters=("Thrallmar",))
    return [p for p in identity.scrub(data).problems if p.startswith(lab_capture.OTHER_CHAT_LABEL)]


@pytest.mark.parametrize(
    "data",
    [
        b'x = "|Hchannel:CHANNEL:2|h[2. Trade]|h Quillmoss-Ashvale: selling flasks"\n',
        b'x = "|Hchannel:GUILD|h[Guild]|h [Quillmoss-Ashvale]: hi"\n',
    ],
    ids=["constructed-control-plain", "constructed-control-bracketed"],
)
def test_control_realm_without_space_is_refused(data: bytes) -> None:
    assert _chat(data)


@pytest.mark.parametrize(
    "data",
    [
        b'x = "|Hchannel:CHANNEL:2|h[2. Trade]|h Quillmoss-Area 52: selling flasks"\n',
        b'x = "|Hchannel:GUILD|h[Guild]|h [Quillmoss-Area 52]: hi"\n',
    ],
    ids=["constructed-plain-realm-with-space", "constructed-bracketed-realm-with-space"],
)
def test_speaker_with_a_realm_holding_a_space_is_refused(data: bytes) -> None:
    assert _chat(data), "another player's chat line passed: realm with a space"
