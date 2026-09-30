"""db2lake on the recorded wago.tools tables (M12-03, L8).

Every table here is a committed recording (M12-01, M11-05; the fixture index
rows), served to `gamedata` by a `Source` that copies the recorded bytes, so
the lake loads them exactly as it would load a download: through
`GameData.table`, into a cache under `tmp_path`. Nothing makes a request
(ADR-0012). The lake and the cache live under `tmp_path`; nothing touches the
real user data directory or an install.
"""

from __future__ import annotations

import csv
import hashlib
import shutil
from pathlib import Path
from typing import BinaryIO

import pytest

from wowlab_core.db2lake import Cell, Lake, attach_alias
from wowlab_core.gamedata import GameData, TableNotPublished

pytestmark = pytest.mark.parser

WAGO = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
B70058 = "1.60.1.70058"
B70009 = "1.60.1.70009"
TRAIT_TABLES = (
    "TraitTree",
    "TraitNode",
    "TraitNodeEntry",
    "TraitDefinition",
    "TraitNodeXTraitNodeEntry",
    "TraitEdge",
    "TraitCond",
    "TraitCurrency",
    "TraitTreeXTraitCurrency",
)


def _recording(table: str, build: str) -> Path:
    whole = WAGO / f"{table}.{build}.csv"
    return whole if whole.exists() else WAGO / f"{table}.{build}.subset.csv"


class _Recordings:
    """A `Source` that hands over the recorded bytes, whole tables and
    ID-filtered subsets alike (a subset stands in for the table it was cut
    from). It counts requests so a test can say none was repeated."""

    name = "fixtures"

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def fetch_builds(self, dest: BinaryIO) -> str:
        raise AssertionError("no test here needs the builds listing")

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        self.requests.append((table, build))
        path = _recording(table, build)
        if not path.exists():
            raise TableNotPublished(table, build)
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, dest)
        return f"fixture:{path.name}"


def _lake(tmp_path: Path, build: str, source: _Recordings | None = None) -> Lake:
    data = GameData(source or _Recordings(), cache_dir=tmp_path / "cache")
    return Lake(build, gamedata=data, lake_dir=tmp_path / "lake")


