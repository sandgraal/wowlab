"""`wowlab_core.verdict`: the file map's Edit cell as a level and in words (M13-21).

The first test walks the packaged `filemap.toml`, so a new Edit cell outside
the fixed mapping fails it. The rest use rows from the packaged map. The
constructed inputs are Edit cells that are not in it and path strings held
to the write gate's rule (no filesystem), labelled `constructed`.
"""

from __future__ import annotations

import pytest

from wowlab_core import filemap, guard, verdict
from wowlab_core.filemap import FileMapEntry
from wowlab_core.verdict import Verdict, edit_level, restorable, tier_words, verdict_of

_GATE = "yes, through wowlab's write gate only, with the client closed"
_UNKNOWN = "no file-map row; wowlab does not know what this file is"
_UNKNOWN_RESTORED = (
    _UNKNOWN
    + "; a whole snapshot restore puts it back like any file here (it skips only Edit `no` files)"
)


def _spec_level(edit: str) -> str | None:
    """The mapping as `docs/LAB_PLAN.md` §15.6 states it, written apart from
    the module so the two can disagree."""
    if edit == "gate" or (edit.startswith("gate (") and edit.endswith(")")):
        return "gate"
    return {"no": "no", "—": "folder", "n/a": "not_applicable"}.get(edit)


def test_every_edit_cell_in_the_file_map_maps_to_exactly_one_level() -> None:
    cells = sorted({e.edit for e in filemap.load().entries})
    assert cells, "the packaged file map has no entries"
    unmapped = [c for c in cells if _spec_level(c) is None]
    assert not unmapped, f"Edit cells with no verdict level in §15.6: {unmapped}"
    for cell in cells:
        assert edit_level(cell) == _spec_level(cell), cell
    # `unclassified` means only "no file-map row"; no cell maps to it.
    assert "unclassified" not in {edit_level(c) for c in cells}


@pytest.mark.parametrize(
    "cell",
    ["yes", "gate-ish", "gate (unclosed", "No", "-", "", "unclassified", "n/a (later)"],
    ids=lambda c: f"constructed-{c!r}",
)
def test_a_cell_outside_the_mapping_is_an_error(cell: str) -> None:
    with pytest.raises(ValueError, match="no verdict level"):
        edit_level(cell)


def _entry(entry_id: str) -> FileMapEntry:
    return filemap.load().entry(entry_id)


