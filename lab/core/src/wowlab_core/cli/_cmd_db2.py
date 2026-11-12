"""`wowlab db2 builds`, `db2 fetch` and `db2 head` (M16-111)."""

import csv
import io
from pathlib import Path
from typing import Annotated

import typer

from wowlab_core import (
    gamedata,
)
from wowlab_core.cli._cmd_doctor import (
    _no_version_cause,
    _open_gamedata,
)
from wowlab_core.cli._common import (
    EXIT_USAGE,
    CliError,
    FlavorOpt,
    JsonOpt,
    RootOpt,
    _bytes,
    _emit,
    _handled,
    _open,
    _Out,
    _safe,
    _say,
    db2_app,
)

# ─── db2 ─────────────────────────────────────────────────────────────────────


class BuildsReport(_Out):
    """`wowlab db2 builds --json`: the source's listing, as it gives it."""

    products: dict[str, list[gamedata.Build]]


class FetchReport(_Out):
    """`wowlab db2 fetch --json`."""

    table: str
    build: str
    path: str
    sidecar: gamedata.Sidecar | None


class HeadReport(_Out):
    """`wowlab db2 head --json`."""

    table: str
    build: str
    rows: list[dict[str, str]]


BuildOpt = Annotated[
    str | None,
    typer.Option("--build", help="Full version string. Default: the flavor's version."),
]


def _build_for(build: str | None, root: Path | None, flavor: str | None) -> str:
    if build is not None:
        return build
    _, chosen, _ = _open(root, flavor)
    if chosen.version is None:
        raise CliError(_unknown_build(chosen.folder, chosen.product, chosen.matching_rows))
    return chosen.version


def _unknown_build(folder: str, product: str, matching_rows: int) -> str:
    """The `--build` refusal for a flavor whose version is unknown, in the
    three cases `doctor` tells apart (M16-118, M16-121): no product, no
    `.build.info` row for it, a row with a blank version. The no-row case
    points at `doctor`, which says what usually causes it."""
    cause = _no_version_cause(folder, product, matching_rows)
    why = " (wowlab doctor says why)" if product and matching_rows == 0 else ""
    return f"{cause}, so the version of {folder} is unknown{why}; pass --build"


def _check_table_key(data: gamedata.GameData, table: str, build: str) -> None:
    """A malformed table name or build string is a usage error (exit 2), not a
    traceback: `GameData` refuses them before any I/O."""
    try:
        data.table_path(table, build)
    except ValueError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc


@db2_app.command("builds", short_help="Products and versions as wago.tools lists them.")
@_handled
def db2_builds(
    product: Annotated[
        str | None, typer.Option("--product", help="Only this product code.")
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """Products and their versions as wago.tools lists them.

    \f
    JSON: BuildsReport."""
    with _open_gamedata() as data:
        listing = data.builds()
    if product is not None:
        if product not in listing:
            raise CliError(f"wago.tools lists no product {product!r}")
        listing = {product: listing[product]}
    if json_out:
        _emit(BuildsReport(products=listing))
        return
    if product is None:
        for name, builds in listing.items():
            _say(f"{name}: {len(builds)} version(s)")
        _say("(--product P lists one product's versions)")
        return
    for b in listing[product]:
        when = f"  {b.created_at}" if b.created_at else ""
        _say(f"{b.version}{when}")


@db2_app.command("fetch", short_help="Download one table for one version into the cache.")
@_handled
def db2_fetch(
    table: Annotated[str, typer.Argument(help="Table name, e.g. ChrClasses.")],
    build: BuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Download one table for one version into the cache (never replaced once
    there).

    \f
    JSON: FetchReport."""
    version = _build_for(build, root, flavor)
    with _open_gamedata() as data:
        _check_table_key(data, table, version)
        path = data.table(table, version)
        sidecar = data.sidecar(table, version)
    report = FetchReport(table=table, build=version, path=str(path), sidecar=sidecar)
    if json_out:
        _emit(report)
        return
    _say(f"{table} for {version}: {path}")
    if sidecar is not None:
        _say(
            f"  from {sidecar.url} at {sidecar.fetched_at:%Y-%m-%d %H:%M:%S} UTC, {_bytes(sidecar.size)}"
        )


@db2_app.command("head", short_help="The first rows of a table, as CSV.")
@_handled
def db2_head(
    table: Annotated[str, typer.Argument(help="Table name, e.g. ChrClasses.")],
    build: BuildOpt = None,
    rows: Annotated[int, typer.Option("-n", "--rows", min=0, help="How many rows.")] = 10,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """The first rows of a table (fetched first if needed), as CSV.

    \f
    JSON: HeadReport."""
    version = _build_for(build, root, flavor)
    got: list[dict[str, str]] = []
    with _open_gamedata() as data:
        _check_table_key(data, table, version)
        for row in data.rows(table, version):
            if len(got) >= rows:
                break
            got.append(row)
    if json_out:
        _emit(HeadReport(table=table, build=version, rows=got))
        return
    if not got:
        _say("(no rows)")
        return
    # Text for a terminal: every column name and cell is escaped by `_safe`
    # before the CSV is written (the rows come from a community-run service
    # or its cache, ADR-0022); `--json` has the exact values.
    fields = list(got[0])
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([_safe(name) for name in fields])
    writer.writerows([_safe(row[name]) for name in fields] for row in got)
    typer.echo(buffer.getvalue(), nl=False)
