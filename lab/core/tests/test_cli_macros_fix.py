"""`wowlab macros fix` (M13-07) through Typer's runner, and `macrofix` itself.

The install is the captured launch-client tree (`fixtures/macos-70888/`,
build 70888) copied into `tmp_path` with its flavor folder named as its
provenance row says (`_forever_`). Its real files are the account's empty
`macros-cache.txt` and the character's, with 29 macros whose `#showtooltip`
is already the first line: on them the fix finds nothing to do and writes
nothing.

Inputs labelled `constructed` are boundary cases (L8): a `macros-cache.txt`
written by the test, in the captured grammar (`VER 3`, a hex id, CRLF), with
made-up names, or such a record appended to the real character file
(`real-plus-constructed`), so the bytes of the real file that the fix must not
touch are the captured ones. Nothing here reads or writes a real install:
`WOWLAB_WOW_ROOT` points at the copy, the process table is a fake one and
the user data directory is redirected into `tmp_path`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, guard, install, macrocheck, macrofix, wtfconfig

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos-70888"
FLAVOR = "_forever_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "LABACCOUNTA"
ACCOUNT_DIR = f"WTF/Account/{ACCOUNT}"
CHARACTER = "Sandgraal-Rizzen"
CHAR_FILE = f"{ACCOUNT_DIR}/1/{CHARACTER}/macros-cache.txt"
ACCOUNT_FILE = f"{ACCOUNT_DIR}/macros-cache.txt"

runner = CliRunner()


class FakeProcess:
    """The `psutil.Process` surface `wowlab_core.process` reads."""

    def __init__(self, pid: int, name: str, exe: str) -> None:
        self._pid, self._name, self._exe = pid, name, exe

    @property
    def pid(self) -> int:
        return self._pid

    def name(self) -> str:
        return self._name

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return [self._exe]

    def status(self) -> str:
        return "running"


def _use_table(monkeypatch: pytest.MonkeyPatch, *procs: FakeProcess) -> None:
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(procs))


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture(autouse=True)
def idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A process table with no client in it."""
    _use_table(monkeypatch, FakeProcess(101, "python3", str(tmp_path / "elsewhere" / "python3")))


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def flavor(root: Path) -> Path:
    return root / FLAVOR


def run(*args: str, input: str | None = None) -> Any:
    return runner.invoke(cli.app, list(args), input=input, terminal_width=10_000)


def ok(*args: str, input: str | None = None) -> Any:
    result = run(*args, input=input)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def fix_json(*args: str) -> cli.MacrosFixReport:
    return cli.MacrosFixReport.model_validate_json(ok("macros", "fix", *args, "--json").stdout)


def _macro(n: int, name: str, *body: str, end: bool = True, eol: str = "\r\n") -> bytes:
    """One constructed record in the captured grammar (CRLF, VER 3, hex id)."""
    lines = [f'VER 3 {0x0100000000000100 + n:016X} "{name}" "134400"', *body]
    if end:
        lines.append("END")
    return "".join(f"{line}{eol}" for line in lines).encode("utf-8")


def _id(n: int) -> str:
    return f"{0x0100000000000100 + n:016X}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _plan(data: bytes, scope: macrocheck.MacroScope = "character") -> macrofix.FixPlan:
    return macrofix.plan(wtfconfig.parse_macros(data), scope=scope, file="x/macros-cache.txt")


def _bodies(data: bytes) -> list[list[str]]:
    return [list(m.body_lines) for m in wtfconfig.parse_macros(data).macros]


# ─── the real files ──────────────────────────────────────────────────────────