def _csv(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        return header, list(reader)


def _as_csv_cell(value: Cell, kind: str) -> str:
    """What the recorded cell must have been for the lake to hold ``value``."""
    if value is None:
        assert kind in ("INTEGER", "REAL"), "only a numeric column holds NULL"
        return ""
    if kind == "INTEGER":
        assert isinstance(value, int)
        return str(value)
    assert isinstance(value, str) if kind == "TEXT" else isinstance(value, float)
    return value if isinstance(value, str) else repr(value)


@pytest.mark.parametrize("build", [B70058, B70009])
@pytest.mark.parametrize("table", TRAIT_TABLES)
def test_trait_table_loads_with_wagos_columns_and_row_count(
    tmp_path: Path, table: str, build: str
) -> None:
    recording = _recording(table, build)
    header, records = _csv(recording)

    with _lake(tmp_path, build) as lake:
        result = lake.load(table)
        schema = lake.schema(table)
        rows = list(lake.rows(table))

    assert result.loaded and result.fetched
    assert [c.name for c in schema.columns] == header, "wago's names, unchanged, in order"
    assert schema.row_count == len(records) == result.table.row_count == len(rows)
    assert schema.sha256 == hashlib.sha256(recording.read_bytes()).hexdigest()
    assert all(list(row) == header for row in rows)


@pytest.mark.parametrize("build", [B70058, B70009])
@pytest.mark.parametrize("table", TRAIT_TABLES)
def test_trait_table_values_are_the_recorded_cells(tmp_path: Path, table: str, build: str) -> None:
    """Every INTEGER and TEXT cell reads back as the recorded text; the
    recorded Trait tables have no REAL column (see the ChrModel test)."""
    _, records = _csv(_recording(table, build))
    with _lake(tmp_path, build) as lake:
        kinds = {c.name: c.type for c in lake.schema(table).columns}
        rows = list(lake.rows(table))
    assert "REAL" not in kinds.values()
    for record, row in zip(records, rows, strict=True):
        assert [_as_csv_cell(v, kinds[k]) for k, v in row.items()] == record


def test_trait_tree_keeps_the_placeholder_columns(tmp_path: Path) -> None:
    """`Field_10_0_0_45697_006` and `_007` are WoWDBDefs placeholders
    (§14.1); the lake keeps them and types them from the data."""
    with _lake(tmp_path, B70058) as lake:
        kinds = {c.name: c.type for c in lake.schema("TraitTree").columns}
        rows = list(lake.rows("TraitTree"))
    assert kinds["Field_10_0_0_45697_006"] == "INTEGER"
    assert kinds["Field_10_0_0_45697_007"] == "INTEGER"
    assert len(rows) == 17
    assert {row["ID"] for row in rows} >= {1116, 1118, 1187, 1188, 1189}
    by_id = {row["ID"]: row for row in rows}
    assert by_id[1116]["TraitSystemID"] == 10
    assert {by_id[i]["TraitSystemID"] for i in (1118, 1187, 1188, 1189)} == {45}


def test_recorded_all_empty_and_numeric_looking_text_columns(tmp_path: Path) -> None:
    """The ADR-0028 rule on real data, pinned so a change is seen (§14.2
    amendment 2026-09-30): `TitleText_lang` is empty in all 17 rows, so it is
    INTEGER and NULL (every non-empty cell, of which there is none, is an
    integer); `OverrideName_lang` holds one cell, `16972`, and 653 empty
    ones, so it is INTEGER too; the two all-empty `Override*_lang` columns
    likewise."""
    with _lake(tmp_path, B70058) as lake:
        tree = {c.name: c.type for c in lake.schema("TraitTree").columns}
        definition = {c.name: c.type for c in lake.schema("TraitDefinition").columns}
        titles = {row["TitleText_lang"] for row in lake.rows("TraitTree")}
        names = [row["OverrideName_lang"] for row in lake.rows("TraitDefinition")]
    assert tree["TitleText_lang"] == "INTEGER" and titles == {None}
    assert definition["OverrideName_lang"] == "INTEGER"
    assert [n for n in names if n is not None] == [16972]
    assert definition["OverrideSubtext_lang"] == definition["OverrideDescription_lang"] == "INTEGER"


def test_spellname_subset_is_integer_ids_and_text_names(tmp_path: Path) -> None:
    _, records = _csv(_recording("SpellName", B70058))
    with _lake(tmp_path, B70058) as lake:
        schema = lake.schema("SpellName")
        rows = list(lake.rows("SpellName"))
    assert [(c.name, c.type) for c in schema.columns] == [("ID", "INTEGER"), ("Name_lang", "TEXT")]
    assert schema.row_count == len(records) == 523
    assert [[str(r["ID"]), r["Name_lang"]] for r in rows] == records


def test_recorded_float_columns_are_real_and_exact(tmp_path: Path) -> None:
    """ChrModel (M11-05's recording) carries wago's fixed-point floats
    (`1.20000004768`); they are REAL, and each reads back as the nearest
    double to the recorded text. `CustomizeOffset_0` holds only integers at
    this build, so it is INTEGER (the ADR's stated consequence)."""
    header, records = _csv(_recording("ChrModel", B70009))
    with _lake(tmp_path, B70009) as lake:
        kinds = {c.name: c.type for c in lake.schema("ChrModel").columns}
        rows = list(lake.rows("ChrModel"))
    assert kinds["CustomizeScale"] == kinds["CameraDistanceOffset"] == "REAL"
    assert kinds["CustomizeOffset_0"] == "INTEGER"
    assert kinds["ID"] == "INTEGER"
    reals = [name for name, kind in kinds.items() if kind == "REAL"]
    assert len(reals) == 10
    for record, row in zip(records, rows, strict=True):
        cells = dict(zip(header, record, strict=True))
        for name in reals:
            assert row[name] == float(cells[name])


def test_trait_tables_join_inside_one_build(tmp_path: Path) -> None:
    """The talent model's path (§14.3): trees → nodes, in SQL over typed ids."""
    with _lake(tmp_path, B70058) as lake:
        for table in ("TraitTree", "TraitNode"):
            lake.load(table)
        per_tree = dict(
            lake.query(
                'SELECT t."ID", count(n."ID") FROM "TraitTree" t '
                'LEFT JOIN "TraitNode" n ON n."TraitTreeID" = t."ID" GROUP BY t."ID"'
            )
        )
    assert len(per_tree) == 17
    assert sum(v for v in per_tree.values() if isinstance(v, int)) == 558


def test_two_builds_attach_and_join(tmp_path: Path) -> None:
    source = _Recordings()
    with _lake(tmp_path, B70009, source) as older:
        older.load("TraitTree")
        older.load("TraitNode")
    with _lake(tmp_path, B70058, source) as lake:
        lake.load("TraitTree")
        alias = lake.attach(B70009)
        assert alias == attach_alias(B70009) == "b160170009"
        joined = list(
            lake.query(
                f'SELECT a."ID", a."TraitSystemID", b."TraitSystemID" FROM "TraitTree" a '
                f'JOIN "{alias}"."TraitTree" b ON b."ID" = a."ID" ORDER BY a."ID"'
            )
        )
        attached_nodes = list(lake.query(f'SELECT count(*) FROM "{alias}"."TraitNode"'))
    assert len(joined) == 17
    assert all(a == b for _, a, b in joined), "the recorded trees keep their systems"
    assert attached_nodes == [(558,)]
    assert sorted(source.requests) == sorted(
        [("TraitTree", B70009), ("TraitNode", B70009), ("TraitTree", B70058)]
    ), "each table fetched once"
