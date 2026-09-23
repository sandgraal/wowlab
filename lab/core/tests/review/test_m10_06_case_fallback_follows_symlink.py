# Probe from review of m10/06-layout-filemap-toc (fix round 1); reproduces
# classify() following a symlink whose name differs from the flavor folder or
# install root only in case, on a case-sensitive volume.
"""`_flavor_spelling` and `_relative_parts` accept a case-only spelling
difference when `Path.samefile` says the two are the same folder. `samefile`
follows symlinks. On a case-sensitive volume (Linux CI) `<root>/_X_` can be a
symlink to the discovered `<root>/_x_`: it is not the flavor folder, yet
`classify(<root>/_X_/WTF/Config.wtf)` returns the gated `config-wtf` row with
`flavor_folder="_x_"`. A symlink with any other name (`_y_`) is judged against
the install-root rows and is `Unclassified`, so a link is followed only when
its name happens to be a case variant. The same holds for a symlinked
case variant of the install root. LAB_PLAN §6.2 amendment (1) and the
`classify` docstring: symlinks are never followed; amendment (2): the case
fallback is for a case-insensitive volume.

Positive control: a symlink named `_y_` is not classified as the flavor.
Fix: compare with `os.lstat` (`os.path.samestat` of the two `lstat` results)
and refuse when the candidate is a link, so a case-insensitive volume still
matches (same inode) and a link never does. Constructed in `tmp_path`;
skipped on a case-insensitive volume, where `_X_` and `_x_` cannot coexist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core.install import discover
from wowlab_core.layout import Classified, classify

BUILD_INFO = (
    b"Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|Product!STRING:0|Version!STRING:0\n"
    b"eu|1|ab|wow_x|1.0.0.1\n"
)


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "WoW"
    flavor = root / "_x_"
    (flavor / "WTF").mkdir(parents=True)
    (flavor / "WTF" / "Config.wtf").write_bytes(b"SET a 1\n")
    (flavor / ".flavor.info").write_bytes(b"Product Flavor!STRING:0\nwow_x\n")
    (root / ".build.info").write_bytes(BUILD_INFO)
    if (root / "_X_").exists():
        pytest.skip("case-insensitive volume: _X_ and _x_ are one folder")
    return root


def _symlink(link: Path, target: str) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink: {exc}")


def test_control_other_named_link_is_not_the_flavor_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)
    _symlink(root / "_y_", "_x_")
    found = classify(root / "_y_" / "WTF" / "Config.wtf", discover(root))
    assert not isinstance(found, Classified), found


def test_case_variant_link_is_not_the_flavor_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)
    _symlink(root / "_X_", "_x_")
    found = classify(root / "_X_" / "WTF" / "Config.wtf", discover(root))
    assert not isinstance(found, Classified), found


def test_case_variant_link_to_the_root_is_not_inside_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)
    _symlink(tmp_path / "wow", "WoW")
    found = classify(tmp_path / "wow" / "_x_" / "WTF" / "Config.wtf", discover(root))
    assert not isinstance(found, Classified), found
