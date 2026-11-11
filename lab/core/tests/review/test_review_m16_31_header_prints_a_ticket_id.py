# Probe from review of m16/31-character-header; reproduces the Character
# header printing a ticket id ("M15-01") to the player for a schema 1-3 record.
"""M16-31 adds Class and Race to the Character tab's header. With no class
or race id, `namedFact` prints "not recorded (<the identity's _absent
reason>)". For every record of schema 1 to 3 (all the committed captures
except `macos-70450/`, and every player who has not reinstalled the
lab-addon since M15-01), that reason is `labaddon.identity_of`'s "the
record is schema N; identity is recorded from schema 4 (the lab-addon from
M15-01: install it ...)". The `--json` text is exempt from the player-copy
rules, but the page is copy a player reads, and `.claude/rules/player-copy.md`
(owner decision 2026-10-21, Q6) says: never print a ticket id. No CLI text
output shows this reason (`char show`, `char gear`, with or without
`--notes`); the header is its first player-facing surface.

Positive control: the report's `identity.class_id_absent` holds the ticket
id, so the probe's pattern finds it where it is. The probe then asks the
drawn header, as `test_ui_character.py` reads it, to hold none.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_ui_character as character

home = character.home
install = character.install
server = character.server
timed = character.timed

TICKET_ID = re.compile(r"\bM\d{2}-\d{2}T?\b")


def test_the_header_prints_no_ticket_id(server: Any, timed: Path, tmp_path: Path) -> None:
    scenario = character._scenario(server, [{"char_tab": True}], [character.NEWER])
    view = character._drive(tmp_path / "node", scenario)["steps"][0]
    facts = dict(zip(view["headerFacts"][::2], view["headerFacts"][1::2], strict=True))
    shown = {k: v for k, v in facts.items() if TICKET_ID.search(v)}
    assert shown == {}, f"the header prints a ticket id to the player: {shown}"
