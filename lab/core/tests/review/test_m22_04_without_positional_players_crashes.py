# Probe from review of m22/04-tooltip-record; reproduces `desktable.without` (crew forget --all) failing on an assert when WowLabDeskDB's players is a positional list
"""Constructed (L8: a hostile, hand-edited table; wowlab and the addon
never write one like it).

`desktable.without` says "A table whose `players` is not a table is left as
it is (a forget then finds the name still there and refuses)". A `players`
that is a Lua table of positional values (`{1, 2}`) is a `LuaTable`, so it
passes that check, and a forget of every player drops its entries; but the
check of the result reads the table back through `to_python`, which gives
a list, and `assert isinstance(held, dict)` raises `AssertionError`. Through
the CLI, `crew forget --all` then ends with an uncaught `AssertionError` (no
message, a traceback) instead of a refusal in the player's words; under
`python -O` the assert is gone and `held.items()` raises `AttributeError`.

Expected: either the entries are dropped and the bytes returned, or a
`DeskTableError` (which the CLI turns into a refusal); never another
exception.

Positive control: the same call on a keyed `players` table works.
"""

from __future__ import annotations

from wowlab_core import desktable, luadata

POSITIONAL = b'WowLabDB = {\n}\nWowLabDeskDB = {\n["schema"] = 1,\n["players"] = {1, 2},\n}\n'
KEYED = (
    b'WowLabDB = {\n}\nWowLabDeskDB = {\n["schema"] = 1,\n["players"] = {\n["A-B"] = {},\n},\n}\n'
)


def test_forget_all_of_positional_players_refuses_or_empties() -> None:
    try:
        out = desktable.without(POSITIONAL, None)
    except desktable.DeskTableError:
        return
    assert out.startswith(b"WowLabDB = {\n}\n")
    assert desktable.DESK_VARIABLE in luadata.parse(out).to_python()


def test_positive_control_keyed_players() -> None:
    out = desktable.without(KEYED, None)
    assert b"A-B" not in out and out.startswith(b"WowLabDB = {\n}\n")
