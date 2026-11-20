"""`snap list`, `snap show` and `snap verify` say of a damaged snapshot only
its id, that its record cannot be read, and how it is removed (M19-13;
docs/LAB_PLAN.md §6.9 as amended for M19-12; ADR-0036 term 4).

Before this, the three commands passed on the manifest loader's message,
which quotes a fragment of the file: a name-realm key or a U+202E planted in
a wrong-typed manifest reached all six outputs. A marker of each kind (a
plain word, a name-realm-shaped key, ESC, U+202E) is planted in a damaged
manifest and searched for in stdout and stderr, text and `--json`. The
commands that load one snapshot through the same helper (`snap diff`, `snap
export`, `snap restore`) and the other listings that name damaged snapshots
(`profile list`) are held to the same.

Real fixtures (L8): the M11-03 capture copied into `tmp_path` as
`test_cli.py` builds it, with the user data directory redirected into
`tmp_path` and a fake process table. Constructed inputs, labelled in the
test names: the damaged manifests (cut short, wrong types, empty, another
format number, not UTF-8) and a link standing in a manifest's place.
Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    _create,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_cli_snap_rm import _player_copy_problems

from wowlab_core import cli, snapshot
from wowlab_core.snapshot import Manifest, VerifyReport

WORD = "PLANTEDM1913WORD"
KEY = "Zarqwyn-Silvermoonx"  # a name-realm-shaped key (constructed)
ESC = "\x1b[31m"
RLO = "\u202e"
PLANTED = f"{WORD} {KEY} {ESC} {RLO}"

# Every way a planted marker, or the loader's own words, could be written.
FORBIDDEN = (
    WORD,
    KEY,
    "\x1b",
    "\\x1b",
    "\\u001b",
    "\\u001B",
    RLO,
    "\\u202e",
    "\\u202E",
    "\\xe2\\x80\\xae",
    "validation",
    "pydantic",
    "json_invalid",
    "input_value",
    "line 1",
    "Expecting",
    "utf-8",
    "codec",
)


def _cut_short(good: bytes) -> bytes:
    data = json.loads(good)
    data["label"] = PLANTED
    text = json.dumps(data, ensure_ascii=False).encode()
    return text[: text.index(WORD.encode()) + len(PLANTED.encode()) + 1]


def _wrong_types(good: bytes) -> bytes:
    data = json.loads(good)
    data["entries"] = PLANTED
    data["created_at"] = [PLANTED, {KEY: 1}]
    data[KEY] = PLANTED
    return json.dumps(data, ensure_ascii=False).encode()


def _empty(good: bytes) -> bytes:
    return b""


def _other_format(good: bytes) -> bytes:
    data = json.loads(good)
    data["format"] = PLANTED
    data["label"] = PLANTED
    return json.dumps(data, ensure_ascii=False).encode()


def _wrong_version(good: bytes) -> bytes:
    data = json.loads(good)
    data["format"] = data["format"] + 1
    data["label"] = PLANTED
    return json.dumps(data, ensure_ascii=False).encode()


def _not_utf8(good: bytes) -> bytes:
    data = json.loads(good)
    data["label"] = PLANTED
    text = json.dumps(data, ensure_ascii=False).encode()
    at = text.index(WORD.encode())
    return text[:at] + b"\xff\xfe" + text[at:]


DAMAGE: dict[str, Callable[[bytes], bytes]] = {
    "truncated": _cut_short,
    "wrong-typed": _wrong_types,
    "wrong-typed-format": _other_format,
    "empty": _empty,
    "wrong-version": _wrong_version,
    "not-utf8": _not_utf8,
}
IDS = [f"constructed-{k}" for k in DAMAGE]


def _manifest_file(user_data: Path, snapshot_id: str) -> Path:
    return user_data / "store" / "manifests" / f"{snapshot_id}.json"


def _damage(user_data: Path, manifest: Manifest, how: str) -> None:
    path = _manifest_file(user_data, manifest.id)
    path.write_bytes(DAMAGE[how](path.read_bytes()))
    with pytest.raises(snapshot.ManifestIntegrityError):
        snapshot.SnapshotStore(user_data / "store").show(manifest.id)


def _said(result: Any) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def _quotes_nothing(result: Any) -> None:
    text = _said(result)
    for raw in FORBIDDEN:
        assert raw not in text, (raw, text)


def _unreadable(sid: str) -> str:
    return (
        f"snapshot {sid} is damaged: its record in wowlab's snapshot folder cannot be read. "
        f"`wowlab snap rm {sid} --break-undo` removes it."
    )


# ─── the three commands, every damage, text and --json ─────────────────────


@pytest.mark.parametrize("how", list(DAMAGE), ids=IDS)
def test_list_show_and_verify_quote_nothing_of_a_damaged_manifest(
    root: Path, user_data: Path, how: str
) -> None:
    good = _create("good")
    bad = _create("bad")
    _damage(user_data, bad, how)
    said = _unreadable(bad.id)

    listed = run("snap", "list")
    assert listed.exit_code == 1
    _quotes_nothing(listed)
    assert good.id in listed.stdout
    assert said in listed.stderr
    as_json = run("snap", "list", "--json")
    assert as_json.exit_code == 1
    _quotes_nothing(as_json)
    report = cli.SnapListReport.model_validate_json(as_json.stdout)
    assert [s.id for s in report.snapshots] == [good.id]
    assert [(d.name, d.reason) for d in report.damaged] == [(f"{bad.id}.json", said)]
    assert f"damaged manifest {bad.id}.json: {said}" in listed.stderr  # text and JSON agree

    for reference in (bad.id, bad.id[:20]):
        shown = run("snap", "show", reference)
        assert shown.exit_code == 1
        _quotes_nothing(shown)
        assert said in shown.stderr
        shown_json = run("snap", "show", reference, "--json")
        assert shown_json.exit_code == 1
        _quotes_nothing(shown_json)
        assert json.loads(shown_json.stdout)["message"] == said

    verified = run("snap", "verify")
    assert verified.exit_code == 1
    _quotes_nothing(verified)
    assert f"  damaged manifest {bad.id}.json: {said}" in verified.stdout.splitlines()
    verified_json = run("snap", "verify", "--json")
    assert verified_json.exit_code == 1
    _quotes_nothing(verified_json)
    vr = VerifyReport.model_validate_json(verified_json.stdout)
    assert [(d.name, d.reason) for d in vr.invalid_manifests] == [(f"{bad.id}.json", said)]

    assert not _player_copy_problems(root, listed.stderr, shown.stderr, verified.stdout)


@pytest.mark.parametrize("how", ["wrong-typed", "not-utf8"], ids=IDS[1:2] + IDS[5:6])
def test_a_reference_that_reads_every_record_quotes_nothing(
    root: Path, user_data: Path, how: str
) -> None:
    _create("good")
    bad = _create("bad")
    _damage(user_data, bad, how)
    for reference in ("latest", "latest-user", "~1"):
        for extra in ((), ("--json",)):
            result = run("snap", "show", reference, *extra)
            assert result.exit_code == 1
            _quotes_nothing(result)
            assert "one or more cannot be read" in _said(result)
            assert "`wowlab snap verify` names each" in _said(result)


@pytest.mark.parametrize("how", ["truncated", "wrong-typed"], ids=IDS[0:2])
def test_diff_export_restore_and_profile_list_quote_nothing(
    root: Path, user_data: Path, tmp_path: Path, how: str
) -> None:
    good = _create("good")
    bad = _create("bad")
    _damage(user_data, bad, how)
    said = _unreadable(bad.id)
    for args in (
        ("snap", "diff", good.id, bad.id),
        ("snap", "diff", bad.id, good.id),
        ("snap", "export", bad.id, str(tmp_path / "out.wowlab")),
        ("snap", "restore", bad.id, "--dry-run"),
    ):
        for extra in ((), ("--json",)):
            result = run(*args, *extra)
            assert result.exit_code == 1, args
            _quotes_nothing(result)
            assert said in _said(result), args
    assert not (tmp_path / "out.wowlab").exists()
    profiles = run("profile", "list", "--json")
    _quotes_nothing(profiles)
    _quotes_nothing(run("profile", "list"))


# ─── a link in a record's place ─────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_link_in_a_records_place_gets_the_leave_it_alone_sentence_constructed(
    root: Path, user_data: Path, tmp_path: Path
) -> None:
    _create("good")
    linked = _create("linked")
    path = _manifest_file(user_data, linked.id)
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(f'{{"label": "{PLANTED}", ', encoding="utf-8")
    path.unlink()
    path.symlink_to(elsewhere)
    sentence = (
        "something other than a snapshot record (a link, or not a regular file) stands in "
        f"the place of the record of {linked.id} in wowlab's snapshot folder"
    )
    tail = "wowlab leaves it alone and never removes it; you can remove it by hand."
    outputs = [
        run("snap", "list"),
        run("snap", "list", "--json"),
        run("snap", "show", linked.id),
        run("snap", "show", linked.id, "--json"),
        run("snap", "verify"),
        run("snap", "verify", "--json"),
    ]
    for result in outputs:
        assert result.exit_code == 1
        _quotes_nothing(result)
        text = _said(result)
        assert sentence in text and tail in text, text
    assert "--break-undo" not in _said(outputs[0])
    assert path.is_symlink()
    assert elsewhere.read_text(encoding="utf-8").startswith('{"label"')


# ─── unchanged for healthy snapshots ────────────────────────────────────────


def test_healthy_snapshots_are_reported_as_before(root: Path) -> None:
    first = _create("one")
    listing = run("snap", "list")
    assert listing.exit_code == 0 and listing.stderr == ""
    shown = ok("snap", "show", first.id, "--json")
    assert shown.stdout.encode("ascii") == snapshot.manifest_bytes(first)
    verified = run("snap", "verify", "--json")
    assert verified.exit_code == 0
    assert VerifyReport.model_validate_json(verified.stdout).invalid_manifests == ()
