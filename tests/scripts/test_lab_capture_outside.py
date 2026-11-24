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
import time
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
    return lab_capture.process_outside(
        data, 0.0, "records.json", "somemanager", "macos", lab_capture.Identity(), home
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


def test_constructed_overlapping_edits_refuse_the_file() -> None:
    # The install id inside a link token: the edits kept never overlap, and
    # the one dropped refuses the file rather than leave part of it.
    data = b'{"install_id":"' + INSTALL_ID + b'","u":"https://x.invalid/?link=' + INSTALL_ID + b'"}'
    scan = lab_capture.outside_scan(data, "/Users/someone")
    spans = sorted((s, e) for s, e, _, _ in scan.edits)
    assert all(a[1] <= b[0] for a, b in itertools.pairwise(spans))
    assert scan.dropped == 1
    assert "two replacements that overlap x1" in _process(data).problems


def test_constructed_an_id_overlapping_the_home_folder_refuses() -> None:
    # Security review L1: the id's edit took the start of the home path and
    # the end of the login survived. Now the overlap refuses the file.
    home = "/Users/someonelong"
    data = b'{"user_id":"zz' + home[:-3].encode() + b'","p":"zz' + home.encode() + b'/Library"}'
    outcome = _process(data, home)
    assert "two replacements that overlap x1" in outcome.problems


@pytest.mark.parametrize(
    "value",
    [b"7.654321e6", b"7654321.0", b'{"n": 1}', b"[7654321]", b"0x7654"],
    ids=["exponent", "float", "object", "list", "hex"],
)
def test_constructed_an_id_of_another_shape_refuses(value: bytes) -> None:
    outcome = _process(b'{"userId": ' + value + b', "a": 1}')
    assert "app or account id in a shape the tool does not replace x1" in outcome.problems


def test_constructed_a_null_id_is_left_alone() -> None:
    outcome = _process(b'{"userId": null, "guid": null}')
    assert outcome.problems == [] and outcome.result.edits == ()


def test_constructed_a_game_instance_guid_is_replaced_everywhere() -> None:
    guid = b"6fe3124a-0000-4147-a43a-d63929a1555d"
    data = (
        b'[{"guid":"' + guid + b'","syncProfile":{"GameInstanceGuid":"' + guid + b'"},'
        b'"installedAddons":[{"gameInstanceID":"' + guid + b'"}]}]'
    )
    outcome = _process(data)
    assert outcome.problems == []
    assert guid not in outcome.result.data
    assert outcome.result.data.count(STAND_IN) == 3


# Security review M2: each was written unchanged before.
@pytest.mark.parametrize(
    "planted",
    [
        b"chris\\u0040example.com",
        b"\\u002FUsers\\u002Fchris\\u002Fx",
        b"Chris\\u00231234",
        b"Player\\u002D1234\\u002D0ABCDEF0",
        b'", "Sync\\u0050rofileID": 7654321, "x": "',
        b"https://x.invalid/?a=1\\u0026token=SECRETTOKEN123",
    ],
    ids=["email", "home", "battletag", "guid", "escaped-key", "escaped-ampersand"],
)
def test_constructed_a_json_escape_of_a_printable_character_refuses(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], planted: bytes
) -> None:
    out = tmp_path / "out"
    assert _capture(_app(home, "records.json", b'{"x": "' + planted + b'"}'), out) == 1
    err = capsys.readouterr().err
    assert "JSON \\u escape of a printable character" in err
    assert _nothing_written(out)


def test_constructed_a_non_ascii_escape_is_not_refused() -> None:
    assert _process(b'{"name": "Caf\\u00e9"}').problems == []


@pytest.mark.parametrize(
    "key",
    [b"accessToken", b"Authorization", b"refresh_token", b"sessionId", b"password", b"apiKey"],
)
def test_constructed_a_key_that_names_a_secret_refuses(key: bytes) -> None:
    outcome = _process(b'{"' + key + b'": "x", "authors": [], "author": "a"}')
    assert outcome.problems == ["JSON key that names a secret x1"]


def test_constructed_a_jwt_refuses() -> None:
    data = b'{"a": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2ln"}'
    assert _process(data).problems == ["signed token (JWT) x1"]


def test_constructed_thousands_of_ids_scrub_quickly() -> None:
    """Security review L2: the time grew with the square of the distinct ids
    (8,000 numeric ids took 18 s). One pass each now."""
    numbers = b",".join(b'{"user_id": %d}' % (10_000_000 + i) for i in range(8_000))
    texts = b",".join(b'{"user_id": "%036d"}' % i for i in range(8_000))
    for body in (numbers, texts):
        data = b"[" + body + b"]"
        start = time.perf_counter()
        outcome = _process(data)
        assert outcome.problems == []
        assert time.perf_counter() - start < 10, "a generous hang guard, not a budget"


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
        FIXTURE.stat().st_mtime,
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
    # The game instance's GUID and the sync id are stand-ins (review L4).
    stand_in = b"00000000-0000-4000-8000-000000000001"
    assert data.count(b'"guid":"' + stand_in + b'"') == 1
    assert data.count(b'"GameInstanceGuid":"' + stand_in + b'"') == 1
    assert data.count(b'"gameInstanceID":"' + stand_in + b'"') == 23
    assert data.count(b'"SyncProfileID":2,') == 1 and data.count(b'"syncProfileID":2}') == 1


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


def test_constructed_out_inside_an_install_is_refused(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    path = _app(home, "records.json", b'{"a": 1}')
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    for out in (root, root / "_retail_", root / "new" / "folder"):
        assert _capture(path, out) == 2
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before


@pytest.mark.parametrize(
    "parts",
    [
        ("App", "Local Storage", "leveldb", "000003.log"),
        ("App", "IndexedDB", "x", "y.json"),
        ("App", ".updaterId"),
        ("App", "Local State"),
        ("App", "agent", "database", "app.db"),
        ("App", "Cookies-dir", "deep", "data.json"),
    ],
    ids=["leveldb", "indexeddb", "updaterid", "local-state", "db", "deep-folder"],
)
def test_constructed_browser_storage_and_deep_secret_folders_are_never_opened(
    home: Path, tmp_path: Path, parts: tuple[str, ...]
) -> None:
    path = home.joinpath("Library", "Application Support", *parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"a": 1}')
    path.chmod(0)  # would fail the open: the refusal comes first
    try:
        assert _capture(path, tmp_path / "out") == 2
    finally:
        path.chmod(0o600)


def test_constructed_a_secret_folder_reached_through_a_link_is_never_opened(
    home: Path, tmp_path: Path
) -> None:
    secret = home / "Library" / "Application Support" / "App" / "Cookies-dir"
    secret.mkdir(parents=True)
    (secret / "data.json").write_bytes(b'{"a": 1}')
    plain = home / "plain"
    try:
        plain.symlink_to(secret, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not available here")
    assert _capture(plain / "data.json", tmp_path / "out") == 2


def test_constructed_a_file_reached_through_a_link_into_an_install_is_refused(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    target = next(p for p in root.rglob("*.lua") if p.is_file())
    link = home / "linked"
    try:
        link.symlink_to(target.parent, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not available here")
    assert _capture(link / target.name, tmp_path / "out") == 2
