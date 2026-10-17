# Probe from review of m13/35-tree-link-test; reproduces a symlink loop inside the install reported as pointing "outside the install"
"""Constructed (boundary, not a real capture): a symlink that points at
itself, and a two-link loop, both inside the install. Their link text reads
fine, but nothing says where they point: `Path.resolve()` raises
RuntimeError ("Symlink loop") on Python 3.12, and `layout.link_facts` turns
that exception into `inside=False`, so `tree` prints "(outside the install)"
for a link whose text names a sibling inside the install. The docstring of
`link_facts` and `Symlink.inside_install` (M13-35 round 1) say `None` is the
answer when "resolving it ends at the link itself, so nothing says where it
points"; a loop is that case. The positive control is an ordinary symlink to
a folder inside the install, which says "inside". Nothing reads or writes a
real install.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wowlab_core import layout


def _symlink(link: Path, text: str) -> None:
    try:
        link.symlink_to(text)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    install = tmp_path / "wow"
    (install / "folder").mkdir(parents=True)
    return install.resolve()


def test_positive_control_an_ordinary_link_inside_says_inside_constructed(root: Path) -> None:
    _symlink(root / "plain", "folder")
    assert layout.link_facts(root / "plain", root) == ("folder", True)


def test_a_link_to_itself_is_not_said_to_point_outside_constructed(root: Path) -> None:
    _symlink(root / "self", "self")
    target, inside = layout.link_facts(root / "self", root)
    assert target == "self"
    assert inside is None, f"a self-loop inside the install reads as inside={inside!r}"


def test_a_two_link_loop_is_not_said_to_point_outside_constructed(root: Path) -> None:
    _symlink(root / "a", "b")
    _symlink(root / "b", "a")
    for name, text in (("a", "b"), ("b", "a")):
        target, inside = layout.link_facts(root / name, root)
        assert target == text
        assert inside is not False, f"{name} -> {text} reads as outside the install"
    assert os.path.lexists(root / "a")
