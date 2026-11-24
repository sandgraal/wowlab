# Probe from review of m22/05-live-companion; reproduces the session recap's new items read from a same-named character on another realm
"""M22-05: the session recap lists "new items, from Syndicator". `live
recap` asks `live.syndicator_items` for the character by the names
`_cmd_live._live_names` makes: the bare character folder name, `First
Second` for a `First-Second` folder, and `Name-Realm`. `syndicator_items`
takes the first entry of `Characters` whose key, or whose
`details.character`, is one of them.

Syndicator keys a character `Name-Realm` and keeps the bare name in
`details.character` beside `details.realm` (the retail layout; the one
committed capture, from the Forever beta, has key and `details.character`
both `First Second` and an empty realm, so it cannot show this). With the
same name on two realms, the first realm's entry matches by its bare
`details.character` before the exact `Name-Realm` key of the second is
looked at: a character on realm B is shown realm A's bags.

CONSTRUCTED Syndicator file, invented names and realms. Positive control:
the character on the first realm gets its own item.
"""

from __future__ import annotations

import types

from wowlab_core import live
from wowlab_core.cli import _cmd_live

SYNDICATOR = b"""
SYNDICATOR_DATA = {
["Characters"] = {
["Inventeda-Fakerealm"] = {
["details"] = {["character"] = "Inventeda", ["realm"] = "Fakerealm", ["realmNormalized"] = "Fakerealm",},
["bags"] = {{{["itemID"] = 1, ["itemCount"] = 1,},},},
},
["Inventeda-Otherfakerealm"] = {
["details"] = {["character"] = "Inventeda", ["realm"] = "Otherfakerealm", ["realmNormalized"] = "Otherfakerealm",},
["bags"] = {{{["itemID"] = 2, ["itemCount"] = 1,},},},
},
},
}
"""


def _items(realm_folder: str) -> list[int]:
    target = types.SimpleNamespace(character_folder="Inventeda", realm_folder=realm_folder)
    got = live.syndicator_items(SYNDICATOR, _cmd_live._live_names(target))  # type: ignore[arg-type]
    assert not isinstance(got, str), got
    return [i.item_id for i in got]


def test_positive_control_the_first_realms_character_gets_its_items() -> None:
    assert _items("Fakerealm") == [1]


def test_the_second_realms_character_gets_its_own_items() -> None:
    assert _items("Otherfakerealm") == [2]
