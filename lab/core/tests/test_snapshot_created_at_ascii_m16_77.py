"""An imported snapshot's date is ASCII digits only (M16-77).

`check_foreign_manifest` holds a manifest from elsewhere (`add_snapshot`,
`snap import`, M16-04) to a well-formed `created_at` that is the moment its
id carries and no later than this computer's clock, so a snapshot from the
future cannot stay the newest (`latest`, `~N`) for ever. Its date pattern
matched `\\d` as Unicode digits and `int()` reads those, so a `created_at`
spelled in Arabic-Indic or fullwidth digits passed as the id's own moment.
`resolve_reference` orders snapshots by `created_at` as text, and every such
digit sorts after every ASCII one, so the import stayed `latest` and `~0`
whatever its date: the refusal of snapshots from the future was bypassed.
The rule: such a date is malformed and the import is refused.

All inputs are constructed (hostile-input, L8): the archive a real
`export_snapshot` wrote from `test_snaparchive.py`'s constructed tree, with
its manifest re-dated (and its id remade, so the fingerprint holds) as an
attacker who writes the archive can. Stores live under `tmp_path`; nothing
reads or writes an install. Each `ascii-control` test shows the same
archive with an ASCII date imports, so each other test fails for the right
reason without the fix.
"""

# ruff: noqa: F811  (fixtures imported from test_snaparchive are requested by name)
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_snaparchive import (
    _forge,
    _other_tree,
    _refused,
    _replace_manifest,
    archive,  # noqa: F401
    source,  # noqa: F401
)

from wowlab_core import snaparchive, snapshot
from wowlab_core.snapshot import ManifestIntegrityError, SnapshotStore

NON_ASCII_ZEROS = [
    pytest.param(0x0660, id="constructed-arabic-indic"),
    pytest.param(0x06F0, id="constructed-extended-arabic-indic"),
    pytest.param(0x0966, id="constructed-devanagari"),
    pytest.param(0xFF10, id="constructed-fullwidth"),
    pytest.param(None, id="constructed-one-arabic-indic-digit-among-ascii"),
]
# Older than any snapshot the receiving player takes in a test.
WHEN = datetime(2020, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)
STAMP = f"{WHEN:%Y%m%dT%H%M%S}.{WHEN.microsecond:06d}Z"
CREATED_AT = f"{WHEN:%Y-%m-%dT%H:%M:%S}.{WHEN.microsecond:06d}Z"


def _spell(zero: int | None) -> str:
    """`CREATED_AT` in the digit set whose zero is `zero`; None changes only
    its last digit, to the Arabic-Indic digit of the same value."""
    if zero is None:
        return CREATED_AT[:-2] + chr(0x0660 + int(CREATED_AT[-2])) + "Z"
    return "".join(chr(zero + int(c)) if c.isdigit() else c for c in CREATED_AT)


def _dated(manifest: snapshot.Manifest, created_at: str) -> bytes:
    """`manifest` with the id moment `STAMP` and the date `created_at`."""
    return _forge(manifest, STAMP, created_at=created_at)


def _receiving(tmp_path: Path) -> tuple[SnapshotStore, snapshot.Manifest]:
    """A store with one snapshot the player took now: newer than `WHEN`."""
    store = SnapshotStore(tmp_path / "store-b")
    own = store.create(_other_tree(tmp_path), ["x"], label="mine")
    return store, own


def test_an_ascii_date_is_accepted_and_sorts_by_its_moment_ascii_control(
    tmp_path: Path, source: tuple[SnapshotStore, snapshot.Manifest], archive: Path
) -> None:
    _, manifest = source
    data = _dated(manifest, CREATED_AT)
    snapshot.check_foreign_manifest(snapshot.parse_manifest(data))
    store, own = _receiving(tmp_path)
    report = snaparchive.import_snapshot(store, _replace_manifest(archive, data))
    assert report.added
    assert snapshot.resolve_reference(store, "latest") == own.id
    assert snapshot.resolve_reference(store, "~0") == own.id
    assert snapshot.resolve_reference(store, "~1") == snapshot.parse_manifest(data).id


@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_check_foreign_manifest_refuses_a_non_ascii_digit_date(
    source: tuple[SnapshotStore, snapshot.Manifest], zero: int | None
) -> None:
    _, manifest = source
    spelled = _spell(zero)
    assert not spelled.isascii()
    forged = snapshot.parse_manifest(_dated(manifest, spelled))
    with pytest.raises(ManifestIntegrityError, match="malformed"):
        snapshot.check_foreign_manifest(forged)


@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_import_refuses_a_non_ascii_digit_date(
    tmp_path: Path,
    source: tuple[SnapshotStore, snapshot.Manifest],
    archive: Path,
    zero: int | None,
) -> None:
    """`snap import`'s path refuses it and adds nothing."""
    _, manifest = source
    hostile = _replace_manifest(archive, _dated(manifest, _spell(zero)))
    _refused(tmp_path, hostile, ManifestIntegrityError, "malformed")


@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_a_non_ascii_digit_date_never_becomes_latest(
    tmp_path: Path,
    source: tuple[SnapshotStore, snapshot.Manifest],
    archive: Path,
    zero: int | None,
) -> None:
    """Dated 2020 in non-ASCII digits, it sorted after the player's own
    snapshot taken now. The import is refused, so `latest` and `~0` still
    name the player's own."""
    _, manifest = source
    store, own = _receiving(tmp_path)
    hostile = _replace_manifest(archive, _dated(manifest, _spell(zero)))
    with pytest.raises(ManifestIntegrityError):
        snaparchive.import_snapshot(store, hostile)
    assert [m.id for m in store.list()] == [own.id]
    assert snapshot.resolve_reference(store, "latest") == own.id
    assert snapshot.resolve_reference(store, "~0") == own.id
