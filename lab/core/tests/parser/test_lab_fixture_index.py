"""The fixture index must describe exactly the fixtures on disk.

This is the parser suite's day-one test. It exists so that `make test-parser`
is a real check from the first commit rather than an empty job that exits 5,
and so that a fixture can never be added without a provenance row (L8).
"""

import re
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
INDEX = FIXTURE_DIR / "README.md"
INCOMING = "incoming"  # gitignored scratch for unreviewed captures
OS_NOISE = frozenset({".DS_Store", "Thumbs.db"})
REQUIRED_COLUMNS = (
    "file",
    "kind",
    "flavor",
    "client_version",
    "platform",
    "captured_by",
    "consent",
    "scrub",
    "edge_cases",
)


def _index_rows() -> dict[str, list[str]]:
    """Parse the markdown table in the index into {path relative to fixtures/: cells}."""
    text = INDEX.read_text(encoding="utf-8")
    rows: dict[str, list[str]] = {}
    header_seen = False
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not header_seen:
            assert tuple(cells) == REQUIRED_COLUMNS, f"index header must be {REQUIRED_COLUMNS}"
            header_seen = True
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue  # separator row
        assert len(cells) == len(REQUIRED_COLUMNS), f"malformed index row: {line!r}"
        name = cells[0].strip("`")
        assert name not in rows, f"{name}: indexed twice"
        rows[name] = cells
    assert header_seen, "fixture index has no table"
    return rows


def _fixtures_on_disk() -> set[str]:
    found: set[str] = set()
    for path in FIXTURE_DIR.rglob("*"):
        if not path.is_file() or path.name in OS_NOISE:
            continue
        rel = path.relative_to(FIXTURE_DIR)
        if rel.parts[0] == INCOMING or rel.as_posix() == INDEX.name:
            continue
        found.add(rel.as_posix())
    return found


@pytest.mark.parser
def test_index_matches_files_on_disk() -> None:
    on_disk = _fixtures_on_disk()
    indexed = set(_index_rows())
    assert indexed == on_disk, (
        f"unindexed fixtures: {sorted(on_disk - indexed)}; "
        f"indexed but missing: {sorted(indexed - on_disk)}"
    )


@pytest.mark.parser
def test_every_fixture_row_records_provenance_consent_and_scrub() -> None:
    for name, cells in _index_rows().items():
        row = dict(zip(REQUIRED_COLUMNS, cells, strict=True))
        for column in ("kind", "flavor", "client_version", "platform", "captured_by", "scrub"):
            assert row[column] and row[column] != "-", f"{name}: {column} is required"
        assert row["consent"] in {"owner", "explicit"}, (
            f"{name}: consent must be owner|explicit, got {row['consent']!r}"
        )
