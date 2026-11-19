"""`wowlab char model` (M15-11, docs/LAB_PLAN.md §17.6).

The install is constructed (ADR-0033 layer a) in `tmp_path` by the parser
suite's `_casc_build`: its archives hold a constructed stand-in for every
file the fixture characters' plans name (`_charmodel_build`), and its
`WTF/` holds the real `macos-70450` lab-addon records, copied. The game
tables are replayed from the recorded wago subsets of 1.60.1.70959 (the
replay serves them for the constructed build's version too); nothing
reaches the network. The user data directory is redirected into
`tmp_path`; every test checks the constructed install is unchanged.
"""

from __future__ import annotations

import csv
import io
import json
import random
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

import pytest
from typer.testing import CliRunner

from wowlab_core import cascfiles, cli, renderplan
from wowlab_core.gamedata import GameData

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _charmodel_build as cb
from _casc_build import (
    FLAVOR,
    PRODUCT,
    VERSION,
    EncodingRow,
    blte,
    ckey_of,
    key_in,
    make_source_install,
    n_block,
    tree_state,
)
from _glb_read import read_glb
from _user_dirs import redirect_user_dirs

HERE = Path(__file__).resolve().parent
runner = CliRunner()
ENUS = 0x2
WAGO = HERE / "fixtures" / "wago"
SCRIPT = HERE.parents[2] / "scripts" / "renderplan_facts.py"


def _recorded(name: str) -> bytes:
    """The recorded rows of `name` at 1.60.1.70959, its subsets joined."""
    paths = cb._script().recordings(WAGO, name, cb.TABLES_BUILD)
    out = io.StringIO()
    writer: Any = None
    for path in paths:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            if writer is None:
                writer = csv.writer(out, lineterminator="\n")
                writer.writerow(header)
            writer.writerows(reader)
    return out.getvalue().encode("utf-8")


class _Tables:
    """Serves the recorded tables for any build asked."""

    name = "fixtures"

    def __init__(self) -> None:
        self.asked: list[str] = []

    def fetch_builds(self, dest: BinaryIO) -> str:
        dest.write(b"{}")
        return "fixture:builds"

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        self.asked.append(table)
        dest.write(_recorded(table))
        return f"fixture:{table}.{build}.csv"


def _plan_files(label: str) -> dict[int, bytes]:
    bare = renderplan.plan_for_record(cb.tables(), cb.record(label))
    return cb.files_for(bare)


def _install(tmp: Path, drop: set[int] = frozenset()) -> Path:  # type: ignore[assignment]
    rng = random.Random(1511)
    files: dict[int, bytes] = {}
    for label in ("Sangraal-Sett", "Dangling-Participle"):
        files.update(_plan_files(label))
    stored = []
    rows: list[EncodingRow] = []
    ids, keys = [], []
    for fdid, data in sorted(files.items()):
        if fdid in drop:
            continue
        ekey = key_in(fdid % 16, rng) + rng.randbytes(7)
        stored.append((ekey, blte([n_block(data)])))
        rows.append((ckey_of(data), len(data), [ekey]))
        ids.append(fdid)
        keys.append(ckey_of(data))
    root = make_source_install(
        tmp / "game", stored=stored, encoding_rows=rows, root_blocks=[(ids, keys, 0, ENUS)], rng=rng
    )
    account = root / FLAVOR / "WTF" / "Account" / cb.ACCOUNT
    shutil.copytree(cb.CHARS.parent, account)
    return root


class Env:
    def __init__(self, root: Path, home: Path, tables: _Tables) -> None:
        self.root = root
        self.home = home
        self.tables = tables

    def run(self, *args: str) -> Any:
        return runner.invoke(
            cli.app,
            ["char", "model", *args, "--root", str(self.root), "--flavor", FLAVOR],
            terminal_width=100,
        )

    def folder(self) -> Path:
        return cascfiles.build_dir(PRODUCT, VERSION)

    def glb(self, label: str = "Sangraal-Sett") -> Path:
        return cascfiles.character_path(PRODUCT, VERSION, cb.ACCOUNT, "1", label)


def _env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drop: set[int], copy_as: str | None = None
) -> Iterator[Env]:
    monkeypatch.delenv("WOWLAB_WOW_ROOT", raising=False)
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    redirect_user_dirs(monkeypatch, home)
    served = _Tables()

    @contextmanager
    def fake() -> Iterator[GameData]:
        yield GameData(served, cache_dir=home / "gamedata")

    monkeypatch.setattr(cli, "_open_gamedata", fake)
    root = _install(tmp_path, drop)
    if copy_as is not None:
        realm = root / FLAVOR / "WTF" / "Account" / cb.ACCOUNT / "1"
        shutil.copytree(realm / "Sangraal-Sett", realm / copy_as)
    before = tree_state(root)
    yield Env(root, home, served)
    assert tree_state(root) == before, "the constructed install changed"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    yield from _env(tmp_path, monkeypatch, set())


