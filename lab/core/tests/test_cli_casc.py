"""`wowlab casc stat` and `casc cat` (M15-06, docs/LAB_PLAN.md §17.1).

Every install here is `constructed` (ADR-0033 layer a: no byte of a real
install file), built by the parser suite's `_casc_build` in `tmp_path`: one
file per state of §17.1 (local, not in the client's local files, not part
of this game version's files, encrypted) and an id that is no file data id.
The user data directory is redirected into `tmp_path`; no test reads or
writes a real install, and each checks the constructed install is
unchanged. `cat` writes only under `casc/<product>-<version>/`: by default
`files/<fdid>-<ckey8>.bin`, a repeat writes nothing and says so, `--out`
inside the folder is written, `--out -` goes to stdout, and every path
outside it (a traversal, the install, a link, the rest of the user data
directory) is refused with nothing written.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from wowlab_core import casc, cascfiles, cli

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _casc_build import (
    FLAVOR,
    PRODUCT,
    VERSION,
    EncodingRow,
    blte,
    ckey_of,
    e_block,
    key_in,
    make_source_install,
    n_block,
    tree_state,
    z_block,
)
from _user_dirs import redirect_user_dirs

runner = CliRunner()

ENUS = 0x2
FRFR = 0x10
PLAIN = b"constructed plain file " * 200
LOCKED = b"constructed: only an encrypted copy"
UNFETCHED = b"constructed: in the root, its record not in the index"
IDS = {"local": 10, "encrypted": 30, "not_local": 50, "not_in_build": 70, "absent": 12345}
BUILD_FOLDER = f"{PRODUCT}-{VERSION}"


def _install(tmp: Path) -> Path:
    rng = random.Random(1506)

    def ek(bucket: int) -> bytes:
        return key_in(bucket, rng) + rng.randbytes(7)

    plain, locked, unfetched = ek(1), ek(4), ek(1)
    stored = [
        (plain, blte([n_block(PLAIN[:7]), z_block(PLAIN[7:])])),
        (locked, blte([n_block(LOCKED[:3]), e_block(decoded=len(LOCKED) - 3)])),
    ]
    rows: list[EncodingRow] = [
        (ckey_of(PLAIN), len(PLAIN), [plain]),
        (ckey_of(LOCKED), len(LOCKED), [locked]),
        (ckey_of(UNFETCHED), len(UNFETCHED), [unfetched]),
    ]
    blocks = [
        (
            [IDS["local"], IDS["encrypted"], IDS["not_local"]],
            [ckey_of(PLAIN), ckey_of(LOCKED), ckey_of(UNFETCHED)],
            0,
            ENUS,
        ),
        ([IDS["not_in_build"]], [ckey_of(PLAIN)], 0, FRFR),
    ]
    return make_source_install(
        tmp / "game", stored=stored, encoding_rows=rows, root_blocks=blocks, rng=rng
    )


class Env:
    def __init__(self, root: Path, home: Path) -> None:
        self.root = root
        self.home = home

    def run(self, *args: str) -> Any:
        return runner.invoke(
            cli.app, [*args, "--root", str(self.root), "--flavor", FLAVOR], terminal_width=100
        )

    def data(self) -> Path:
        return cascfiles.casc_dir().resolve().parent

    def folder(self) -> Path:
        return cascfiles.build_dir(PRODUCT, VERSION)

    def default(self) -> Path:
        return self.folder() / "files" / f"{IDS['local']}-{ckey_of(PLAIN).hex()[:8]}.bin"

    def files(self) -> dict[str, tuple[int, int]]:
        return tree_state(self.home)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("WOWLAB_WOW_ROOT", raising=False)
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    redirect_user_dirs(monkeypatch, home)
    root = _install(tmp_path)
    before = tree_state(root)
    built = Env(root, home)
    yield built
    assert tree_state(root) == before, "the constructed install changed"


# ─── stat ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "state", "words"),
    [
        ("local", "local", "local"),
        ("not_local", "not_local", "not in your client's local files"),
        # Listed only for another locale: part of this game version, but not
        # among what this client loads (domain review of 2026-11-18).
        ("not_in_build", "not_in_build", casc.WORDS_NOT_LOADED),
        ("absent", "not_in_build", "not part of this game version's files"),
        ("encrypted", "encrypted", "encrypted"),
    ],
)
def test_stat_names_each_state_constructed(env: Env, name: str, state: str, words: str) -> None:
    fdid = str(IDS[name])
    result = env.run("casc", "stat", "--fdid", fdid, "--json")
    assert result.exit_code == 0, result.output
    report = cli.CascStatReport.model_validate_json(result.stdout)
    assert (report.state, report.words, report.fdid) == (state, words, IDS[name])
    assert (report.product, report.version, report.flavor) == (PRODUCT, VERSION, FLAVOR)
    if state == "local":
        assert report.size == len(PLAIN)
        assert report.content_key == ckey_of(PLAIN).hex()
    else:
        assert report.size is None and report.content_key is None
    text = env.run("casc", "stat", "--fdid", fdid)
    assert text.exit_code == 0, text.output
    lines = text.stdout.splitlines()
    assert lines[0] == f"file {fdid} in {FLAVOR} ({PRODUCT} {VERSION}): {words}"
    if state == "local":
        assert lines[1:] == [
            f"  size: {len(PLAIN):,} bytes",
            f"  content key: {ckey_of(PLAIN).hex()}",
        ]
    else:
        assert len(lines) == 2, lines  # the answer, then one caveat
    if state == "not_local":
        assert report.note is not None and report.note.startswith(casc.NOTE_NOT_LOCAL)
        assert lines[1] == (
            "  it is part of this game version but not on this computer; wowlab never "
            "downloads game files, and whether the game fetches it later is not yet confirmed"
        )
    assert not env.data().joinpath("casc").exists(), "stat writes nothing"


@pytest.mark.parametrize("fdid", ["-1", str(2**31)])
def test_stat_of_a_number_that_is_no_file_data_id_is_unknown_constructed(
    env: Env, fdid: str
) -> None:
    result = env.run("casc", "stat", f"--fdid={fdid}", "--json")
    assert result.exit_code == 0, result.output
    report = cli.CascStatReport.model_validate_json(result.stdout)
    assert (report.state, report.words) == ("unknown_id", "unknown id")
    assert report.note == "a file data id is a whole number from 0 to 2,147,483,647"
    assert env.run("casc", "stat", f"--fdid={fdid}").stdout.startswith(
        f"file {fdid} in {FLAVOR} ({PRODUCT} {VERSION}): unknown id\n"
    )
    cat = env.run("casc", "cat", f"--fdid={fdid}")
    assert cat.exit_code == 1
    assert cat.stderr == (
        f"wowlab: cannot extract file {fdid}: it is not a file data id (a whole number from 0 "
        "to 2,147,483,647); nothing was written\n"
    )


def test_stat_without_archives_is_one_error_line_constructed(env: Env) -> None:
    data = env.root / "Data"
    data.rename(env.root.parent / "Data-moved")
    try:
        result = env.run("casc", "stat", "--fdid", "10")
    finally:
        (env.root.parent / "Data-moved").rename(data)
    assert result.exit_code == 1
    assert result.stderr.startswith("wowlab: cannot read the game-data archives of ")
    assert len(result.stderr.splitlines()) == 1


# ─── cat: the default place, and a repeat ────────────────────────────────────


def test_cat_writes_to_its_default_place_then_says_already_extracted_constructed(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--json")
    assert result.exit_code == 0, result.output
    report = cli.CascCatReport.model_validate_json(result.stdout)
    target = env.default()
    assert report.outcome == "written"
    assert Path(report.path) == target
    assert (report.size, report.content_key) == (len(PLAIN), ckey_of(PLAIN).hex())
    assert target.read_bytes() == PLAIN
    assert target.parent.parent.name == BUILD_FOLDER
    assert target.parent.parent.parent == cascfiles.casc_dir()
    if os.name == "posix":
        for folder in (cascfiles.casc_dir(), env.folder(), target.parent):
            assert folder.stat().st_mode & 0o777 == 0o700, folder
    assert [p.name for p in target.parent.iterdir()] == [target.name], "no temp file is left"
    after = env.files()

    def no_read(self: casc.CascSource, fdid: int, **kw: object) -> None:
        raise AssertionError("a file already extracted is not decoded again")

    monkeypatch.setattr(casc.CascSource, "read", no_read)
    again = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--json")
    assert again.exit_code == 0, again.output
    assert cli.CascCatReport.model_validate_json(again.stdout).outcome == "already_extracted"
    text = env.run("casc", "cat", "--fdid", str(IDS["local"]))
    assert text.exit_code == 0, text.output
    assert text.stdout == (
        f"Already extracted: {target}\n  file {IDS['local']}, the same content key; "
        "nothing was written\n"
    )
    assert env.files() == after, "a repeat writes nothing"


def test_cat_text_names_the_file_written_constructed(env: Env) -> None:
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]))
    assert result.exit_code == 0, result.output
    assert result.stdout == (
        f"Extracted file {IDS['local']} ({len(PLAIN):,} bytes) from {PRODUCT} {VERSION} "
        f"to {env.default()}\n"
    )


def test_cat_never_overwrites_other_bytes_at_its_default_place_constructed(env: Env) -> None:
    target = env.default()
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x" * len(PLAIN))
    before = env.files()
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]))
    assert result.exit_code == 1
    assert "holds other bytes" in result.stderr and "nothing was written" in result.stderr
    assert env.files() == before


@pytest.mark.parametrize("name", ["not_local", "not_in_build", "encrypted", "absent"])
def test_cat_of_a_file_that_is_not_local_writes_nothing_constructed(env: Env, name: str) -> None:
    result = env.run("casc", "cat", "--fdid", str(IDS[name]))
    assert result.exit_code == 1
    assert result.stderr.startswith(f"wowlab: cannot extract file {IDS[name]}: it is ")
    assert "nothing was written" in result.stderr
    assert not cascfiles.casc_dir().exists()


# ─── cat --out ───────────────────────────────────────────────────────────────


def test_cat_out_inside_the_build_folder_is_written_constructed(env: Env) -> None:
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--out", "models/body.bin")
    assert result.exit_code == 0, result.output
    assert (env.folder() / "models" / "body.bin").read_bytes() == PLAIN
    absolute = env.folder() / "elsewhere.bin"
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--out", str(absolute), "--json")
    assert result.exit_code == 0, result.output
    assert cli.CascCatReport.model_validate_json(result.stdout).path == str(absolute)
    assert absolute.read_bytes() == PLAIN


def test_cat_out_dash_writes_the_bytes_to_stdout_constructed(env: Env) -> None:
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--out", "-")
    assert result.exit_code == 0, result.output
    assert result.stdout_bytes == PLAIN
    assert not cascfiles.casc_dir().exists(), "stdout writes no file"


def test_cat_out_dash_is_refused_with_json_and_on_a_terminal_constructed(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--out", "-", "--json")
    assert result.exit_code == 2
    assert "--json cannot" in result.stderr

    class Tty:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdout", Tty())
    with pytest.raises(cli.CliError, match="to the terminal"):
        cli.casc_cat.__wrapped__(  # type: ignore[attr-defined]
            fdid=IDS["local"], out="-", root=env.root, flavor=FLAVOR, json_out=False
        )


def _outside(env: Env) -> list[str]:
    folder = env.folder()
    return [
        "../other-build/x.bin",  # another build's folder
        "../../pages/looks.html",  # the rest of the user data directory
        "files/../../x.bin",
        str(env.root.parent / "loose.bin"),  # anywhere else
        str(folder),  # the folder itself
        ".",
        "characters/x.bin",  # char model's folder
        "characters. /x.bin",  # the same, as Windows reads trailing dots and spaces
        "files",  # the default folder's own name (review of 2026-11-18, F1)
        "files/11-deadbeef.bin",  # another file's default name
        "files/sub/x.bin",
        "FILES/x.bin",
        "files./x.bin",
        "x.bin.",  # a part ending in a dot (S2)
        "x.bin ",  # or a space
        "sub./x.bin",
        "x.bin:stream",  # a ':' (an alternate data stream on Windows)
        "Characters/x.bin",  # the same, in another letter case
        str(folder.parent / f"{BUILD_FOLDER}x" / "y.bin"),  # a name that only starts the same
    ]


def test_a_traversal_out_is_refused_constructed(env: Env) -> None:
    for out in _outside(env):
        before = env.files()
        result = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--out", out)
        assert result.exit_code == 1, (out, result.output)
        assert result.stderr.startswith("wowlab: refused: "), (out, result.stderr)
        assert "nothing was written" in result.stderr, out
        assert env.files() == before, out
    assert not (env.root.parent / "loose.bin").exists()


def test_out_naming_the_install_is_refused_constructed(env: Env) -> None:
    for target in (env.root / FLAVOR / "x.bin", env.root / "Data" / "x.bin", env.root):
        result = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--out", str(target))
        assert result.exit_code == 1, (target, result.output)
        assert "refused" in result.stderr
    assert not (env.root / FLAVOR / "x.bin").exists()


@pytest.mark.skipif(os.name != "posix", reason="symlinks need privileges on Windows")
def test_out_through_a_link_into_the_install_is_refused_constructed(env: Env) -> None:
    env.folder().mkdir(parents=True)
    (env.folder() / "into").symlink_to(env.root / FLAVOR, target_is_directory=True)
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--out", "into/x.bin")
    assert result.exit_code == 1, result.output
    assert "refused" in result.stderr
    assert not (env.root / FLAVOR / "x.bin").exists()
    (env.folder() / "file.bin").symlink_to(env.root / ".build.info")
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--out", "file.bin")
    assert result.exit_code == 1, result.output


@pytest.mark.skipif(os.name != "posix", reason="symlinks need privileges on Windows")
def test_a_casc_folder_that_is_a_link_is_refused_constructed(env: Env) -> None:
    elsewhere = env.root.parent / "elsewhere"
    elsewhere.mkdir()
    cascfiles.casc_dir().parent.mkdir(parents=True, exist_ok=True)
    cascfiles.casc_dir().symlink_to(elsewhere, target_is_directory=True)
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]))
    assert result.exit_code == 1, result.output
    assert "a link is never followed" in result.stderr
    assert list(elsewhere.iterdir()) == []


def test_out_naming_a_folder_is_refused_constructed(env: Env) -> None:
    (env.folder() / "sub").mkdir(parents=True)
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--out", "sub")
    assert result.exit_code == 1
    assert "is a folder" in result.stderr


def test_an_empty_out_is_a_usage_error_constructed(env: Env) -> None:
    result = env.run("casc", "cat", "--fdid", str(IDS["local"]), "--out", "")
    assert result.exit_code == 2


# ─── cascfiles itself ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("product", "version"),
    [("../x", "1.2"), ("wow", ".."), ("wow-x", "1.2"), ("", "1.2"), ("wow", "1/2"), ("w", "1\\2")],
)
def test_an_unsafe_folder_part_is_refused_constructed(product: str, version: str) -> None:
    with pytest.raises(cascfiles.CascOutError, match="not a safe folder name"):
        cascfiles.build_dir(product, version)


def test_a_default_name_needs_an_id_and_a_content_key_constructed() -> None:
    assert cascfiles.default_name(5, "ab" * 16) == "5-abababab.bin"
    for fdid, key in ((-1, "ab" * 16), (2**31, "ab" * 16), (5, "AB" * 16), (5, "ab" * 15)):
        with pytest.raises(cascfiles.CascOutError):
            cascfiles.default_name(fdid, key)


def test_write_new_without_hard_links_still_writes_once_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect_user_dirs(monkeypatch, tmp_path / "home")
    folder = cascfiles.build_dir("wow_x", "1.2.3.4")
    target = folder / "sub" / "a.bin"
    data = b"constructed bytes"
    key = ckey_of(data).hex()

    def no_link(src: object, dst: object) -> None:
        raise OSError(1, "hard links are not supported here")

    monkeypatch.setattr(os, "link", no_link)
    renamed: list[tuple[str, str]] = []
    real_rename = os.rename

    def spy_rename(src: Any, dst: Any) -> None:
        renamed.append((Path(src).name, Path(dst).name))
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", spy_rename)
    assert cascfiles.write_new(data, target, folder, key) is True
    assert target.read_bytes() == data
    # Written once, to the temp file, and published by a rename.
    assert len(renamed) == 1 and renamed[0][1] == "a.bin"
    assert renamed[0][0].startswith(".a.bin.") and renamed[0][0].endswith(".tmp")
    assert cascfiles.write_new(data, target, folder, key) is False
    assert [p.name for p in target.parent.iterdir()] == ["a.bin"]
    with pytest.raises(cascfiles.CascOutError, match="do not match"):
        cascfiles.write_new(b"other", folder / "b.bin", folder, key)
    assert not (folder / "b.bin").exists()


def test_write_new_that_loses_a_race_keeps_the_other_file_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect_user_dirs(monkeypatch, tmp_path / "home")
    folder = cascfiles.build_dir("wow_x", "1.2.3.4")
    target = folder / "a.bin"
    data = b"constructed bytes"
    real_link = os.link

    def racing(src: Any, dst: Any) -> None:
        Path(dst).write_bytes(b"someone else's bytes")
        real_link(src, dst)

    monkeypatch.setattr(os, "link", racing)
    with pytest.raises(cascfiles.CascOutError, match="holds other bytes"):
        cascfiles.write_new(data, target, folder, ckey_of(data).hex())
    assert target.read_bytes() == b"someone else's bytes"
    assert [p.name for p in folder.iterdir()] == ["a.bin"]


def test_json_reports_round_trip_constructed(env: Env) -> None:
    result = env.run("casc", "stat", "--fdid", str(IDS["encrypted"]), "--json")
    payload = json.loads(result.stdout)
    assert payload["state"] == "encrypted" and payload["key_names"]
    text = env.run("casc", "stat", "--fdid", str(IDS["encrypted"]))
    assert text.stdout.splitlines()[1].startswith(
        "  wowlab does not decrypt the game's files (key name "
    )


def test_write_new_without_hard_links_never_replaces_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where a rename refuses an existing name (Windows), a file that
    appeared after the last check is kept and the write refused."""
    redirect_user_dirs(monkeypatch, tmp_path / "home")
    folder = cascfiles.build_dir("wow_x", "1.2.3.4")
    target = folder / "a.bin"
    data = b"constructed bytes"

    def no_link(src: object, dst: object) -> None:
        raise OSError(1, "hard links are not supported here")

    def windows_rename(src: Any, dst: Any) -> None:
        Path(dst).write_bytes(b"another program's bytes")  # appears after the check
        raise FileExistsError(17, "exists", str(dst))

    monkeypatch.setattr(os, "link", no_link)
    monkeypatch.setattr(os, "rename", windows_rename)
    with pytest.raises(cascfiles.CascOutError, match="holds other bytes"):
        cascfiles.write_new(data, target, folder, ckey_of(data).hex())
    assert target.read_bytes() == b"another program's bytes"
    assert [p.name for p in folder.iterdir()] == ["a.bin"], "the temp file is removed"


