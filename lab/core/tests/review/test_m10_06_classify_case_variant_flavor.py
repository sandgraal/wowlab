# Probe from review of m10/06-layout-filemap-toc; reproduces classify() giving
# a different answer for the same existing file when the flavor folder or the
# install root is spelled in another case on a case-insensitive volume.
"""Every segment below the flavor folder is matched case-insensitively
(`filemap._segment` folds case; layout finds `WTF`, `Account` … by folded
name), but `layout._classify` compares the install root with `_is_within`
(exact `Path` parts) and the flavor folder with `rel[0] in flavor_folders`
(exact string). On macOS and Windows `<root>/_X_/WTF/Config.wtf` is the same
file as `<root>/_x_/WTF/Config.wtf`, yet the first is `Unclassified` ("no
file-map row matches", judged against the install-root rows) and a root
spelled `<…>/wow` gives "not inside the install root".

Positive control: the as-discovered spelling classifies as `config-wtf`.
Fix: when the path exists, compare the root and flavor folder by
`os.path.samefile`/`casefold` on case-insensitive volumes (or resolve the
first segments against the on-disk spelling, as `_child` already does).
Constructed in `tmp_path`; skipped on a case-sensitive volume.
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
    if not (root / "_X_" / "WTF" / "Config.wtf").exists():
        pytest.skip("case-sensitive volume: the spellings are different paths")
    return root


def _entry_id(found: object) -> str | None:
    return found.entry.id if isinstance(found, Classified) else None


def test_control_discovered_spelling_classifies_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)
    found = classify(root / "_x_" / "WTF" / "Config.wtf", discover(root))
    assert _entry_id(found) == "config-wtf"


def test_flavor_folder_in_another_case_classifies_the_same_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)
    found = classify(root / "_X_" / "WTF" / "Config.wtf", discover(root))
    assert _entry_id(found) == "config-wtf", found


def test_install_root_in_another_case_classifies_the_same_constructed(tmp_path: Path) -> None:
    root = _install(tmp_path)
    found = classify(tmp_path / "wow" / "_x_" / "WTF" / "Config.wtf", discover(root))
    assert _entry_id(found) == "config-wtf", found
