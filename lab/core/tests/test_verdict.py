"""`wowlab_core.verdict`: the file map's Edit cell as a level and in words (M13-21).

The first test walks the packaged `filemap.toml`, so a new Edit cell outside
the fixed mapping fails it. The rest use rows from the packaged map. The
constructed inputs are Edit cells that are not in it and path strings held
to the write gate's rule (no filesystem), labelled `constructed`. The
link tests (M13-25 round 2) copy the captured tree (`fixtures/macos/`) into
`tmp_path` and add constructed symlinks; nothing touches a real install.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from wowlab_core import cli, filemap, guard, verdict
from wowlab_core.filemap import FileMapEntry
from wowlab_core.verdict import Verdict, edit_level, restorable, tier_words, verdict_of

_CAPTURE = Path(__file__).resolve().parent / "fixtures" / "macos"
_FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
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


def test_a_no_folder_names_files_for_paths() -> None:
    """`snap restore --paths` takes file paths: a `no` folder in the gate's
    area points at the files in it, not at itself (domain review, #195)."""
    entry = _entry("blizzard-addon-export")
    assert entry.edit == "no"
    v = verdict_of(entry, "flavor", "Interface/AddOns/Blizzard_Foo", folder=True)
    assert v == Verdict(
        level="no",
        restorable=None,
        server_may_replace=False,
        words="no; wowlab leaves the files in it alone "
        "(a restore writes one only if you name that file with --paths)",
    )
    outside = verdict_of(_entry("logs-other"), "flavor", "Logs", folder=True)
    assert outside.words == "no; wowlab never writes it"


_LINK = (
    "a link, or inside one; wowlab never follows it or writes through it, "
    "and a restore does not put it back"
)


@pytest.mark.parametrize(
    ("entry_id", "path", "folder", "level", "words"),
    [
        ("addon", "Interface/AddOns/Linked-constructed", True, "gate", _LINK),
        ("addon", "Interface/AddOns/Linked-constructed/x.toc", False, "gate", _LINK),
        ("wtf-folder", "WTF", True, "folder", _LINK),
        ("savedvariables-backup", "WTF/A/SavedVariables/x.lua.bak", False, "no", _LINK),
        ("blizzard-addon-export", "Interface/AddOns/Blizzard_Foo", True, "no", _LINK),
        ("screenshots", "Screenshots", True, "not_applicable", _LINK),
        (
            "fonts",
            "Fonts/FRIZQT__.TTF",
            False,
            "gate",
            _LINK + "; unsupported by Blizzard, a patch can reset it",
        ),
        (None, "WTF/Linked-constructed", True, "unclassified", "no file-map row; " + _LINK),
        (None, "WTF/Linked-constructed/x.txt", False, "unclassified", "no file-map row; " + _LINK),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_a_link_has_link_words_at_every_level(
    entry_id: str | None, path: str, folder: bool, level: str, words: str
) -> None:
    """The gate refuses any path through a link, a snapshot never captures
    one, and tree never enters one: no level's own words apply."""
    entry = _entry(entry_id) if entry_id is not None else None
    v = verdict_of(entry, "flavor", path, folder=folder, link=True)
    assert v.level == level
    assert v.restorable is None
    assert v.words == words
    assert "puts it back" not in v.words and "yes" not in v.words


def test_a_linked_server_may_replace_row_keeps_the_fact() -> None:
    entry = next(e for e in filemap.load().entries if e.edit == "gate (server may replace)")
    v = verdict_of(entry, "flavor", "WTF/x", link=True)
    assert v.server_may_replace is True
    assert v.words == _LINK


def test_tier_words_keep_an_unknown_tier_as_it_is() -> None:
    assert tier_words("B") == "B: works, but Blizzard does not support it and a patch can reset it"
    assert tier_words("Z-constructed") == "Z-constructed"


# ─── the badge label (M13-25, owner 2026-10-16) ──────────────────────────────

_OWNER_LABELS = {
    "gate": "wowlab can edit",
    "no": "wowlab won't touch",
    "folder": "folder",
    "not_applicable": "not used",
    "unclassified": "unknown",
}


def test_every_level_has_the_owners_label() -> None:
    assert verdict.LABELS == _OWNER_LABELS
    assert verdict.SERVER_LABEL == "server may overwrite"


def test_every_row_and_no_row_carry_their_label() -> None:
    for entry in filemap.load().entries:
        for folder in (False, True):
            found = verdict_of(entry, entry.base, "WTF/x", folder=folder)
            assert found.label == _OWNER_LABELS[found.level], entry.id
            expected = "server may overwrite" if found.server_may_replace else None
            assert found.server_label == expected, entry.id
    unknown = verdict_of(None, "flavor", "WTF/x")
    assert (unknown.label, unknown.server_label, unknown.link) == ("unknown", None, False)
    linked = verdict_of(None, "flavor", "WTF/x", link=True)
    assert (linked.label, linked.server_label, linked.link) == (_LINK_LABEL, None, True)


# ─── a link's label (M13-25 round 2) ─────────────────────────────────────────

_LINK_LABEL = "a link; wowlab won't follow it"


def test_a_link_gets_the_link_label_at_every_row() -> None:
    """A link keeps its row's level but never reads as editable: the gate
    refuses any path through a link, whatever the row says."""
    assert verdict.LINK_LABEL == _LINK_LABEL
    for entry in filemap.load().entries:
        for folder in (False, True):
            v = verdict_of(entry, "flavor", "Interface/AddOns/X", folder=folder, link=True)
            assert v.link is True, entry.id
            assert v.label == _LINK_LABEL, entry.id
            assert v.level == edit_level(entry.edit), entry.id
            assert "wowlab can edit" not in (v.label, v.server_label), entry.id
            plain = verdict_of(entry, "flavor", "Interface/AddOns/X", folder=folder)
            assert plain.link is False and plain.label == _OWNER_LABELS[plain.level], entry.id


def _linked_install(tmp_path: Path) -> Path:
    """The captured tree (`fixtures/macos/`) with a symlinked addon folder
    under `Interface/AddOns/` and a linked folder holding a file under
    `WTF/` (both constructed)."""
    root = tmp_path / "wow"
    root.mkdir()
    shutil.copy2(_CAPTURE / ".build.info", root / ".build.info")
    flavor = root / _FLAVOR
    shutil.copytree(_CAPTURE / "forever", flavor)
    outside = tmp_path / "outside-constructed"
    (outside / "LinkedAddon").mkdir(parents=True)
    (outside / "LinkedAddon" / "LinkedAddon.toc").write_text("## Interface: 1\n", encoding="utf-8")
    (outside / "LinkedWTF").mkdir()
    (outside / "LinkedWTF" / "Config.wtf").write_text('SET a "1"\n', encoding="utf-8")
    try:
        (flavor / "Interface" / "AddOns" / "LinkedAddon").symlink_to(
            outside / "LinkedAddon", target_is_directory=True
        )
        (flavor / "WTF" / "Linked").symlink_to(outside / "LinkedWTF", target_is_directory=True)
    except OSError:
        pytest.skip("this system does not let the test create a symlink")
    return root


def _cli_json(*args: str) -> Any:
    result = CliRunner().invoke(cli.app, [*args, "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_a_symlinked_addon_is_drawn_as_a_link_constructed(tmp_path: Path) -> None:
    root = _linked_install(tmp_path)
    items = {i["path"]: i for i in _cli_json("tree", "--root", str(root), "--explain")["items"]}
    linked = items["Interface/AddOns/LinkedAddon"]
    assert linked["kind"] == "symlink"
    assert linked["verdict"]["link"] is True
    assert linked["verdict"]["label"] == _LINK_LABEL
    assert linked["verdict"]["words"].startswith("a link, or inside one")
    assert items["Interface/AddOns/DBM-Brawlers"]["verdict"]["link"] is False
    report = _cli_json("explain", "--root", str(root), f"{_FLAVOR}/Interface/AddOns/LinkedAddon")
    assert (report["verdict"]["link"], report["verdict"]["label"]) == (True, _LINK_LABEL)


def test_a_file_inside_a_linked_folder_is_a_link_constructed(tmp_path: Path) -> None:
    root = _linked_install(tmp_path)
    report = _cli_json("explain", "--root", str(root), f"{_FLAVOR}/WTF/Linked/Config.wtf")
    assert report["verdict"]["link"] is True
    assert report["verdict"]["label"] == _LINK_LABEL
    assert report["verdict"]["restorable"] is None
    direct = _cli_json("explain", "--root", str(root), f"{_FLAVOR}/WTF/Config.wtf")
    assert (direct["verdict"]["link"], direct["verdict"]["label"]) == (False, "wowlab can edit")


def test_the_label_is_additive_and_keeps_the_words() -> None:
    found = verdict_of(filemap.load().entry("config-wtf"), "flavor", "WTF/Config.wtf")
    assert list(found.model_dump()) == [
        "level",
        "restorable",
        "server_may_replace",
        "words",
        "label",
        "server_label",
        "link",
    ]
    assert found.words == _GATE
    assert found.link is False
    assert Verdict.model_validate_json(found.model_dump_json()) == found


@pytest.mark.parametrize(
    "fields",
    [
        {"level": "gate", "label": "wowlab won't touch"},
        {"level": "no", "label": "wowlab can edit"},
        {"level": "gate", "server_may_replace": False, "server_label": "server may overwrite"},
        {"level": "gate", "server_may_replace": True, "server_label": None},
        {"level": "gate", "link": True, "label": "wowlab can edit"},
        {"level": "gate", "link": False, "label": "a link; wowlab won't follow it"},
    ],
)
def test_a_label_that_disagrees_is_refused_constructed(fields: dict[str, object]) -> None:
    data: dict[str, object] = {"restorable": True, "server_may_replace": False, "words": "w"}
    data.update(fields)
    with pytest.raises(ValueError):
        Verdict.model_validate(data)
