"""`wowlab char macros` and `macroplace` (M14-07, docs/LAB_PLAN.md §16.2).

The install is the captured tree (`fixtures/macos/`, build 69913) copied into
`tmp_path` with its flavor folder named as its provenance row says
(`_classic_beta_`), as `test_cli_macros_check.py` does. Its real
`macros-cache.txt` files (an empty account file and one character macro)
and its account `bindings-cache.wtf` (three `bind` lines) give the report on
the fixture install. The action bars, bindings and spellbook come from the
real schema-3 record (`fixtures/macos-70425/`, M13-05), copied into the
character folder that holds the captured macro: a `cross-capture`
combination (two builds, two characters), said in the test ids. That record
has two macro slots with no subtype: slot 64 holds 149 and slot 65 holds 140.

Inputs labelled `constructed` are boundary cases (L8): a `macros-cache.txt`
written by the test with made-up names and bodies (no player's macro name
or body is in this file), a record with a macro slot's id changed or its
macro slots turned into item slots, and a `bind` line added to the account
`bindings-cache.wtf`. Nothing here reads or writes a real install:
`WOWLAB_WOW_ROOT` points at the copy, and the user data and user config
directories are redirected into `tmp_path`.

No placement rule exists yet that a capture has confirmed, so no output may
carry `confirmed` (`docs/handoffs/M14-macro-place.md`).
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, keymap, labaddon, macroplace

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
RECORD = f"{CHAR_DIR}/SavedVariables/WowLab.lua"
SCHEMA_1_CHARACTER = "1/Labchard-Labrealmg"  # a schema-1 WowLab.lua (M11-03), no macros
MAY_BE = (
    "it may be an item, a pet, mount or toy spell, or a spell learned after the record was saved"
)
# "confirmed" as a word of its own, not inside "unconfirmed".
CONFIRMED = re.compile(r"(?<![A-Za-z])confirmed")

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "userdata" / "wowlab"
    config = tmp_path / "userconfig" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config)
    return config


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


def report(*args: str) -> cli.CharMacrosReport:
    out = ok("char", "macros", *args, "--json").stdout
    return cli.CharMacrosReport.model_validate_json(out)


def _state(path: Path) -> dict[str, tuple[bytes | None, int]]:
    out: dict[str, tuple[bytes | None, int]] = {}
    for p in sorted(path.rglob("*")):
        st = p.lstat()
        out[p.relative_to(path).as_posix()] = (
            p.read_bytes() if p.is_file() else None,
            st.st_mtime_ns,
        )
    return out


def _put_record(flavor: Path, data: bytes | None = None) -> Path:
    """The real schema-3 record (or `data`) as CHARACTER's WowLab.lua."""
    target = flavor / RECORD
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(SCHEMA_3.read_bytes() if data is None else data)
    return target


def _macro(n: int, name: str, *body: str) -> bytes:
    """One constructed record in the captured grammar (CRLF, VER 3, hex id)."""
    lines = [f'VER 3 {0x0100000000000000 + n:016X} "{name}" "134400"', *body, "END"]
    return "".join(f"{line}\r\n" for line in lines).encode("utf-8")


def _put_macros(flavor: Path, *records: bytes, folder: str = CHAR_DIR) -> None:
    (flavor / folder / "macros-cache.txt").write_bytes(b"".join(records))


def _twenty_nine(flavor: Path) -> None:
    """Constructed: 29 character macros, as many as the 70425 capture's
    file had, so the candidate numbering reads slots 64 (149) and 65 (140)
    as the 29th and the 20th."""
    _put_macros(flavor, *(_macro(n, f"Lab {n:02d}", f"/say lab test {n}") for n in range(1, 30)))


def _by_position(rep: cli.CharMacrosReport) -> dict[tuple[str, int], cli.CharMacro]:
    return {(m.scope, m.position): m for m in rep.macros}


# ─── the fixture install ─────────────────────────────────────────────────────


