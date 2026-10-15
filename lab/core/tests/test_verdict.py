"""`wowlab_core.verdict`: the file map's Edit cell as a level and in words (M13-21).

The first test walks the packaged `filemap.toml`, so a new Edit cell outside
the fixed mapping fails it. The rest use rows from the packaged map; the only
constructed inputs are Edit cells that are not in it, labelled `constructed`.
"""

from __future__ import annotations

import pytest

from wowlab_core import filemap
from wowlab_core.filemap import FileMapEntry
from wowlab_core.verdict import Verdict, edit_level, restorable, tier_words, verdict_of


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
            Verdict(
                level="gate",
                restorable=True,
                words="yes, through wowlab's write gate only, with the client closed",
            ),
        ),
        (
            "fonts",
            "flavor",
            "Fonts/FRIZQT__.TTF",
            Verdict(
                level="gate",
                restorable=True,
                words="yes, through wowlab's write gate only, with the client closed; "
                "unsupported by Blizzard, a patch can reset it",
            ),
        ),
        (
            "build-info",
            "root",
            ".build.info",
            Verdict(level="no", restorable=False, words="no; wowlab never writes it"),
        ),
        (
            "savedvariables-backup",
            "flavor",
            "WTF/Account/A/SavedVariables/X.lua.bak",
            Verdict(
                level="no",
                restorable=True,
                words="no; wowlab leaves it alone (a restore writes it only if you name it "
                "with --paths)",
            ),
        ),
        (
            "screenshots",
            "flavor",
            "Screenshots/a.jpg",
            Verdict(
                level="not_applicable", restorable=False, words="n/a; not a file wowlab touches"
            ),
        ),
        (
            "wtf-folder",
            "flavor",
            "WTF",
            Verdict(level="folder", restorable=True, words="—; a folder, see the files in it"),
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_the_verdict_for_a_row(entry_id: str, base: str, path: str, expected: Verdict) -> None:
    assert verdict_of(_entry(entry_id), base, path) == expected


def test_a_path_with_no_row_is_unclassified() -> None:
    v = verdict_of(None, "flavor", "WTF/unknown-constructed.txt")
    assert v == Verdict(level="unclassified", restorable=True, words="no file-map row")
    assert verdict_of(None, None, "/elsewhere").level == "unclassified"


def test_server_may_replace_gets_its_own_words() -> None:
    entry = next(e for e in filemap.load().entries if e.edit == "gate (server may replace)")
    assert verdict_of(entry, "flavor", "WTF/x").words == (
        "yes, through wowlab's write gate only, with the client closed; the server may "
        "replace it at login (sync settings: wowlab doctor)"
    )


@pytest.mark.parametrize(
    ("base", "path", "expected"),
    [
        ("flavor", "WTF/Config.wtf", True),
        ("flavor", "wtf/Config.wtf", True),
        ("flavor", "Interface/AddOns/X", True),
        ("flavor", "Fonts", True),
        ("flavor", "Logs/x.txt", False),
        ("flavor", "WTFx/y", False),
        ("root", "WTF/Config.wtf", False),
        (None, "WTF/Config.wtf", False),
    ],
)
def test_restorable_is_the_gate_subtrees_of_a_flavor(
    base: str | None, path: str, expected: bool
) -> None:
    assert restorable(base, path) is expected


def test_tier_words_keep_an_unknown_tier_as_it_is() -> None:
    assert tier_words("B") == "B: works, but Blizzard does not support it and a patch can reset it"
    assert tier_words("Z-constructed") == "Z-constructed"
