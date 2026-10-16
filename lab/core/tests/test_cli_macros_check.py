"""`wowlab macros check` (M13-06, docs/LAB_PLAN.md §15.2) through Typer's runner.

The install is the captured tree (`fixtures/macos/`, build 69913) copied into
`tmp_path` with its flavor folder named as its provenance row says
(`_classic_beta_`), as `test_cli.py` does. Its real `macros-cache.txt` files
(an empty account file and one character macro) give the report on the
fixture install. The spellbook and action bars come from the real schema-3
record (`fixtures/macos-70425/`, M13-05), copied into a character folder of
that tree: a `cross-capture` combination (two builds, two characters), said
in the test ids.

Inputs labelled `constructed` are boundary cases (L8): a `macros-cache.txt`
written by the test with one macro per rule, each with made-up names (no
player's macro name or body is in this file), a record with its macro slots
turned into item slots, a tampered record, a hostile macro name. Nothing
here reads or writes a real install: `WOWLAB_WOW_ROOT` points at the copy
and the user data directory is redirected into `tmp_path`.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, macrocheck

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
SCHEMA_3 = (
    FIXTURES
    / "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables/WowLab.lua"
)
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
ACCOUNT_DIR = f"WTF/Account/{ACCOUNT}"
CHARACTER = "Labcharb-Labrealmd"  # holds the captured character macros-cache.txt
CHAR_DIR = f"{ACCOUNT_DIR}/1/{CHARACTER}"
SCHEMA_1_CHARACTER = "1/Labchard-Labrealmg"  # a schema-1 WowLab.lua (M11-03), no macros

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


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


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def report(*args: str) -> cli.MacrosCheckReport:
    return cli.MacrosCheckReport.model_validate_json(ok("macros", "check", *args, "--json").stdout)


def _state(root: Path) -> dict[str, tuple[bytes | None, int]]:
    out: dict[str, tuple[bytes | None, int]] = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[p.relative_to(root).as_posix()] = (
            p.read_bytes() if p.is_file() else None,
            st.st_mtime_ns,
        )
    return out


def _put_record(flavor: Path, data: bytes | None = None) -> Path:
    """The real schema-3 record (or `data`) as CHARACTER's WowLab.lua."""
    target = flavor / CHAR_DIR / "SavedVariables" / "WowLab.lua"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(SCHEMA_3.read_bytes() if data is None else data)
    return target


def _macro(n: int, name: str, *body: str) -> bytes:
    """One constructed record in the captured grammar (CRLF, VER 3, hex id)."""
    lines = [f'VER 3 {0x0100000000000000 + n:016X} "{name}" "134400"', *body, "END"]
    return "".join(f"{line}\r\n" for line in lines).encode("utf-8")


def _put_macros(flavor: Path, *records: bytes, folder: str = CHAR_DIR) -> None:
    (flavor / folder / "macros-cache.txt").write_bytes(b"".join(records))


def _ids(rep: cli.MacrosCheckReport, rule: macrocheck.Rule) -> list[str]:
    return [f.macro.id for f in rep.findings if f.rule == rule]


def _id(n: int) -> str:
    return f"{0x0100000000000000 + n:016X}"


# ─── the fixture install ─────────────────────────────────────────────────────


def test_macros_check_reports_on_the_fixture_install(root: Path) -> None:
    before = _state(root)
    rep = report("--character", CHARACTER)
    assert [(f.scope, f.file, f.macros) for f in rep.files] == [
        ("account", f"{ACCOUNT_DIR}/macros-cache.txt", 0),
        ("character", f"{CHAR_DIR}/macros-cache.txt", 1),
    ]
    assert rep.character == f"1/{CHARACTER}"
    assert rep.record_file is None and rep.record_schema is None
    assert rep.findings == []
    assert [(c.rule, c.reason) for c in rep.cannot_judge] == [
        ("spell-not-in-spellbook", "no spellbook record"),
        ("not-on-action-bar", "no spellbook record"),
    ]
    assert (rep.length_limit, rep.length_unit) == (255, macrocheck.LENGTH_UNIT)
    assert "UTF-8 bytes" in rep.length_unit
    [m] = rep.macros
    assert (m.id, m.line, m.complete) == ("0100000000000001", 1, True)
    assert m.bytes == m.characters == 41  # the captured body is ASCII
    text = ok("macros", "check", "--character", CHARACTER).stdout
    assert "Findings: 0\n" in text
    assert "  spell-not-in-spellbook: no spellbook record\n" in text
    assert "counted in UTF-8 bytes" in text and "[verify]" in text
    assert _state(root) == before  # reads never write (L1)


def test_macros_check_without_character_checks_the_account_file(root: Path) -> None:
    rep = report()
    assert [f.scope for f in rep.files] == ["account"]
    assert rep.character is None and rep.macros == []
    assert all("use --character" in c.reason for c in rep.cannot_judge)


