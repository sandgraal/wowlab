"""maptable: the game's `Map` table as the open-world rule reads it (M22-22).

`worldpvp.in_open_world` tells the open world from dungeons, raids and
battlegrounds by each map's `InstanceType` in the build-keyed `Map` table
(ADR-0022). This module reads that table from the `gamedata` cache for one
game version, for `crew scan-log`, `live recap` and the Live view alike, so
the three say the same thing about it. It never fetches: `wowlab db2 fetch
Map` downloads the table, and a table already cached is never replaced
(L5), so a cached table that cannot be read is said as such
(`unreadable`), never as missing: downloading it again would change
nothing.

Kept apart from `worldpvp`, `live` and `crewdb`, which import no network
module: this one imports `gamedata` (only its cache reader is used).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wowlab_core import gamedata, worldpvp

__all__ = ["MapTable", "MapTableState", "load"]

MapTableState = Literal["read", "missing", "unreadable", "no_version"]


class MapTable(BaseModel):
    """The game's map table for one game version: `read` (cached and read:
    `maps` holds each map's `InstanceType` by id), `missing` (not cached for
    `version`), `unreadable` (cached, but it cannot be read or holds no map;
    `detail` says why) or `no_version` (the game's version is unknown, so no
    table can be looked for)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: MapTableState
    version: str | None = None
    maps: dict[int, int] = Field(default_factory=dict, repr=False)
    detail: str | None = None
    path: str | None = None  # the cached file, when there is one

    def usable(self) -> dict[int, int] | None:
        """The maps for `worldpvp.in_open_world`, or None when the table was
        not read (the difficulty decides then)."""
        return self.maps if self.state == "read" else None


def load(version: str | None, *, cache_dir: Path | None = None) -> MapTable:
    """The `Map` table cached for exactly `version`, read; never fetched
    and never written."""
    if version is None:
        return MapTable(state="no_version")
    try:
        rows = gamedata.cached_rows(worldpvp.MAP_TABLE, version, cache_dir=cache_dir)
    except ValueError as exc:  # a version that is not a full build string
        return MapTable(state="no_version", version=version, detail=str(exc))
    if rows is None:
        return MapTable(state="missing", version=version)
    base = cache_dir if cache_dir is not None else gamedata.default_cache_dir()
    path = str(base / "tables" / version / f"{worldpvp.MAP_TABLE}.csv")
    try:
        maps = worldpvp.map_types(rows)
    except (OSError, ValueError, gamedata.GameDataError) as exc:
        return MapTable(state="unreadable", version=version, detail=str(exc), path=path)
    if not maps:
        return MapTable(state="unreadable", version=version, detail="it holds no map", path=path)
    return MapTable(state="read", version=version, maps=maps, path=path)
