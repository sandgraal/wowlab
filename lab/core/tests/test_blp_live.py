"""`blp` on real textures of the owner's install, by hand (M15-07; ADR-0033
layer c). Never run in CI (ADR-0012):

    WOWLAB_WOW_ROOT=<the install> uv run pytest -m live lab/core/tests/test_blp_live.py -s

The textures are those of the derived fixture
(`fixtures/macos-70959/derived/blp.json`), read through `casc` and decoded
here; skipped when the install has patched past that build. Each decoded
texture's header, format and the MD5 of its RGBA must be the recorded ones
(at least three, of DXT1, DXT5 and palette), each refusal the recorded
one, each PNG the right size, and §17.2's target holds on a real
1024 x 1024 DXT1: at most 0.5 s, best of five. L1: every file opened
read-only (`blp` itself opens nothing).
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import blp, casc, png

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _live_install import live_source

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "macos-70959" / "derived" / "blp.json"
FACTS: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
_WRITING = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_OPENED: list[list[tuple[str, int]]] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event == "open" and _OPENED:
        path, _mode, flags = args
        if isinstance(path, (str, bytes, os.PathLike)):
            _OPENED[-1].append((os.fsdecode(path), flags if isinstance(flags, int) else 0))


sys.addaudithook(_audit)


@pytest.fixture(scope="module")
def source() -> casc.CascSource:
    return live_source(FACTS, "blp")


def _rows() -> list[dict[str, Any]]:
    return [*FACTS["textures"], FACTS["timing"]]


@pytest.mark.live
def test_live_textures_decode_to_the_recorded_pixels(source: casc.CascSource) -> None:
    opened: list[tuple[str, int]] = []
    _OPENED.append(opened)
    decoded: set[str] = set()
    try:
        for row in _rows():
            data = source.read(row["fdid"])
            header = blp.parse_header(data)
            h = row["header"]
            assert (header.encoding, header.alpha_depth, header.alpha_type) == (
                h["encoding"],
                h["alpha_depth"],
                h["alpha_type"],
            )
            assert (header.width, header.height, header.mip_count) == (
                h["width"],
                h["height"],
                h["mip_count"],
            )
            if "refused" in row:
                with pytest.raises(blp.BlpError) as caught:
                    blp.decode(data)
                assert type(caught.value).__name__ == row["refused"]
                continue
            image = blp.decode(data)
            assert image.format == row["format"]
            assert hashlib.md5(image.rgba, usedforsecurity=False).hexdigest() == row["rgba_md5"]
            encoded = png.encode(image.width, image.height, image.rgba)
            assert struct.unpack(">II", encoded[16:24]) == (image.width, image.height)
            decoded.add(image.format)
            print(f"{row['fdid']}: {image.format} {image.width}x{image.height} ok")
    finally:
        _OPENED.remove(opened)
    assert {"dxt1", "dxt5", "palette"} <= decoded
    assert opened and all(flags & _WRITING == 0 for _, flags in opened)


@pytest.mark.live
def test_live_a_real_1024_square_dxt1_decodes_within_half_a_second(
    source: casc.CascSource,
) -> None:
    timing = FACTS["timing"]
    data = source.read(timing["fdid"])
    best = float("inf")
    for _ in range(5):
        started = time.perf_counter()
        image = blp.decode(data)
        best = min(best, time.perf_counter() - started)
    assert (image.format, image.width, image.height) == ("dxt1", 1024, 1024)
    print(f"{timing['fdid']}: 1024x1024 DXT1 in {best:.3f} s (best of 5)")
    assert best <= 0.5
