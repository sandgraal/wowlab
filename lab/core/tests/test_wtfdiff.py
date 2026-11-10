"""`wtfdiff`: keys, macros and CVars by name between two versions of a WTF
text file (M16-10).

Real captures (`fixtures/README.md` rows for `macos/forever/WTF/...`): the
account `bindings-cache.wtf` (CRLF), the account and character
`config-cache.wtf`, the empty account `macros-cache.txt` and the
character's one-macro `macros-cache.txt`. Where two real versions of one
file do not exist, the other side is the capture with a byte-level edit,
and the test name says `constructed` (L8: a boundary input).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import wtfdiff
from wowlab_core.wtfdiff import NamedChange

WTF = Path(__file__).resolve().parent / "fixtures" / "macos" / "forever" / "WTF"
ACCOUNT = WTF / "Account" / "90000001#6"
CHARACTER = ACCOUNT / "1" / "Labcharb-Labrealmd"
BINDINGS = (ACCOUNT / "bindings-cache.wtf").read_bytes()
ACCOUNT_CONFIG = (ACCOUNT / "config-cache.wtf").read_bytes()
CHARACTER_CONFIG = (CHARACTER / "config-cache.wtf").read_bytes()
ACCOUNT_MACROS = (ACCOUNT / "macros-cache.txt").read_bytes()
CHARACTER_MACROS = (CHARACTER / "macros-cache.txt").read_bytes()


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("WTF/Account/A/bindings-cache.wtf", "bindings"),
        ("WTF/Account/A/R/C/macros-cache.txt", "macros"),
        ("WTF/Account/A/config-cache.wtf", "cvars"),
        ("WTF/Config.wtf", "cvars"),
        ("WTF/CONFIG.WTF", "cvars"),
        ("WTF\\Account\\A\\bindings-cache.wtf", "bindings"),
        ("WTF/Account/A/click-bindings-cache.txt", None),
        ("WTF/Account/A/SavedVariables/WowLab.lua", None),
        ("WTF/Account/A/bindings-cache.wtf.bak", None),
    ],
)
def test_kind_of_names_the_four_files_by_file_name(path: str, kind: str | None) -> None:
    assert wtfdiff.kind_of(path) == kind


def test_identical_files_have_no_change() -> None:
    for kind, data in (
        ("bindings", BINDINGS),
        ("cvars", ACCOUNT_CONFIG),
        ("macros", CHARACTER_MACROS),
    ):
        diff = wtfdiff.compare(kind, data, data)  # type: ignore[arg-type]
        assert diff.changes == () and not diff.other_lines_differ


def test_bindings_from_nothing_lists_every_key_added() -> None:
    diff = wtfdiff.compare("bindings", None, BINDINGS)
    assert diff.changes == (
        NamedChange(what="key", name="BUTTON3", before=None, after="MULTIACTIONBAR1BUTTON2"),
        NamedChange(what="key", name="Q", before=None, after="MULTIACTIONBAR1BUTTON1"),
        NamedChange(what="key", name="E", before=None, after="MULTIACTIONBAR1BUTTON3"),
    )


def test_bindings_rebound_added_and_removed_keys_constructed() -> None:
    later = BINDINGS.replace(
        b"bind Q MULTIACTIONBAR1BUTTON1\r\n", b"bind Q ACTIONBUTTON1\r\n"
    ).replace(b"bind E MULTIACTIONBAR1BUTTON3\r\n", b"bind F INTERACTTARGET\r\n")
    diff = wtfdiff.compare("bindings", BINDINGS, later)
    assert diff.changes == (
        NamedChange(what="key", name="Q", before="MULTIACTIONBAR1BUTTON1", after="ACTIONBUTTON1"),
        NamedChange(what="key", name="F", before=None, after="INTERACTTARGET"),
        NamedChange(what="key", name="E", before="MULTIACTIONBAR1BUTTON3", after=None),
    )
    assert not diff.other_lines_differ


def test_bindings_reordered_or_line_endings_changed_is_no_change_constructed() -> None:
    lines = BINDINGS.split(b"\r\n")
    reordered = b"\n".join(reversed([ln for ln in lines if ln])) + b"\n"
    diff = wtfdiff.compare("bindings", BINDINGS, reordered)
    assert diff.changes == () and not diff.other_lines_differ


def test_an_unknown_line_that_differs_is_flagged_constructed() -> None:
    later = BINDINGS + b"bind  X  SPACED\r\n"  # two spaces: not the bind grammar
    diff = wtfdiff.compare("bindings", BINDINGS, later)
    assert diff.changes == () and diff.other_lines_differ


def test_cvars_between_the_account_and_character_files() -> None:
    """Two real config-cache.wtf files: no name in common, so every CVar of
    one is removed and every CVar of the other added."""
    diff = wtfdiff.compare("cvars", ACCOUNT_CONFIG, CHARACTER_CONFIG)
    added = [c for c in diff.changes if c.before is None]
    removed = [c for c in diff.changes if c.after is None]
    assert len(added) == CHARACTER_CONFIG.count(b"SET ") == 21
    assert len(removed) == ACCOUNT_CONFIG.count(b"SET ") == 30
    assert added[0] == NamedChange(
        what="cvar", name="collapsedCurrencyCategoryDefaults", before=None, after="v21###$###&"
    )
    assert NamedChange(what="cvar", name="cameraView", before="5", after=None) in removed
    assert NamedChange(what="cvar", name="lastSelectedClubId", before=None, after="") in added


def test_cvar_value_changed_compares_names_without_case_constructed() -> None:
    later = ACCOUNT_CONFIG.replace(b'SET cameraView "5"', b'SET CAMERAVIEW "2"').replace(
        b'SET autoLootDefault "1"', b'SET AUTOLOOTDEFAULT "1"'
    )
    diff = wtfdiff.compare("cvars", ACCOUNT_CONFIG, later)
    # A respelled name with the same value is no change; the new spelling is shown.
    assert diff.changes == (NamedChange(what="cvar", name="CAMERAVIEW", before="5", after="2"),)


def test_cvar_set_twice_compares_the_last_line_constructed() -> None:
    later = ACCOUNT_CONFIG + b'SET cameraView "1"\n'
    diff = wtfdiff.compare("cvars", ACCOUNT_CONFIG, later)
    assert diff.changes == (NamedChange(what="cvar", name="cameraView", before="5", after="1"),)


def test_macro_added_from_the_empty_account_file() -> None:
    assert ACCOUNT_MACROS == b""  # the capture holds no account macro
    diff = wtfdiff.compare("macros", ACCOUNT_MACROS, CHARACTER_MACROS)
    assert diff.changes == (
        NamedChange(
            what="macro",
            name="PolyArc",
            before=None,
            after="/castsequence Arcane Intellect, Polymorph",
            occurrence=1,
            position=1,
        ),
    )
    assert not diff.repeated_names
    back = wtfdiff.compare("macros", CHARACTER_MACROS, None)
    assert [(c.name, c.before is not None, c.after) for c in back.changes] == [
        ("PolyArc", True, None)
    ]


def test_macro_text_and_icon_changes_are_named_constructed() -> None:
    text = CHARACTER_MACROS.replace(b"Polymorph", b"Frostbolt")
    icon = CHARACTER_MACROS.replace(b'"134400"', b'"136071"')
    both = text.replace(b'"134400"', b'"136071"')
    for later, parts in ((text, ("text",)), (icon, ("icon",)), (both, ("text", "icon"))):
        (change,) = wtfdiff.compare("macros", CHARACTER_MACROS, later).changes
        assert (change.name, change.parts) == ("PolyArc", parts)


def test_macros_of_one_name_are_compared_in_turn_constructed() -> None:
    record = CHARACTER_MACROS
    second = record.replace(b"0100000000000001", b"0100000000000002").replace(
        b"Polymorph", b"Frostbolt"
    )
    diff = wtfdiff.compare("macros", record, record + second)
    assert [
        (c.name, c.occurrence, c.position, c.before, c.after is not None) for c in diff.changes
    ] == [("PolyArc", 2, 2, None, True)]
    assert diff.repeated_names
    # Removing the first of the two shifts the second into its place.
    shifted = wtfdiff.compare("macros", record + second, second)
    assert [(c.name, c.occurrence, c.parts, c.after is None) for c in shifted.changes] == [
        ("PolyArc", 1, ("text",), False),
        ("PolyArc", 2, (), True),
    ]
    assert shifted.repeated_names


def test_a_blank_macro_name_keeps_its_place_constructed() -> None:
    """A later real run's macro names were all a single space (the
    `macos-70425` provenance row); this edit of the capture stands for it."""
    blank = CHARACTER_MACROS.replace(b'"PolyArc"', b'" "')
    diff = wtfdiff.compare("macros", CHARACTER_MACROS, CHARACTER_MACROS + blank)
    (added,) = diff.changes
    assert (added.name, added.occurrence, added.position) == (" ", 1, 2)


def test_macro_id_change_alone_is_no_change_by_name_constructed() -> None:
    later = CHARACTER_MACROS.replace(b"0100000000000001", b"0100000000000009")
    diff = wtfdiff.compare("macros", CHARACTER_MACROS, later)
    assert diff.changes == () and not diff.other_lines_differ
