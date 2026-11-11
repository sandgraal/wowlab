"""`doctor` ends with next steps (M16-37, docs/LAB_PLAN.md §18.1 item 5).

The "Next steps" block covers, for each flavor, whether the lab-addon is
installed and made for this client, how many characters have a lab-addon
record, and whether a combat log is under Logs/. Each item that needs
doing names the one command or in-game action that does it. With no
install, the one item is the install, and the problem and the how-to come
before it. The text says each item from the same fields `--json` carries,
and `--redact` keeps the block.

The install is the captured tree (`fixtures/macos/`) copied into
`tmp_path`, as `test_cli.py` builds it: no lab-addon installed, two
character records, one combat log. Each test takes one item away.
Constructed (labelled in the test ids): a removed record, a removed or
emptied Logs/, a client "updated" by a byte replacement of the version cell
in `.build.info`, a listing that fails, and hand-built steps for the
several-flavor layout. Nothing here reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from test_cli import (
    VERSION,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, combatlog, install, labaddon

REINSTALL = "wowlab addon install lab"
FOLDER = "_classic_beta_"  # test_cli.FLAVOR; tests may name it (L6)


def _report(*args: str) -> cli.DoctorReport:
    result = run("doctor", "--json", *args)
    assert result.exit_code in (0, 1), (result.stdout, result.stderr, result.exception)
    return cli.DoctorReport.model_validate_json(result.stdout)


def _steps(*args: str) -> dict[str, cli.NextStep]:
    return {s.item: s for s in _report(*args).next_steps}


def _flat(text: str) -> str:
    return " ".join(text.split())


def _text(*args: str) -> str:
    result = run("doctor", *args)
    assert result.exit_code in (0, 1), (result.stdout, result.stderr, result.exception)
    return result.stdout


def _block(text: str) -> str:
    """The "Next steps" block of a text report, up to the notes footer."""
    start = text.index("Next steps")
    end = text.index(" --notes shows", start)
    return _flat(text[start:end])


def _agrees(text: str, report: cli.DoctorReport) -> None:
    """Every item of `--json` is said in the text block, fix and all."""
    block = _block(text)
    for step in report.next_steps:
        said = cli._next_step_answer(step)
        if step.fix is not None:
            said += f" {step.fix}"
        assert _flat(said) in block, (said, block)


def _set_version(root: Path, version: str) -> None:
    """Constructed: the client "updated" to `version`."""
    info = root / ".build.info"
    data = info.read_bytes()
    assert data.count(VERSION.encode()) == 1
    info.write_bytes(data.replace(VERSION.encode(), version.encode()))


# ─── the captured tree: lab-addon missing, two records, one log ──────────────


def test_captured_tree_has_three_items_in_order(flavor: Path) -> None:
    report = _report()
    assert [(s.item, s.flavor_folder) for s in report.next_steps] == [
        ("lab_addon", FOLDER),
        ("characters", FOLDER),
        ("combat_log", FOLDER),
    ]
    steps = {s.item: s for s in report.next_steps}
    assert steps["lab_addon"].state == "missing"
    assert steps["lab_addon"].fix == f"Close the game, then run `{REINSTALL}`."
    assert (steps["characters"].state, steps["characters"].count) == ("ok", 2)
    assert steps["characters"].fix is None
    assert (steps["combat_log"].state, steps["combat_log"].count) == ("ok", 1)
    assert steps["combat_log"].fix is None


def test_text_block_says_what_json_says(flavor: Path) -> None:
    text = _text()
    _agrees(text, _report())
    block = _block(text)
    assert block.startswith("Next steps: Lab-addon: not installed.")
    assert "Characters recorded: 2." in block
    assert "Combat log: present (1 under Logs/)." in block


def test_block_comes_after_the_problems_and_before_the_footer(flavor: Path) -> None:
    text = _text()
    lines = text.splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith("Next steps"))
    assert any(ln.startswith("Snapshot store:") for ln in lines[:at])
    footer = next(i for i, ln in enumerate(lines) if "--notes shows" in ln)
    assert at < footer
    assert all(ln.startswith("  ") for ln in lines[at + 1 : footer])


def test_doctor_reads_no_record_to_count_them(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count is the listing's: no `WowLab.lua` is opened for it."""

    def refuse(*_a: object, **_k: object) -> object:
        raise AssertionError("doctor opened a lab-addon record")

    monkeypatch.setattr(labaddon, "read_char", refuse)
    monkeypatch.setattr(labaddon, "parse_char", refuse)
    assert _steps()["characters"].count == 2


