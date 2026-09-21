# Probe from review of m10/08-gamedata-client; reproduces: on a filesystem without hard links, the check-then-rename fallback lets the loser of a race replace a cached table (L5, ADR-0022).
"""`_publish_without_overwrite` falls back to `exists()` then `rename()` when
`hardlink_to` raises `OSError`. On POSIX `rename` replaces its target, so a
table another process publishes between the two calls is overwritten. The
module docstring promises the opposite ("never replaced ... by a process that
lost a race").

Both tests are constructed (L8: our own cache, not an external format). The
interleaving is forced by wrapping the publishing call so the winner's file
appears immediately before it, which is the window in question. The control
runs the same interleaving through the hard-link path, where the winner
survives.
"""

from __future__ import annotations

import errno
import sys
from pathlib import Path

import httpx
import pytest

from wowlab_core.gamedata import GameData, WagoSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
TABLE = "ChrClasses"
BUILD = "1.60.1.69876"
CSV_BYTES = (FIXTURES / f"{TABLE}.{BUILD}.csv").read_bytes()
WINNER = b"ID\nwinner\n"  # constructed: what the other process cached first


def _client(tmp_path: Path) -> GameData:
    def recorded(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/csv; charset=UTF-8"}, content=CSV_BYTES
        )

    source = WagoSource(transport=httpx.MockTransport(recorded), sleep=lambda _s: None)
    return GameData(source, cache_dir=tmp_path / "gamedata")


def test_control_constructed_winner_survives_on_the_hard_link_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _client(tmp_path)
    final = data.table_path(TABLE, BUILD)
    real_link = Path.hardlink_to

    def winner_lands_just_before_link(self: Path, target: Path) -> None:
        if self == final:
            final.write_bytes(WINNER)
        real_link(self, target)

    monkeypatch.setattr(Path, "hardlink_to", winner_lands_just_before_link)
    assert data.table(TABLE, BUILD).read_bytes() == WINNER


@pytest.mark.skipif(sys.platform == "win32", reason="rename refuses an existing target there")
def test_constructed_winner_survives_when_the_filesystem_has_no_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = _client(tmp_path)
    final = data.table_path(TABLE, BUILD)
    real_rename = Path.rename

    def no_hard_links(self: Path, target: Path) -> None:
        raise OSError(errno.ENOTSUP, "constructed: filesystem without hard links")

    def winner_lands_just_before_rename(self: Path, target: Path) -> Path:
        if Path(target) == final:
            final.write_bytes(WINNER)
        return real_rename(self, target)

    monkeypatch.setattr(Path, "hardlink_to", no_hard_links)
    monkeypatch.setattr(Path, "rename", winner_lands_just_before_rename)

    path = data.table(TABLE, BUILD)
    assert path.read_bytes() == WINNER, (
        "a table cached by another process was replaced by the loser of the race (L5)"
    )
