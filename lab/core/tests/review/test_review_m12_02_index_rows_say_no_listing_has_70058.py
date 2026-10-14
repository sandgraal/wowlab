# Probe from review of m12/02-table-not-published; reproduces fixture index rows saying "in no committed listing" for a build the new committed listing lists
"""M12-02 commits `wago/builds.2026-09-30.json.gz`, which lists 1.60.1.70058
under `wow_classic_beta`. The M12-01 rows for the 70058 recordings still give
their product as `n/a (in no committed listing)`, a statement about the
repository that this branch makes false. The 70009 rows show the form a
listed build takes: `n/a (listed under wow_classic_beta)`.

Control: the check reads the real index and the real listings, finds the
70009 rows in the "listed under" form, and flags a constructed row that
claims no listing for a version a committed listing has.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NO_LISTING = "n/a (in no committed listing)"


def _listed_versions() -> set[str]:
    versions: set[str] = set()
    for path in sorted((FIXTURES / "wago").glob("builds*.json.gz")):
        listing = json.loads(gzip.decompress(path.read_bytes()))
        versions |= {b["version"] for builds in listing.values() for b in builds}
    return versions


def _wago_rows() -> list[list[str]]:
    rows = []
    for line in (FIXTURES / "README.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("| `wago/"):
            rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _stale(rows: list[list[str]], listed: set[str]) -> list[str]:
    """Rows whose product cell says no committed listing has their version,
    though one does (cells: file, kind, product, client_version, ...)."""
    return [row[0] for row in rows if row[2] == NO_LISTING and row[3] in listed]


def test_control_the_check_reads_the_index_and_flags_a_stale_row() -> None:
    listed = _listed_versions()
    rows = _wago_rows()
    assert "1.60.1.70009" in listed
    assert any(
        row[3] == "1.60.1.70009" and row[2] == "n/a (listed under wow_classic_beta)" for row in rows
    ), "the 70009 rows show the form a listed build takes"
    constructed = [["`wago/X.1.60.1.70009.csv`", "wago-csv", NO_LISTING, "1.60.1.70009"]]
    assert _stale(constructed, listed) == ["`wago/X.1.60.1.70009.csv`"]


def test_no_index_row_says_no_committed_listing_has_a_listed_build() -> None:
    stale = _stale(_wago_rows(), _listed_versions())
    assert stale == [], (
        f"{len(stale)} index row(s) say {NO_LISTING!r} for a build a committed "
        f"listing lists: {stale}"
    )
