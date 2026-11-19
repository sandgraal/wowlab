"""Crew records stay out of what a player shares (M19-05, docs/LAB_PLAN.md
§20.5 term 4, ADR-0036 term 4, ADR-0031).

CONSTRUCTED INPUT, every test: each crew book is built at run time by
`parser/_crew_build.py` (ADR-0036 term 3: no real crew record enters the
repository) inside a copy of the captured install `fixtures/macos-70888/` in
`tmp_path`. No test reads a real install, the user data directory is
redirected into `tmp_path`, the process table is a fake one with no client
in it, and each doctor test checks the copy is unchanged afterwards (L1).

What is pinned:

- `wowlab doctor`, with and without `--redact`, as text and as `--json`,
  says of a crew book only whether it is on or off and how many players it
  keeps, or that it could not be read: no key, name, realm, guild, note or
  instance name, whether the book is in `WowLab.lua` or in the game's backup
  `WowLab.lua.bak`. A malformed `WowLab.lua` whose refused token is a name
  is reported by its place only, never by the token.
- A profile never carries the account `WowLab.lua` or its `.bak`: every
  preset excludes both, a `--subtree` naming either is refused, and a saved
  profile's snapshot holds neither (nor any byte of a constructed name).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from wowlab_core import cli, crewbook, layout, profiles, snapshot

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _casc_build import tree_state
from _crew_build import (
    ACCOUNT,
    FLAVOR,
    GUILD,
    KEY_A,
    KEY_B,
    KEY_C,
    KEY_D,
    NOTE,
    WHERE,
    account_file,
    book,
    install,
    player,
)
from _user_dirs import redirect_user_dirs
from test_cli import FakeProcess, _plain, _use_table

runner = CliRunner()

# Invented guild, note and instance texts beyond the helper's, one per
# player, so a leak of any one field of any one entry is caught.
GUILD_B = "Invented Second Testing Guild"
NOTE_C = "invented note: stood in the fire twice"
WHERE_D = "Invented Other Test Raid"


def _parts(key: str) -> tuple[str, str]:
    name, _, realm = key.partition("-")
    return name, realm


# Every constructed text the books below hold that names or describes a
# player: the keys whole, their name and realm parts, guilds, notes and
# instance names.
FORBIDDEN: tuple[str, ...] = tuple(
    dict.fromkeys(
        [
            *(k for k in (KEY_A, KEY_B, KEY_C, KEY_D)),
            *(p for k in (KEY_A, KEY_B, KEY_C, KEY_D) for p in _parts(k)),
            GUILD,
            GUILD_B,
            NOTE,
            NOTE_C,
            WHERE,
            WHERE_D,
        ]
    )
)

# The name part of a key, written as a bare identifier where a value is
# expected: the parser refuses it, and the token it refuses is that name.
REFUSED_NAME = _parts(KEY_A)[0]


def _players() -> dict[str, Any]:
    return {
        KEY_A: player(rating="good", note=NOTE),
        KEY_B: player(guild=GUILD_B),
        KEY_C: player(rating="bad", note=NOTE_C),
        KEY_D: player(where=WHERE_D),
    }


def _full(on: bool | None = True) -> bytes:
    return book(_players(), on=on, current=[KEY_A, KEY_B])


def _malformed() -> bytes:
    """A book whose guild value is a bare name: a refused document whose
    refused token is a name."""
    data = _full()
    quoted = b'"' + GUILD.encode() + b'"'
    assert quoted in data
    return data.replace(quoted, REFUSED_NAME.encode(), 1)


def _with_unread_entry() -> bytes:
    """One entry the reader lists as unread (its level is text), the rest read."""
    players = _players()
    players[KEY_B] = player(guild=GUILD_B, level="sixty")
    return book(players)


class Env:
    """A copy of the captured install, its account `WowLab.lua` holding
    `crew`, and the game's backup `.bak` holding `bak` when given."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.before = tree_state(root)

    @property
    def sv(self) -> Path:
        return self.root / FLAVOR / "WTF" / "Account" / ACCOUNT / "SavedVariables"

    def settle(self) -> None:
        self.before = tree_state(self.root)

    def run(self, *args: str) -> Any:
        return runner.invoke(
            cli.app, [*args, "--root", str(self.root)], terminal_width=200, catch_exceptions=False
        )


@pytest.fixture
def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Env]]:
    monkeypatch.delenv("WOWLAB_WOW_ROOT", raising=False)
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    redirect_user_dirs(monkeypatch, home)
    _use_table(monkeypatch, FakeProcess(101, "python3", str(tmp_path / "elsewhere" / "python3")))
    made: list[Env] = []

    def build(crew: bytes | None, *, bak: bytes | None = None) -> Env:
        folder = tmp_path / f"i{len(made)}"
        folder.mkdir()
        env = Env(install(folder, crew))
        if bak is not None:
            # The game's backup: the captured file with `bak` appended.
            (env.sv / "WowLab.lua.bak").write_bytes(account_file(bak))
        env.settle()
        made.append(env)
        return env

    yield build
    for env in made:
        assert tree_state(env.root) == env.before, "doctor wrote into the install (L1)"


