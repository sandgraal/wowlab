"""Snapshot ids are ASCII digits only (M16-77).

A snapshot id is `<8 digits>T<6 digits>.<6 digits>Z-<8 lowercase hex>`, the
moment it was taken plus a random part, and a manifest is stored as
`manifests/<id>.json` (`docs/LAB_PLAN.md` §6.9). The M16-70T and M16-70
reviews found `snapshot._ID_RE` matched the digits as Unicode digits, as
the journal's id check did: a manifest named and identified with
Arabic-Indic, fullwidth or other non-ASCII digits loaded as a snapshot.
`snap list` listed it, `snap show <id>` resolved it, and, since such a
digit sorts after every ASCII one, `latest` and `~0` offered it as the
newest snapshot. The rule: such a manifest is not a snapshot. It is a
manifest that does not load, reported as damaged like any other.

All inputs are constructed (hostile-input, L8): the manifest a real
`snap create` wrote is copied to a name whose digits are not ASCII, with
its `id` changed to match and its bytes in the store's canonical encoding.
The install is the captured tree copied into `tmp_path` by `test_cli.py`'s
fixtures, the user data directory is redirected and the process table is
a fake one with no client. Nothing reads or writes a real install. The
`ascii-control` test proves the planting itself works, so each other test
fails for the right reason without the fix.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    _state,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import snapshot

# The zero of each digit set; None spells one digit of an ASCII id in
# Arabic-Indic and keeps the rest.
NON_ASCII_ZEROS = [
    pytest.param(0x0660, id="constructed-arabic-indic"),
    pytest.param(0x06F0, id="constructed-extended-arabic-indic"),
    pytest.param(0x0966, id="constructed-devanagari"),
    pytest.param(0xFF10, id="constructed-fullwidth"),
    pytest.param(None, id="constructed-one-arabic-indic-digit-among-ascii"),
]
# The moment part of an id: `YYYYmmddTHHMMSS.ffffffZ`.
MOMENT = len("YYYYmmddTHHMMSS.ffffffZ")


def _spell(real_id: str, zero: int | None) -> str:
    """`real_id` with its moment's digits in the set whose zero is `zero`.
    None changes only the last microsecond digit, to the Arabic-Indic digit
    of the same value. The hex part is kept: it is the fingerprint of the
    entries, which `_parse` checks."""
    moment = real_id[:MOMENT]
    if zero is None:
        last = moment[-2]
        spelled = moment[:-2] + chr(0x0660 + int(last)) + "Z"
    else:
        spelled = "".join(chr(zero + int(c)) if c.isdigit() else c for c in moment)
    return spelled + real_id[MOMENT:]


def _real_snapshot() -> snapshot.Manifest:
    ok("snap", "create")
    [manifest] = snapshot.SnapshotStore().list()
    return manifest


def _plant_bytes(real: snapshot.Manifest, uid: str) -> bytes:
    """`real` identified as `uid`, in the store's canonical encoding (the id
    is not in the fingerprint, so the copy is otherwise intact)."""
    return snapshot.manifest_bytes(real.model_copy(update={"id": uid}))


def _plant(real: snapshot.Manifest, uid: str) -> str:
    path = snapshot.SnapshotStore().manifests_dir / f"{uid}.json"
    path.write_bytes(_plant_bytes(real, uid))
    assert path.is_file()
    return uid


def _out(result: Any) -> str:
    return f"{result.stdout}\n{result.stderr}\n{result.exception!r}"


def _listed(result: Any) -> list[str]:
    return [s["id"] for s in json.loads(result.stdout)["snapshots"]]


# ─── the positive control: an ASCII name is a snapshot ──────────────────────


def test_an_ascii_named_copy_is_listed_resolved_and_offered_ascii_control(root: Path) -> None:
    """The planting mechanics on an ASCII name: the copy is listed, `show`
    resolves it, and `latest` offers the later id of the two (same `created_at`)."""
    real = _real_snapshot()
    later = real.id[: MOMENT - 2] + str((int(real.id[MOMENT - 2]) + 1) % 10) + "Z"
    uid = _plant(real, later + real.id[MOMENT:])
    result = ok("snap", "list", "--json")
    assert sorted(_listed(result)) == sorted([real.id, uid])
    assert json.loads(ok("snap", "show", uid, "--json").stdout)["id"] == uid
    newest = max(real.id, uid)
    assert json.loads(ok("snap", "show", "latest", "--json").stdout)["id"] == newest
    assert snapshot.SnapshotStore().resolve_id(uid) == uid
    assert snapshot.parse_manifest(_plant_bytes(real, uid)).id == uid


# ─── not listed ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_a_non_ascii_digit_manifest_is_not_listed(root: Path, zero: int | None) -> None:
    """Listed as a manifest that does not load, never as a snapshot."""
    real = _real_snapshot()
    uid = _plant(real, _spell(real.id, zero))
    assert not uid.isascii()
    result = run("snap", "list", "--json")
    assert _listed(result) == [real.id], _out(result)
    damaged = json.loads(result.stdout)["damaged"]
    assert [d["name"] for d in damaged] == [f"{uid}.json"]
    assert result.exit_code == 1, _out(result)
    text = run("snap", "list")
    assert text.stdout.splitlines() == [
        line for line in text.stdout.splitlines() if uid not in line
    ]
    assert [m.id for m in snapshot.SnapshotStore().list_lenient().manifests] == [real.id]
    with pytest.raises(snapshot.ManifestIntegrityError):
        snapshot.SnapshotStore().list()


# ─── not resolved ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_a_non_ascii_digit_id_is_not_resolved(root: Path, zero: int | None) -> None:
    """Not by the library, not by `snap show`, and `snap restore` changes
    nothing in the install."""
    real = _real_snapshot()
    uid = _plant(real, _spell(real.id, zero))
    store = snapshot.SnapshotStore()
    with pytest.raises(snapshot.SnapshotError):
        store.show(uid)
    with pytest.raises(snapshot.SnapshotError):
        snapshot.resolve_reference(store, uid)
    result = run("snap", "show", uid, "--json")
    assert result.exit_code != 0, _out(result)
    assert uid not in result.stdout
    state = _state(root)
    restored = run("snap", "restore", uid, "--yes")
    assert restored.exit_code != 0, _out(restored)
    assert _state(root) == state


@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_a_non_ascii_digit_id_is_not_a_snapshot_id_from_elsewhere(zero: int | None) -> None:
    """`parse_manifest`, which holds an archive's manifest (M16-04), refuses it."""
    real_id = "20261110T120000.123456Z-abcdef12"
    uid = _spell(real_id, zero)
    body = json.dumps({"id": uid, "format": snapshot.MANIFEST_FORMAT}).encode("ascii")
    with pytest.raises(snapshot.ManifestIntegrityError, match="carries no snapshot id"):
        snapshot.parse_manifest(body)


