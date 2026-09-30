"""db2lake's lake: L5, the rebuild, attaching, L1 and the caller's names (M12-03).

The tables are the committed recordings (served through `GameData.table`
into a cache under `tmp_path`), except where a test says `constructed`: a
changed cache file, a hostile name, a synthetic install. Every install here
is a synthetic tree under `tmp_path`; nothing touches the real user data
directory, a real install, or the network (ADR-0012).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

import platformdirs
import pytest

from wowlab_core import db2lake
from wowlab_core.db2lake import (
    Lake,
    LakeError,
    LakeLocationError,
    SourceChanged,
    attach_alias,
    default_lake_dir,
)
from wowlab_core.gamedata import GameData

WAGO = Path(__file__).resolve().parent / "fixtures" / "wago"
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


class _Recordings:
    """Serves `wago/<table>.<build>.csv`; records every request."""

    name = "fixtures"

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def fetch_builds(self, dest: BinaryIO) -> str:
        raise AssertionError("not needed")

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        self.requests.append((table, build))
        with (WAGO / f"{table}.{build}.csv").open("rb") as handle:
            shutil.copyfileobj(handle, dest)
        return f"fixture:{table}.{build}.csv"


def _gamedata(tmp_path: Path, source: _Recordings | None = None) -> GameData:
    return GameData(source or _Recordings(), cache_dir=tmp_path / "cache")


def _lake(tmp_path: Path, build: str = B70058, source: _Recordings | None = None) -> Lake:
    return Lake(build, gamedata=_gamedata(tmp_path, source), lake_dir=tmp_path / "lake")


def _dump(path: Path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return list(connection.iterdump())
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): _sha256(p) if p.is_file() else "dir"
        for p in sorted(root.rglob("*"))
    }


# ── L5 ─────────────────────────────────────────────────────────────────────


def test_a_second_load_is_a_no_op(tmp_path: Path) -> None:
    source = _Recordings()
    with _lake(tmp_path, source=source) as lake:
        first = lake.load("TraitNode")
    before = _sha256(tmp_path / "lake" / f"{B70058}.sqlite")
    with _lake(tmp_path, source=source) as lake:
        second = lake.load("TraitNode")
        again = lake.load("TraitNode")
        rows = list(lake.rows("TraitNode"))
    assert first.loaded and first.fetched
    assert not second.loaded and not second.fetched and not again.loaded
    assert second.table == first.table
    assert len(rows) == first.table.row_count == 558
    assert _sha256(tmp_path / "lake" / f"{B70058}.sqlite") == before, "the file is untouched"
    assert source.requests == [("TraitNode", B70058)], "fetched once"


def test_constructed_changed_source_is_an_error_citing_l5(tmp_path: Path) -> None:
    """Constructed: the cached CSV is rewritten after the load (a cache the
    test owns, under `tmp_path`). The lake refuses to take the new bytes and
    keeps what it loaded."""
    data = _gamedata(tmp_path)
    with Lake(B70058, gamedata=data, lake_dir=tmp_path / "lake") as lake:
        loaded = lake.load("TraitEdge").table
    cached = data.table_path("TraitEdge", B70058)
    original = cached.read_bytes()
    cached.write_bytes(original.replace(b"\n", b"\r\n"))  # same rows, other bytes
    lake_file = tmp_path / "lake" / f"{B70058}.sqlite"
    before = _dump(lake_file)

    with Lake(B70058, gamedata=data, lake_dir=tmp_path / "lake") as lake:
        with pytest.raises(SourceChanged, match=r"L5") as caught:
            lake.load("TraitEdge")
        with pytest.raises(SourceChanged, match=r"L5"):
            lake.rows("TraitEdge")
        with pytest.raises(SourceChanged, match=r"L5"):
            lake.schema("TraitEdge")
    assert caught.value.expected == loaded.sha256
    assert caught.value.found == _sha256(cached)
    assert _dump(lake_file) == before, "nothing in the lake changed"

    cached.write_bytes(original)
    with Lake(B70058, gamedata=data, lake_dir=tmp_path / "lake") as lake:
        assert not lake.load("TraitEdge").loaded
        assert len(list(lake.rows("TraitEdge"))) == 96


def test_constructed_cache_that_differs_from_its_fetch_record_is_not_loaded(
    tmp_path: Path,
) -> None:
    """Constructed: the cached CSV no longer has the SHA-256 its sidecar
    records (edited after the download). Nothing is loaded from it."""
    data = _gamedata(tmp_path)
    cached = data.table("TraitCurrency", B70058)
    assert data.sidecar("TraitCurrency", B70058) is not None
    cached.write_bytes(cached.read_bytes() + b"9999,0,0,0,0,0,0,0\n")
    with Lake(B70058, gamedata=data, lake_dir=tmp_path / "lake") as lake:
        with pytest.raises(SourceChanged, match=r"fetch record.*L5"):
            lake.load("TraitCurrency")
        assert lake.tables() == []


def test_a_table_the_cache_no_longer_holds_stays_as_loaded(tmp_path: Path) -> None:
    """The lake is derived, but it does not refetch to re-check: with the
    cached CSV gone there is nothing to compare, and the load stands."""
    source = _Recordings()
    data = _gamedata(tmp_path, source)
    with Lake(B70058, gamedata=data, lake_dir=tmp_path / "lake") as lake:
        lake.load("TraitTree")
    data.table_path("TraitTree", B70058).unlink()
    with Lake(B70058, gamedata=data, lake_dir=tmp_path / "lake") as lake:
        assert not lake.load("TraitTree").loaded
        assert len(list(lake.rows("TraitTree"))) == 17
    assert source.requests == [("TraitTree", B70058)]


def test_a_deleted_database_is_rebuilt_with_the_same_content(tmp_path: Path) -> None:
    source = _Recordings()
    lake_file = tmp_path / "lake" / f"{B70058}.sqlite"
    with _lake(tmp_path, source=source) as lake:
        for table in TRAIT_TABLES:
            lake.load(table)
        rows = {table: list(lake.rows(table)) for table in TRAIT_TABLES}
    first = _dump(lake_file)
    lake_file.unlink()

    with _lake(tmp_path, source=source) as lake:
        for table in reversed(TRAIT_TABLES):  # order of loading does not matter to content
            assert lake.load(table).loaded
        assert {table: list(lake.rows(table)) for table in TRAIT_TABLES} == rows
    rebuilt = _dump(lake_file)
    assert sorted(rebuilt) == sorted(first)
    assert len(source.requests) == len(TRAIT_TABLES), "rebuilt from the cache, not refetched"


def test_a_lake_that_loaded_nothing_has_no_file(tmp_path: Path) -> None:
    with _lake(tmp_path) as lake:
        assert lake.tables() == []
        assert not lake.path.exists()
    assert not (tmp_path / "lake").exists()


def test_tables_lists_what_was_loaded(tmp_path: Path) -> None:
    with _lake(tmp_path) as lake:
        lake.load("TraitTree")
        lake.load("TraitEdge")
        listed = lake.tables()
    assert [(t.name, t.row_count) for t in listed] == [("TraitEdge", 96), ("TraitTree", 17)]
    assert listed[1].sha256 == _sha256(WAGO / f"TraitTree.{B70058}.csv")


def test_rows_load_on_demand(tmp_path: Path) -> None:
    source = _Recordings()
    with _lake(tmp_path, source=source) as lake:
        assert len(list(lake.rows("TraitCond"))) == 172
        assert [t.name for t in lake.tables()] == ["TraitCond"]
        assert lake.schema("TraitCond").row_count == 172
    assert source.requests == [("TraitCond", B70058)]


def test_fetched_says_whether_the_cache_had_the_table(tmp_path: Path) -> None:
    data = _gamedata(tmp_path)
    data.table("TraitTree", B70058)
    with Lake(B70058, gamedata=data, lake_dir=tmp_path / "lake") as lake:
        assert not lake.load("TraitTree").fetched
        assert lake.load("TraitEdge").fetched


def test_constructed_load_racing_another_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed race: another lake object (another process, in effect)
    loads the table between this one's check and its transaction. Same bytes:
    a no-op. Other bytes: `SourceChanged`, and the first load stands."""
    source = _Recordings()
    real_scan = db2lake._scan
    rival = _lake(tmp_path, source=source)
    pending: list[str] = []

    def scan_then_rival_loads(path: Path) -> db2lake._Scan:
        result = real_scan(path)
        if pending:
            rival.load(pending.pop())  # its own scan runs with nothing pending
        return result

    monkeypatch.setattr(db2lake, "_scan", scan_then_rival_loads)
    pending.append("TraitTree")
    with Lake(B70058, gamedata=_gamedata(tmp_path, source), lake_dir=tmp_path / "lake") as lake:
        result = lake.load("TraitTree")
    assert not pending
    assert not result.loaded
    assert result.table.row_count == 17

    other_cache = GameData(_Recordings(), cache_dir=tmp_path / "other-cache")
    edited = other_cache.table("TraitEdge", B70058)
    edited.write_bytes(edited.read_bytes().replace(b"\n", b"\r\n"))
    edited.with_name(edited.name + ".json").unlink()  # no fetch record to disagree with
    pending.append("TraitEdge")
    with (
        Lake(B70058, gamedata=other_cache, lake_dir=tmp_path / "lake") as lake,
        pytest.raises(SourceChanged, match="another process"),
    ):
        lake.load("TraitEdge")
    assert not pending
    rival.close()
    with _lake(tmp_path, source=source) as lake:
        assert lake.load("TraitEdge").table.sha256 == _sha256(WAGO / f"TraitEdge.{B70058}.csv")