def test_macros_check_with_the_schema_3_record_cross_capture_constructed(flavor: Path) -> None:
    """The captured /castsequence macro read against the captured warlock
    spellbook: both its spells are mage spells, so neither is in it."""
    _put_record(flavor)
    rep = report("--character", CHARACTER)
    assert rep.record_schema == 3
    assert rep.record_file == f"{CHAR_DIR}/SavedVariables/WowLab.lua"
    spells = [f for f in rep.findings if f.rule == "spell-not-in-spellbook"]
    assert [(f.line, f.detail) for f in spells] == [
        (2, "'Arcane Intellect' is not in the spellbook record"),
        (2, "'Polymorph' is not in the spellbook record"),
    ]
    [bars] = [c for c in rep.cannot_judge if c.rule == "not-on-action-bar"]
    assert bars.macro is None
    assert "2 macro slot(s) in action slots 1 to 180" in bars.reason
    assert "[verify]" in bars.reason
    assert rep.on_no_bar_at_least == 0


# ─── one constructed macro per rule ──────────────────────────────────────────


def test_length_rule_counts_utf8_bytes_constructed(flavor: Path) -> None:
    _put_macros(
        flavor,
        _macro(1, "Mac at limit", "/say " + "a" * 250),  # 255 bytes
        _macro(2, "Mac over", "/say " + "a" * 251),  # 256 bytes
        _macro(3, "Mac wide", "/say " + "é" * 126),  # 131 characters, 257 bytes
        _macro(4, "Mac lines", "/say " + "a" * 124, "/say " + "a" * 120),  # 129 + LF + 125
    )
    rep = report("--character", CHARACTER)
    assert _ids(rep, "length") == [_id(2), _id(3)]
    wide = next(m for m in rep.macros if m.id == _id(3))
    assert (wide.bytes, wide.characters) == (257, 131)
    lines = next(m for m in rep.macros if m.id == _id(4))
    assert lines.bytes == 255
    detail = next(f.detail for f in rep.findings if f.macro.id == _id(3))
    assert detail == "the body is 257 bytes (131 characters), over 255"


def test_spell_rule_reads_the_spellbook_record_constructed(flavor: Path) -> None:
    _put_record(flavor)
    _put_macros(
        flavor,
        _macro(1, "Mac known", "#showtooltip Shadow Bolt", "/cast !shadow bolt"),
        _macro(2, "Mac unknown", "/cast [mod:shift] Fireball; [@focus] Corruption"),
        _macro(3, "Mac sequence", "/castsequence reset=3 Corruption, Frostbolt"),
        _macro(4, "Mac rank", "/cast Shadow Bolt(Rank 3)"),
        _macro(5, "Mac other", "/use 13", "/use Fireball", "/cast [@player]"),
    )
    rep = report("--character", CHARACTER)
    spells = [f for f in rep.findings if f.rule == "spell-not-in-spellbook"]
    assert [(f.macro.id, f.line, f.detail) for f in spells] == [
        (_id(2), 6, "'Fireball' is not in the spellbook record"),
        (_id(3), 9, "'Frostbolt' is not in the spellbook record"),
    ]
    [rank] = [c for c in rep.cannot_judge if c.rule == "spell-not-in-spellbook"]
    assert rank.macro is not None and rank.macro.id == _id(4)
    assert rank.line == 12 and "explicit rank" in rank.reason and "[verify]" in rank.reason


def test_action_bar_rule_with_no_macro_slot_constructed(flavor: Path) -> None:
    data = SCHEMA_3.read_bytes()
    assert data.count(b'["type"] = "macro",') == 2
    _put_record(flavor, data.replace(b'["type"] = "macro",', b'["type"] = "item",'))
    _put_macros(flavor, _macro(1, "Mac A", "/say a"), _macro(2, "Mac B", "/say b"))
    rep = report("--character", CHARACTER)
    assert _ids(rep, "not-on-action-bar") == [_id(1), _id(2)]
    assert not [c for c in rep.cannot_judge if c.rule == "not-on-action-bar"]
    assert rep.on_no_bar_at_least is None


def test_action_bar_rule_bounds_what_it_cannot_judge_constructed(flavor: Path) -> None:
    _put_record(flavor)
    _put_macros(flavor, *(_macro(n, f"Mac {n}", f"/say {n}") for n in range(1, 6)))
    rep = report("--character", CHARACTER)
    assert _ids(rep, "not-on-action-bar") == []
    [bars] = [c for c in rep.cannot_judge if c.rule == "not-on-action-bar"]
    assert "at least 3 of the 5 macros are on no action bar" in bars.reason
    assert rep.on_no_bar_at_least == 3