def test_writes_the_glb_and_its_pngs_into_characters(env: Env) -> None:
    result = env.run("--character", "Sangraal-Sett", "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    glb = env.glb()
    assert report["path"] == str(glb) and report["outcome"] == "written"
    assert glb.parent == env.folder() / "characters" / cb.ACCOUNT
    data = glb.read_bytes()
    parsed = read_glb(data)
    assert len(parsed.nodes) == report["glb"]["nodes"] == 3  # the body and two weapons
    assert report["glb"]["bytes"] == len(data)
    names = sorted(p.name for p in glb.parent.iterdir())
    assert names == sorted(
        ["1-Sangraal-Sett.glb", *(f"1-Sangraal-Sett.{n}.png" for n in ("body", "hair", "eyes"))]
    )
    assert [p["name"] for p in report["pngs"]] == ["body", "hair", "eyes"]
    assert report["gear_as_of"]["source"] == "record"
    assert report["look_as_of"]["source"] == "record" and report["stand_in_look"] is False
    assert report["rest_pose"] and report["likeness"]
    assert report["tables_downloaded"] == list(renderplan.REQUIRED_TABLES)
    assert all(f["stat"]["state"] == "local" for f in report["files"])
    assert {m["what"] for m in report["models"]} == {"body", "item"}
    cli.CharModelReport.model_validate(report)


def test_the_text_says_the_dates_the_pose_and_what_it_is_not(env: Env) -> None:
    result = env.run("--character", "Sangraal-Sett")
    assert result.exit_code == 0, result.output
    text = " ".join(result.stdout.split())
    assert "Gear as of your last logout or /reload" in text
    assert "Look as of" in text and "barber shop" in text
    assert "rest pose" in text
    assert "not a picture of how the character looks in game" in text
    assert "hair and eyes are separate textures" in text
    assert "notes: --notes shows them" in text
    assert text.count("(not yet confirmed)") <= 1


@pytest.mark.parametrize("label", ["Sangraal-Sett", "Dangling-Participle"])
def test_the_text_is_player_copy(env: Env, label: str) -> None:
    """The player-output lint (M16-15) on the whole text, notes included."""
    from test_cli_player_copy import hits

    for extra in ((), ("--notes",)):
        result = env.run("--character", label, *extra)
        assert result.exit_code == 0, result.output
        assert hits(result.stdout) == [] and hits(result.stderr) == []


def test_the_first_run_says_it_downloads_the_tables_then_never_again(env: Env) -> None:
    first = env.run("--character", "Sangraal-Sett")
    assert first.exit_code == 0, first.output
    assert "downloading 23 game tables" in " ".join(first.stderr.split())
    asked = len(env.tables.asked)
    second = env.run("--character", "Sangraal-Sett")
    assert second.exit_code == 0, second.output
    assert "downloading" not in second.stderr and len(env.tables.asked) == asked


def test_a_stand_in_look_says_so(env: Env) -> None:
    result = env.run("--character", "Dangling-Participle", "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["stand_in_look"] is True
    text = env.run("--character", "Dangling-Participle").stdout
    assert "stand-in" in text and "not the character's look" in " ".join(text.split())


def test_a_rerun_replaces_the_model_and_removes_a_png_it_no_longer_makes(env: Env) -> None:
    assert env.run("--character", "Sangraal-Sett").exit_code == 0
    glb = env.glb()
    stale = glb.with_name("1-Sangraal-Sett.texture8.png")
    stale.write_bytes(b"left by an older run")
    other = glb.with_name("1-Sangraal-Sett.notes.txt")
    other.write_bytes(b"not ours")
    glb.write_bytes(b"an older model")
    result = env.run("--character", "Sangraal-Sett", "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["outcome"] == "replaced" and report["removed"] == [str(stale)]
    assert not stale.exists() and other.read_bytes() == b"not ours"
    read_glb(glb.read_bytes())
    assert not [p for p in glb.parent.iterdir() if p.name.endswith(".tmp")]


def test_a_link_in_characters_is_refused_and_nothing_written(env: Env, tmp_path: Path) -> None:
    glb = env.glb()
    glb.parent.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere.glb"
    elsewhere.write_bytes(b"theirs")
    glb.symlink_to(elsewhere)
    result = env.run("--character", "Sangraal-Sett")
    assert result.exit_code == 1
    assert "link" in result.stderr and elsewhere.read_bytes() == b"theirs"


def test_a_body_model_that_is_not_local_exits_1_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = renderplan.plan_for_record(cb.tables(), cb.record("Sangraal-Sett")).body.file_data_id
    for env in _env(tmp_path, monkeypatch, {body}):
        result = env.run("--character", "Sangraal-Sett")
        assert result.exit_code == 1, result.output
        assert f"file {body}" in result.stderr and "nothing was written" in result.stderr
        assert not cascfiles.characters_dir(PRODUCT, VERSION).exists()


def test_out_writes_inside_the_build_folder_and_never_over_another_file(env: Env) -> None:
    result = env.run("--character", "Sangraal-Sett", "--out", "mine.glb", "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    target = env.folder() / "mine.glb"
    assert report["path"] == str(target) and report["outcome"] == "written"
    assert (env.folder() / "mine.body.png").is_file()
    again = json.loads(
        env.run("--character", "Sangraal-Sett", "--out", "mine.glb", "--json").stdout
    )
    assert again["outcome"] == "already_there"
    target.write_bytes(b"someone else's")
    refused = env.run("--character", "Sangraal-Sett", "--out", "mine.glb")
    assert refused.exit_code == 1 and "never overwrites" in refused.stderr
    assert target.read_bytes() == b"someone else's"


@pytest.mark.parametrize(
    "out",
    [
        "../outside.glb",
        "characters/x.glb",
        "files/x.glb",
        "Characters./x.glb",
        "sub:stream.glb",
    ],
    ids=["traversal", "characters", "files", "characters-variant", "colon"],
)
def test_out_outside_the_build_folder_or_reserved_is_refused(env: Env, out: str) -> None:
    result = env.run("--character", "Sangraal-Sett", "--out", out)
    assert result.exit_code == 1, result.output
    assert "refused" in result.stderr and "nothing was written" in result.stderr
    assert env.tables.asked == []  # refused before any work
    assert not cascfiles.casc_dir().exists() or not any(
        p.suffix in (".glb", ".png") for p in cascfiles.casc_dir().rglob("*")
    )


def test_out_into_the_install_is_refused(env: Env) -> None:
    result = env.run("--character", "Sangraal-Sett", "--out", str(env.root / "x.glb"))
    assert result.exit_code == 1 and "refused" in result.stderr


@pytest.mark.parametrize(("out", "words"), [("-", "not offered"), ("", "empty"), ("x.png", ".glb")])
def test_out_usage_errors(env: Env, out: str, words: str) -> None:
    result = env.run("--character", "Sangraal-Sett", "--out", out)
    assert result.exit_code == 2 and words in result.stderr


def test_an_unsafe_folder_name_is_refused_before_any_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed: a character folder whose name holds a dot."""
    for env in _env(tmp_path, monkeypatch, set(), copy_as="Sangraal.Sett"):
        result = env.run("--character", "Sangraal.Sett")
        assert result.exit_code == 1, result.output
        assert "not a safe file name" in result.stderr
        assert env.tables.asked == []


@pytest.mark.parametrize(
    "name",
    ["a.b", ".", "..", "x/y", "x\\y", "a:b", " a", "a ", "", "NUL", "com1", "a\x01b", "a" * 101],
)
def test_safe_name_refuses(name: str) -> None:
    with pytest.raises(cascfiles.CascOutError):
        cascfiles.safe_name(name, "character folder")


@pytest.mark.parametrize(
    "name", ["Sangraal-Sett", "90000001#6", "Labrealmb Partb", "Dângling", "1"]
)
def test_safe_name_admits(name: str) -> None:
    assert cascfiles.safe_name(name, "folder") == name


def test_replace_derived_only_writes_under_characters(env: Env) -> None:
    folder = env.folder()
    with pytest.raises(cascfiles.CascOutError):
        cascfiles.replace_derived(b"x", folder / "files" / "1-00000000.bin", folder)
    with pytest.raises(cascfiles.CascOutError):
        cascfiles.replace_derived(b"x", folder / "characters", folder)
    target = folder / "characters" / "a" / "b.glb"
    cascfiles.replace_derived(b"one", target, folder)
    cascfiles.replace_derived(b"two", target, folder)
    assert target.read_bytes() == b"two"
    assert oct(target.parent.stat().st_mode & 0o777) in ("0o700", "0o777")