def test_real_character_file_has_nothing_to_fix_and_nothing_is_written(
    flavor: Path, user_data: Path
) -> None:
    before = (flavor / CHAR_FILE).read_bytes()
    rep = fix_json("--character", CHARACTER)
    assert rep.file == CHAR_FILE and rep.scope == "character"
    assert rep.character == f"1/{CHARACTER}"
    assert rep.fixes == [] and rep.written is False and rep.transaction is None
    assert rep.target_sha256 == _sha(before) and rep.fixed_sha256 is None
    assert (flavor / CHAR_FILE).read_bytes() == before
    assert guard.history() == ()
    text = ok("macros", "fix", "--character", CHARACTER).stdout
    assert f"Nothing to fix in {CHAR_FILE}." in text


def test_real_character_file_plans_to_its_own_bytes() -> None:
    data = (CAPTURE / "forever" / CHAR_FILE).read_bytes()
    plan = _plan(data)
    assert plan.fixes == [] and plan.data == data


def test_real_empty_account_file_has_nothing_to_fix(flavor: Path) -> None:
    assert (flavor / ACCOUNT_FILE).read_bytes() == b""
    rep = fix_json()
    assert rep.scope == "account" and rep.file == ACCOUNT_FILE and rep.fixes == []
    assert rep.character is None and rep.written is False


def test_no_macros_file_is_said_and_nothing_written_constructed(flavor: Path) -> None:
    (flavor / ACCOUNT_FILE).unlink()
    rep = fix_json()
    assert rep.file is None and rep.target_sha256 is None and rep.fixes == []
    assert "macros-cache.txt" in ok("macros", "fix").stdout
    assert not (flavor / ACCOUNT_FILE).exists()


# ─── the fixable rule ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("directive", "body", "fixed"),
    [
        pytest.param(
            "#showtooltip",
            ["/cast Corruption", "#showtooltip Corruption"],
            ["#showtooltip Corruption", "/cast Corruption"],
            id="constructed-showtooltip-second",
        ),
        pytest.param(
            "#showtooltip",
            ["/stopcasting", "/cast [harm] Fear", "#SHOWTOOLTIP"],
            ["#SHOWTOOLTIP", "/stopcasting", "/cast [harm] Fear"],
            id="constructed-showtooltip-upper-third",
        ),
        pytest.param(
            "#show",
            ["/stopcasting", "#show Corruption", "/cast Corruption"],
            ["#show Corruption", "/stopcasting", "/cast Corruption"],
            id="constructed-show-second",
        ),
    ],
)
def test_a_directive_not_first_moves_to_the_top(
    directive: str, body: list[str], fixed: list[str]
) -> None:
    data = _macro(1, "Mac fix", *body)
    plan = _plan(data)
    assert len(plan.fixes) == 1
    fix = plan.fixes[0]
    assert fix.rule == "showtooltip-not-first" and fix.macro.id == _id(1)
    assert fix.before == body and fix.after == fixed
    assert _bodies(plan.data) == [fixed]
    assert len(plan.data) == len(data)
    # The fixed macro passes the rule it was fixed for.
    again = wtfconfig.parse_macros(plan.data)
    sources = [macrocheck.SourceMacro(scope="character", file="x", macro=m) for m in again.macros]
    assert not [
        f for f in macrocheck.check(sources, None).findings if f.rule == "showtooltip-not-first"
    ]
    assert directive.casefold() in fixed[0].casefold()


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(
            _macro(1, "Mac two", "#showtooltip", "/cast Fear", "#showtooltip Fear"),
            id="constructed-first-and-later",
        ),
        pytest.param(
            _macro(1, "Mac two", "/cast Fear", "#show Fear", "#showtooltip Fear"),
            id="constructed-two-later",
        ),
        pytest.param(
            _macro(1, "Mac cut", "/cast Fear", "#showtooltip Fear", end=False),
            id="constructed-no-end",
        ),
        pytest.param(
            _macro(1, "Mac indented", "/cast Fear", "  #showtooltip Fear"),
            id="constructed-indented",
        ),
    ],
)
def test_a_macro_the_player_must_decide_is_not_fixable(record: bytes) -> None:
    sources = [
        macrocheck.SourceMacro(scope="character", file="x", macro=m)
        for m in wtfconfig.parse_macros(record).macros
    ]
    findings = [
        f for f in macrocheck.check(sources, None).findings if f.rule == "showtooltip-not-first"
    ]
    assert findings and not any(f.fixable for f in findings)
    plan = _plan(record)
    assert plan.fixes == [] and plan.data == record


