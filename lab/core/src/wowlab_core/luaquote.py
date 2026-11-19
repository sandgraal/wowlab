"""luaquote: a string as a Lua 5.1 literal, for writing into a SavedVariables file.

Shared by the modules that put a new string into a parsed document before
`luadata.serialize` writes it: `svmerge` (a key it adds) and `crewedit` (a
rating or note it sets). Moved here from `svmerge` unchanged (M19-03,
docs/LAB_PLAN.md §20.3). It builds bytes only; it writes nothing (L1) and
runs nothing (L3).
"""

from __future__ import annotations


def lua_quote(data: bytes) -> bytes:
    """A double-quoted Lua 5.1 literal that loads as `data`, for writing
    into a file (other bytes stay raw, as the client writes them)."""
    out = bytearray(b'"')
    for byte in data:
        if byte in (0x22, 0x5C):  # " and backslash
            out += b"\\" + bytes([byte])
        elif byte == 0x0A:
            out += b"\\n"
        elif byte == 0x0D:
            out += b"\\r"
        elif byte < 0x20 or byte == 0x7F:
            out += b"\\%03d" % byte
        else:
            out.append(byte)
    out += b'"'
    return bytes(out)
