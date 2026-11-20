"""`sv merge --from <id>` and `--base <id>` on a damaged snapshot say M19-13's
sentence, never the loader's words (M19-14).

Before this, `_cmd_sv._snapshot_sv` let `store.show`'s error through, and
that error quotes a fragment of the snapshot's record. A marker (two
name-realm-shaped keys, ESC, U+202E) is planted in a wrong-typed and in a
non-UTF-8 manifest and searched for in stdout and stderr, text and `--json`.

Real fixtures (L8): the M11-03 capture copied into `tmp_path` as
`test_cli.py` builds it, with the user data directory redirected into
`tmp_path` and a fake process table. Constructed, labelled in the test
names: the damaged manifests and the keys planted in them
(`Inventeda-Fakerealm`, `Inventedb-Fakerealm`), which no real install holds.
Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    ACCOUNT,
    _create,
    _state,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_cli_snap_rm import _player_copy_problems

import wowlab_core
from wowlab_core import cli, snapshot
from wowlab_core.cli import _cmd_snap, _cmd_sv

KEY_A = "Inventeda-Fakerealm"  # constructed name-realm-shaped keys
KEY_B = "Inventedb-Fakerealm"
WORD = "PLANTEDM1914WORD"
ESC = "\x1b[31m"
RLO = "\u202e"
PLANTED = f"{WORD} {KEY_A} {ESC} {RLO}"

FORBIDDEN = (
    WORD,
    KEY_A,
    KEY_B,
    "Inventeda",
    "Inventedb",
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
    "Expecting",
    "utf-8",
    "codec",
)

ACCT = f"WTF/Account/{ACCOUNT}"
CHAR_A = "Labchard-Labrealmg"
DBM_NAME = "DBM-Party-Vanilla.lua"
DBM = f"{ACCT}/SavedVariables/{DBM_NAME}"
DBM_VAR = "DBMPartyVanilla_AllSavedVars"
ENABLED = b'["Enabled"] = true,'


def _wrong_typed(good: bytes) -> bytes:
    data = json.loads(good)
    data["entries"] = PLANTED
    data["created_at"] = [PLANTED, {KEY_B: 1}]
    data[KEY_A] = PLANTED
    return json.dumps(data, ensure_ascii=False).encode()


def _not_utf8(good: bytes) -> bytes:
    data = json.loads(good)
    data["label"] = f"{PLANTED} {KEY_B}"
    text = json.dumps(data, ensure_ascii=False).encode()
    at = text.index(WORD.encode())
    return text[:at] + b"\xff\xfe" + text[at:]


DAMAGE: dict[str, Callable[[bytes], bytes]] = {
    "constructed-wrong-typed": _wrong_typed,
    "constructed-not-utf8": _not_utf8,
}


def _damage(user_data: Path, sid: str, how: str) -> None:
    path = user_data / "store" / "manifests" / f"{sid}.json"
    path.write_bytes(DAMAGE[how](path.read_bytes()))
    with pytest.raises(snapshot.ManifestIntegrityError):
        snapshot.SnapshotStore(user_data / "store").show(sid)


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


def _merge(source: str, *extra: str) -> Any:
    return run(
        "sv", "merge", DBM_NAME, "--from", source, "--into", CHAR_A,
        "--key", f'{DBM_VAR}["Unknown"]', "--replace", "--yes", *extra,
    )  # fmt: skip


@pytest.mark.parametrize("how", list(DAMAGE))
def test_merge_from_a_damaged_snapshot_quotes_nothing(
    root: Path, user_data: Path, how: str
) -> None:
    bad = _create("bad")
    _damage(user_data, bad.id, how)
    said = _unreadable(bad.id)
    before = _state(root)
    for reference in (bad.id, bad.id[:20]):
        text = _merge(reference)
        assert text.exit_code == 1, _said(text)
        _quotes_nothing(text)
        assert said in " ".join(text.stderr.split())
        as_json = _merge(reference, "--json")
        assert as_json.exit_code == 1, _said(as_json)
        _quotes_nothing(as_json)
        assert json.loads(as_json.stdout)["message"] == said  # text and JSON agree
        assert not _player_copy_problems(root, text.stderr)
    assert _state(root) == before  # nothing written


@pytest.mark.parametrize("how", list(DAMAGE))
def test_merge_with_a_damaged_base_quotes_nothing(root: Path, user_data: Path, how: str) -> None:
    good = _create("good")
    bad = _create("bad")
    _damage(user_data, bad.id, how)
    for extra in ((), ("--json",)):
        result = run(
            "sv", "merge", DBM_NAME, "--from", good.id, "--base", bad.id, "--into", CHAR_A,
            "--yes", *extra,
        )  # fmt: skip
        assert result.exit_code == 1, _said(result)
        _quotes_nothing(result)
        assert _unreadable(bad.id) in " ".join(_said(result).split())


def test_the_sentence_is_written_once() -> None:
    # One function says it, shared by the snap commands and sv merge.
    assert _cmd_sv._damaged_words is _cmd_snap._damaged_words is cli._damaged_words
    src = Path(wowlab_core.__file__).parent
    phrase = "is damaged: its record in wowlab's snapshot folder cannot be read."
    holders = [
        p.relative_to(src).as_posix()
        for p in sorted(src.rglob("*.py"))
        if phrase in p.read_text(encoding="utf-8")
    ]
    assert holders == ["cli/_common.py"]


def test_a_healthy_snapshot_merges_as_before(flavor: Path) -> None:
    good = _create("before")
    target = flavor / DBM
    real = target.read_bytes()
    at = real.index(ENABLED, real.index(b'\r\n["Unknown"] = {'))
    target.write_bytes(real[:at] + b'["Enabled"] = false,' + real[at + len(ENABLED) :])
    result = _merge(good.id)
    assert result.exit_code == 0, _said(result)
    assert target.read_bytes() == real