def test_only_the_first_line_rule_is_fixed_and_others_are_left_constructed() -> None:
    data = b"".join(
        [
            _macro(1, "Mac over", "/say " + "a" * 300),
            _macro(2, "Mac brackets", "/cast [mod:shift Fireball"),
            _macro(3, "Mac dup", "/cast Fear"),
            _macro(4, "Mac dup", "/cast Fear"),
            _macro(5, "Mac fix", "/cast Fear", "#showtooltip Fear"),
        ]
    )
    plan = _plan(data)
    assert [f.macro.id for f in plan.fixes] == [_id(5)]
    assert {f.rule for f in plan.left} == {
        "length",
        "unbalanced-brackets",
        "duplicate-name",
        "duplicate-body",
    }
    assert not any(f.fixable for f in plan.left)
    cut = data.index(_macro(5, "Mac fix", "/cast Fear", "#showtooltip Fear"))
    assert plan.data[:cut] == data[:cut], "the macros it does not fix keep every byte (L4)"


def test_endings_bom_and_lines_outside_records_keep_their_place_constructed() -> None:
    """A mixed-ending body, a BOM and an unknown line: each line's ending stays
    where it was, so only the moved texts differ."""
    record = (
        b'VER 3 0100000000000101 "Mac mixed" "134400"\r\n'
        b"/cast Fear\n"
        b"/stopcasting\r\n"
        b"#showtooltip Fear\r\n"
        b"END\r\n"
    )
    data = wtfconfig.BOM + b"a stray line\n" + record + b"tail with no break"
    plan = _plan(data)
    assert len(plan.fixes) == 1
    assert plan.data == (
        wtfconfig.BOM + b"a stray line\n"
        b'VER 3 0100000000000101 "Mac mixed" "134400"\r\n'
        b"#showtooltip Fear\n"
        b"/cast Fear\r\n"
        b"/stopcasting\r\n"
        b"END\r\n"
        b"tail with no break"
    )


def test_non_utf8_bytes_in_a_moved_line_survive_constructed() -> None:
    data = (
        b'VER 3 0100000000000101 "Mac bytes" "134400"\r\n'
        b"/cast \xff\xfe\r\n"
        b"#showtooltip \xe9\r\n"
        b"END\r\n"
    )
    plan = _plan(data)
    assert plan.data == (
        b'VER 3 0100000000000101 "Mac bytes" "134400"\r\n'
        b"#showtooltip \xe9\r\n"
        b"/cast \xff\xfe\r\n"
        b"END\r\n"
    )


def test_macros_check_marks_the_fixable_finding_and_says_so_constructed(flavor: Path) -> None:
    (flavor / CHAR_FILE).write_bytes(
        _macro(1, "Mac fix", "/cast Fear", "#showtooltip Fear")
        + _macro(2, "Mac two", "/cast Fear", "#show Fear", "#showtooltip Fear")
    )
    out = ok("macros", "check", "--character", CHARACTER, "--json").stdout
    rep = cli.MacrosCheckReport.model_validate_json(out)
    marks = {(f.macro.id, f.fixable) for f in rep.findings if f.rule == "showtooltip-not-first"}
    assert marks == {(_id(1), True), (_id(2), False)}
    text = ok("macros", "check", "--character", CHARACTER).stdout
    hint = f"(wowlab macros fix --character {CHARACTER} moves it to the first line)"
    assert text.count(hint) == 1
    assert text.count("moves it to the first line") == 1