def test_char_macros_on_the_fixture_install_with_no_record_places_nothing(root: Path) -> None:
    before = _state(root)
    rep = report("--character", CHARACTER)
    assert _state(root) == before  # reads only (L1)
    assert (rep.flavor_folder, rep.account, rep.character, rep.chosen_by) == (
        FLAVOR,
        ACCOUNT,
        f"1/{CHARACTER}",
        "--character",
    )
    assert rep.record_file is None and rep.record_schema is None
    assert rep.record_error is not None and "no WowLab.lua" in rep.record_error
    assert [(f.scope, f.path, f.macros, f.unknown_lines) for f in rep.files] == [
        ("account", f"{ACCOUNT_DIR}/macros-cache.txt", 0, 0),
        ("character", f"{CHAR_DIR}/macros-cache.txt", 1, 0),
    ]
    assert [(b.scope, b.path, b.bind_lines) for b in rep.bindings_files] == [
        ("account", f"{ACCOUNT_DIR}/bindings-cache.wtf", 3),
        ("character", None, 0),
    ]
    [m] = rep.macros
    assert (m.scope, m.position, m.line, m.id, m.name, m.icon, m.complete) == (
        "character",
        1,
        1,
        "0100000000000001",
        "PolyArc",
        "134400",
        True,
    )
    assert m.body_lines == ["/castsequence Arcane Intellect, Polymorph"]
    assert m.bytes == m.characters == 41
    assert m.placement.status == "none"
    assert (m.placement.index, m.placement.slots, m.placement.keys) == (121, [], [])
    assert m.placement.reason.startswith("cannot place: no action-bar record: no lab-addon record")
    assert m.findings == [] and m.cannot_judge == []
    assert [c.rule for c in rep.cannot_judge] == ["spell-not-in-spellbook", "not-on-action-bar"]
    assert cli.CharMacrosReport.model_validate_json(rep.model_dump_json()) == rep
    lines = ok("char", "macros", "--character", CHARACTER).stdout.splitlines()
    assert lines[1].startswith("Record: none (no WowLab.lua in 1/Labcharb-Labrealmd.")
    assert "Macros: 1 (0 with a candidate slot, 1 with none; 0 findings)" in lines
    assert (
        "    placement: none: cannot place: no action-bar record: no lab-addon record: no "
        "WowLab.lua in 1/Labcharb-Labrealmd."
    ) in "\n".join(lines)
    assert _state(root) == before


def test_char_macros_json_1_macro_none_2_findings_cross_capture(flavor: Path, root: Path) -> None:
    _put_record(flavor)
    before = _state(root)
    rep = report("--character", CHARACTER)
    assert _state(root) == before  # reads only (L1)
    assert (rep.record_file, rep.record_schema, rep.record_error) == (RECORD, 3, None)
    assert rep.record_mtime_ns == (flavor / RECORD).stat().st_mtime_ns
    [m] = rep.macros
    assert m.placement.status == "none"
    assert m.placement.index == 121  # character macro 1 in the candidate numbering
    assert m.placement.reason == (
        "no macro slot with no subtype on the action bars (slots 1 to 180) holds index 121, "
        "which the candidate numbering [verify] gives character macro 1; under another "
        "numbering it may be on one"
    )
    assert [(f.rule, f.line, f.detail) for f in m.findings] == [
        (
            "spell-not-in-spellbook",
            2,
            f"'Arcane Intellect' is not in the spellbook record ({MAY_BE})",
        ),
        ("spell-not-in-spellbook", 2, f"'Polymorph' is not in the spellbook record ({MAY_BE})"),
    ]
    [bars] = rep.cannot_judge
    assert bars.rule == "not-on-action-bar" and bars.macro is None
    assert rep.notes[0] == macroplace.CANDIDATE_RULE
    assert cli.CharMacrosReport.model_validate_json(rep.model_dump_json()) == rep


def test_char_macros_text_on_the_fixture_install_cross_capture(flavor: Path) -> None:
    _put_record(flavor)
    lines = ok("char", "macros", "--character", CHARACTER).stdout.splitlines()
    assert lines[0] == f"Character: 1/{CHARACTER} in account {ACCOUNT}"
    assert lines[1].startswith(f"Record: {RECORD} (schema 3), written ")
    assert lines[2].startswith(f"Account macros: 0 macros in {ACCOUNT_DIR}/macros-cache.txt, ")
    assert lines[3].startswith(f"Character macros: 1 macro in {CHAR_DIR}/macros-cache.txt, ")
    assert lines[4].startswith(f"Account bindings: {ACCOUNT_DIR}/bindings-cache.wtf, written ")
    assert lines[5] == "Character bindings: no bindings-cache.wtf"
    assert lines[6] == "Macros: 1 (0 with a candidate slot, 1 with none; 2 findings)"
    assert lines[7] == (
        '  character macro 1 "PolyArc" (id 0100000000000001, icon 134400, line 1, 41 characters)'
    )
    assert lines[8] == "      /castsequence Arcane Intellect, Polymorph"
    assert lines[9].startswith("    placement: none: no macro slot with no subtype")
    assert lines[10] == (
        "    finding spell-not-in-spellbook (line 2): 'Arcane Intellect' is not in the "
        f"spellbook record ({MAY_BE})"
    )
    assert "Cannot judge for any macro: 1" in lines
    assert macroplace.CANDIDATE_RULE in lines


