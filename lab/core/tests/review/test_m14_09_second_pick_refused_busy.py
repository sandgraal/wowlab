# Probe from review of m14/09-character-keys-macros; reproduces a second quick
# pick in the Character tab getting `503 busy` for the character now shown.
"""M14-09's one pick handler (`pickCharacter` in page.js) starts three runs at
once for every pick (`char gear`, `char binds`, `char macros`), and a later
pick only stops the earlier pick's replies from being drawn (`seq`): the
earlier runs keep their server slots until they end. The server runs at most
`ui.MAX_CONCURRENT_RUNS` (4) at once and refuses the next with 503 `busy`
(ADR-0029). So two picks less than one run apart (two arrow-key presses in
the focused picker, each a `change`) send six runs: the first pick's three
hold three slots, and two of the second pick's three are refused. The page
draws "Refused: busy" in those panels for the character it now shows, and
choosing the same option again fires no `change`, so nothing retries them.
Before M14-09 a pick was one run and two quick picks were two.

This drives the real server with the exact request bodies the page sends
(`_pick_requests`, as `test_ui_character_panels.py` asserts them) on the
`cross-capture` install of that file. Positive control: the second pick's
three runs alone all succeed.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_ui_character_panels as panels
from test_ui_character import NEWER, _pick_requests
from test_ui_explorer import _send

home = panels.home
install = panels.install
server = panels.server
timed = panels.timed
picked = panels.picked


def _post(server: Any, body: dict[str, Any], out: dict[str, int], tag: str) -> None:
    reply = _send(server, "POST", "/api/run", json.dumps(body).encode("utf-8"))
    out[tag] = reply.status


def _pick(server: Any, character: str, out: dict[str, int], tag: str) -> list[threading.Thread]:
    """One pick, as the page makes it. Before fix round 1 the page started a
    pick's three runs together (this probe started three threads). Since fix
    round 1 `pickCharacter` awaits each run before the next, so a pick is one
    thread posting its three runs in order. That is an upper bound on the
    page: the real page also stops a pick left behind after its current run,
    which this thread does not. Changed by the implementer in fix round 1;
    the assertions are unchanged."""

    def one_after_another() -> None:
        for body in _pick_requests(character):
            _post(server, body, out, f"{tag} {' '.join(body['command'])}")

    thread = threading.Thread(target=one_after_another)
    thread.start()
    return [thread]


def test_one_pick_alone_gets_all_three_runs(server: Any, picked: Path) -> None:
    """Positive control: the second character's three runs, with nothing
    else running, are all served."""
    out: dict[str, int] = {}
    for thread in _pick(server, panels.PICKED, out, "second"):
        thread.join(120)
    assert out == {f"second char {c}": 200 for c in ("gear", "binds", "macros")}


def test_a_second_quick_pick_gets_all_three_runs(server: Any, picked: Path) -> None:
    out: dict[str, int] = {}
    threads = _pick(server, NEWER, out, "first")
    time.sleep(0.3)  # a second arrow-key press; one run takes longer than this
    threads += _pick(server, panels.PICKED, out, "second")
    for thread in threads:
        thread.join(120)
    second = {k: v for k, v in out.items() if k.startswith("second")}
    # Expected: the character now shown gets its gear, keys and macros.
    # Observed: two of the three are 503 busy ("Refused: busy" in the panel).
    assert second == {f"second char {c}": 200 for c in ("gear", "binds", "macros")}, out
