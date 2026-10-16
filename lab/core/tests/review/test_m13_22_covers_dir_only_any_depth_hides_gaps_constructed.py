# Probe from review of m13/22-whole-install-gaps (80036ce); reproduces FileMap.covers answering yes for a dirs-only `X/**/` row, so `filemap gaps` skips files below X that have no row.
"""M13-22, §15.6: `filemap gaps` lists every path with no file-map row.
`FileMap.covers` lets the walk skip a folder when one `.../**` row matches
everything below it. `_covers_below` looks at `_segments(p)`, which strips a
trailing `/`, so a match pattern `X/**/` (legal in the grammar: "a match
pattern ending in `/` matches directories only") is taken as covering every
path below `X`. It matches only folders: a file below `X` has no row, and
the walk never looks for it.

Constructed (L8, boundary): `filemap.toml` holds no `**/` pattern today, so
a one-entry map is built; the install is the real `macos` capture's
`.build.info` and `.flavor.info` in a constructed flavor folder in
`tmp_path`. Only the flavor folder's groups are compared.
Positive control: with `X/**` (any kind) the same file is not a gap, and
with no `X/**` row at all it is.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from wowlab_core import filemap, gaps, install
from wowlab_core.filemap import FileMap, FileMapEntry, Section

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FLAVOR = "_classic_beta_"  # tests may name it (L6)


def _map(*match: str) -> FileMap:
    rows = [
        FileMapEntry(
            id="flavor-folder",
            section="s",
            base="flavor",
            doc_path=".",
            what="w",
            written_by="w",
            edit="—",
            tier="t",
            module="m",
            match=(".",),
        )
    ]
    if match:
        rows.append(
            FileMapEntry(
                id="x",
                section="s",
                base="flavor",
                doc_path="X/",
                what="w",
                written_by="w",
                edit="—",
                tier="t",
                module="m",
                match=match,
            )
        )
    return FileMap(sections=(Section(id="s", title="t"),), entries=tuple(rows))


def _gap_paths(fm: FileMap, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    root = tmp_path / "World of Warcraft"
    (root / FLAVOR / "X" / "sub").mkdir(parents=True)
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copy2(FIXTURES / "macos" / "forever" / ".flavor.info", root / FLAVOR / ".flavor.info")
    (root / FLAVOR / "X" / "sub" / "orphan-constructed.bin").write_bytes(b"abc")
    monkeypatch.setattr(filemap, "load", lambda: fm)
    report = gaps.find_gaps(install.discover(root))
    assert report.flavor_folders == [FLAVOR]
    found = [g.path for group in report.groups if group.base == "flavor" for g in group.gaps]
    return [path for path in found if path != ".flavor.info"]  # no row in this one-entry map


def test_covers_is_no_for_a_dirs_only_any_depth_row_constructed() -> None:
    fm = _map("X/", "X/**/")
    assert fm.classify(("X", "f.txt"), "flavor", "file") is None  # the file has no row
    assert not fm.covers(("X",), "flavor")


def test_a_file_below_a_dirs_only_any_depth_row_is_a_gap_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "X/sub/orphan-constructed.bin" in _gap_paths(_map("X/", "X/**/"), tmp_path, monkeypatch)


def test_positive_control_any_kind_row_covers_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _gap_paths(_map("X/", "X/**"), tmp_path, monkeypatch) == []


def test_positive_control_no_row_is_a_gap_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _gap_paths(_map(), tmp_path, monkeypatch) == ["X"]
