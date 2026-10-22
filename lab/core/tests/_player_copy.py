"""Comparing text output with the library's notes (M16-15).

Text output says the first `[verify]` of a run as "(not yet confirmed)" and
leaves later ones out (`wowlab_core.cli._player_copy`); `--json` keeps every
marker. A test that checks a note's words in text output compares both sides
with `unmarked`, which takes out the marker and its rendering alike, so the
test does not depend on which note of the run came first.
"""

from __future__ import annotations

import re

_MARKER = re.compile(
    r"[ \t]?(?:\(\*{0,2}\[verify\]\*{0,2}\)|\*{0,2}\[verify\]\*{0,2}|\(not yet confirmed\))"
)


def unmarked(text: str) -> str:
    """`text` without `[verify]` markers or their "(not yet confirmed)"."""
    return _MARKER.sub("", text)
