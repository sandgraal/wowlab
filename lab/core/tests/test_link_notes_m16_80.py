"""The older link notes say what the readers now say (M16-80).

Four notes outside M16-65's readers named a link in the words from before
#429 ("wowlab does not follow it"). Each now ends in the readers' sentence,
`snapshot.not_read_through`, as `labaddon.NOT_FOLLOWED` and
`labaddon.LINKED_FILE` say it ("real folder", "real file", or "real one"
where the file map cannot tell), or the readers' rule alone where their
action clause does not fit (the screenshot clause, the first run's sync
line). Every sentence is spelled out here in full, so a change to the
readers' words shows up as a failure in this file too.

Constructed: the notes are built from in-memory values and file-map rows;
nothing reads or writes an install. The end-to-end runs of each site are in
`test_cli_tree_links.py`, `test_cli_restore_screenshots.py` and
`test_cli_first_run.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import cli, firstrun, labaddon, layout, snapshot

FOLDER_WORDS = (
    "wowlab does not read through links, so it reads this only when the real folder is there"
)
FILE_WORDS = "wowlab does not read through links, so it reads this only when the real file is there"
ONE_WORDS = "wowlab does not read through links, so it reads this only when the real one is there"


def test_the_words_are_the_readers_constructed() -> None:
    assert f"a link, not followed; {FOLDER_WORDS}" == labaddon.NOT_FOLLOWED
    assert f"a link, not followed; {FILE_WORDS}" == labaddon.LINKED_FILE


def test_tree_linked_flavor_folder_line_constructed() -> None:
    """A `_note` message is a literal; it is held to the readers' words here."""
    assert f"{{}} is a link; {FOLDER_WORDS}. Nothing in it is listed." == cli._FLAVOR_LINK_NOTE
    assert (
        "{} is a link; " + snapshot.not_read_through("folder") + ". Nothing in it is listed."
    ) == cli._FLAVOR_LINK_NOTE


@pytest.mark.parametrize(
    ("target", "inside", "stands_for", "expected"),
    [
        pytest.param(
            "/elsewhere",
            False,
            "folder",
            f"link to /elsewhere (outside the install), not followed; {FOLDER_WORDS}",
            id="constructed-folder-outside",
        ),
        pytest.param(
            "../Logs",
            True,
            "folder",
            f"link to ../Logs (inside the install), not followed; {FOLDER_WORDS}",
            id="constructed-folder-inside",
        ),
        pytest.param(
            None,
            None,
            "file",
            f"link (where it points is unknown), not followed; {FILE_WORDS}",
            id="constructed-file-unknown",
        ),
        pytest.param(
            "/elsewhere/FRIZQT__.TTF",
            False,
            "one",
            f"link to /elsewhere/FRIZQT__.TTF (outside the install), not followed; {ONE_WORDS}",
            id="constructed-one-outside",
        ),
    ],
)
def test_tree_link_note_constructed(
    target: str | None, inside: bool | None, stands_for: str, expected: str
) -> None:
    assert cli._link_note(target, inside, stands_for) == expected


@pytest.fixture
def flavor_tree(tmp_path: Path) -> layout.Layout:
    root = tmp_path / "World of Warcraft"
    (root / "_constructed_" / "WTF" / "Account").mkdir(parents=True)
    return layout.Layout(root / "_constructed_", install_root=root)


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        pytest.param("WTF/Config.wtf", "file", id="constructed-config-wtf"),
        pytest.param(
            "WTF/Account/90000001#1/SavedVariables/Constructed.lua",
            "file",
            id="constructed-saved-variables-file",
        ),
        pytest.param("WTF/Account/90000001#1", "folder", id="constructed-account-folder"),
        pytest.param("Interface/AddOns/Constructed", "folder", id="constructed-addon-folder"),
        pytest.param("Fonts", "folder", id="constructed-fonts-folder"),
        # One row for files and folders alike, below the folder it hangs from.
        pytest.param("Fonts/FRIZQT__.TTF", "one", id="constructed-font-file"),
        pytest.param(
            "Interface/AddOns/Constructed/Constructed.toc", "one", id="constructed-addon-toc"
        ),
        pytest.param("NoRowHere", "one", id="constructed-no-row"),
    ],
)
def test_a_link_stands_for_a_file_only_where_the_file_map_says_so_constructed(
    flavor_tree: layout.Layout, rel: str, expected: str
) -> None:
    """Nothing here exists on disk: the place is classified as each kind."""
    stands_for = cli._link_stands_for(
        flavor_tree.classify(rel, is_dir=False), flavor_tree.classify(rel, is_dir=True)
    )
    assert stands_for == expected


def test_a_root_level_file_with_no_row_stands_for_neither_constructed(
    flavor_tree: layout.Layout,
) -> None:
    """A name at the install root no row has, as a file or as a folder."""
    place = flavor_tree.install_root / "constructed-loose.txt"
    as_file = flavor_tree.classify(place, is_dir=False)
    as_folder = flavor_tree.classify(place, is_dir=True)
    assert as_file.base == as_folder.base == "root"
    assert cli._link_stands_for(as_file, as_folder) == "one"


@pytest.mark.parametrize(
    ("unread", "clause"),
    [
        pytest.param(
            1,
            "1 not read (it is reached through Screenshots/, which is now a link, and wowlab "
            "does not read through links):",
            id="constructed-one",
        ),
        pytest.param(
            3,
            "3 not read (each is reached through Screenshots/, which is now a link, and wowlab "
            "does not read through links):",
            id="constructed-three",
        ),
    ],
)
def test_snap_restore_screenshot_clause_constructed(unread: int, clause: str) -> None:
    aside = [
        cli.NotRestoredPath(
            path=f"Screenshots/WoWScrnShot_{n}.jpg",
            entry_id="screenshots",
            what="w",
            disk="behind_link",
        )
        for n in range(unread)
    ]
    first = cli._not_restored_lines("S1", aside)[0]
    assert first == (
        f"  Not restored: {unread} screenshot(s) wowlab never writes (file-map Edit "
        f'"n/a"; --paths cannot restore them either). They stay in snapshot S1; {clause}'
    )


@pytest.mark.parametrize(
    ("path", "behind"),
    [
        pytest.param("WTF", "your settings and SavedVariables are", id="constructed-wtf"),
        pytest.param("WTF/Account", "what is behind it is", id="constructed-below-wtf"),
    ],
)
def test_first_run_sync_link_line_constructed(path: str, behind: str) -> None:
    hint = firstrun.SyncHint(
        service="Dropbox", path=path, evidence="constructed", link_target="/Dropbox/WTF"
    )
    scan = firstrun.FlavorScan.model_construct(sync=[hint])
    assert firstrun._sync_lines(scan, wtf_is_link=path == "WTF") == [
        f"  {path} is a link into a folder Dropbox appears to sync (/Dropbox/WTF). "
        f"wowlab does not read through links, so {behind} not in this snapshot; back up the "
        "folder it points to yourself."
    ]
