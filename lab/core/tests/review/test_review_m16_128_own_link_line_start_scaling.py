# Probe from review of m16/128-capture-own-guids-everywhere; reproduces quadratic scrub time on an LF-only file with many own speaker links (line-start rfind for CR scans to the file start).
"""CONSTRUCTED INPUT (L8: boundary case). Every byte string here is invented.
No test needs or touches a real install.

`second_speaker` finds a link's line start with
`max(rfind(b"\\n", ...), rfind(b"\\r", ...))`. In a file with no CR (the usual
SavedVariables on macOS), the CR search walks back to byte 0 for every own
link, so a chat history of n own lines costs O(n^2): 5k lines 0.42 s, 20k
lines 5.5 s on the reviewer's machine (main: 0.08 s and 0.32 s). The same file
with CRLF line ends stays linear, which is the positive control.

A ratio, not an absolute time, so a loaded machine does not flip it: four
times the lines should cost about four times as long, not sixteen.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import lab_capture  # noqa: E402

OWN_LINK = b"|Hplayer:Thrallmar-Area52:3:GUILD|h[Thrallmar]|h"


def _seconds(lines: int, sep: bytes) -> float:
    identity = lab_capture.Identity(own_characters=[("Thrallmar", "Area 52")])
    data = sep.join([b'\t"' + OWN_LINK + b': ok",'] * lines) + sep
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        identity.scrub(data)
        best = min(best, time.perf_counter() - started)
    return best


def _ratio(sep: bytes) -> float:
    return _seconds(16000, sep) / max(_seconds(4000, sep), 1e-6)


def test_constructed_positive_control_crlf_file_scales_linearly() -> None:
    assert _ratio(b"\r\n") < 8.0


def test_constructed_lf_only_file_with_many_own_links_scales_linearly() -> None:
    ratio = _ratio(b"\n")
    assert ratio < 8.0, f"4x the lines cost {ratio:.1f}x the time"
