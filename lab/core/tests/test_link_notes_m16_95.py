"""A linked client file in an account folder is a file in `tree`'s link note
(M16-95).

`WTF/Account/<acct>/macros-cache.txt` has its own row as a file, which spells
out the file's name (`account-macros-cache`). As a folder the same place
matches `realm-folder`, which takes a folder of any name there
(`WTF/Account/*/<name>/`). M16-91's rule covers only a general row of a
folder above the place, so the note said "real one"; a realm folder named
`macros-cache.txt` is not one the client makes, so the note says "real
file". Every row of the account folder that names a fixed file is pinned
below, one case each.

`WTF/Account/SavedVariables/<Addon>.lua` (and `.lua.bak`) has a file row
whose name is a wildcard, so the rule does not reach it, and classified with
nothing on disk the folder row there is `realm-folder`: "real one", as the
map alone cannot tell. On a real walk the folder `WTF/Account/SavedVariables`
is no account folder (M13-50), so no account row applies below it, the
folder side has no row and the note says "real file"; the end-to-end case
pins that.

Constructed (L8, boundary): the unit cases classify places that do not exist
on disk. The end-to-end runs copy the captured tree (`fixtures/macos/`)
into `tmp_path` and put a symlink to a constructed file outside the install
at the place of a client file. Nothing reads or writes a real install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, layout

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "WTF/Account/90000001#6"  # the captured tree's account folder
FILE_END = "wowlab does not read through links, so it reads this only when the real file is there"
ONE_END = "wowlab does not read through links, so it reads this only when the real one is there"
ACCT = "WTF/Account/90000001#1"

# (place below the account folder, the file row it gets): every row of
# `WTF/Account/<ACCOUNT>/` that spells out a file name.
ACCOUNT_FILES = [
    ("config-cache.wtf", "account-config-cache"),
    ("bindings-cache.wtf", "account-bindings-cache"),
    ("macros-cache.txt", "account-macros-cache"),
    ("SavedVariables.lua", "account-blizzard-savedvariables"),
    ("SavedVariables.lua.bak", "savedvariables-backup"),
    ("edit-mode-cache-account.txt", "account-edit-mode-cache"),
    ("edit-mode-cache-account.old", "account-edit-mode-cache-old"),
    ("chat-frontend-cache.txt", "account-chat-frontend-cache"),
    ("flagged-cache-account.txt", "flagged-cache"),
    ("tts-cache-account.txt", "tts-cache"),
    ("character-list-order.txt", "character-list-order"),
]


@pytest.fixture
def flavor_tree(tmp_path: Path) -> layout.Layout:
    root = tmp_path / "World of Warcraft"
    (root / "_constructed_" / "WTF" / "Account").mkdir(parents=True)
    return layout.Layout(root / "_constructed_", install_root=root)


@pytest.mark.parametrize(
    ("name", "row"), [pytest.param(n, r, id=f"constructed-{n}") for n, r in ACCOUNT_FILES]
)
def test_a_client_file_in_an_account_folder_stands_for_a_file_constructed(
    flavor_tree: layout.Layout, name: str, row: str
) -> None:
    rel = f"{ACCT}/{name}"
    as_file = flavor_tree.classify(rel, is_dir=False)
    as_folder = flavor_tree.classify(rel, is_dir=True)
    assert isinstance(as_file, layout.Classified) and as_file.entry.id == row
    # The pair the rule is for: without it this place said "real one".
    assert isinstance(as_folder, layout.Classified) and as_folder.entry.id == "realm-folder"
    assert cli._link_stands_for(as_file, as_folder) == "file"


@pytest.mark.parametrize(
    "rel",
    [
        pytest.param("WTF/Account/SavedVariables/Constructed.lua", id="constructed-lua"),
        pytest.param("WTF/Account/SavedVariables/Constructed.lua.bak", id="constructed-bak"),
    ],
)
def test_a_client_savedvariables_file_follows_the_not_an_account_check_constructed(
    flavor_tree: layout.Layout, rel: str
) -> None:
    """The file row names no fixed file, so only M13-50 decides: with the
    walk's list naming `SavedVariables` as no account, the folder side has
    no row and the note says "file"; with nothing on disk to judge, the map
    cannot tell and it stays "one"."""
    judged = ("SavedVariables",)
    as_file = flavor_tree.classify(rel, is_dir=False, not_accounts=judged)
    as_folder = flavor_tree.classify(rel, is_dir=True, not_accounts=judged)
    assert isinstance(as_folder, layout.Unclassified)
    assert cli._link_stands_for(as_file, as_folder) == "file"
    unjudged_folder = flavor_tree.classify(rel, is_dir=True)
    assert isinstance(unjudged_folder, layout.Classified)
    assert unjudged_folder.entry.id == "realm-folder"
    assert cli._link_stands_for(flavor_tree.classify(rel, is_dir=False), unjudged_folder) == "one"


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        # A name no file row spells out: a realm folder, as before.
        pytest.param(f"{ACCT}/Constructed Realm", "folder", id="constructed-realm-folder"),
        pytest.param(f"{ACCT}/12345", "folder", id="constructed-numeric-folder"),
        pytest.param(f"{ACCT}/SavedVariables", "folder", id="constructed-sv-folder"),
        # A gate temp name: the `wowlab-temp` row's `<wowlab-temp>` token
        # spells out no one name, so the map cannot tell.
        pytest.param(
            f"{ACCT}/.wowlab-0123456789abcdef0123456789abcdef.tmp",
            "one",
            id="constructed-gate-temp-name",
        ),
        # One row for both kinds below the folder it hangs from: unchanged.
        pytest.param("Fonts/FRIZQT__.TTF", "one", id="constructed-font-file"),
        pytest.param(
            "Interface/AddOns/Constructed/Constructed.toc", "one", id="constructed-addon-toc"
        ),
    ],
)
def test_other_places_keep_their_word_constructed(
    flavor_tree: layout.Layout, rel: str, expected: str
) -> None:
    stands_for = cli._link_stands_for(
        flavor_tree.classify(rel, is_dir=False), flavor_tree.classify(rel, is_dir=True)
    )
    assert stands_for == expected


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import platformdirs

    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    install = tmp_path / "wow"
    install.mkdir()
    shutil.copy2(CAPTURE / ".build.info", install / ".build.info")
    shutil.copytree(CAPTURE / "forever", install / FLAVOR)
    monkeypatch.chdir(tmp_path)
    return install


def _notes(root: Path) -> dict[str, Any]:
    result = CliRunner().invoke(cli.app, ["tree", "--root", str(root), "--json"])
    assert result.exit_code == 0, result.output
    return {i["path"]: i["note"] for i in json.loads(result.stdout)["items"]}


def _link(link: Path, tmp_path: Path) -> None:
    target = tmp_path / "shared" / link.name
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"constructed\n")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


@pytest.mark.parametrize(
    "rel",
    [
        pytest.param(f"{ACCOUNT}/macros-cache.txt", id="constructed-macros-cache"),
        pytest.param(f"{ACCOUNT}/config-cache.wtf", id="constructed-config-cache"),
        pytest.param(
            "WTF/Account/SavedVariables/Blizzard_Constructed.lua", id="constructed-client-sv"
        ),
    ],
)
def test_tree_says_a_linked_account_folder_file_is_a_file_constructed(
    root: Path, tmp_path: Path, rel: str
) -> None:
    _link(root / FLAVOR / rel, tmp_path)
    note = _notes(root)[rel]
    assert note.endswith(FILE_END), note
    assert ONE_END not in note, note
