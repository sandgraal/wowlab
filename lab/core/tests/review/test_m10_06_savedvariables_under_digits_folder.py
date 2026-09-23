# Probe from review of m10/06-layout-filemap-toc (fix round 1); reproduces
# classify() calling a SavedVariables folder under a digits folder a Forever
# character folder while the inventory says it is not a character.
"""Fix round 1 added `WTF/Account/*/*/SavedVariables` to the `exclude` of the
`character-folder` row and made `_scan_wtf` skip `SavedVariables` under every
realm-depth folder. The `second-name-character-folder` row
(`WTF/Account/*/<digits>/*/`) got no such exclude, so for
`WTF/Account/<A>/<digits>/SavedVariables/` the inventory lists no character
but `classify()` returns `second-name-character-folder`. A folder named
`SavedVariables` has no first and second name.

Positive control: the realm-name case (`<Realm>/SavedVariables/`) is no
longer `character-folder`. Fix: give `second-name-character-folder` the same
`exclude = ["WTF/Account/*/*/SavedVariables"]`. Constructed in `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path

from wowlab_core.layout import Classified, Layout


def _layout(tmp_path: Path) -> Layout:
    flavor = tmp_path / "WoW" / "_x_"
    for rel in (
        "WTF/Account/ACC/Realm/SavedVariables/Foo.lua",
        "WTF/Account/ACC/1/SavedVariables/Foo.lua",
        "WTF/Account/ACC/1/Anna-Bee/config-cache.wtf",
    ):
        path = flavor / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return Layout(flavor)


def _entry_id(found: object) -> str | None:
    return found.entry.id if isinstance(found, Classified) else None


def test_control_realm_name_case_is_not_a_character_constructed(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    assert _entry_id(lay.classify("WTF/Account/ACC/Realm/SavedVariables")) != "character-folder"
    assert _entry_id(lay.classify("WTF/Account/ACC/1/Anna-Bee")) == "second-name-character-folder"


def test_digits_folder_savedvariables_is_not_a_character_constructed(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    characters = [c.folder for a in lay.accounts() for r in a.realms for c in r.characters]
    assert characters == ["Anna-Bee"], "the inventory agrees it is not a character"
    found = lay.classify("WTF/Account/ACC/1/SavedVariables")
    assert _entry_id(found) != "second-name-character-folder", found