def test_constructed_source_changing_between_the_passes_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed: the cached file changes after the inference pass. The
    insert pass sees other bytes; the transaction rolls back."""
    data = _gamedata(tmp_path)
    cached = data.table("TraitNodeEntry", B70058)
    real_scan = db2lake._scan

    def scan_then_edit(path: Path) -> db2lake._Scan:
        result = real_scan(path)
        path.write_bytes(path.read_bytes() + b"999999,1,1,0,0\n")
        return result

    monkeypatch.setattr(db2lake, "_scan", scan_then_edit)
    with Lake(B70058, gamedata=data, lake_dir=tmp_path / "lake") as lake:
        with pytest.raises(SourceChanged, match="changed"):
            lake.load("TraitNodeEntry")
        assert lake.tables() == []
        assert (
            list(lake.query("SELECT name FROM sqlite_master WHERE name = 'TraitNodeEntry'")) == []
        )
    assert cached.exists()


# ── the caller's names ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(n, id=f"constructed-{i}")
        for i, n in enumerate(
            [
                "",
                "Trait Tree",
                "TraitTree;DROP TABLE x",
                '"TraitTree"',
                "../TraitTree",
                "Trait\x00Tree",
                "Tráit",
                "_lake_tables",
                "_LAKE_META",
                "sqlite_master",
                "SQLITE_sequence",
            ]
        )
    ],
)
def test_a_table_name_is_checked_before_anything_is_asked(tmp_path: Path, name: str) -> None:
    source = _Recordings()
    with _lake(tmp_path, source=source) as lake:
        with pytest.raises(ValueError):
            lake.load(name)
        with pytest.raises(ValueError):
            lake.rows(name)
        with pytest.raises(ValueError):
            lake.schema(name)
    assert source.requests == []
    assert not (tmp_path / "cache").exists()


def test_constructed_a_name_that_differs_only_in_case_is_refused(tmp_path: Path) -> None:
    """SQLite ignores ASCII case in names; `traittree` is not a second table."""
    source = _Recordings()
    with _lake(tmp_path, source=source) as lake:
        lake.load("TraitTree")
        with pytest.raises(LakeError, match="same name"):
            lake.load("traittree")
        with pytest.raises(LakeError, match="same name"):
            lake.rows("TRAITTREE")
    assert source.requests == [("TraitTree", B70058)]


@pytest.mark.parametrize("build", ["1.60.1", "1.60.1.70058.0", "1.60.1.x", "../1.2.3.4", ""])
def test_constructed_a_build_string_is_checked(tmp_path: Path, build: str) -> None:
    with pytest.raises(ValueError, match="build"):
        Lake(build, gamedata=_gamedata(tmp_path), lake_dir=tmp_path / "lake")
    with pytest.raises(ValueError, match="build"):
        attach_alias(build)


# ── attaching ─────────────────────────────────────────────────────────────


def test_attach_is_read_only(tmp_path: Path) -> None:
    source = _Recordings()
    with _lake(tmp_path, B70009, source) as older:
        older.load("TraitEdge")
    other = tmp_path / "lake" / f"{B70009}.sqlite"
    before = _sha256(other)
    with _lake(tmp_path, B70058, source) as lake:
        lake.load("TraitEdge")
        alias = lake.attach(B70009)
        assert lake.attach(B70009) == alias, "attaching again is a no-op"
        with pytest.raises(LakeError, match=r"readonly|read-only|query_only"):
            list(lake.query(f'DELETE FROM "{alias}"."TraitEdge"'))
        with pytest.raises(LakeError, match=r"readonly|read-only|query_only"):
            list(lake.query('DELETE FROM "TraitEdge"'))
        assert list(lake.query(f'SELECT count(*) FROM "{alias}"."TraitEdge"')) == [(96,)]
    assert _sha256(other) == before


def test_attach_refuses_own_build_missing_lake_and_alias_collision(tmp_path: Path) -> None:
    with _lake(tmp_path) as lake:
        lake.load("TraitTree")
        with pytest.raises(LakeError, match="own build"):
            lake.attach(B70058)
        with pytest.raises(LakeError, match="no lake for build"):
            lake.attach(B70009)
    # Constructed builds whose digits are the same: 1.60.1.70009 and 16.0.1.70009.
    with _lake(tmp_path, B70009) as older:
        older.load("TraitTree")
    twin = "16.0.1.70009"
    assert attach_alias(twin) == attach_alias(B70009)
    shutil.copyfile(tmp_path / "lake" / f"{B70009}.sqlite", tmp_path / "lake" / f"{twin}.sqlite")
    with _lake(tmp_path) as lake:
        lake.attach(B70009)
        with pytest.raises(LakeError, match="both attach as b160170009"):
            lake.attach(twin)


def test_constructed_a_file_that_records_another_build_is_refused(tmp_path: Path) -> None:
    """Constructed: a lake file copied under another build's name. It answers
    for the build it was loaded for, nowhere else."""
    with _lake(tmp_path, B70009) as older:
        older.load("TraitTree")
    lake_dir = tmp_path / "lake"
    shutil.copyfile(lake_dir / f"{B70009}.sqlite", lake_dir / "1.2.3.4.sqlite")
    with (
        _lake(tmp_path, "1.2.3.4") as renamed,
        pytest.raises(LakeError, match=r"holds build '1\.60\.1\.70009'"),
    ):
        renamed.tables()
    with _lake(tmp_path) as lake:
        lake.load("TraitTree")
        with pytest.raises(LakeError, match=r"holds build '1\.60\.1\.70009'"):
            lake.attach("1.2.3.4")
        assert list(lake.query("PRAGMA database_list"))[-1][1] == "main", "detached again"


def test_constructed_a_file_that_is_not_a_database_is_reported(tmp_path: Path) -> None:
    lake_dir = tmp_path / "lake"
    lake_dir.mkdir()
    (lake_dir / f"{B70058}.sqlite").write_bytes(b"not a database, just bytes" * 100)
    with _lake(tmp_path) as lake, pytest.raises(LakeError, match="delete it"):
        lake.load("TraitTree")


# ── L1: never inside an install ───────────────────────────────────────────


def _install(root: Path, marker: str = ".build.info") -> Path:
    root.mkdir(parents=True)
    (root / marker).write_text("constructed\n", encoding="utf-8")
    (root / "_flavor_").mkdir()
    return root


@pytest.mark.parametrize("marker", [".build.info", ".flavor.info"])
def test_constructed_a_lake_directory_inside_an_install_is_refused(
    tmp_path: Path, marker: str
) -> None:
    install = _install(tmp_path / "Game", marker)
    before = _files(install)
    for inside in (install, install / "_flavor_" / "db2lake", install / "a" / "b"):
        with pytest.raises(LakeLocationError, match="L1"):
            Lake(B70058, gamedata=_gamedata(tmp_path), lake_dir=inside)
    assert _files(install) == before


@pytest.mark.skipif(sys.platform == "win32", reason="making a link needs a privilege on Windows")
def test_constructed_a_lake_reached_through_a_link_into_an_install_is_refused(
    tmp_path: Path,
) -> None:
    install = _install(tmp_path / "Game")
    before = _files(install)
    linked_dir = tmp_path / "linked-lake"
    linked_dir.symlink_to(install / "_flavor_", target_is_directory=True)
    with pytest.raises(LakeLocationError, match="L1"):
        Lake(B70058, gamedata=_gamedata(tmp_path), lake_dir=linked_dir)

    lake_dir = tmp_path / "lake"
    lake_dir.mkdir()
    (lake_dir / f"{B70058}.sqlite").symlink_to(install / "_flavor_" / "lake.sqlite")
    with _lake(tmp_path) as lake, pytest.raises(LakeLocationError, match="L1"):
        lake.load("TraitTree")

    (lake_dir / f"{B70009}.sqlite").symlink_to(install / "_flavor_" / "old.sqlite")
    (lake_dir / f"{B70058}.sqlite").unlink()
    with _lake(tmp_path) as lake:
        lake.load("TraitTree")
        with pytest.raises(LakeLocationError, match="L1"):
            lake.attach(B70009)
    assert _files(install) == before, "nothing was created in the install"


@pytest.mark.skipif(sys.platform == "win32", reason="making a link needs a privilege on Windows")
def test_constructed_a_symlink_loop_is_refused(tmp_path: Path) -> None:
    loop = tmp_path / "loop"
    loop.symlink_to(tmp_path / "loop")
    with pytest.raises(LakeLocationError, match="cannot be resolved"):
        Lake(B70058, gamedata=_gamedata(tmp_path), lake_dir=loop / "db2lake")


def test_the_default_lake_directory_is_under_user_data() -> None:
    assert default_lake_dir() == platformdirs.user_data_path("wowlab") / "db2lake"


def test_lake_files_are_named_by_build_under_the_lake_directory(tmp_path: Path) -> None:
    with _lake(tmp_path) as lake:
        lake.load("TraitTree")
        assert lake.path == tmp_path / "lake" / f"{B70058}.sqlite"
    names = sorted(p.name for p in (tmp_path / "lake").iterdir())
    assert names == [f"{B70058}.sqlite"], "no journal or temp file left behind"
    connection = sqlite3.connect(tmp_path / "lake" / f"{B70058}.sqlite")
    try:
        meta = dict(connection.execute("SELECT key, value FROM _lake_meta"))
    finally:
        connection.close()
    assert meta == {"format": "1", "build": B70058}


def _all_cells(lake: Lake, table: str) -> Iterator[object]:
    for row in lake.rows(table):
        yield from row.values()


def test_models_serialise_as_json(tmp_path: Path) -> None:
    """Pydantic models cross the module boundary (M12-04 prints them)."""
    with _lake(tmp_path) as lake:
        schema = lake.schema("TraitCurrency")
        result = lake.load("TraitCurrency")
        cells = list(_all_cells(lake, "TraitCurrency"))
    assert json.loads(schema.model_dump_json())["columns"][0] == {"name": "ID", "type": "INTEGER"}
    assert json.loads(result.model_dump_json())["loaded"] is False
    assert all(isinstance(c, int) for c in cells)
    assert os.fspath(lake.lake_dir) == os.fspath(tmp_path / "lake")
