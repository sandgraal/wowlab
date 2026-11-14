"""`wowlab names fetch` (M18-02): the one download that turns ids into names.

The names of races, classes, specs and professions are read from four game
tables cached for the client's exact version (`gamedata.NAME_TABLES`). This
command downloads whichever of them are missing for each installed
flavor's version, through `gamedata` (the only network client, ADR-0019,
ADR-0022): one request per missing table, keyed by the full build string,
and never a table that is already cached (L5). Nothing else fetches them:
the readers that show names (`char list --summary`, `char gear`, the UI)
only read the cache, and `doctor` and `char list --summary` name this
command when a table is missing.
"""

from typing import Literal

import typer

from wowlab_core import gamedata, install
from wowlab_core.cli._cmd_doctor import (
    _name_words,
    _no_version_cause,
    _open_gamedata,
)
from wowlab_core.cli._common import (
    EXIT_ERROR,
    FlavorOpt,
    JsonOpt,
    RootOpt,
    _discover,
    _emit,
    _given_flavor,
    _handled,
    _Out,
    _say_prose,
    _select_flavor,
    names_app,
)

# ─── names ───────────────────────────────────────────────────────────────────

NamesTableStatus = Literal["cached", "fetched", "not_published", "error", "not_tried"]


class NamesTable(_Out):
    """One name table for one flavor's version: `cached` (it was already
    there, untouched), `fetched` (downloaded now), `not_published` (the
    source has no such table for this version, or does not list the
    version), `error` (the download failed; `detail` says why) or
    `not_tried` (an earlier failure stopped the downloads; `detail` names
    it). `path` is the cached file when there is one."""

    table: str
    status: NamesTableStatus
    path: str | None = None
    detail: str | None = None


class NamesFlavor(_Out):
    """One flavor folder: its version (None when discovery cannot read one:
    `detail` says why and no table is tried) and each name table's status."""

    folder: str
    version: str | None
    tables: list[NamesTable]
    detail: str | None = None


class NamesFetchReport(_Out):
    """`wowlab names fetch --json`: each flavor's name tables, and how many
    downloads were made. The command exits 1 unless every flavor ends with
    all of its name tables cached."""

    flavors: list[NamesFlavor]
    fetched: int


def _fetch_names(
    data: gamedata.GameData,
    f: install.Flavor,
    stopped: list[str],
    unpublished: dict[str, str],
) -> tuple[NamesFlavor, int]:
    """The name tables of one flavor, each downloaded only when missing.
    `stopped` holds the failure that ends every later download (the source
    unreachable, the cache unusable); `unpublished` the versions the source
    does not list, with its words, so no request is repeated for them."""
    if f.version is None:
        cause = _no_version_cause(f.folder, f.product, f.matching_rows)
        return NamesFlavor(folder=f.folder, version=None, tables=[], detail=cause), 0
    version = f.version
    missing = set(gamedata.missing_tables(gamedata.NAME_TABLES, version, cache_dir=data.cache_dir))
    tables: list[NamesTable] = []
    fetched = 0
    for name in gamedata.NAME_TABLES:
        path = data.table_path(name, version)
        if name not in missing:
            tables.append(NamesTable(table=name, status="cached", path=str(path)))
            continue
        if stopped:
            tables.append(NamesTable(table=name, status="not_tried", detail=stopped[0]))
            continue
        if version in unpublished:
            tables.append(
                NamesTable(table=name, status="not_published", detail=unpublished[version])
            )
            continue
        try:
            got = data.table(name, version)
        except gamedata.BuildNotPublished as exc:
            unpublished[version] = str(exc)
            tables.append(NamesTable(table=name, status="not_published", detail=str(exc)))
        except gamedata.TableNotPublished as exc:
            tables.append(NamesTable(table=name, status="not_published", detail=str(exc)))
        except (gamedata.SourceUnavailable, gamedata.CacheLocationError) as exc:
            stopped.append(str(exc))
            tables.append(NamesTable(table=name, status="error", detail=str(exc)))
        except (gamedata.GameDataError, OSError) as exc:
            tables.append(NamesTable(table=name, status="error", detail=str(exc)))
        else:
            fetched += 1
            tables.append(NamesTable(table=name, status="fetched", path=str(got)))
    return NamesFlavor(folder=f.folder, version=version, tables=tables), fetched


def _names_lines(f: NamesFlavor) -> list[str]:
    """What the text says about one flavor: the answer first, then each
    failure with its reason."""
    if f.version is None:
        return [f"Names for {f.folder}: not downloaded, because {f.detail}."]
    head = f"Names for {f.folder} ({f.version})"
    got = [t for t in f.tables if t.status == "fetched"]
    had = [t for t in f.tables if t.status == "cached"]
    failed = [t for t in f.tables if t.status not in ("fetched", "cached")]
    if not got and not failed:
        return [f"{head}: already downloaded; nothing fetched."]
    parts: list[str] = []
    if got:
        parts.append(f"downloaded the names of {_name_words([t.table for t in got])}")
    if had:
        parts.append(f"the names of {_name_words([t.table for t in had])} were already here")
    if failed:
        parts.append(
            f"the names of {_name_words([t.table for t in failed])} could not be downloaded"
        )
    lines = [f"{head}: {'; '.join(parts)}."]
    reasons = list(dict.fromkeys(t.detail for t in failed if t.detail))
    lines += [f"  {reason}" for reason in reasons]
    return lines


@names_app.command(
    "fetch", short_help="Download the tables that name races, classes, specs and professions."
)
@_handled
def names_fetch(
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Download the game tables that name races, classes, specs and
    professions, for each installed flavor's version (or only --flavor's).
    Only the tables not already downloaded are fetched, one request each,
    from wago.tools; a table already downloaded is never replaced. Until
    they are here, wowlab shows ids instead of names.

    \f
    JSON: NamesFetchReport."""
    inst, _ = _discover(root)
    chosen = (
        [_select_flavor(inst, flavor)] if _given_flavor(flavor) is not None else list(inst.flavors)
    )
    flavors: list[NamesFlavor] = []
    fetched = 0
    stopped: list[str] = []
    unpublished: dict[str, str] = {}
    with _open_gamedata() as data:
        for f in chosen:
            row, n = _fetch_names(data, f, stopped, unpublished)
            flavors.append(row)
            fetched += n
    report = NamesFetchReport(flavors=flavors, fetched=fetched)
    if json_out:
        _emit(report)
    else:
        if not flavors:
            _say_prose(f"{inst.root} has no flavor folders; nothing fetched.")
        for row in flavors:
            for line in _names_lines(row):
                indent = line[: len(line) - len(line.lstrip(" "))]
                _say_prose(line.lstrip(" "), indent=indent, hang="    ", keep=[row.folder])
    done = bool(flavors) and all(
        f.version is not None and all(t.status in ("cached", "fetched") for t in f.tables)
        for f in flavors
    )
    if not done:
        raise typer.Exit(EXIT_ERROR)
