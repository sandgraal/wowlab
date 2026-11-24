"""`wowlab char sim-export` with the game's hotfix cache over its talent
table (M22-23), on the `test_cli_char_sim_export.py` setup: a copy of the
`macos-70888` capture in `tmp_path`, the recorded `TraitNodeEntry` of
70888 in a redirected wowlab cache, and no fetch.

The hotfix caches are constructed (L8, labelled): an `XFTH` file in the
copy's `Cache/ADB/enUS/` holding one `TraitNodeEntry` record for the first
talent the record holds, written with the shipped layout (its ID inline),
under the copy's build.
"""

# ruff: noqa: F811  (fixtures imported from test_cli_char_sim_export are requested by name)

from __future__ import annotations

import csv
import sys
from pathlib import Path

from wowlab_core import cli, hotfix, labaddon, simexport

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _hotfix_build import Rec, cache, encode_row
from test_cli_char_sim_export import (  # noqa: F401 - the fixtures are used by name
    BUILD,
    FOREVER,
    _record,
    _tree,
    cached,
    launch,
    run,
    user_data,
)

TABLE = simexport.TALENT_TABLE


def _first_entry(root: Path, folder: Path) -> dict[str, str]:
    """The cached row of the first talent entry the record holds."""
    ranks = simexport.purchased(labaddon.read_char(_record(root)))
    assert isinstance(ranks, list) and ranks
    entry = ranks[0][0]
    with (folder / f"{TABLE}.csv").open(encoding="utf-8", newline="") as handle:
        [row] = [r for r in csv.DictReader(handle) if r["ID"] == str(entry)]
    return row


def _write(root: Path, records: list[Rec]) -> None:
    folder = root / FOREVER / "Cache" / "ADB" / "enUS"
    folder.mkdir(parents=True)
    (folder / hotfix.CACHE_FILE).write_bytes(cache(int(BUILD.rsplit(".", 1)[1]), records))


def _report(launch: Path) -> cli.CharSimExportReport:
    done = run("--json")
    assert done.exit_code == 0, done.output
    return cli.CharSimExportReport.model_validate_json(done.stdout)


def test_a_cached_talent_row_is_used_and_said_constructed(launch: Path, cached: Path) -> None:
    row = _first_entry(launch, cached)
    layout = hotfix.layouts()[TABLE]
    _write(launch, [Rec(TABLE, int(row["ID"]), encode_row(layout, row))])
    before = _tree(launch)
    plain = _report(launch)
    assert plain.hotfix_cache is not None and plain.hotfix_cache.applied
    [counts] = plain.hotfix_cache.tables
    assert (counts.table, counts.replaced, counts.added) == (TABLE, 1, 0)
    done = run()
    assert done.exit_code == 0, done.output
    said = f"Talents read with the game table {TABLE} of {BUILD} and your game's hotfix cache."
    assert said in done.stderr.splitlines()
    assert _tree(launch) == before


def test_a_talent_the_cache_removes_is_left_out_constructed(launch: Path, cached: Path) -> None:
    without = _report(launch).sim.export.get("talents")
    row = _first_entry(launch, cached)
    _write(launch, [Rec(TABLE, int(row["ID"]), status=2)])
    report = _report(launch)
    assert report.hotfix_cache is not None
    assert report.hotfix_cache.tables[0].removed == 1
    assert report.sim.export.get("talents") != without
    assert any("talent" in x.what for x in report.sim.left_out)


def test_a_cache_of_another_build_leaves_the_talents_alone_constructed(
    launch: Path, cached: Path
) -> None:
    without = _report(launch).sim.export
    row = _first_entry(launch, cached)
    folder = launch / FOREVER / "Cache" / "ADB" / "enUS"
    folder.mkdir(parents=True)
    (folder / hotfix.CACHE_FILE).write_bytes(cache(1, [Rec(TABLE, int(row["ID"]), status=2)]))
    report = _report(launch)
    assert report.sim.export == without
    assert report.hotfix_cache is not None and not report.hotfix_cache.applied
    assert report.hotfix_cache.refused is not None
    assert report.hotfix_cache.refused.startswith("the hotfix cache is of build 1 ")