def test_char_macros_without_character_takes_the_latest_record(flavor: Path) -> None:
    record = _put_record(flavor)
    newest = max(p.stat().st_mtime_ns for p in flavor.rglob("WowLab.lua"))
    os.utime(record, ns=(newest + 10**9, newest + 10**9))
    rep = report()
    assert (rep.character, rep.chosen_by, rep.record_schema) == (f"1/{CHARACTER}", "latest", 3)
    assert [m.name for m in rep.macros] == ["PolyArc"]
    assert "(written last; choose another with --character)" in ok("char", "macros").stdout


def test_a_schema_1_record_places_nothing(root: Path) -> None:
    _put_macros(
        root / FLAVOR,
        _macro(1, "Lab one", "/say lab one"),
        folder=f"{ACCOUNT_DIR}/1/Labchard-Labrealmg",
    )
    rep = report("--character", SCHEMA_1_CHARACTER)
    assert (rep.record_schema, rep.record_error) == (1, None)
    [m] = [m for m in rep.macros if m.scope == "character"]
    assert m.placement.status == "none"
    assert m.placement.reason == (
        f"{keymap.CANNOT_PLACE}: no action-bar record (schema 1 has none; {keymap.GET_RECORD})"
    )


# ─── the placement states the code can reach ─────────────────────────────────


def test_unconfirmed_with_one_candidate_slot_and_a_key_constructed(flavor: Path) -> None:
    _put_record(flavor)
    _twenty_nine(flavor)
    bindings = flavor / ACCOUNT_DIR / "bindings-cache.wtf"
    bindings.write_bytes(bindings.read_bytes() + b"bind CTRL-F7 MULTIACTIONBAR1BUTTON4\r\n")
    rep = report("--character", CHARACTER)
    by = _by_position(rep)
    placed = {p: m.placement for p, m in by.items() if m.placement.status != "none"}
    assert sorted(placed) == [("character", 20), ("character", 29)]
    twenty_nine = placed[("character", 29)]
    assert (twenty_nine.status, twenty_nine.index) == ("unconfirmed", 149)
    assert [(s.slot, s.page_note, s.unfollowed_page) for s in twenty_nine.slots] == [
        (64, "bottom left bar button 4, page 6", False)
    ]
    assert [(k.key, k.command, k.slot, k.sources) for k in twenty_nine.keys] == [
        ("CTRL-F7", "MULTIACTIONBAR1BUTTON4", 64, ["account file"])
    ]
    assert twenty_nine.reason == (
        "character macro 29 in its file's order is macro index 149 in the candidate numbering "
        "[verify], and slot 64 holds a macro with index 149 and no subtype"
    )
    twenty = placed[("character", 20)]
    assert (twenty.index, [s.slot for s in twenty.slots], twenty.keys) == (140, [65], [])
    assert by[("character", 1)].placement.status == "none"
    text = ok("char", "macros", "--character", CHARACTER).stdout
    assert "Macros: 29 (2 with a candidate slot, 27 with none; 0 findings)" in text
    assert "    may sit at slot 64 (unconfirmed): bottom left bar button 4, page 6\n" in text
    assert (
        "      key CTRL-F7 (MULTIACTIONBAR1BUTTON4; account file only: disagrees with the record)\n"
    ) in text
    assert "    may sit at slot 65 (unconfirmed): bottom left bar button 5, page 6\n" in text
    assert "      no key reaches it on the page its bar shows by default\n" in text


def test_unconfirmed_with_two_candidate_slots_constructed(flavor: Path) -> None:
    data = SCHEMA_3.read_bytes()
    slot_65 = b'["id"] = 140,\r\n["type"] = "macro",'
    assert data.count(slot_65) == 1
    _put_record(flavor, data.replace(slot_65, b'["id"] = 149,\r\n["type"] = "macro",'))
    _twenty_nine(flavor)
    rep = report("--character", CHARACTER)
    by = _by_position(rep)
    p = by[("character", 29)].placement
    assert (p.status, p.index, [s.slot for s in p.slots]) == ("unconfirmed", 149, [64, 65])
    assert p.reason.endswith("slots 64, 65 hold a macro with index 149 and no subtype")
    assert by[("character", 20)].placement.status == "none"
    assert sum(1 for m in rep.macros if m.placement.status != "none") == 1
    text = ok("char", "macros", "--character", CHARACTER).stdout
    assert "may sit at slot 64 (unconfirmed)" in text
    assert "may sit at slot 65 (unconfirmed)" in text


