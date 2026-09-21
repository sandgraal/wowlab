# Probe from review of m10/08-gamedata-client; reproduces: on a filesystem without hard links, a table another process cached in the publish window is replaced by the loser (L5, ADR-0022). Supersedes test_m10_08_no_hardlink_fallback_overwrites.py.
"""Why this file replaces the first probe: that one landed the winner inside a
patched `Path.rename(tmp -> final)` and then required the winner to survive
the real rename. POSIX rename always replaces, so code that holds L5 must
never make that call, and then the winner never landed and the assertion
could not pass. It graded one mechanism, not the invariant.

This one grades the invariant and nothing else: **if another process's file
appeared under the final name, its bytes are still there afterwards.** How
the library gets there is its business. It may publish some other way, or
refuse the location with a typed `GameDataError`; both pass. What it may not
do is leave a partial file, or the loser's bytes over the winner's.

The filesystem without hard links is simulated at `os.link`, which
`Path.hardlink_to` calls. The winner is landed at the last moment before
each primitive that could put a file under the final name (`os.link`,
`os.rename`, `os.replace`; `Path.rename`, `Path.replace` and `shutil.move`
go through these). All inputs are constructed: this is our own cache, not an
external format (L8). The recorded CSV is served with the recorded
`Content-Disposition`, so the probe does not depend on how a bare
`WagoSource` treats a missing header.
"""

from __future__ import annotations

import errno
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from wowlab_core.gamedata import GameData, GameDataError, WagoSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
TABLE = "ChrClasses"
BUILD = "1.60.1.69876"
CSV_BYTES = (FIXTURES / f"{TABLE}.{BUILD}.csv").read_bytes()
CSV_HEADERS = {
    "content-type": "text/csv; charset=UTF-8",
    "content-disposition": (
        f"attachment; filename=\"{TABLE}.{BUILD}.csv\"; filename*=UTF-8''{TABLE}.{BUILD}.csv"
    ),
}
WINNER = b"ID\nwinner\n"  # constructed: what the other process cached first


def _client(tmp_path: Path) -> tuple[GameData, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recorded(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers=CSV_HEADERS, content=CSV_BYTES)

    source = WagoSource(transport=httpx.MockTransport(recorded), sleep=lambda _s: None)
    return GameData(source, cache_dir=tmp_path / "gamedata"), requests


class _Race:
    """Lands the winner under `final` just before a chosen primitive runs."""

    def __init__(self, final: Path) -> None:
        self.final = final
        self.landed = False

    def land(self) -> None:
        if not self.final.exists():
            self.final.parent.mkdir(parents=True, exist_ok=True)
            self.final.write_bytes(WINNER)
            self.landed = True

    def targets_final(self, dst: Any) -> bool:
        return Path(os.fspath(dst)) == self.final


def _fetch(data: GameData) -> GameDataError | None:
    try:
        data.table(TABLE, BUILD)
    except GameDataError as refused:  # a typed refusal is a legitimate outcome
        return refused
    return None


def _assert_l5(race: _Race, refused: GameDataError | None) -> None:
    if race.landed:
        assert race.final.read_bytes() == WINNER, (
            "a table cached by another process was replaced by the loser of the race (L5)"
        )
    elif refused is None:
        assert race.final.read_bytes() == CSV_BYTES, "published, so it must be the whole body"
    else:
        assert not race.final.exists(), "refused, so nothing may sit under the final name"
    leftovers = (
        [p.name for p in race.final.parent.glob("*.part")] if race.final.parent.exists() else []
    )
    assert leftovers == [], "no temp file may remain"


def test_control_constructed_winner_survives_where_hard_links_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, _requests = _client(tmp_path)
    race = _Race(data.table_path(TABLE, BUILD))
    real_link = os.link

    def winner_lands_just_before_link(src: Any, dst: Any, **kwargs: Any) -> None:
        if race.targets_final(dst):
            race.land()
        real_link(src, dst, **kwargs)

    monkeypatch.setattr(os, "link", winner_lands_just_before_link)
    refused = _fetch(data)

    assert refused is None
    assert race.landed, "the control must actually run the race"
    _assert_l5(race, refused)


def _no_hard_links(_src: Any, _dst: Any, **_kwargs: Any) -> None:
    raise OSError(errno.ENOTSUP, "constructed: filesystem without hard links")


@pytest.mark.skipif(sys.platform == "win32", reason="rename refuses an existing target there")
def test_constructed_winner_landing_before_the_link_attempt_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data, _requests = _client(tmp_path)
    race = _Race(data.table_path(TABLE, BUILD))

    def winner_lands_then_link_is_unsupported(src: Any, dst: Any, **kwargs: Any) -> None:
        if race.targets_final(dst):
            race.land()
        _no_hard_links(src, dst)

    monkeypatch.setattr(os, "link", winner_lands_then_link_is_unsupported)
    _assert_l5(race, _fetch(data))


@pytest.mark.skipif(sys.platform == "win32", reason="rename refuses an existing target there")
@pytest.mark.parametrize("primitive", ["rename", "replace"])
def test_constructed_winner_landing_before_a_rename_onto_the_final_name_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, primitive: str
) -> None:
    data, _requests = _client(tmp_path)
    race = _Race(data.table_path(TABLE, BUILD))
    real: Callable[..., None] = getattr(os, primitive)

    def winner_lands_just_before(src: Any, dst: Any, **kwargs: Any) -> None:
        if race.targets_final(dst):
            race.land()
        real(src, dst, **kwargs)

    monkeypatch.setattr(os, "link", _no_hard_links)
    monkeypatch.setattr(os, primitive, winner_lands_just_before)
    _assert_l5(race, _fetch(data))
