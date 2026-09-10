"""The fixture index must describe exactly the fixtures on disk.

This is the parser suite's day-one test. It exists so that `make test-parser`
is a real check from the first commit rather than an empty job that reports
green, and so that a fixture can never be added without a provenance row.
"""

import re
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "simc"
INDEX = FIXTURE_DIR / "README.md"
REQUIRED_COLUMNS = (
    "file",
    "class",
    "spec",
    "game_version",
    "captured_by",
    "consent",
    "sections",
    "edge_cases",
)


def _index_rows() -> dict[str, list[str]]:
    """Parse the markdown table in the index into {filename: cells}."""
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
        rows[cells[0].strip("`")] = cells
    assert header_seen, "fixture index has no table"
    return rows


@pytest.mark.parser
def test_index_matches_files_on_disk() -> None:
    on_disk = {p.name for p in FIXTURE_DIR.glob("*.simc")}
    indexed = set(_index_rows())
    assert indexed == on_disk, (
        f"unindexed fixtures: {sorted(on_disk - indexed)}; "
        f"indexed but missing: {sorted(indexed - on_disk)}"
    )


@pytest.mark.parser
def test_every_fixture_row_records_provenance_and_consent() -> None:
    for name, cells in _index_rows().items():
        captured_by = cells[REQUIRED_COLUMNS.index("captured_by")]
        consent = cells[REQUIRED_COLUMNS.index("consent")]
        assert captured_by and captured_by != "-", f"{name}: captured_by is required"
        assert consent in {"owner", "explicit", "public-post"}, (
            f"{name}: consent must be owner|explicit|public-post, got {consent!r}"
        )


@pytest.mark.parser
def test_every_fixture_is_a_real_addon_export() -> None:
    """Real exports begin with the addon's header comment and a class-key line."""
    class_keys = {
        "warrior", "paladin", "hunter", "rogue", "priest", "deathknight", "shaman",
        "mage", "warlock", "monk", "druid", "demonhunter", "evoker",
    }  # fmt: skip
    for path in FIXTURE_DIR.glob("*.simc"):
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines and lines[0].startswith("# "), f"{path.name}: missing addon header comment"
        assert any(re.match(rf"^({'|'.join(class_keys)})=", line) for line in lines[:12]), (
            f"{path.name}: no class-key name line in the first 12 lines"
        )