def test_none_on_no_bar_with_the_checkers_finding_constructed(flavor: Path) -> None:
    data = SCHEMA_3.read_bytes()
    assert data.count(b'["type"] = "macro",') == 2
    _put_record(flavor, data.replace(b'["type"] = "macro",', b'["type"] = "item",'))
    _put_macros(flavor, _macro(1, "Lab A", "/say lab a"), _macro(2, "Lab B", "/say lab b"))
    rep = report("--character", CHARACTER)
    for m in rep.macros:
        assert m.placement.status == "none"
        assert m.placement.reason == "the action-bar record has no macro slot (slots 1 to 180)"
        assert [(f.rule, f.detail) for f in m.findings] == [
            (
                "not-on-action-bar",
                f"on none of 1/{CHARACTER}'s action bars (slots 1 to 180): the record has no "
                "macro slot there",
            )
        ]
    assert not [c for c in rep.cannot_judge if c.rule == "not-on-action-bar"]


def test_findings_are_the_checkers_own_joined_by_macro_constructed(flavor: Path) -> None:
    _put_record(flavor)
    _put_macros(
        flavor,
        _macro(1, "Lab clean", "/say lab clean"),
        _macro(2, "Lab broken", "/cast [mod:shift Fireball", "#showtooltip"),
        _macro(3, "Lab broken", "/say lab other"),
        _macro(4, "Lab rank", "/cast Shadow Bolt(Rank 3)"),
    )
    rep = report("--character", CHARACTER)
    checked = cli.MacrosCheckReport.model_validate_json(
        ok("macros", "check", "--character", CHARACTER, "--json").stdout
    )
    by = _by_position(rep)
    assert by[("character", 1)].findings == []
    assert [f.rule for f in by[("character", 2)].findings] == [
        "unbalanced-brackets",
        "showtooltip-not-first",
    ]
    assert [f.rule for f in by[("character", 3)].findings] == ["duplicate-name"]
    assert [c.rule for c in by[("character", 4)].cannot_judge] == ["spell-not-in-spellbook"]
    # Every finding and every per-macro "cannot judge" is the checker's own,
    # word for word, and none is lost or added in the join.
    assert sorted(
        (f for m in rep.macros for f in m.findings), key=lambda f: f.model_dump_json()
    ) == sorted(checked.findings, key=lambda f: f.model_dump_json())
    assert [c for m in rep.macros for c in m.cannot_judge] + rep.cannot_judge == sorted(
        checked.cannot_judge, key=lambda c: c.macro is None
    )
    text = ok("char", "macros", "--character", CHARACTER).stdout
    assert (
        "    finding unbalanced-brackets (line 5): body line 1: its [ and ] do not pair up (a "
        "chat command with a localized name is checked as a command)\n"
    ) in text
    assert "Macros: 4 (0 with a candidate slot, 4 with none; 3 findings)" in text


def test_an_account_macro_is_placed_by_its_own_numbering_constructed(flavor: Path) -> None:
    data = SCHEMA_3.read_bytes()
    slot_65 = b'["id"] = 140,\r\n["type"] = "macro",'
    _put_record(flavor, data.replace(slot_65, b'["id"] = 2,\r\n["type"] = "macro",'))
    _put_macros(
        flavor,
        _macro(1, "Lab acct 1", "/say lab acct 1"),
        _macro(2, "Lab acct 2", "/say lab acct 2"),
        folder=ACCOUNT_DIR,
    )
    _put_macros(
        flavor,
        _macro(1, "Lab char 1", "/say lab char 1"),
        _macro(2, "Lab char 2", "/say lab char 2"),
    )
    rep = report("--character", CHARACTER)
    by = _by_position(rep)
    assert [(p, m.placement.index, m.placement.status) for p, m in by.items()] == [
        (("account", 1), 1, "none"),
        (("account", 2), 2, "unconfirmed"),
        (("character", 1), 121, "none"),
        (("character", 2), 122, "none"),
    ]
    assert [s.slot for s in by[("account", 2)].placement.slots] == [65]


# ─── no output carries `confirmed` ───────────────────────────────────────────


def _setup_fixture(flavor: Path) -> None:
    """The fixture install as captured: no record in the macro's character folder."""


def _setup_record(flavor: Path) -> None:
    _put_record(flavor)


def _setup_one(flavor: Path) -> None:
    _put_record(flavor)
    _twenty_nine(flavor)


