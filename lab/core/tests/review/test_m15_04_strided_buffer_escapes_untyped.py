# Probe from review of m15/04-casc-blte; reproduces a non-contiguous buffer escaping decode_blte as BufferError instead of a CascError
"""Constructed (not a real capture): a valid BLTE seen through a strided
memoryview (every other byte of a doubled buffer). `_octets` promises "a flat
view of unsigned bytes, whatever buffer it is", and `decode_blte` and
`parse_blte` take any `Buffer`; but `_octets` returns a 1-D `B` view as it
is, contiguous or not, and `hashlib.md5` then raises `BufferError`
("underlying buffer is not C-contiguous"), which is not a `CascError`. The
expected behaviour is either a decode (copy the view to contiguous bytes) or
a typed refusal. The positive control is the same bytes through a plain
slice. Nothing reads or writes a real install.
"""

from __future__ import annotations

import hashlib
import struct

import pytest

from wowlab_core import casc

PAYLOAD = b"constructed plain block"


def _blte() -> bytes:
    block = b"N" + PAYLOAD
    row = struct.pack(">II16s", len(block), len(PAYLOAD), hashlib.md5(block).digest())
    return b"BLTE" + struct.pack(">I", 12 + 24) + b"\x0f" + (1).to_bytes(3, "big") + row + block


def test_a_strided_view_decodes_or_is_refused_with_a_casc_error_constructed() -> None:
    data = _blte()
    doubled = bytes(b for x in data for b in (x, 0))
    view = memoryview(doubled)[::2]
    assert bytes(view) == data
    try:
        out = casc.decode_blte(view)
    except casc.CascError:
        return
    assert out == PAYLOAD


def test_positive_control_a_contiguous_slice_decodes_constructed() -> None:
    data = _blte()
    padded = b"xx" + data + b"yy"
    assert casc.decode_blte(memoryview(padded)[2:-2]) == PAYLOAD
    with pytest.raises(casc.CascMalformedError):
        casc.decode_blte(memoryview(padded)[2:-3])
