"""`scripts/lab_capture.py --file`: one file from outside an install (M22-25).

An addon manager keeps its records in its own user-data folder, outside any
install, so the install mode cannot take them. The single-file mode runs the
same detectors with the same refusal on anything left over, blanks per-user
link tokens (WowUp's `?link=`), replaces app and account ids (WowUp's
`install_id`, CurseForge's `SyncProfileID`) and this machine's home folder,
byte for byte, and says in the provenance row that the file came from
outside an install.

CONSTRUCTED INPUT, except where a test names the committed CurseForge
capture (`fixtures/curseforge/macos/AddonGameInstance.json`, a real file
that went through this mode). Every other file is built in `tmp_path` with
invented values, and the home folder is a stand-in under `tmp_path`. No test
reads a real app folder or a real install, and none writes to one.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from test_lab_capture import build_install, lab_capture

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "lab/core/tests/fixtures/curseforge/macos/AddonGameInstance.json"
)
TOKEN = b"ZmFrZS10b2tlbi1mb3ItYS10ZXN0LW9ubHk%3D"
INSTALL_ID = b"8d6a1c2e-1111-4222-8333-444455556666"
STAND_IN = b"00000000-0000-4000-8000-000000000001"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in home folder: `Path.home()` is it for the test."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("USERPROFILE", str(folder))
    return folder


def _app(home: Path, name: str, body: bytes, app: str = "SomeManager") -> Path:
    folder = home / "Library" / "Application Support" / app
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(body)
    return path


def _capture(path: Path, out: Path, *extra: str) -> int:
    argv = ["--file", str(path), "--out", str(out), "--platform", "macos", *extra]
    if "--source" not in extra:
        argv += ["--source", "somemanager"]
    return int(lab_capture.main(argv))


def _written(out: Path, name: str, source: str = "somemanager") -> bytes:
    return (out / source / "macos" / name).read_bytes()


def _nothing_written(out: Path) -> bool:
    return not out.exists() or not any(p.is_file() for p in out.rglob("*"))


def _process(data: bytes, home: str = "/Users/someone") -> Any:
    facts = FIXTURE.stat()
    return lab_capture.process_outside(
        data, facts, "records.json", "somemanager", "macos", lab_capture.Identity(), home
    )


# ─── what it rewrites, byte for byte ─────────────────────────────────────────


def test_constructed_a_link_token_is_blanked_and_nothing_else_moves(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = (
        b'{\n\t"a": {\n\t\t"downloadUrl": "https://addons.example/d?link=' + TOKEN + b'",\n'
        b'\t\t"name": "Pawn",\n\t\t"installedVersion": "2.13.16"\n\t}\n}'
    )
    src = _app(home, "addons.json", body)
    out = tmp_path / "out"
    assert _capture(src, out) == 0
    written = _written(out, "addons.json")
    assert written == body.replace(TOKEN, b"REDACTED")
    assert src.read_bytes() == body  # the app's file is never changed
    row = capsys.readouterr().out.splitlines()[-1]
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[0] == "`somemanager/macos/addons.json`"
    assert cells[1] == "somemanager-addons-json"
    assert cells[2] == "n/a (outside an install)"
    assert cells[7] == "link-tokens-blanked: 1"
    assert cells[8].startswith(
        "from outside an install: `~/Library/Application Support/SomeManager/addons.json`"
    )
    assert hashlib.sha256(body).hexdigest() in cells[8]
    assert TOKEN.decode() not in row


def test_constructed_an_app_id_is_replaced_wherever_its_text_appears(
    home: Path, tmp_path: Path
) -> None:
    body = (
        b'{"install_id": "' + INSTALL_ID + b'", "note": "sent as ' + INSTALL_ID + b'",'
        b' "other_id": "' + INSTALL_ID[:-1] + b'7"}'
    )
    out = tmp_path / "out"
    assert _capture(_app(home, "preferences.json", body), out) == 0
    written = _written(out, "preferences.json")
    assert INSTALL_ID not in written
    assert written.count(STAND_IN) == 2
    assert INSTALL_ID[:-1] + b"7" in written  # another value, not a key on the list


def test_constructed_a_numeric_account_id_is_replaced_under_its_key(
    home: Path, tmp_path: Path
) -> None:
    body = b'[{"SyncProfileID":1232241,"Profile":{"syncProfileID": 1232241},"fileLength":12322}]'
    out = tmp_path / "out"
    assert _capture(_app(home, "Instances.json", body), out) == 0
    assert _written(out, "Instances.json") == (
        b'[{"SyncProfileID":1,"Profile":{"syncProfileID": 1},"fileLength":12322}]'
    )


def test_constructed_a_numeric_id_found_outside_its_key_refuses(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = b'{"userId": 98765432, "url": "https://example.invalid/u/98765432/page"}'
    out = tmp_path / "out"
    assert _capture(_app(home, "state.json", body), out) == 1
    err = capsys.readouterr().err
    assert "app or account id found outside its key x1" in err
    assert "98765432" not in err
    assert _nothing_written(out)


@pytest.mark.skipif(os.name == "nt", reason="a POSIX home; a drive-letter home is graded below")
def test_constructed_this_machines_home_is_replaced_in_every_spelling(
    home: Path, tmp_path: Path
) -> None:
    posix = str(home).encode()
    doubled = posix.replace(b"/", b"\\\\")
    body = (
        b'{"installPath":"' + posix + b'/Games/World of Warcraft/_forever_/",'
        b'"win":"' + doubled + b'\\\\Games","longer":"' + posix + b'x/kept"}'
    )
    out = tmp_path / "out"
    assert _capture(_app(home, "Instances.json", body), out) == 0
    assert _written(out, "Instances.json") == (
        b'{"installPath":"/Volumes/Labdisk/Games/World of Warcraft/_forever_/",'
        b'"win":"\\\\Volumes\\\\Labdisk\\\\Games","longer":"' + posix + b'x/kept"}'
    )


def test_constructed_home_spellings_of_a_windows_home() -> None:
    pairs = dict(lab_capture._home_pairs("C:\\Users\\someone"))
    assert pairs[b"C:\\Users\\someone"] == b"D:\\Labdisk"
    assert pairs[b"C:/Users/someone"] == b"D:/Labdisk"
    assert pairs[b"C:\\\\Users\\\\someone"] == b"D:\\\\Labdisk"
    assert lab_capture._home_pairs("/") == []


def test_constructed_overlapping_edits_keep_the_longer_one() -> None:
    # The install id inside a link token: one edit, the token's, and nothing left.
    data = b'{"install_id":"' + INSTALL_ID + b'","u":"https://x.invalid/?link=' + INSTALL_ID + b'"}'
    scan = lab_capture.outside_scan(data, "/Users/someone")
    spans = sorted((s, e) for s, e, _, _ in scan.edits)
    assert all(a[1] <= b[0] for a, b in itertools.pairwise(spans))
    outcome = _process(data)
    assert outcome.problems == []
    assert INSTALL_ID not in outcome.result.data


# ─── what it refuses ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("planted", "label"),
    [
        (b"someone@example.invalid", "email address"),
        (b"/Users/someoneelse/Games", "home path"),
        (b"Player-1234-0ABCDEF0", "unmapped player GUID"),
        (b"Someone#12345", "BattleTag"),
    ],
    ids=["email", "other-home", "player-guid", "battletag"],
)
def test_constructed_a_leftover_identifier_refuses_the_file(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], planted: bytes, label: str
) -> None:
    out = tmp_path / "out"
    assert _capture(_app(home, "records.json", b'{"x": "' + planted + b'"}'), out) == 1
    err = capsys.readouterr().err
    assert label in err
    assert planted.decode() not in err
    assert _nothing_written(out)


def test_constructed_an_extra_name_is_rewritten_like_in_an_install(
    home: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    body = b'{"author": "Fakeownername"}'
    assert _capture(_app(home, "r.json", body), out, "--extra-name", "Fakeownername") == 0
    assert _written(out, "r.json") == b'{"author": "Labnamea"}'


def test_constructed_with_root_the_install_identity_map_is_used(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    out = tmp_path / "out"
    # The account folder's name is an identity string of that install.
    body = b'{"path": "WTF/Account/123456789#1/SavedVariables"}'
    assert _capture(_app(home, "r.json", body), out, "--root", str(root)) == 0
    written = _written(out, "r.json")
    assert b"123456789#1" not in written
    assert "identity-rewritten: 1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "body",
    [
        b"\x1f\x8b\x08\x00compressed",
        b'{"a": "b\x00c"}',
        b'{"a": "\xff\xfe"}',
    ],
    ids=["compressed", "nul", "not-utf8"],
)
def test_constructed_a_file_that_is_not_plain_text_refuses(
    home: Path, tmp_path: Path, body: bytes
) -> None:
    out = tmp_path / "out"
    assert _capture(_app(home, "records.json", body), out) == 1
    assert _nothing_written(out)


def test_constructed_an_sqlite_file_refuses(home: Path, tmp_path: Path) -> None:
    path = _app(home, "app.json", b"")
    path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    out = tmp_path / "out"
    assert _capture(path, out) == 1
    assert _nothing_written(out)


@pytest.mark.parametrize(
    "name",
    ["sensitive.json", "Cookies", "auth-token.json", "Login Data", "credentials.json"],
)
def test_constructed_a_name_that_suggests_a_secret_is_never_opened(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    path = _app(home, name, b"{}")
    path.chmod(0)  # would fail the open: the refusal comes first
    out = tmp_path / "out"
    try:
        assert _capture(path, out) == 2
    finally:
        path.chmod(0o600)
    assert "never opened" in capsys.readouterr().err
    assert _nothing_written(out)


def test_constructed_a_file_in_a_secret_named_folder_is_never_opened(
    home: Path, tmp_path: Path
) -> None:
    folder = home / "Library" / "Application Support" / "App" / "Session Storage"
    folder.mkdir(parents=True)
    (folder / "000003.log").write_bytes(b"{}")
    assert _capture(folder / "000003.log", tmp_path / "out") == 2


def test_constructed_a_symlink_is_not_followed(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = _app(home, "real.json", b'{"a": 1}')
    link = target.with_name("records.json")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are not available here")
    out = tmp_path / "out"
    assert _capture(link, out) == 2
    assert "symbolic link" in capsys.readouterr().err
    assert _nothing_written(out)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
@pytest.mark.timeout(20)
def test_constructed_a_fifo_is_refused_without_waiting(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = home / "Library" / "Application Support" / "App"
    folder.mkdir(parents=True)
    fifo = folder / "records.json"
    os.mkfifo(fifo)
    out = tmp_path / "out"
    assert _capture(fifo, out) == 2  # a blocking open would hang here
    assert "not a regular file" in capsys.readouterr().err


def test_constructed_a_file_past_the_bound_is_not_read(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _app(home, "records.json", b"{" + b" " * 2048 + b"}")
    out = tmp_path / "out"
    assert _capture(path, out, "--max-bytes", "1024") == 2
    assert "larger than 1024 bytes" in capsys.readouterr().err
    assert _nothing_written(out)


def test_constructed_a_file_inside_an_install_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    inside = next(p for p in root.rglob("*.lua") if p.is_file())
    assert _capture(inside, tmp_path / "out") == 2


def test_constructed_out_inside_the_apps_folder_is_refused(home: Path, tmp_path: Path) -> None:
    path = _app(home, "records.json", b"{}")
    assert _capture(path, path.parent / "staging") == 2
    assert sorted(p.name for p in path.parent.iterdir()) == ["records.json"]


def test_constructed_an_existing_output_needs_reuse_out(home: Path, tmp_path: Path) -> None:
    path = _app(home, "records.json", b'{"a": 1}')
    out = tmp_path / "out"
    assert _capture(path, out) == 0
    assert _capture(path, out) == 2
    assert _capture(path, out, "--reuse-out") == 0


@pytest.mark.parametrize(
    "extra",
    [("--sv", "X.lua"), ("--flavor", "_retail_"), ("--pseudonymise-other-players",)],
    ids=["sv", "flavor", "others"],
)
def test_constructed_install_options_are_refused_with_file(
    home: Path, tmp_path: Path, extra: tuple[str, ...]
) -> None:
    path = _app(home, "records.json", b"{}")
    assert _capture(path, tmp_path / "out", *extra) == 2


@pytest.mark.parametrize("source", [None, "Curse Forge", "../up"], ids=["none", "space", "dots"])
def test_constructed_source_must_be_a_plain_folder_name(
    home: Path, tmp_path: Path, source: str | None
) -> None:
    path = _app(home, "records.json", b"{}")
    argv = ["--file", str(path), "--out", str(tmp_path / "out"), "--platform", "macos"]
    if source is not None:
        argv += ["--source", source]
    assert lab_capture.main(argv) == 2


def test_constructed_root_or_file_is_needed(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        lab_capture.parse_args(["--out", str(tmp_path)])


# ─── the committed capture ───────────────────────────────────────────────────


def test_the_curseforge_capture_is_a_fixed_point_and_passes_every_detector() -> None:
    """The committed file went through this mode: run again, nothing in it
    is changed and nothing in it is refused."""
    data = FIXTURE.read_bytes()
    outcome = lab_capture.process_outside(
        data,
        FIXTURE.stat(),
        FIXTURE.name,
        "curseforge",
        "macos",
        lab_capture.Identity(),
        "/Users/someone",
    )
    assert outcome.problems == []
    assert outcome.result.data == data
    assert outcome.result.edits == ()
    assert re.search(rb"/Users/|\\\\Users\\\\", data) is None
    assert data.count(b'"SyncProfileID":1,') == 1 and data.count(b'"syncProfileID":1}') == 1


def test_constructed_a_short_text_id_is_replaced_under_its_key_only(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    body = b'{"userId": "ab12", "note": "lab12x"}'
    assert _capture(_app(home, "a.json", body), out) == 0
    assert _written(out, "a.json") == b'{"userId": "' + STAND_IN + b'", "note": "lab12x"}'
    elsewhere = b'{"userId": "ab12", "path": "/u/ab12/"}'
    assert _capture(_app(home, "b.json", elsewhere), out) == 1
    assert "app or account id found outside its key x1" in capsys.readouterr().err