# ─── each item missing ───────────────────────────────────────────────────────


def test_lab_addon_installed_is_ok_and_nothing_to_do_constructed(flavor: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    steps = _steps()
    assert steps["lab_addon"].state == "ok" and steps["lab_addon"].fix is None
    text = _text()
    assert _block(text).startswith(
        "Next steps: nothing to do. Lab-addon: installed, made for this client."
    )
    _agrees(text, _report())


def test_lab_addon_for_another_client_names_the_reinstall_constructed(
    root: Path, flavor: Path
) -> None:
    ok("addon", "install", "lab", "--yes")
    _set_version(root, "1.60.2.70500")
    step = _steps()["lab_addon"]
    assert step.state == "stale"
    assert step.fix == f"Close the game, then run `{REINSTALL}`."
    text = _text()
    assert "Lab-addon: not made for this client version. Close the game, then run" in _block(text)
    _agrees(text, _report())


def test_no_character_record_names_the_in_game_action_constructed(flavor: Path) -> None:
    for record in (flavor / "WTF" / "Account").glob("*/*/*/SavedVariables/WowLab.lua"):
        record.unlink()
    step = _steps()["characters"]
    assert (step.state, step.count) == ("missing", 0)
    assert step.fix is not None
    assert "log in on each character" in step.fix and "/reload" in step.fix
    text = _text()
    assert "Characters recorded: none. In game," in _block(text)
    _agrees(text, _report())


def test_account_wide_record_is_not_a_character_constructed(flavor: Path) -> None:
    """The account's own `WowLab.lua` stays; it records no character."""
    for record in (flavor / "WTF" / "Account").glob("*/*/*/SavedVariables/WowLab.lua"):
        record.unlink()
    assert list((flavor / "WTF" / "Account").glob("*/SavedVariables/WowLab.lua"))
    assert _steps()["characters"].count == 0


@pytest.mark.parametrize("how", ["removed", "emptied"], ids=lambda h: f"logs-{h}-constructed")
def test_no_combat_log_names_slash_combatlog(flavor: Path, how: str) -> None:
    logs = flavor / "Logs"
    if how == "removed":
        shutil.rmtree(logs)
    else:
        for f in logs.iterdir():
            f.unlink()
    step = _steps()["combat_log"]
    assert (step.state, step.count) == ("missing", 0)
    assert step.fix is not None and "/combatlog" in step.fix
    text = _text()
    assert "Combat log: none under Logs/. In game, type /combatlog" in _block(text)
    _agrees(text, _report())


def test_every_item_missing_lists_each_fix_constructed(flavor: Path) -> None:
    for record in (flavor / "WTF" / "Account").glob("*/*/*/SavedVariables/WowLab.lua"):
        record.unlink()
    shutil.rmtree(flavor / "Logs")
    report = _report()
    assert [s.state for s in report.next_steps] == ["missing", "missing", "missing"]
    assert all(s.fix for s in report.next_steps)
    text = _text()
    assert _block(text).startswith("Next steps: Lab-addon: not installed.")
    _agrees(text, report)


def test_unreadable_logs_is_unknown_with_why_constructed(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_directory: object) -> list[Path]:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(combatlog, "find_logs", fail)
    step = _steps()["combat_log"]
    assert (step.state, step.fix) == ("unknown", None)
    assert step.detail == "wowlab could not look in Logs/: Permission denied"
    _agrees(_text(), _report())


def test_unseen_character_folders_are_unknown_not_none_constructed(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unseen = labaddon.AllCharacters(
        characters=[],
        not_looked_at=[labaddon.NotLookedAt(path="WTF/Account", reason="a link")],
    )
    monkeypatch.setattr(labaddon, "survey", lambda *_a, **_k: unseen)
    step = _steps()["characters"]
    assert (step.state, step.count, step.fix) == ("unknown", 0, None)
    assert step.detail is not None and "1 place" in step.detail
    _agrees(_text(), _report())


# ─── no install: the problem and the how-to first ────────────────────────────


def test_no_install_has_one_step_after_the_problem_and_how_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nowhere = tmp_path / "nothing-here"
    nowhere.mkdir()
    monkeypatch.setenv(install.ENV_ROOT, str(nowhere))
    report = _report()
    assert report.install_root is None
    assert [(s.item, s.state, s.flavor_folder) for s in report.next_steps] == [
        ("install", "missing", None)
    ]
    text = _text()
    lines = text.splitlines()
    assert lines[0].startswith("Install: ")  # the problem comes first
    how_to = next(i for i, ln in enumerate(lines) if "wowlab doctor --root" in ln)
    steps_at = next(i for i, ln in enumerate(lines) if ln.startswith("Next steps"))
    assert how_to < steps_at
    # The first run's intro, when there is one, follows the whole report.
    intro = [i for i, ln in enumerate(lines) if ln.startswith("First run")]
    assert all(i > steps_at for i in intro)
    _agrees(text, report)


def test_no_install_fix_names_the_root_option_and_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(install.ENV_ROOT, str(tmp_path / "missing"))
    (step,) = _report().next_steps
    assert step.fix is not None
    assert '`wowlab doctor --root "<that folder>"`' in step.fix
    assert install.ENV_ROOT in step.fix


def test_lost_build_info_fix_names_scan_and_repair_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (root / ".build.info").unlink()
    report = _report()
    assert report.build_info_missing
    (step,) = report.next_steps
    assert step.item == "install" and step.fix is not None
    assert "Scan and Repair" in step.fix and "--root" in step.fix
    _agrees(_text(), report)


# ─── --redact keeps the block ────────────────────────────────────────────────


def test_redact_keeps_the_items_and_states(flavor: Path) -> None:
    plain = _report()
    redacted = _report("--redact")
    assert [(s.item, s.state, s.count) for s in redacted.next_steps] == [
        (s.item, s.state, s.count) for s in plain.next_steps
    ]
    # A command that names nobody passes through the redaction unchanged.
    assert redacted.next_steps[0].fix == plain.next_steps[0].fix
    _agrees(_text("--redact"), redacted)


def test_redact_replaces_a_name_in_a_fix_constructed(flavor: Path) -> None:
    """A fix is text: a name given to --redact-name is replaced in it too."""
    redacted = _report("--redact-name", FOLDER)
    for step in redacted.next_steps:
        assert step.flavor_folder != FOLDER
        assert FOLDER not in (step.fix or "")


# ─── several flavors ─────────────────────────────────────────────────────────


def test_several_flavors_name_the_flavor_in_the_command() -> None:
    state = cli.addoninstall.LabAddonState(status="not_installed")
    one = cli._lab_addon_step("_a_", state, several=False)
    two = cli._lab_addon_step("_a_", state, several=True)
    assert one.fix == f"Close the game, then run `{REINSTALL}`."
    assert two.fix == f"Close the game, then run `{REINSTALL} --flavor _a_`."


def test_several_flavors_group_the_items_constructed(capsys: pytest.CaptureFixture[str]) -> None:
    steps = [
        cli.NextStep(item="lab_addon", flavor_folder="_a_", state="ok"),
        cli.NextStep(item="combat_log", flavor_folder="_a_", state="ok", count=2),
        cli.NextStep(item="lab_addon", flavor_folder="_b_", state="ok"),
    ]
    cli._print_next_steps(steps)
    assert capsys.readouterr().out.splitlines() == [
        "Next steps: nothing to do.",
        "  _a_:",
        "    Lab-addon: installed, made for this client.",
        "    Combat log: present (2 under Logs/).",
        "  _b_:",
        "    Lab-addon: installed, made for this client.",
    ]