def _doctor(env: Env, *args: str) -> str:
    """`doctor` past its first run (a snapshot taken first, without --redact,
    which skips the first run anyway): stdout and stderr together."""
    if "--redact" not in args and not any(a.startswith("--redact-name") for a in args):
        snap = env.run("snap", "create", "-m", "first")
        assert snap.exit_code == 0, snap.output
    result = env.run("doctor", "--offline", *args)
    assert result.exit_code in (0, 1), result.output
    if "--json" in args:
        # Nothing on stderr either; stdout alone is the report.
        assert not _leaks(result.stderr)
        return str(result.stdout)
    return _plain(result.stdout) + _plain(result.stderr)


def _leaks(text: str) -> list[str]:
    folded = text.casefold()
    return [w for w in FORBIDDEN if w.casefold() in folded]


def _report(text: str) -> cli.DoctorReport:
    return cli.DoctorReport.model_validate_json(text)


def _books(report: cli.DoctorReport) -> list[cli.CrewBookSummary]:
    return [b for f in report.flavors for b in f.crew_books]


MODES = [(), ("--redact",)]
MODE_IDS = ["plain", "redact"]
FORMS = [(), ("--json",)]
FORM_IDS = ["text", "json"]

# Each case: the live file's book (None: the captured file alone), the
# backup's book (None: no backup), and the state doctor reports for the
# account (None: no line, the live file holds no book).
CASES: dict[str, tuple[Callable[[], bytes | None], Callable[[], bytes | None], str | None]] = {
    "on": (_full, lambda: None, "on"),
    "off": (lambda: _full(on=False), lambda: None, "off"),
    "on-absent": (lambda: _full(on=None), lambda: None, "off"),
    "unread-entry": (_with_unread_entry, lambda: None, "on"),
    "malformed-name-token": (_malformed, lambda: None, "unreadable"),
    "unknown-schema": (lambda: book(_players(), schema=7), lambda: None, "unreadable"),
    "only-in-bak": (lambda: None, _full, None),
    "in-both": (_full, lambda: _full(on=False), "on"),
    "malformed-bak": (_full, _malformed, "on"),
}


@pytest.mark.parametrize("form", FORMS, ids=FORM_IDS)
@pytest.mark.parametrize("mode", MODES, ids=MODE_IDS)
@pytest.mark.parametrize("case", list(CASES), ids=[f"constructed-{c}" for c in CASES])
def test_constructed_doctor_names_no_crew_record(
    make: Callable[..., Env], case: str, mode: tuple[str, ...], form: tuple[str, ...]
) -> None:
    live, bak, state = CASES[case]
    env = make(live(), bak=bak())
    out = _doctor(env, *mode, *form)
    assert not _leaks(out), f"doctor {' '.join(mode + form)} printed crew text"
    if form:
        report = _report(out)
        books = _books(report)
        assert [b.state for b in books] == ([state] if state else [])
        dumped = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
        assert not _leaks(dumped)
    else:
        lines = [line for line in out.splitlines() if "Crew book" in line]
        assert len(lines) == (1 if state else 0), out


@pytest.mark.parametrize("mode", MODES, ids=MODE_IDS)
def test_constructed_doctor_says_on_and_a_count(
    make: Callable[..., Env], mode: tuple[str, ...]
) -> None:
    env = make(_full())
    text = _doctor(env, *mode)
    account = "<ACCOUNT>" if mode else ACCOUNT
    assert (
        f"Crew book (game account {account}): on, 4 players (as of your last logout or /reload)"
        in text
    ), text
    report = _report(_doctor(env, *mode, "--json"))
    (summary,) = _books(report)
    assert (summary.state, summary.players, summary.unread, summary.reason) == ("on", 4, 0, None)
    assert summary.account == account


def test_constructed_doctor_says_off_with_the_count_kept(make: Callable[..., Env]) -> None:
    env = make(_full(on=False))
    text = _doctor(env, "--redact")
    assert "Crew book (game account <ACCOUNT>): off (not recording), 4 players kept" in text


def test_constructed_doctor_counts_an_unread_entry_without_its_key(
    make: Callable[..., Env],
) -> None:
    env = make(_with_unread_entry())
    text = _doctor(env, "--redact")
    assert "on, 4 players, 1 of them unreadable" in text, text
    (summary,) = _books(_report(_doctor(env, "--redact", "--json")))
    assert (summary.players, summary.unread) == (4, 1)


