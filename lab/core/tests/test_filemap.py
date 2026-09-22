"""The file map: one data source for `docs/LAB_FILE_MAP.md` and `classify()`.

Sync direction: `lab/core/src/wowlab_core/filemap.toml` is the source; the
doc's tables are rendered from it (`scripts/gen_file_map.py --write`).
`test_doc_tables_are_rendered_from_the_data` fails when either side is edited
alone. The rest pins the matcher on its own vocabulary; paths here are
constructed strings (no filesystem), labelled `constructed`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from wowlab_core import filemap
from wowlab_core.filemap import FileMap, load, parse, render_doc, render_section

REPO = Path(__file__).resolve().parents[3]
DOC = REPO / "docs" / "LAB_FILE_MAP.md"
DATA = REPO / "lab" / "core" / "src" / "wowlab_core" / filemap.DATA_FILE


def test_doc_tables_are_rendered_from_the_data() -> None:
    doc = DOC.read_text(encoding="utf-8")
    assert render_doc(doc) == doc, (
        "docs/LAB_FILE_MAP.md and filemap.toml have drifted; edit the TOML and run "
        "`uv run python scripts/gen_file_map.py --write`"
    )


def test_every_doc_table_row_is_a_data_row() -> None:
    """Belt and braces: each row between the markers is exactly one entry,
    in data order, and no other table in the doc looks like a file-map row."""
    doc = DOC.read_text(encoding="utf-8")
    fm = load()
    for section in fm.sections:
        block = doc.split(f"<!-- filemap:begin {section.id} -->\n", 1)[1]
        block = block.split(f"<!-- filemap:end {section.id} -->", 1)[0]
        rows = block.splitlines()[2:]
        expected = [e for e in fm.entries if e.section == section.id]
        assert [r.split(" | ", 1)[0].removeprefix("| ") for r in rows] == [
            e.doc_path for e in expected
        ]
    headed = [line for line in doc.splitlines() if line.startswith("| Path | What |")]
    assert len(headed) == len(fm.sections), "a file-map table outside the markers"


def test_drift_is_detected_constructed() -> None:
    doc = DOC.read_text(encoding="utf-8")
    edited = doc.replace("| Account keybinds |", "| Account key bindings |")
    assert edited != doc
    assert render_doc(edited) == doc, "a hand edit inside the markers is overwritten"
    first = load().entries[0]
    renamed = first.model_copy(update={"what": first.what + " (edited)"})
    fm = FileMap(sections=load().sections, entries=(renamed, *load().entries[1:]))
    assert render_doc(doc, fm) != doc, "a data edit is not in the doc"


def test_missing_or_extra_markers_are_refused_constructed() -> None:
    doc = DOC.read_text(encoding="utf-8")
    without = re.sub(r"<!-- filemap:(begin|end) root -->\n?", "", doc)
    with pytest.raises(ValueError, match="'root' has 0 marked blocks"):
        render_doc(without)
    extra = doc + "\n<!-- filemap:begin nosuch -->\n<!-- filemap:end nosuch -->\n"
    with pytest.raises(ValueError, match="names no section"):
        render_doc(extra)


def test_generator_script_check_passes() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_file_map", REPO / "scripts/gen_file_map.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0


def test_packaged_data_is_the_repository_file() -> None:
    assert load() == parse(DATA.read_text(encoding="utf-8"))


def test_render_section_shape() -> None:
    table = render_section("root").splitlines()
    assert table[0] == "| Path | What | Written by / when | Edit | Tier | Module |"
    assert table[1] == "|---|---|---|---|---|---|"
    assert table[2].startswith("| `.build.info` |")


def test_patterns_name_no_flavor_product_interface_or_build() -> None:
    """L6: nothing classify() uses names a flavor. Display text may (the
    doc's executable row lists example names); patterns may not."""
    forbidden = re.compile(r"_[a-z]+_|wow_|wowt|\b\d{5,6}\b|retail|classic|beta|ptr", re.I)
    for e in load().entries:
        for pattern in (*e.match, *e.exclude):
            assert not forbidden.search(pattern), f"{e.id}: {pattern!r}"


@pytest.mark.parametrize(
    "text",
    [
        '[[section]]\nid = "a"\ntitle = "A"\n',
        '[[section]]\nid = "a"\ntitle = "A"\n'
        + (
            '[[entry]]\nid = "x"\nsection = "b"\n'
            'base = "root"\ndoc_path = "p"\nwhat = "w"\nwritten_by = "c"\nedit = "no"\n'
            'tier = "A"\nmodule = "none"\nmatch = ["x"]\n'
        ),
        '[[section]]\nid = "a"\ntitle = "A"\n'
        + (
            '[[entry]]\nid = "x"\nsection = "a"\n'
            'base = "root"\ndoc_path = "p | q"\nwhat = "w"\nwritten_by = "c"\nedit = "no"\n'
            'tier = "A"\nmodule = "none"\nmatch = ["x"]\n'
        ),
        '[[section]]\nid = "a"\ntitle = "A"\n'
        + (
            '[[entry]]\nid = "x"\nsection = "a"\n'
            'base = "root"\ndoc_path = "p"\nwhat = "w"\nwritten_by = "c"\nedit = "no"\n'
            'tier = "A"\nmodule = "none"\nmatch = ["../x"]\n'
        ),
    ],
    ids=[
        "constructed-empty-section",
        "constructed-unknown-section",
        "constructed-pipe-in-cell",
        "constructed-dotdot-pattern",
    ],
)
def test_incoherent_data_is_refused_constructed(text: str) -> None:
    with pytest.raises(ValueError):
        parse(text)


def _id(parts: str, base: str = "flavor", kind: str = "file") -> str | None:
    found = load().classify(tuple(parts.split("/")) if parts else (), base, kind)  # type: ignore[arg-type]
    return found.id if found else None


@pytest.mark.parametrize(
    ("path", "kind", "expected"),
    [
        ("", "dir", "flavor-folder"),
        ("WTF/Account/ACC/1/", "dir", "numeric-realm-folder"),
        ("WTF/Account/ACC/Area 52/", "dir", "realm-folder"),
        ("WTF/Account/ACC/1/First-Second/", "dir", "second-name-character-folder"),
        ("WTF/Account/ACC/Area 52/First/", "dir", "character-folder"),
        ("WTF/Account/ACC/SavedVariables/", "dir", "savedvariables-folder"),
        ("WTF/Account/ACC/Area 52/First/SavedVariables/", "dir", "savedvariables-folder"),
        ("WTF/Account/ACC/SavedVariables/Foo.lua", "file", "account-savedvariables"),
        ("WTF/Account/ACC/SavedVariables/Foo.lua.bak", "file", "savedvariables-backup"),
        ("WTF/Account/ACC/SavedVariables.lua", "file", "account-blizzard-savedvariables"),
        ("WTF/Account/ACC/SavedVariables.lua.bak", "file", "savedvariables-backup"),
        ("WTF/Account/ACC/1/F-S/SavedVariables/Foo.lua", "file", "character-savedvariables"),
        ("WTF/Account/ACC/R/F/SavedVariables/Foo.lua.bak", "file", "savedvariables-backup"),
        ("WTF/Account/ACC/R/F/AddOns.txt", "file", "character-addons-txt"),
        ("wtf/account/acc/r/f/addons.TXT", "file", "character-addons-txt"),
        ("Interface/AddOns/Foo/", "dir", "addon"),
        ("Interface/AddOns/Foo/Libs/x.lua", "file", "addon"),
        ("Interface/AddOns/Blizzard_Foo/", "dir", "blizzard-addon-export"),
        ("Interface/AddOns/Blizzard_Foo/Foo.lua", "file", "blizzard-addon-export"),
        ("Interface/AddOns/", "dir", "addons-folder"),
        ("Interface/", "dir", "interface-folder"),
        ("Interface/Icons/Foo.blp", "file", "interface-override"),
        ("Interface/Foo.blp", "file", "interface-override"),
        ("Interface/Icons/", "dir", "interface-override"),
        ("Fonts/FRIZQT__.TTF", "file", "fonts"),
        ("Cache/WDB/enUS/creaturecache.wdb", "file", "wdb-cache"),
        ("Logs/WoWCombatLog-092226_120000.txt", "file", "combat-log"),
        ("Logs/Client.log", "file", "diagnostic-logs"),
        ("Screenshots/WoWScrnShot_092226_120000.jpg", "file", "screenshots"),
        ("Some.exe", "file", "client-executable"),
        ("Some Client.app/", "dir", "client-executable"),
        ("Interface/AddOns/Foo/.DS_Store", "file", "os-metadata"),
        ("WTF/Account/ACC/unknown-new-file.txt", "file", None),
        ("WTF/Account/ACC/Config.wtf", "file", None),
        ("Interface/AddOns", "file", None),
        ("WTF/Account/ACC/SavedVariables/sub/", "dir", None),
    ],
    ids=lambda v: f"constructed-{v}" if isinstance(v, str) and "/" in v else None,
)
def test_matcher_on_constructed_paths(path: str, kind: str, expected: str | None) -> None:
    assert _id(path.rstrip("/"), "flavor", kind) == expected


@pytest.mark.parametrize(
    ("path", "kind", "expected"),
    [
        (".build.info", "file", "build-info"),
        ("Data/data/data.000", "file", "casc-data"),
        ("Data/indices/", "dir", "casc-data"),
        (".DS_Store", "file", "os-metadata"),
        ("WTF/Config.wtf", "file", None),
        ("Data/other", "file", None),
    ],
    ids=lambda v: f"constructed-root-{v}" if isinstance(v, str) and "." in v else None,
)
def test_root_rows_on_constructed_paths(path: str, kind: str, expected: str | None) -> None:
    assert _id(path.rstrip("/"), "root", kind) == expected


def test_unknown_kind_matches_either_constructed() -> None:
    assert _id("WTF/Account/ACC/1", "flavor", "any") == "numeric-realm-folder"
    assert _id("WTF/Account/ACC/config-cache.wtf", "flavor", "any") == "account-config-cache"


def test_entries_expose_the_edit_column() -> None:
    fm = load()
    assert fm.entry("config-wtf").gated
    assert fm.entry("tts-cache").gated, "gate (server may replace) is still gated"
    assert not fm.entry("savedvariables-backup").gated
    assert not fm.entry("account-folder").gated
    with pytest.raises(KeyError):
        fm.entry("no-such-entry")
