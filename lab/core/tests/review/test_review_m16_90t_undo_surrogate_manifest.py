# Probe from review of m16/90t-history-path-graders; reproduces the undo grader's
# claim that a snapshot manifest cannot hold a lone surrogate (it can, in the
# store's canonical encoding; only the grader's own json.dumps rewrite breaks it).
"""Constructed. The undo grader of `test_guard_history_paths_m16_90.py` drops
its lone-surrogate case, saying a manifest cannot hold one. The store writes
lone surrogates in its canonical encoding (`snapshot.manifest_bytes`, module
docstring), and a manifest rewritten that way loads. The grader rewrites the
manifest with plain `json.dumps`, which is what does not load. So the case can
be graded by rewriting with `manifest_bytes`."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from wowlab_core.snapshot import Manifest, SnapshotStore, manifest_bytes


def _load(name: str, filename: str) -> Any:
    path = Path(__file__).resolve().parent.parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


T = _load("_review_m16_90t_undo_surrogate", "test_guard_history_paths_m16_90.py")
G = T.G
_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
world = G.world
install_root = G.install_root
flavor = G.flavor
idle = G.idle

ODD_ROOT = "/constructed/odd" + chr(0xDC80) + "folder/World of Warcraft"


def _manifest(guard: Any, tmp_path: Path, flavor: Any) -> tuple[SnapshotStore, str, Path]:
    store, _, raw = T.gate_record(guard, tmp_path, flavor)
    snapshots = SnapshotStore(store)
    file = snapshots.manifests_dir / f"{raw['snapshot_id']}.json"
    return snapshots, raw["snapshot_id"], file


def test_constructed_canonical_rewrite_holds_a_lone_surrogate(
    guard: Any, tmp_path: Path, flavor: Any, idle: None
) -> None:
    """Positive control: the store's own encoding carries the surrogate."""
    snapshots, sid, file = _manifest(guard, tmp_path, flavor)
    manifest = Manifest.model_validate_json(file.read_bytes())
    file.write_bytes(manifest_bytes(manifest.model_copy(update={"install_root": ODD_ROOT})))
    assert snapshots.show(sid).install_root == ODD_ROOT


def test_constructed_the_graders_rewrite_is_what_cannot_hold_it(
    guard: Any, tmp_path: Path, flavor: Any, idle: None
) -> None:
    """The defect: the grader's json.dumps rewrite is not the store's encoding."""
    snapshots, sid, file = _manifest(guard, tmp_path, flavor)
    data = json.loads(file.read_bytes().decode("utf-8"))
    data["install_root"] = ODD_ROOT
    file.write_bytes((json.dumps(data, ensure_ascii=True, indent=1) + "\n").encode("ascii"))
    try:
        snapshots.show(sid)
    except Exception:
        return
    raise AssertionError("the grader's rewrite loaded; the drop has some other reason")