def test_the_hint_names_the_options_that_reach_the_file_constructed(flavor: Path) -> None:
    """Bare `macros fix` writes only the account's file: a character finding's
    hint names --character, and --account when check was given it."""
    (flavor / CHAR_FILE).write_bytes(_macro(1, "Mac char", "/cast Fear", "#showtooltip Fear"))
    (flavor / ACCOUNT_FILE).write_bytes(_macro(2, "Mac acc", "/cast Fear", "#show Fear"))
    text = ok("macros", "check", "--character", CHARACTER, "--account", ACCOUNT).stdout
    assert (
        f'"Mac char" (line 3): #showtooltip is on body line 2, not the first '
        f"(wowlab macros fix --character {CHARACTER} --account {ACCOUNT} moves it to the "
        "first line)"
    ) in text
    assert f"(wowlab macros fix --account {ACCOUNT} moves it to the first line)" in text
    bare = ok("macros", "check").stdout
    assert "(wowlab macros fix moves it to the first line)" in bare
    # Following each hint fixes that finding.
    ok("macros", "fix", "--character", CHARACTER, "--account", ACCOUNT, "--yes")
    ok("macros", "fix", "--account", ACCOUNT, "--yes")
    rep = cli.MacrosCheckReport.model_validate_json(
        ok("macros", "check", "--character", CHARACTER, "--json").stdout
    )
    assert not [f for f in rep.findings if f.rule == "showtooltip-not-first"]


def test_the_hint_quotes_a_realm_folder_with_a_space_constructed(flavor: Path) -> None:
    """A realm folder named with a space (constructed): the hint quotes it for
    a shell, keeps it on one line at any width, and the quoted command works."""
    folder = flavor / ACCOUNT_DIR / "Lab Realm" / "Labchar"
    folder.mkdir(parents=True)
    (folder / "macros-cache.txt").write_bytes(
        _macro(1, "Mac spaced", "/cast Fear", "#showtooltip Fear")
    )
    spec = "Lab Realm/Labchar"
    hint = "wowlab macros fix --character 'Lab Realm/Labchar' moves it to the first line"
    wide = ok("macros", "check", "--character", spec).stdout
    assert f"({hint})" in wide
    narrow = runner.invoke(cli.app, ["macros", "check", "--character", spec], terminal_width=40)
    assert narrow.exit_code == 0, narrow.stderr
    lines = narrow.stdout.splitlines()
    first = next(i for i, line in enumerate(lines) if "showtooltip-not-first" in line)
    assert "first line)" not in lines[first], "the finding wraps at this width"
    assert any("'Lab Realm/Labchar'" in line for line in lines[first:])
    ok("macros", "fix", "--character", spec, "--yes")
    assert _bodies((folder / "macros-cache.txt").read_bytes()) == [
        ["#showtooltip Fear", "/cast Fear"]
    ]


def test_an_indented_directive_is_reported_but_not_fixed_constructed(flavor: Path) -> None:
    data = _macro(1, "Mac indented", "/cast Fear", "  #showtooltip Fear")
    (flavor / CHAR_FILE).write_bytes(data)
    rep = cli.MacrosCheckReport.model_validate_json(
        ok("macros", "check", "--character", CHARACTER, "--json").stdout
    )
    (finding,) = [f for f in rep.findings if f.rule == "showtooltip-not-first"]
    assert finding.fixable is False
    assert (
        "moves it to the first line" not in ok("macros", "check", "--character", CHARACTER).stdout
    )
    assert fix_json("--character", CHARACTER, "--yes").fixes == []
    assert (flavor / CHAR_FILE).read_bytes() == data


# ─── the command: plan, confirm, write, undo ─────────────────────────────────


def _append_fixable(flavor: Path) -> tuple[bytes, bytes]:
    """The real character file with one constructed fixable macro after it
    (`real-plus-constructed`): (the real bytes, the whole file)."""
    real = (flavor / CHAR_FILE).read_bytes()
    whole = real + _macro(1, "Mac fix", "/cast Corruption", "#showtooltip Corruption")
    (flavor / CHAR_FILE).write_bytes(whole)
    return real, whole


