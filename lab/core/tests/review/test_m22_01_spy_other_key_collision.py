# Probe from review of m22/01-spy-reader; reproduces spy._plain / spy._player dropping a value from `other` when a number or boolean key and a string key spell the same text
"""Constructed (not a real capture; invented names). `SpyPlayer.other` is
documented to keep "every field this reader does not know ... as read", but
`_key_text` writes the key `[1]` as the text "[1]" and `[true]` as
"[true]", so a row holding both `[1]` and `["[1]"]` (or `[true]` and
`["[true]"]`) keeps only the later one, at the top of `other` and inside a
nested table alike. The positive control is the same row with keys that do
not collide: every value is kept. Nothing reads or writes an install.
"""

from __future__ import annotations

from wowlab_core import spy

SOURCE = spy.SpySource(file="x/Spy.lua", account="A", character="1/Inventa-Fakerealm")


def _other(body: str) -> dict[str, spy.Plain]:
    data = spy.parse_character(
        f'SpyPerCharDB = {{ version = "1.1", PlayerData = {{ ["Inventa-Fakerealm"] = {{ {body} }} }} }}'.encode(),
        SOURCE,
    )
    (player,) = data.players
    return player.other


def _values(value: object) -> list[object]:
    if isinstance(value, dict):
        return [v for x in value.values() for v in _values(x)]
    if isinstance(value, list):
        return [v for x in value for v in _values(x)]
    return [value]


def test_constructed_control_keys_that_do_not_collide_are_all_kept() -> None:
    other = _other('[2] = "int", ["k"] = "str", [false] = "f", extra = { [3] = "a", ["b"] = "b" }')
    assert sorted(map(str, _values(other))) == ["a", "b", "f", "int", "str"]


def test_constructed_a_number_key_and_its_spelling_as_a_string_are_both_kept() -> None:
    other = _other('[1] = "int", ["[1]"] = "str", extra = { [1] = "a", ["[1]"] = "b" }')
    assert sorted(map(str, _values(other))) == ["a", "b", "int", "str"]


def test_constructed_a_boolean_key_and_its_spelling_as_a_string_are_both_kept() -> None:
    other = _other('[true] = "t", ["[true]"] = "st"')
    assert sorted(map(str, _values(other))) == ["st", "t"]
