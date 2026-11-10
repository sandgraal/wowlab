# Probe from review of m16/04-snap-export-import; reproduces: `_check_end_record` reads the
# Zip64 record only when the end record holds 0xFFFF/0xFFFFFFFF, but `zipfile` reads it
# whenever its locator is present, so an unmarked end record bypasses the pre-check.
"""Constructed (L8 hostile/boundary). A zip of many tiny entries is rewritten
so its classic end record claims one entry and a 46-byte directory (no Zip64
markers), while a Zip64 locator and record just before it give the true
count and size. `_check_end_record` checks only the classic values and lets
it through; CPython's `zipfile._EndRecData64` follows the locator whatever
the classic fields say, so `ZipFile` is built and parses every entry before
`_check_entries` refuses the count. The fix's own test pins "ZipFile built
0 times" for an over-count archive; this one is built once.

Positive control: the same archive with the classic record left honest (its
real count and directory size) is refused before `ZipFile` is built.
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import pytest

from wowlab_core import snaparchive
from wowlab_core.snaparchive import ARCHIVE_COMMENT, ArchiveError
from wowlab_core.snapshot import SnapshotStore

_N = 200


def _archive(tmp_path: Path, *, honest: bool) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for i in range(_N):
            zf.writestr(f"objects/{i:064x}", b"")
    raw = buf.getvalue()
    eocd = struct.Struct("<4s4H2LH")
    _, _, _, _, count, cd_size, cd_offset, _ = eocd.unpack(raw[-eocd.size :])
    assert count == _N
    body = raw[: cd_offset + cd_size]
    zip64_at = len(body)
    zip64 = struct.pack(
        "<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, cd_size, cd_offset
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, zip64_at, 1)
    claimed_count, claimed_size = (count, cd_size) if honest else (1, 46)
    end = eocd.pack(
        b"PK\x05\x06",
        0,
        0,
        claimed_count,
        claimed_count,
        claimed_size,
        cd_offset,
        len(ARCHIVE_COMMENT),
    )
    path = tmp_path / ("honest.zip" if honest else "lying.zip")
    path.write_bytes(body + zip64 + locator + end + ARCHIVE_COMMENT)
    return path


def _built(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    built: list[int] = []
    real = zipfile.ZipFile

    class Spy(real):  # type: ignore[misc,valid-type]
        def __init__(self, *a: object, **k: object) -> None:
            built.append(1)
            super().__init__(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(snaparchive.zipfile, "ZipFile", Spy)
    return built


def test_an_unmarked_end_record_with_a_zip64_locator_is_refused_before_zipfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _archive(tmp_path, honest=False)
    built = _built(monkeypatch)
    with pytest.raises(ArchiveError):
        snaparchive.import_snapshot(
            SnapshotStore(tmp_path / "store"), path, max_entries=10, max_directory_bytes=4096
        )
    assert built == [], "zipfile parsed the whole directory the pre-check was to bound"


def test_positive_control_an_honest_end_record_is_refused_before_zipfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _archive(tmp_path, honest=True)
    built = _built(monkeypatch)
    with pytest.raises(ArchiveError):
        snaparchive.import_snapshot(
            SnapshotStore(tmp_path / "store"), path, max_entries=10, max_directory_bytes=4096
        )
    assert built == []