def test_write_new_sweeps_temp_files_older_than_a_day_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run killed midway (the UI's timeout kills the whole process group)
    leaves its temp file; the next write into that folder removes it once it
    is a day old, and leaves a fresh one, a link and any other name alone."""
    redirect_user_dirs(monkeypatch, tmp_path / "home")
    folder = cascfiles.build_dir("wow_x", "1.2.3.4")
    files = folder / "files"
    files.mkdir(parents=True)
    old = files / f".10-abcdef01.bin.{'a' * 32}.tmp"
    fresh = files / f".11-abcdef01.bin.{'b' * 32}.tmp"
    other = files / ".notes.tmp"
    for path in (old, fresh, other):
        path.write_bytes(b"cut short")
    day_ago = time.time() - 2 * 24 * 60 * 60
    os.utime(old, (day_ago, day_ago))
    os.utime(other, (day_ago, day_ago))
    kept = [fresh, other]
    if os.name == "posix":
        link = files / f".12-abcdef01.bin.{'c' * 32}.tmp"
        link.symlink_to(old)
        os.utime(link, (day_ago, day_ago), follow_symlinks=False)
        kept.append(link)
    data = b"constructed bytes"
    target = files / f"13-{ckey_of(data).hex()[:8]}.bin"
    assert cascfiles.write_new(data, target, folder, ckey_of(data).hex(), default=True)
    assert not os.path.lexists(old)
    assert all(os.path.lexists(p) for p in kept)
    assert target.read_bytes() == data


@pytest.mark.parametrize("version", ["1.2.", "1.2.3.", "1. ", "a:b", "x" * 65])
def test_a_version_windows_would_rename_is_refused_constructed(version: str) -> None:
    with pytest.raises(cascfiles.CascOutError, match="not a safe folder name"):
        cascfiles.build_dir("wow", version)


def test_only_the_default_name_goes_into_files_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect_user_dirs(monkeypatch, tmp_path / "home")
    folder = cascfiles.build_dir("wow_x", "1.2.3.4")
    good = folder / "files" / "10-abcdef01.bin"
    assert cascfiles.check_out(good, folder, default=True) == good
    for bad in ("files/x.bin", "files/10-abcdef01.bin/x", "files/10-ABCDEF01.bin", "files"):
        with pytest.raises(cascfiles.CascOutError):
            cascfiles.check_out(bad, folder, default=True)


@pytest.mark.skipif(os.name != "posix", reason="the probe makes a file where a folder goes")
def test_a_file_where_a_folder_goes_is_named_as_such_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect_user_dirs(monkeypatch, tmp_path / "home")
    folder = cascfiles.build_dir("wow_x", "1.2.3.4")
    folder.mkdir(parents=True)
    (folder / "sub").write_bytes(b"a file")
    with pytest.raises(cascfiles.CascOutError, match="a part that is a file, not a folder"):
        cascfiles.check_out("sub/x.bin", folder)