def _setup_two(flavor: Path) -> None:
    data = SCHEMA_3.read_bytes()
    _put_record(
        flavor,
        data.replace(
            b'["id"] = 140,\r\n["type"] = "macro",', b'["id"] = 149,\r\n["type"] = "macro",'
        ),
    )
    _twenty_nine(flavor)


def _setup_no_bar(flavor: Path) -> None:
    data = SCHEMA_3.read_bytes()
    _put_record(flavor, data.replace(b'["type"] = "macro",', b'["type"] = "item",'))
    _put_macros(flavor, _macro(1, "Lab A", "/say lab a"))


def _setup_findings(flavor: Path) -> None:
    _put_record(flavor)
    _put_macros(flavor, _macro(1, "Lab broken", "/cast [mod:shift Fireball", "#showtooltip"))


SETUPS = {
    "fixture-install": _setup_fixture,
    "cross-capture": _setup_record,
    "constructed-unconfirmed-one-slot": _setup_one,
    "constructed-unconfirmed-two-slots": _setup_two,
    "constructed-none-on-no-bar": _setup_no_bar,
    "constructed-findings": _setup_findings,
}


@pytest.mark.parametrize("setup", list(SETUPS), ids=list(SETUPS))
def test_no_output_carries_confirmed(flavor: Path, setup: str) -> None:
    SETUPS[setup](flavor)
    for args in (("--character", CHARACTER), ("--character", SCHEMA_1_CHARACTER), ()):
        out = ok("char", "macros", *args, "--json").stdout
        rep = cli.CharMacrosReport.model_validate_json(out)
        assert {m.placement.status for m in rep.macros} <= {"unconfirmed", "none"}
        assert CONFIRMED.search(out) is None, setup
        text = ok("char", "macros", *args).stdout
        assert CONFIRMED.search(text) is None, setup


# ─── macroplace ──────────────────────────────────────────────────────────────


def _bars() -> labaddon.ActionBars:
    record = labaddon.read_char(SCHEMA_3)
    assert isinstance(record, labaddon.CharDBV3)
    assert isinstance(record.actionbars, labaddon.ActionBars)
    return record.actionbars


def test_candidate_index_numbers_account_then_character_macros() -> None:
    assert macroplace.candidate_index("account", 1) == 1
    assert macroplace.candidate_index("account", 120) == 120
    assert macroplace.candidate_index("account", 121) is None
    assert macroplace.candidate_index("character", 1) == 121
    assert macroplace.candidate_index("character", 29) == 149
    with pytest.raises(ValueError):
        macroplace.candidate_index("character", 0)


def test_place_on_the_real_record_gives_two_unconfirmed() -> None:
    order: list[tuple[macroplace.MacroScope, int]] = [("character", n) for n in range(1, 30)]
    placed = macroplace.place(order, _bars(), [])
    assert [(i + 1, p.slots[0].slot) for i, p in enumerate(placed) if p.slots] == [
        (20, 65),
        (29, 64),
    ]
    assert {p.status for p in placed} == {"unconfirmed", "none"}


def test_place_past_the_account_indices_constructed() -> None:
    [p] = macroplace.place([("account", 121)], _bars(), [])
    assert (p.status, p.index) == ("none", None)
    assert p.reason.startswith("account macro 121 is past the 120 indices")


def test_place_names_a_subtyped_macro_slot_constructed() -> None:
    bars = _bars()
    slots = [s.model_copy(update={"sub_type": "spell"}) if s.slot == 65 else s for s in bars.slots]
    [p] = macroplace.place([("character", 20)], bars.model_copy(update={"slots": slots}), [])
    assert p.status == "none"
    assert "holds index 140" in p.reason
    assert p.reason.endswith(
        "65: macro slot with a subtype, whose id is the spell or item the macro shows "
        "[verify], may hold any macro"
    )


def test_place_says_when_the_bars_are_incomplete_constructed() -> None:
    bars = _bars()
    slots = [s.model_copy(update={"type": None}) if s.slot == 1 else s for s in bars.slots]
    [p] = macroplace.place([("character", 1)], bars.model_copy(update={"slots": slots}), [])
    assert p.status == "none"
    assert p.reason.endswith(
        "the action-bar record is incomplete (1 slot(s) have no type), so a macro slot may "
        "be one of those dropped"
    )


def test_place_with_an_absent_section_constructed() -> None:
    absent = labaddon.AbsentProbedSection(absent="in combat")
    [p] = macroplace.place([("character", 1)], absent, [])
    assert p.status == "none"
    assert p.reason == f"{keymap.CANNOT_PLACE}: the action-bar section is absent: in combat"