# ─── not offered ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
@pytest.mark.parametrize("reference", ["latest", "~0", "latest-user"])
def test_a_non_ascii_digit_manifest_is_never_offered(
    root: Path, zero: int | None, reference: str
) -> None:
    """Its digits sort after every ASCII digit, so with the bug `latest`
    and `~0` named it as the newest snapshot. Now it is a damaged manifest:
    `snap show` prints no snapshot under its id."""
    real = _real_snapshot()
    uid = _plant(real, _spell(real.id, zero))
    try:
        named = snapshot.resolve_reference(snapshot.SnapshotStore(), reference)
    except snapshot.SnapshotError:
        named = None
    assert named != uid
    result = run("snap", "show", reference, "--json")
    assert result.exit_code != 0, _out(result)
    assert uid not in result.stdout, _out(result)


@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_an_imported_record_with_non_ascii_digits_is_not_counted(
    root: Path, zero: int | None
) -> None:
    """An empty file under `imported/` names an id `add_snapshot` added
    (M16-04); a name in non-ASCII digits is not such an id."""
    real = _real_snapshot()
    uid = _spell(real.id, zero)
    store = snapshot.SnapshotStore()
    (store.path / "imported").mkdir(exist_ok=True)
    (store.path / "imported" / uid).write_bytes(b"")
    assert uid not in store.imported_ids()
