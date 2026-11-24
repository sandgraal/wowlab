"""The Crew tab's one-click download of the game's map table (M22-22).

When `crew scan-log` says the map table is missing, the tab offers one
button; it runs `db2 fetch` for the `Map` table of the read's flavor, as
every other leaf runs, then reads the logs again, which checks what the
earlier read kept against the table.

CONSTRUCTED INPUT, as `test_ui_crew`: a copy of the captured install
`fixtures/macos-70888/` with a constructed crew book and a constructed
combat log (`parser/_world_lines.py`), driven under Node through the real
server and the real CLI. The shim is `test_ui_confirm_writes`'s with one
line more, which gives the CLI the owner's game id (the captured install's
lab-addon records hold none, as `test_crew_world` sets it). The map table
is the real recording `fixtures/wago/Map.1.60.1.71037.csv`, written into
the redirected data directory's gamedata cache between two steps under the
captured install's version (a test placement: the table is that of the
next build), so the download is answered from the cache: no request
leaves the machine.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_ui_crew as crew_tab
from test_ui_confirm_writes import _SHIM, _serving
from test_ui_crew import Crew, _bodies, _drive

from wowlab_core import install as discovery

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _crew_build import ACCOUNT, FLAVOR, KEY_A, book, install, player
from _world_lines import FOE_A, FOE_D, OWNER_GUID, hits_you, log, you_hit, zone

MAP_TABLE = Path(__file__).resolve().parent / "fixtures" / "wago" / "Map.1.60.1.71037.csv"
_OWNER_LINE = (
    # No request may leave the machine: any HTTP send in the CLI fails.
    "import httpx as _httpx\n"
    "def _no_network(*a, **k):\n"
    "    raise RuntimeError('a test made a network request')\n"
    "_httpx.Client.send = _no_network\n"
    "import wowlab_core.cli as _cli\n"
    f"_cli._owner_guids = lambda lay, account: ({{{OWNER_GUID!r}: None}}, 0)\n"
)
_ANCHOR = "from wowlab_core.cli import app\n"
_MAPS_STEP = r"""
      } else if (step.maps_button !== undefined) {
        const b = ids["crew-maps-fetch"];
        out.steps.push({ hidden: b.hidden, disabled: b.disabled, label: b.textContent,
          noteHidden: ids["crew-maps-note"].hidden, status: ids["crew-status"].textContent });
"""


def _shim() -> str:
    assert _SHIM.count(_ANCHOR) == 1
    return _SHIM.replace(_ANCHOR, _OWNER_LINE + _ANCHOR, 1)


# The crew harness sends only `crew` runs to the real server; this one
# sends the download too.
_ROUTED = 'body.command[0] === "crew"'
_ROUTED_TOO = '(body.command[0] === "crew" || body.command[0] === "db2")'


def _harness() -> str:
    text = _plain_harness()
    anchor = crew_tab._ANCHOR
    assert text.count(anchor) == 1 and text.count(_ROUTED) == 1
    text = text.replace(_ROUTED, _ROUTED_TOO, 1)
    return text.replace(anchor, _MAPS_STEP.lstrip("\n") + anchor, 1)


_plain_harness = crew_tab._harness


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Crew]:
    monkeypatch.delenv(discovery.ENV_FLAVOR, raising=False)
    monkeypatch.setattr(crew_tab, "_harness", _harness)
    folder = tmp_path / "m22-22"
    folder.mkdir()
    root = install(folder, book({KEY_A: player(met=2)}))
    monkeypatch.setenv(discovery.ENV_ROOT, str(root))
    logs = root / FLAVOR / "Logs"
    logs.mkdir(exist_ok=True)
    (logs / "WoWCombatLog-040126_021630.txt").write_bytes(
        log(
            zone(5, instance=33, name="Shadowfang Keep", difficulty=0),
            you_hit(FOE_D, 10),
            hits_you(FOE_D, 11),
            zone(400, instance=0, name="Silverpine Forest", difficulty=0),
            you_hit(FOE_A, 1000),
        )
    )
    data = folder / "userdata"
    argv = folder / "argv.jsonl"
    context = _serving(wowlab_argv=[sys.executable, "-c", _shim(), str(data), str(argv)])
    server = context.__enter__()
    try:
        yield Crew(server, root, data, argv)
    finally:
        context.__exit__(None, None, None)


def test_constructed_the_map_table_offer_downloads_it_and_reads_again(
    served: Crew, tmp_path: Path
) -> None:
    (flavor,) = discovery.discover(served.root).flavors
    assert flavor.version is not None
    cached = served.data / "gamedata" / "tables" / flavor.version / "Map.csv"
    steps: list[dict[str, Any]] = [
        {"crew_tab": True},
        {"maps_button": True},
        {"crew_click": "crew-scan-log"},
        {"maps_button": True},
        {"crew_write": str(cached), "b64": _b64(MAP_TABLE.read_bytes())},
        {"crew_click": "crew-maps-fetch"},
        {"maps_button": True},
    ]
    cached.parent.mkdir(parents=True, exist_ok=True)
    out = _drive(served, tmp_path / "node", steps)
    _, before, scanned, offered, _, fetched, after = out["steps"]
    assert before["hidden"] is True
    assert _bodies(scanned)[0] == {
        "command": ["crew", "scan-log"],
        "params": {"account": ACCOUNT, "flavor": FLAVOR},
    }
    assert offered["hidden"] is False and offered["noteHidden"] is False
    # The read's own map line follows its sentence in the tab.
    assert "is not downloaded" in offered["status"]
    page = (crew_tab._TEMPLATES / "page.html").read_text(encoding="utf-8")
    assert (
        '<p id="crew-maps-note" class="names-note" hidden>Downloads the game\'s public map table '
        "from wago.tools, sending your game version.</p>" in page
    )
    assert (
        '<button id="crew-maps-fetch" type="button" hidden>Download the game\'s map table</button>'
        in page
    )
    bodies = _bodies(fetched)
    assert len(bodies) == 3, (after["status"], served.argvs())  # fetch, read, list
    assert bodies[0] == {"command": ["db2", "fetch"], "params": {"table": "Map", "flavor": FLAVOR}}
    assert bodies[1] == {
        "command": ["crew", "scan-log"],
        "params": {"account": ACCOUNT, "flavor": FLAVOR},
    }
    assert after["hidden"] is True and after["noteHidden"] is True
    assert "removed 1 that was in a dungeon, raid, battleground or arena" in after["status"]
    # The second read checked the first read's two encounters and removed
    # the one in Shadowfang Keep.
    report = served.report(("crew", "scan-log"), {"account": ACCOUNT, "flavor": FLAVOR})
    assert report["map_table"] is True
    world = served.report(
        ("crew", "matches"), {"account": ACCOUNT, "flavor": FLAVOR, "world": True}
    )
    assert [e["zone"] for e in world["encounters"]] == ["Silverpine Forest"]
    fetches = [a for a in served.argvs() if a[:2] == ["db2", "fetch"]]
    assert len(fetches) == 1 and "Map" in fetches[0]


def test_constructed_a_table_that_cannot_be_read_is_not_offered_for_download(
    served: Crew, tmp_path: Path
) -> None:
    (flavor,) = discovery.discover(served.root).flavors
    assert flavor.version is not None
    cached = served.data / "gamedata" / "tables" / flavor.version / "Map.csv"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"ID,InstanceType\n33,1,extra\n")  # CONSTRUCTED: malformed
    steps: list[dict[str, Any]] = [
        {"crew_tab": True},
        {"crew_click": "crew-scan-log"},
        {"maps_button": True},
    ]
    out = _drive(served, tmp_path / "node", steps)
    _, _, offered = out["steps"]
    assert offered["hidden"] is True and offered["noteHidden"] is True
    assert "downloaded but cannot be read" in offered["status"]
    assert not [a for a in served.argvs() if a[:2] == ["db2", "fetch"]]


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")
