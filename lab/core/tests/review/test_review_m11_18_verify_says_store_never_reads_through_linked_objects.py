# Probe from review of m11/18-snapshot-create-links; reproduces `snap verify` telling the owner the store never reads through a linked `objects/` while `read_file` (and so guard's restore and `snap diff`) reads through it
"""M11-18 changes `snap verify`'s line for a linked `objects/` from
"corrupt object: ." to

    objects/ is a link: nothing under it was checked, and the store never
    reads or writes through it

The second half is not true of reads. `SnapshotStore.read_object` opens
`objects/<ab>/<rest>` by path with `O_NOFOLLOW` on the last component only
(M11-11), so it follows a linked `objects/`; `read_file`, guard's restore,
undo and rollback, and `snap diff` all go through it. The ticket itself says
so ("guard can still restore through the link"), and so does the new comment
block in `snapshot.py` ("a read through the link still finds them"). An
owner reading the CLI line would believe a restore cannot take bytes from
the outside directory, which is what it does.

Expected (either is a fix): the CLI line makes no claim that the store never
reads through the link, or reads through a linked `objects/` are refused.

Constructed input (our own store format), POSIX symlinks only. The user data
directory is redirected into `tmp_path`; nothing touches an install. The
positive controls show the line is printed and that the read really goes
through the link, so the failure is the wording, not the setup.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, snapshot
from wowlab_core.snapshot import SnapshotStore

CONFIG_BYTES = b'SET portal "US"\n'

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "symlink"),
    reason="POSIX symbolic links (creating them needs privileges on Windows)",
)


def _linked_objects_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    redirected = tmp_path / "redirected-userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    root = tmp_path / "Install"
    (root / "WTF").mkdir(parents=True)
    (root / "WTF" / "Config.wtf").write_bytes(CONFIG_BYTES)
    store = SnapshotStore(snapshot.default_store_path())
    assert redirected in store.path.parents
    manifest = store.create(root, ["WTF"], now=datetime(2026, 9, 28, 12, tzinfo=UTC))
    outside = tmp_path / "objects-elsewhere"
    store.objects_dir.rename(outside)
    store.objects_dir.symlink_to(outside, target_is_directory=True)
    result = CliRunner().invoke(cli.app, ["snap", "verify"])
    return manifest.id, result.stdout + result.stderr


def test_constructed_positive_control_the_read_goes_through_the_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id, out = _linked_objects_store(tmp_path, monkeypatch)
    assert "objects/ is a link" in out
    store = SnapshotStore(snapshot.default_store_path())
    assert store.read_file(snapshot_id, "WTF/Config.wtf") == CONFIG_BYTES


def test_constructed_verify_does_not_claim_reads_never_go_through_a_linked_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_id, out = _linked_objects_store(tmp_path, monkeypatch)
    store = SnapshotStore(snapshot.default_store_path())
    try:
        store.read_file(snapshot_id, "WTF/Config.wtf")
    except (snapshot.SnapshotError, OSError):
        return  # reads refuse the link: the CLI claim is then true
    assert "never reads" not in out, (
        "snap verify says the store never reads through the linked objects/, "
        "but read_file just read the object through it:\n" + out
    )
