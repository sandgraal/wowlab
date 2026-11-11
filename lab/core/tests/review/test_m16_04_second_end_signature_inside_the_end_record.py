# Probe from review of m16/04-snap-export-import; reproduces: `_check_end_record` ignores the
# end record's disk fields, where a second end signature can sit; `zipfile` takes the last
# signature (`rfind`), so it reads a directory whose size the pre-check never bounded.
"""Constructed (L8 hostile/boundary). A zip of many tiny entries is rewritten
so its end record's two disk fields spell the end-record signature
`PK\\x05\\x06`. `_check_end_record` reads the record at its fixed place, does
not look at the disk fields, and finds a 1-entry, 46-byte directory: within
bounds. CPython's `zipfile._EndRecData` takes the *last* signature in the
file's tail, four bytes later, whose directory-size field is this record's
offset field: here the true directory, so `ZipFile` is built and parses all
of it. The offset field is any 32-bit number the file is long enough for,
so with a large enough archive that directory can be far over
`MAX_DIRECTORY_BYTES`.

Positive control: the same archive with honest fields and zero disk fields
is refused before `ZipFile` is built.
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
_EOCD = struct.Struct("<4s4H2LH")


def _archive(tmp_path: Path, *, hostile: bool) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for i in range(_N):
            zf.writestr(f"objects/{i:064x}", b"")
    raw = buf.getvalue()
    _, _, _, _, count, cd_size, cd_offset, _ = _EOCD.unpack(raw[-_EOCD.size :])
    body = raw[: cd_offset + cd_size]
    if hostile:
        # disk = b"PK", cd_disk = b"\x05\x06": a second signature at +4, whose
        # directory size is the offset field below and whose own place is
        # 4 bytes past the end of the true directory.
        disk, cd_disk = 0x4B50, 0x0605
        end = _EOCD.pack(b"PK\x05\x06", disk, cd_disk, 1, 1, 46, cd_size + 4, len(ARCHIVE_COMMENT))
    else:
        end = _EOCD.pack(
            b"PK\x05\x06", 0, 0, count, count, cd_size, cd_offset, len(ARCHIVE_COMMENT)
        )
    path = tmp_path / ("hostile.zip" if hostile else "honest.zip")
    path.write_bytes(body + end + ARCHIVE_COMMENT)
    return path


def _parsed(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    infos: list[int] = []
    real = zipfile.ZipInfo

    class Spy(real):  # type: ignore[misc,valid-type]
        __slots__ = ()

        def __init__(self, *a: object, **k: object) -> None:
            infos.append(1)
            super().__init__(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(zipfile, "ZipInfo", Spy)
    return infos


def test_a_second_end_signature_in_the_disk_fields_is_refused_before_zipfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _archive(tmp_path, hostile=True)
    infos = _parsed(monkeypatch)
    with pytest.raises(ArchiveError):
        snaparchive.import_snapshot(
            SnapshotStore(tmp_path / "store"), path, max_entries=10, max_directory_bytes=4096
        )
    assert len(infos) == 0, f"zipfile parsed {len(infos)} directory entries past the pre-check"


def test_positive_control_honest_fields_are_refused_before_zipfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _archive(tmp_path, hostile=False)
    infos = _parsed(monkeypatch)
    with pytest.raises(ArchiveError):
        snaparchive.import_snapshot(
            SnapshotStore(tmp_path / "store"), path, max_entries=10, max_directory_bytes=4096
        )
    assert len(infos) == 0