def test_plan_without_yes_writes_nothing_real_plus_constructed(flavor: Path) -> None:
    _, whole = _append_fixable(flavor)
    result = run("macros", "fix", "--character", CHARACTER, input="")
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert '"Mac fix" (line 117): body line 2 moves to the top' in result.stdout
    assert "#showtooltip Corruption" in result.stdout
    assert cli._FIX_UNCONFIRMED in result.stdout
    assert result.stderr.endswith(f"\n{cli.CONFIRM_DECLINED}\n")
    assert (flavor / CHAR_FILE).read_bytes() == whole
    assert guard.history() == ()


def test_plan_json_names_its_pin_and_writes_nothing_real_plus_constructed(flavor: Path) -> None:
    _, whole = _append_fixable(flavor)
    result = run("macros", "fix", "--character", CHARACTER, "--json", input="")
    assert result.exit_code == 1
    assert result.stderr.endswith(f"\n{cli.CONFIRM_DECLINED}\n")
    rep = cli.MacrosFixReport.model_validate_json(result.stdout)
    assert rep.written is False and rep.target_sha256 == _sha(whole)
    assert rep.fixed_sha256 == _sha(_plan(whole).data)
    assert [f.macro.id for f in rep.fixes] == [_id(1)]
    assert cli._FIX_UNCONFIRMED in rep.notes
    assert (flavor / CHAR_FILE).read_bytes() == whole


def test_yes_writes_only_the_fixed_macro_and_undo_restores_real_plus_constructed(
    flavor: Path,
) -> None:
    real, whole = _append_fixable(flavor)
    rep = fix_json("--character", CHARACTER, "--yes")
    assert rep.written is True and rep.transaction is not None
    after = (flavor / CHAR_FILE).read_bytes()
    assert after[: len(real)] == real, "every captured byte is kept (L4)"
    assert after[len(real) :] == _macro(1, "Mac fix", "#showtooltip Corruption", "/cast Corruption")
    assert _sha(after) == rep.fixed_sha256
    records = guard.history()
    assert [r.id for r in records] == [rep.transaction]
    changes = [(p.path, p.before, p.after) for p in records[0].paths]
    assert changes == [(CHAR_FILE, _sha(whole), _sha(after))]
    assert records[0].label == f"macros fix {CHAR_FILE}"
    undone = run("undo", "--yes")
    assert undone.exit_code == 0, (undone.stdout, undone.stderr)
    assert (flavor / CHAR_FILE).read_bytes() == whole, "undo puts the original bytes back"


def test_text_run_with_yes_says_what_it_did_constructed(flavor: Path) -> None:
    _append_fixable(flavor)
    out = ok("macros", "fix", "--character", CHARACTER, "--yes").stdout
    assert f"Fixed 1 macro in {CHAR_FILE}." in out
    assert "`wowlab undo` puts the old macros back." in out
    assert (
        "Character macros changed this way were kept after login in tests on one game "
        "version; account macros were not tested."
    ) in out


def test_account_file_is_fixed_by_default_constructed(flavor: Path) -> None:
    data = _macro(1, "Mac acc", "/cast Fear", "#showtooltip Fear")
    (flavor / ACCOUNT_FILE).write_bytes(data)
    char_before = (flavor / CHAR_FILE).read_bytes()
    rep = fix_json("--yes")
    assert rep.scope == "account" and rep.written
    assert _bodies((flavor / ACCOUNT_FILE).read_bytes()) == [["#showtooltip Fear", "/cast Fear"]]
    assert (flavor / CHAR_FILE).read_bytes() == char_before