@pytest.mark.parametrize(
    ("entry_id", "base", "path", "expected"),
    [
        (
            "config-wtf",
            "flavor",
            "WTF/Config.wtf",
            Verdict(level="gate", restorable=True, server_may_replace=False, words=_GATE),
        ),
        (
            "fonts",
            "flavor",
            "Fonts/FRIZQT__.TTF",
            Verdict(
                level="gate",
                restorable=True,
                server_may_replace=False,
                words=_GATE + "; unsupported by Blizzard, a patch can reset it",
            ),
        ),
        (
            "build-info",
            "root",
            ".build.info",
            Verdict(
                level="no",
                restorable=False,
                server_may_replace=False,
                words="no; wowlab never writes it",
            ),
        ),
        (
            "savedvariables-backup",
            "flavor",
            "WTF/Account/A/SavedVariables/X.lua.bak",
            Verdict(
                level="no",
                restorable=True,
                server_may_replace=False,
                words="no; wowlab leaves it alone (a restore writes it only if you name it "
                "with --paths)",
            ),
        ),
        (
            "screenshots",
            "flavor",
            "Screenshots/a.jpg",
            Verdict(
                level="not_applicable",
                restorable=False,
                server_may_replace=False,
                words="n/a; not a file wowlab touches",
            ),
        ),
        (
            "wtf-folder",
            "flavor",
            "WTF",
            Verdict(
                level="folder",
                restorable=None,
                server_may_replace=False,
                words="—; a folder, see the files in it",
            ),
        ),
        (
            "flavor-folder",
            "flavor",
            "",
            Verdict(
                level="folder",
                restorable=None,
                server_may_replace=False,
                words="—; a folder, see the files in it",
            ),
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_the_verdict_for_a_row(entry_id: str, base: str, path: str, expected: Verdict) -> None:
    assert verdict_of(_entry(entry_id), base, path) == expected


def test_a_path_with_no_row_is_unclassified() -> None:
    inside = verdict_of(None, "flavor", "WTF/unknown-constructed.txt")
    assert inside == Verdict(
        level="unclassified", restorable=True, server_may_replace=False, words=_UNKNOWN_RESTORED
    )
    outside = verdict_of(None, "flavor", "Cache/unknown-constructed.txt")
    assert outside == Verdict(
        level="unclassified", restorable=False, server_may_replace=False, words=_UNKNOWN
    )
    assert verdict_of(None, None, "/elsewhere").words == _UNKNOWN


def test_server_may_replace_is_a_field_not_only_words() -> None:
    rows = [e for e in filemap.load().entries if e.edit == "gate (server may replace)"]
    assert rows
    for entry in rows:
        v = verdict_of(entry, "flavor", "WTF/x")
        assert v.server_may_replace is True
        assert v.words == (
            _GATE + "; the server may replace it at login (sync settings: wowlab doctor)"
        )
    others = [e for e in filemap.load().entries if e.edit != "gate (server may replace)"]
    assert not any(verdict_of(e, "flavor", "WTF/x").server_may_replace for e in others)


# Flavor-relative paths, constructed, held to the gate's lexical rule. The
# first block is what the domain review of PR #195 found the old
# area-only rule got wrong.
GATE_PATHS = [
    "Interface/AddOns/Foo/bin/helper.exe",
    "Interface/AddOns/Foo/run.bat",
    "Interface/AddOns/Foo/lib.so.1.2",
    "Interface/AddOns/Foo/Foo.app/Contents/Info.plist",
    "wtf/Config.wtf",
    "Interface/addons/Foo/Foo.toc",
    "INTERFACE/AddOns/Foo/Foo.toc",
    "fonts/FRIZQT__.TTF",
    # Allowed.
    "WTF/Config.wtf",
    "WTF/Account/90000001#6/SavedVariables/Foo.lua",
    "WTF/Account/90000001#6/SavedVariables/Foo.lua.bak",
    "WTF/Account/A/Realm/Char/layout-local.txt",
    "Interface/AddOns/Foo/Foo.toc",
    "Interface/AddOns/Foo/media/a b.tga",
    "Interface/AddOns/Blizzard_Foo/x.lua",
    "Interface/ICONS/x.blp",
    "Fonts/FRIZQT__.TTF",
    "WTF/.DS_Store",
    "WTF/._Config.wtf",
    # Roots and folders the gate does not write as files.
    "",
    "WTF",
    "Interface",
    "Fonts",
    "Interface/AddOns",
    "Interface/AddOns/Foo",
    # Outside the allowlist.
    "Logs/x.txt",
    "Cache/x",
    "Screenshots/a.jpg",
    "Data/data.000",
    ".flavor.info",
    "WTFx/y",
    # Not plain names.
    "/WTF/Config.wtf",
    "WTF//Config.wtf",
    "WTF/./Config.wtf",
    "WTF/../Interface/x",
    "WTF/Config.wtf.",
    "WTF/Config.wtf ",
    "WTF/con.txt",
    "WTF/COM1",
    "WTF/lpt¹.log",
    "WTF/a:b",
    "WTF/a\\b",
    "WTF/a\tb",
    "WTF/a\x7fb",
    "WTF/" + "x" * 255,
    "WTF/" + "x" * 256,
    "WTF/\udcff",
]


def _from_guard_tables() -> list[str]:
    """Cases generated from guard's own tables when this module loads, so a
    suffix, reserved name or forbidden character guard gains is exercised
    even when no hand-written path above has it."""
    shared = set(guard._EXECUTABLE_SUFFIXES) & set(verdict._EXECUTABLE_SUFFIXES)
    out: list[str] = []
    for suffix in guard._EXECUTABLE_SUFFIXES:
        out.append(f"Interface/AddOns/Foo/media/a b{suffix}")
        # Other letter cases for the suffixes both copies list: the gate
        # compares case-insensitively. A suffix only guard has is caught by
        # its lowercase case above (and by the table-equality test below).
        if suffix in shared:
            out.append(f"Interface/AddOns/Foo/media/a b{suffix.upper()}")
            out.append(f"Interface/AddOns/Foo/{suffix[1:].capitalize()}{suffix.title()}/x.lua")
    for name in sorted(guard._RESERVED_NAMES):
        out.append(f"WTF/{name}")
        out.append(f"WTF/{name.upper()}.txt")
        out.append(f"Interface/AddOns/Foo/{name}.lua")
    for char in sorted(guard._FORBIDDEN_CHARACTERS):
        out.append(f"WTF/a{char}b")
    return out


GATE_PATHS = list(dict.fromkeys([*GATE_PATHS, *_from_guard_tables()]))


def test_the_copied_tables_equal_guards() -> None:
    assert verdict._ROOTS == guard._ROOTS
    assert verdict._ADDONS == guard._ADDONS
    assert verdict._EXECUTABLE_SUFFIXES == guard._EXECUTABLE_SUFFIXES
    assert verdict._VERSIONED_SHARED_LIBRARY.pattern == guard._VERSIONED_SHARED_LIBRARY.pattern
    assert verdict._VERSIONED_SHARED_LIBRARY.flags == guard._VERSIONED_SHARED_LIBRARY.flags
    assert verdict._RESERVED_NAMES == guard._RESERVED_NAMES
    assert verdict._FORBIDDEN_CHARACTERS == guard._FORBIDDEN_CHARACTERS
    assert verdict._MAX_NAME_BYTES == guard._MAX_NAME_BYTES


@pytest.mark.parametrize("path", GATE_PATHS, ids=lambda p: f"constructed-{p!r}")
def test_restorable_agrees_with_the_write_gate(path: str) -> None:
    try:
        guard._check_rel(path)
    except guard.PathNotAllowedError:
        gate = False
    else:
        gate = True
    assert restorable("flavor", path) is gate
    assert verdict_of(None, "flavor", path).restorable is gate
    assert restorable("root", path) is False
    assert restorable(None, path) is False


def test_a_no_file_the_gate_refuses_is_never_written() -> None:
    """A `no` row outside what the gate writes gets the plain words."""
    entry = _entry("savedvariables-backup")
    v = verdict_of(entry, "flavor", "wtf/Account/A/SavedVariables/X.lua.bak")
    assert v.restorable is False
    assert v.words == "no; wowlab never writes it"


def test_every_folder_has_no_restorable_answer() -> None:
    """A folder is judged by the files under it, whatever its row's level."""
    addon = verdict_of(_entry("addon"), "flavor", "Interface/AddOns/Foo", folder=True)
    assert addon.level == "gate"
    assert addon.restorable is None
    logs = verdict_of(_entry("logs-other"), "flavor", "Logs", folder=True)
    assert logs.level == "no"
    assert logs.restorable is None
    fonts = verdict_of(_entry("fonts"), "flavor", "Fonts", folder=True)
    assert fonts.restorable is None
    unknown = verdict_of(None, "flavor", "WTF/Weird-constructed", folder=True)
    assert unknown == Verdict(
        level="unclassified",
        restorable=None,
        server_may_replace=False,
        words="no file-map row; wowlab does not know what this folder is",
    )


def test_tier_words_keep_an_unknown_tier_as_it_is() -> None:
    assert tier_words("B") == "B: works, but Blizzard does not support it and a patch can reset it"
    assert tier_words("Z-constructed") == "Z-constructed"