def test_duplicate_rules_constructed(flavor: Path) -> None:
    _put_macros(
        flavor,
        _macro(3, "Mac A", "/say z"),
        _macro(4, "Mac C", "/say y"),
        _macro(5, "Mac D", "/say w"),
        folder=CHAR_DIR,
    )
    # The account file holds the first two; the character file the other three.
    _put_macros(
        flavor, _macro(1, "Mac A", "/say x"), _macro(2, "Mac B", "/say y"), folder=ACCOUNT_DIR
    )
    rep = report("--character", CHARACTER)
    names = [f for f in rep.findings if f.rule == "duplicate-name"]
    bodies = [f for f in rep.findings if f.rule == "duplicate-body"]
    assert [(f.macro.scope, f.macro.id) for f in names] == [("character", _id(3))]
    assert names[0].same_as is not None
    assert (names[0].same_as.scope, names[0].same_as.id) == ("account", _id(1))
    assert [(f.macro.scope, f.macro.id) for f in bodies] == [("character", _id(4))]
    assert bodies[0].same_as is not None and bodies[0].same_as.id == _id(2)


def test_unbalanced_bracket_rule_constructed(flavor: Path) -> None:
    _put_macros(
        flavor,
        _macro(1, "Mac open", "/cast [mod:shift Corruption"),
        _macro(2, "Mac close", "/cast mod:shift] Corruption"),
        _macro(3, "Mac fine", "/cast [mod:shift][@focus] Corruption; Fear"),
        _macro(4, "Mac chat", "/say [ is free text"),
    )
    rep = report("--character", CHARACTER)
    assert _ids(rep, "unbalanced-brackets") == [_id(1), _id(2)]


def test_showtooltip_rule_constructed(flavor: Path) -> None:
    _put_macros(
        flavor,
        _macro(1, "Mac first", "#showtooltip", "/cast Corruption"),
        _macro(2, "Mac second", "/cast Corruption", "#showtooltip Corruption"),
        _macro(3, "Mac upper", "/stopcasting", "#SHOWTOOLTIP"),
    )
    rep = report("--character", CHARACTER)
    assert _ids(rep, "showtooltip-not-first") == [_id(2), _id(3)]
    second = next(f for f in rep.findings if f.macro.id == _id(2))
    assert second.line == 7 and second.detail == "#showtooltip is on body line 2, not the first"


# ─── without a usable record ─────────────────────────────────────────────────


def test_schema_1_record_cannot_judge_the_record_rules_constructed(flavor: Path) -> None:
    folder = f"{ACCOUNT_DIR}/{SCHEMA_1_CHARACTER}"
    _put_macros(flavor, _macro(1, "Mac over", "/say " + "a" * 300), folder=folder)
    rep = report("--character", SCHEMA_1_CHARACTER)
    assert rep.record_schema == 1
    assert [(c.rule, c.reason) for c in rep.cannot_judge] == [
        ("spell-not-in-spellbook", "no spellbook record"),
        ("not-on-action-bar", "no spellbook record"),
    ]
    assert _ids(rep, "length") == [_id(1)]  # the other rules still run


def test_unreadable_record_is_cannot_judge_not_an_error_constructed(flavor: Path) -> None:
    _put_record(flavor, b'WowLabCharDB = { ["schema"] = 9, }\n')
    _put_macros(flavor, _macro(1, "Mac A", "#showtooltip", "/cast Corruption"))
    rep = report("--character", CHARACTER)
    assert rep.record_error is not None and rep.record_schema is None
    assert [c.rule for c in rep.cannot_judge] == ["spell-not-in-spellbook", "not-on-action-bar"]
    assert all(c.reason.endswith("was not read") for c in rep.cannot_judge)


def test_no_character_file_is_reported_constructed(flavor: Path) -> None:
    (flavor / CHAR_DIR / "macros-cache.txt").unlink()
    rep = report("--character", CHARACTER)
    assert [(f.scope, f.file) for f in rep.files][1] == ("character", None)
    assert (
        "Character macros: no macros-cache.txt"
        in ok("macros", "check", "--character", CHARACTER).stdout
    )


def test_unknown_character_is_a_usage_error_constructed(root: Path) -> None:
    result = run("macros", "check", "--character", "Nobody")
    assert result.exit_code == 2, result.stdout


def test_hostile_macro_name_is_escaped_in_text_constructed(flavor: Path) -> None:
    _put_macros(flavor, _macro(1, "Mac\x1b[2J", "/cast [x"))
    out = ok("macros", "check", "--character", CHARACTER).stdout
    assert "\x1b" not in out
    assert '"Mac\\x1b[2J"' in out
    rep = report("--character", CHARACTER)
    assert rep.findings[0].name == "Mac\x1b[2J"


# ─── the library ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line", "names"),
    [
        ("/cast Fear", ["Fear"]),
        ("  /CAST [mod:alt,@focus] Fear; [nomod] !Corruption", ["Fear", "Corruption"]),
        (
            "/castsequence reset=target/5 Immolate, Corruption, Fear",
            ["Immolate", "Corruption", "Fear"],
        ),
        ("/castrandom Fear, Shoot", ["Fear", "Shoot"]),
        ("#show Fear", ["Fear"]),
        ("#showtooltip", []),
        ("/cast 13", []),
        ("/cast item:6948", []),
        ("/use Fear", []),
        ("/cast [mod Fear", []),
        ("/say Fear", []),
    ],
)
def test_spell_names_constructed(line: str, names: list[str]) -> None:
    assert macrocheck.spell_names(line) == names