def test_refused_while_the_client_runs_constructed(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, whole = _append_fixable(flavor)
    exe = flavor / "World of Warcraft Classic.app" / "Contents" / "MacOS" / "World of Warcraft"
    _use_table(monkeypatch, FakeProcess(7300, "World of Warcraft", str(exe)))
    result = run("macros", "fix", "--character", CHARACTER, "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "write gate" in result.stderr
    assert (flavor / CHAR_FILE).read_bytes() == whole
    assert guard.history() == ()


# ─── --expect ────────────────────────────────────────────────────────────────


def test_expect_from_the_plan_lets_the_confirmed_run_write_constructed(flavor: Path) -> None:
    _append_fixable(flavor)
    plan = run("macros", "fix", "--character", CHARACTER, "--json", input="")
    rep = cli.MacrosFixReport.model_validate_json(plan.stdout)
    pin = ":".join(str(getattr(rep, f)) for f in cli.EXPECT_FIELDS[("macros", "fix")])
    done = fix_json("--character", CHARACTER, "--yes", f"--expect={pin}")
    assert done.written is True


def test_expect_refuses_a_file_changed_after_the_plan_constructed(flavor: Path) -> None:
    _append_fixable(flavor)
    plan = run("macros", "fix", "--character", CHARACTER, "--json", input="")
    rep = cli.MacrosFixReport.model_validate_json(plan.stdout)
    changed = (flavor / CHAR_FILE).read_bytes() + _macro(9, "Mac new", "/dance")
    (flavor / CHAR_FILE).write_bytes(changed)
    result = run(
        "macros",
        "fix",
        "--character",
        CHARACTER,
        "--yes",
        f"--expect={rep.target_sha256}:{rep.fixed_sha256}",
    )
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert (flavor / CHAR_FILE).read_bytes() == changed
    assert guard.history() == ()


def test_expect_refuses_when_there_is_now_nothing_to_fix_constructed(flavor: Path) -> None:
    real, whole = _append_fixable(flavor)
    pin = f"{_sha(real)}:{_sha(_plan(whole).data)}"
    result = run("macros", "fix", "--character", CHARACTER, "--yes", f"--expect={pin}")
    assert result.exit_code == 3
    (flavor / CHAR_FILE).write_bytes(real)
    result = run("macros", "fix", "--character", CHARACTER, "--yes", f"--expect={pin}")
    assert result.exit_code == 3
    assert (flavor / CHAR_FILE).read_bytes() == real


@pytest.mark.parametrize(
    "pin", ["abc", "a" * 64, "A" * 64 + ":" + "a" * 64, "a" * 64 + ":" + "b" * 63]
)
def test_expect_not_two_sha256s_is_a_usage_error_constructed(flavor: Path, pin: str) -> None:
    result = run("macros", "fix", "--yes", f"--expect={pin}")
    assert result.exit_code == 2


def test_expect_fields_name_fields_of_the_report() -> None:
    assert cli.EXPECT_FIELDS[("macros", "fix")] == ("target_sha256", "fixed_sha256")
    assert {"target_sha256", "fixed_sha256"} <= set(cli.MacrosFixReport.model_fields)


def test_help_says_persistence_is_unconfirmed_and_undo_goes_back() -> None:
    result = runner.invoke(cli.app, ["macros", "fix", "--help"], terminal_width=200)
    assert result.exit_code == 0
    text = " ".join(result.stdout.split())
    assert (
        "Character macros changed this way were kept after login in two tests on one "
        "game version; account macros, other game versions and server-synced macros were "
        "not tested."
    ) in text
    assert "not yet confirmed" not in text
    assert "`wowlab undo` puts the old macros back" in text
    assert "--yes" in text and "--expect" in text


def test_json_report_round_trips(flavor: Path) -> None:
    _append_fixable(flavor)
    rep = fix_json("--character", CHARACTER, "--yes")
    assert cli.MacrosFixReport.model_validate_json(rep.model_dump_json()) == rep
    raw = json.loads(rep.model_dump_json())
    assert raw["fixes"][0]["after"][0] == "#showtooltip Corruption"