@pytest.mark.parametrize("mode", MODES, ids=MODE_IDS)
def test_constructed_a_refused_name_token_is_not_quoted(
    make: Callable[..., Env], mode: tuple[str, ...]
) -> None:
    """The parser refuses the bare name, and its own message quotes it;
    doctor gives the place only."""
    data = _malformed()
    env = make(data)
    with pytest.raises(Exception) as caught:
        crewbook.parse_book((env.sv / "WowLab.lua").read_bytes())
    assert REFUSED_NAME in str(caught.value), "the plant does not reach the parser's message"
    text = _doctor(env, *mode)
    (line,) = [line for line in text.splitlines() if "Crew book" in line]
    assert "could not be read. WowLab.lua is not readable as saved variables (at line" in line
    assert REFUSED_NAME.casefold() not in text.casefold()
    raw = _doctor(env, *mode, "--json")
    assert REFUSED_NAME.casefold() not in raw.casefold()
    (summary,) = _books(_report(raw))
    assert summary.state == "unreadable" and summary.players is None
    assert summary.reason is not None and "line" in summary.reason


def test_constructed_no_book_no_line(make: Callable[..., Env]) -> None:
    """The captured file holds no book: doctor says nothing about one."""
    env = make(None)
    assert "Crew book" not in _doctor(env)
    assert _books(_report(_doctor(env, "--json"))) == []


def test_constructed_doctor_redact_json_carries_no_record_field() -> None:
    """The model has no field a record, key, name, guild or note could go in."""
    assert set(cli.CrewBookSummary.model_fields) == {
        "account",
        "file",
        "state",
        "players",
        "unread",
        "reason",
    }


# ─── profiles never carry the account WowLab.lua or its .bak ──────────────────

SV_REL = f"WTF/Account/{ACCOUNT}/SavedVariables"
LAB_FILES = {f"{SV_REL}/WowLab.lua", f"{SV_REL}/WowLab.lua.bak"}


def test_profile_presets_exclude_the_account_wowlab_lua_and_bak() -> None:
    always = profiles.always_excluded()
    assert {"SavedVariables/WowLab.lua", "SavedVariables/WowLab.lua.bak"} <= set(always.account)
    for rel in LAB_FILES:
        assert profiles.is_always_excluded(rel), rel


@pytest.mark.parametrize(
    "presets",
    [[p] for p in sorted(profiles.presets())] + [sorted(profiles.presets())],
    ids=[*sorted(profiles.presets()), "all"],
)
def test_constructed_every_preset_excludes_the_crew_book(
    make: Callable[..., Env], presets: list[str]
) -> None:
    env = make(_full(), bak=_full())
    lay = layout.Layout(env.root / FLAVOR)
    sel = profiles.select(lay, preset_names=presets)
    assert set(sel.excluded) >= LAB_FILES
    assert not LAB_FILES & set(sel.subtrees)


@pytest.mark.parametrize("rel", sorted(LAB_FILES))
def test_constructed_a_subtree_naming_the_book_is_refused(
    make: Callable[..., Env], rel: str
) -> None:
    env = make(_full(), bak=_full())
    with pytest.raises(profiles.ProfileError, match="lab-addon"):
        profiles.select(layout.Layout(env.root / FLAVOR), subtrees=[rel])


def test_constructed_a_saved_profile_holds_no_crew_book(make: Callable[..., Env]) -> None:
    """`profile save` with every preset (the addons preset takes the whole
    account SavedVariables/ folder): the snapshot lists neither file, and no
    object in the store holds a constructed name."""
    env = make(_full(), bak=_full())
    flags = [x for p in sorted(profiles.presets()) for x in ("--preset", p)]
    result = env.run("profile", "save", "everything", *flags, "--json")
    assert result.exit_code == 0, result.output
    store = snapshot.SnapshotStore()
    (manifest,) = store.list()
    paths = {e.path.casefold() for e in manifest.entries}
    for rel in LAB_FILES:
        assert f"{FLAVOR}/{rel}".casefold() not in paths
    assert any(p.endswith(".lua") for p in paths), "the save took no SavedVariables at all"
    marker = crewbook.CREW_VARIABLE.encode()
    hashes = [e.sha256 for e in manifest.entries if e.sha256]
    assert hashes, "the save stored no file"
    for sha in hashes:
        data = store.read_object(sha)
        assert marker not in data, sha
        assert not [w for w in FORBIDDEN if w.encode() in data], sha
    for obj in store.path.rglob("*"):
        if obj.is_file():
            raw = obj.read_bytes()
            assert marker not in raw, obj.name
            assert not [w for w in FORBIDDEN if w.encode() in raw], obj.name
    assert tree_state(env.root) == env.before, "profile save wrote into the install (L1)"


# ─── the bug-report template ─────────────────────────────────────────────────

BUG_TEMPLATE = Path(__file__).resolve().parents[3] / ".github" / "ISSUE_TEMPLATE" / "bug.yml"


def test_the_bug_report_warning_names_the_crew_book() -> None:
    """§20.5: the bug-report template's warning names the crew book, and
    says `doctor --redact` leaves it out."""
    text = BUG_TEMPLATE.read_text(encoding="utf-8")
    warning = text.split("- type: textarea", 1)[0]
    assert "crew book" in warning
    assert "WowLab.lua.bak" in warning
    assert "It leaves the crew book out" in text
