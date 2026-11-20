# Probe from review of m19/12-snap-rm-damaged; reproduces snap gc telling the player to remove a linked manifest with `snap rm`, which refuses links
"""Constructed store from the M11-03 capture (test_cli's fixtures): a link
planted in place of a manifest, pointing at a readable file outside the
store. `snap gc` refuses (right), but its new M19-12 sentence says
"`wowlab snap rm <full id> --break-undo` removes it", and `snap rm` of that
full id with `--break-undo` refuses the link. The refusal must not point at
a command that will refuse. Positive control: for a manifest cut short
(constructed), the same advice is true and `snap rm` removes it."""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    _create,
    _edit_syndicator,
    flavor,  # noqa: F401
    idle,  # noqa: F401
    replayed,  # noqa: F401
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

ON = b'"show_tooltips_on_shift"] = false'
OFF = b'"show_tooltips_on_shift"] = true'


def _two(flavor: Path) -> tuple[str, str]:
    a = _create("a")
    _edit_syndicator(flavor, ON, OFF)
    b = _create("b")
    return a.id, b.id


def test_positive_control_a_cut_manifest_is_removable_as_gc_says_constructed(
    flavor: Path, user_data: Path
) -> None:
    a_id, _ = _two(flavor)
    path = user_data / "store" / "manifests" / f"{a_id}.json"
    path.write_bytes(path.read_bytes()[:40])
    gc = run("snap", "gc", "--yes")
    assert gc.exit_code == 1 and "snap rm <full id> --break-undo" in gc.stderr
    assert run("snap", "rm", a_id, "--break-undo", "--yes").exit_code == 0


def test_gc_does_not_point_a_linked_manifest_at_snap_rm_constructed(
    flavor: Path, user_data: Path, tmp_path: Path
) -> None:
    a_id, _ = _two(flavor)
    path = user_data / "store" / "manifests" / f"{a_id}.json"
    outside = tmp_path / "elsewhere.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    gc = run("snap", "gc", "--yes")
    assert gc.exit_code == 1
    rm = run("snap", "rm", a_id, "--break-undo", "--yes")
    assert rm.exit_code == 1 and path.is_symlink(), "snap rm refuses the link"
    assert "snap rm <full id> --break-undo" not in gc.stderr, (
        "gc's refusal sends the player to snap rm, which refuses this: " + gc.stderr
    )
