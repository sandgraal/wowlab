# Probe from review of m10/06-layout-filemap-toc; reproduces a SavedVariables
# folder at realm depth being inventoried and classified as a character.
"""`WTF/Account/<A>/<Realm>/SavedVariables/` is not a shape the client writes,
but the layout meets it on any hand-edited tree. `SavedVariables` cannot be a
character (character names are at most 12 letters; this is 14), yet:

- `accounts()` lists a `Character(folder="SavedVariables")` under the realm;
- `wtf_files()` types `…/<Realm>/SavedVariables/Foo.lua` as a `character`
  file with `character_folder="SavedVariables"`;
- `classify()` returns the `character-folder` row for the folder.

Positive control: the same folder at account depth is excluded from realms
(`realm-folder` excludes it, `_scan_wtf` skips it). Expected: the realm-depth
folder is not a character; its files get scope `other` and the folder no
`character-folder` row (add `WTF/Account/*/*/SavedVariables` to that row's
`exclude`, skip it in `_scan_wtf`, and require `sub[3]` not to be
`SavedVariables` in `_as_wtf_file`). Constructed in `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path

from wowlab_core.layout import Classified, Layout


def _layout(tmp_path: Path) -> Layout:
    flavor = tmp_path / "WoW" / "_x_"
    for rel in (
        "WTF/Account/ACC/SavedVariables/Acct.lua",
        "WTF/Account/ACC/Realm/SavedVariables/Foo.lua",
        "WTF/Account/ACC/Realm/Char/AddOns.txt",
    ):
        path = flavor / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return Layout(flavor)


def test_control_account_depth_savedvariables_is_not_a_realm_constructed(tmp_path: Path) -> None:
    (account,) = _layout(tmp_path).accounts()
    assert [r.folder for r in account.realms] == ["Realm"]


def test_realm_depth_savedvariables_is_not_a_character_constructed(tmp_path: Path) -> None:
    lay = _layout(tmp_path)
    (account,) = lay.accounts()
    (realm,) = account.realms
    assert [c.folder for c in realm.characters] == ["Char"]


def test_realm_depth_savedvariables_file_is_not_character_scoped_constructed(
    tmp_path: Path,
) -> None:
    files = {f.path: f for f in _layout(tmp_path).wtf_files()}
    stray = files["WTF/Account/ACC/Realm/SavedVariables/Foo.lua"]
    assert stray.scope != "character", stray


def test_realm_depth_savedvariables_folder_is_not_the_character_row_constructed(
    tmp_path: Path,
) -> None:
    found = _layout(tmp_path).classify("WTF/Account/ACC/Realm/SavedVariables")
    assert not (isinstance(found, Classified) and found.entry.id == "character-folder")
