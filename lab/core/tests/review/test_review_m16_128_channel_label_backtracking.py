# Probe from review of m16/128-capture-own-guids-everywhere; reproduces exponential backtracking in CHANNEL_LINK_RE/_LINK_REST_RE on a run of `[a]` labels, and a cross-realm `Name-Realm52:` second speaker that passes.
"""CONSTRUCTED INPUT (L8: hostile-input and boundary cases). Every byte string
here is invented. No test needs or touches a real install.

1. M16-128 let a channel or player link's label be `\\[[^\\]\\r\\n]*\\]` as an
   alternative inside a `(...|[^|\\r\\n]|...)*` loop. `[` is matched by both
   alternatives, so a line holding `||Hchannel:x||h` and then n `[a]` with no
   closer is tried about 2^n ways: n=22 took 2.2 s in `Identity.scrub` on the
   reviewer's machine, n=40 (a 130-byte line another player can type into chat)
   would take days. The rule before M16-128 was linear on the same input.
2. The second-speaker rule wants a letter before the colon, so a cross-realm
   speaker whose realm ends in a digit (`Quillmoss-Area52:`) still passes after
   the owner's own speaker link.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SCRIPTS = REPO / "scripts"

_RUN = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    import lab_capture as lc
    n = int(sys.argv[2])
    closer = sys.argv[3] == "1"
    line = b'x = "||Hchannel:x||h' + b"[a]" * n + (b"||h" if closer else b"") + b'"\\n'
    lc.Identity(own_characters=[("Thrallmar", "Area 52")]).scrub(line)
    """
)


def _scrub_finishes(n: int, *, closer: bool, seconds: float = 20.0) -> bool:
    try:
        subprocess.run(
            [sys.executable, "-c", _RUN, str(SCRIPTS), str(n), "1" if closer else "0"],
            check=True,
            timeout=seconds,
        )
    except subprocess.TimeoutExpired:
        return False
    return True


def test_constructed_positive_control_closed_label_run_is_fast() -> None:
    assert _scrub_finishes(40, closer=True)


def test_constructed_unclosed_label_run_does_not_backtrack_exponentially() -> None:
    assert _scrub_finishes(40, closer=False), "scrub did not finish in 20 s on a 130-byte line"


sys.path.insert(0, str(SCRIPTS))
import lab_capture  # noqa: E402

GUILD = b"|Hchannel:GUILD|h[Guild]|h "
OWN_LINK = b"|Hplayer:Thrallmar-Area52:3:GUILD|h[Thrallmar]|h"


def _chat(line: bytes) -> list[str]:
    identity = lab_capture.Identity(own_characters=[("Thrallmar", "Area 52")])
    problems = identity.scrub(b'x = "' + line + b'"\n').problems
    return [p for p in problems if p.startswith(lab_capture.OTHER_CHAT_LABEL)]


def test_constructed_positive_control_plain_second_speaker_refuses() -> None:
    assert _chat(GUILD + OWN_LINK + b": ok Quillmoss-Stormrage: secret")


def test_constructed_cross_realm_second_speaker_with_digit_realm_refuses() -> None:
    assert _chat(GUILD + OWN_LINK + b": ok Quillmoss-Area52: secret")
