# Probe from review of m22/18-ui-source-lists; reproduces a `SPELL x`, `ITEM x` or `MACRO x` key binding told as "an addon's key binding: the game's own interface has no such command"
"""CONSTRUCTED INPUT (L8, boundary case): a `bindings-cache.wtf` whose keys
are bound with the client's own non-command binding kinds, `SPELL <name>`,
`ITEM <name>` and `MACRO <name>` (what `SetBindingSpell`, `SetBindingItem`
and `SetBindingMacro` save, and what `GetBindingAction` returns). They are
not named commands of any `Bindings.xml`, so `clientlists.binding_command`
has none of them; `keymap._command_words` then says the game's own
interface has no such command and `KeyRow.client_command` is False, and the
Keys panel draws the row in the "not the client's" colour. The client runs
such a binding itself, with or without the addon that set it. The positive
control is a plain upper-case name no binding file defines, which is an
addon's and keeps that mark."""

from __future__ import annotations

import pytest

from wowlab_core import keymap, wtfconfig

FILE = (
    b"bind 1 SPELL Fireball\n"
    b"bind 2 ITEM Hearthstone\n"
    b"bind 3 MACRO Lab macro\n"
    b"bind F3 LABADDON_DO_THING\n"
)


def _rows() -> dict[str, keymap.KeyRow]:
    doc = wtfconfig.parse_bindings(FILE)
    km = keymap.join(account=doc, character=None, bindings=None, actionbars=None, spellbook=None)
    return {r.key: r for r in km.keys}


@pytest.mark.parametrize("key", ["1", "2", "3"])
def test_a_spell_item_or_macro_binding_is_not_called_an_addons_command_constructed(
    key: str,
) -> None:
    row = _rows()[key]
    assert row.client_command is not False, (row.command, row.does)
    assert "has no such command" not in row.does, (row.command, row.does)


def test_positive_control_a_plain_unknown_name_is_still_an_addons_constructed() -> None:
    row = _rows()["F3"]
    assert row.client_command is False
    assert "has no such command" in row.does
