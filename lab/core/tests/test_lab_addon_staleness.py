"""The lab-addon's staleness is visible (M16-06).

- `doctor` compares the installed `WowLab.toc`'s `## Interface:` with the
  one the flavor's discovered version gives, and names
  `wowlab addon install lab` when they differ;
- `char list` and `char show` flag a record saved by an older client than
  the installed one;
- `addon install` writes `## Version:` (the running wowlab's) and drops the
  template's comment from the installed copy.

The install is the captured tree (`fixtures/macos/`) copied into
`tmp_path`, as `test_cli.py` builds it; its lab-addon records were saved by
client 1.60.1.70009. Nothing here reads or writes a real install.
Constructed (labelled in the test ids): every `.build.info` version other
than the captured one (the client "updated" by a byte replacement of the
version cell), TOC templates and planted installed TOCs, and a link in
place of the installed TOC. Tests may name versions and interface numbers;
the library derives them (L6).
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import os
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

import wowlab_core
from wowlab_core import addoninstall, cli, install, labaddon, longpath
from wowlab_core.toc import parse_toc

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / "lab" / "addon" / "WowLab"
LAB = "Interface/AddOns/WowLab"
INTERFACE = 16001  # 1.60.1 by the patch-number rule; tests may name it (L6)
SAVED_BY = "1.60.1.70009"  # the client block of both captured records
FIRST = "1/Labchard-Labrealmg"
SECOND = "1/Labcharb-Labrealmf"
REINSTALL = "wowlab addon install lab"


def _set_version(root: Path, version: str) -> None:
    """Constructed: the client "updated" to `version` (the captured
    `.build.info` with its version cell replaced, every other byte kept)."""
    info = root / ".build.info"
    data = info.read_bytes()
    assert data.count(VERSION.encode()) == 1
    info.write_bytes(data.replace(VERSION.encode(), version.encode()))


def _doctor_json(*args: str) -> cli.DoctorReport:
    result = run("doctor", "--json", *args)
    assert result.exit_code in (0, 1), (result.stdout, result.stderr, result.exception)
    return cli.DoctorReport.model_validate_json(result.stdout)


def _lab_state(*args: str) -> addoninstall.LabAddonState:
    (f,) = _doctor_json(*args).flavors
    return f.lab_addon


def _doctor_text() -> str:
    result = run("doctor", "--yes")
    assert result.exit_code in (0, 1), (result.stdout, result.stderr, result.exception)
    return " ".join(result.stdout.split())


# ─── addon install: ## Version: and no template comment ──────────────────────


def test_installed_toc_adds_version_after_interface_and_drops_the_comment() -> None:
    template = (SOURCE / "WowLab.toc").read_bytes()
    assert b"\n# " in template  # the template comment the installed copy drops
    out = addoninstall.installed_toc(template, INTERFACE, "0.1.0")
    lines = out.split(b"\n")
    assert lines[0] == b"## Interface: 16001"
    assert lines[1] == b"## Version: 0.1.0"
    assert not [ln for ln in lines if ln.startswith(b"#") and not ln.startswith(b"##")]
    assert b"@WOWLAB_" not in out and b"\n\n\n" not in out
    doc = parse_toc(out)
    assert doc.get("Version") == "0.1.0"
    assert doc.interface is not None and doc.interface.versions == (INTERFACE,)
    # Everything else is the template's, in its order: every directive and file line.
    kept = [ln for ln in template.split(b"\n") if not ln.startswith(b"# ")]
    assert [ln for ln in lines if ln and not ln.startswith(b"## Version")] == [
        ln.replace(b"@WOWLAB_INTERFACE@", b"16001") for ln in kept if ln
    ]


def test_installed_toc_keeps_crlf_constructed() -> None:
    template = b"## Interface: @WOWLAB_INTERFACE@\r\n# a comment\r\n\r\n## Title: x\r\nCore.lua\r\n"
    assert addoninstall.installed_toc(template, 120105, "1.2.3") == (
        b"## Interface: 120105\r\n## Version: 1.2.3\r\n\r\n## Title: x\r\nCore.lua\r\n"
    )


def test_installed_toc_drops_only_a_doubled_blank_line_constructed() -> None:
    template = b"## Interface: @WOWLAB_INTERFACE@\n\n# one\n# two\n\nCore.lua\n"
    assert addoninstall.installed_toc(template, 1, "v") == (
        b"## Interface: 1\n## Version: v\n\nCore.lua\n"
    )


@pytest.mark.parametrize(
    "version",
    ["", " 0.1.0", "0.1.0\n## Interface: 1", "0.1-caf\u00e9", "x" * 65],
    ids=["empty", "leading-space", "newline", "non-ascii", "too-long"],
)
def test_installed_toc_refuses_a_version_a_toc_cannot_hold_constructed(version: str) -> None:
    template = (SOURCE / "WowLab.toc").read_bytes()
    with pytest.raises(addoninstall.AddonSourceError):
        addoninstall.installed_toc(template, INTERFACE, version)


def test_installed_toc_refuses_a_template_with_its_own_version_constructed() -> None:
    template = b"## Interface: @WOWLAB_INTERFACE@\n## Version: 9\nCore.lua\n"
    with pytest.raises(addoninstall.AddonSourceError, match="Version"):
        addoninstall.installed_toc(template, INTERFACE, "0.1.0")


def test_addon_install_writes_this_wowlab_version(flavor: Path) -> None:
    result = ok("addon", "install", "lab", "--yes")
    assert f"## Version: {wowlab_core.__version__}" in result.stdout
    toc = (flavor / LAB / "WowLab.toc").read_bytes()
    assert parse_toc(toc).get("Version") == wowlab_core.__version__
    assert b"Template (ADR-0026)" not in toc
    assert not [ln for ln in toc.split(b"\n") if ln.startswith(b"#") and not ln.startswith(b"##")]


def test_addon_install_json_says_the_version(flavor: Path) -> None:
    out = ok("addon", "install", "lab", "--dry-run", "--json").stdout
    report = cli.AddonInstallReport.model_validate_json(out)
    assert report.addon_version == wowlab_core.__version__
    assert not (flavor / LAB).exists()


def test_a_copy_with_the_template_comment_is_replaced_on_reinstall_constructed(
    flavor: Path,
) -> None:
    """An older wowlab's copy (the template comment kept, no ## Version:)
    differs from what install writes now, so a reinstall rewrites the TOC."""
    folder = flavor / LAB
    ok("addon", "install", "lab", "--yes")
    old = addoninstall.fill_toc((SOURCE / "WowLab.toc").read_bytes(), INTERFACE)
    (folder / "WowLab.toc").write_bytes(old)
    report = cli.AddonInstallReport.model_validate_json(
        ok("addon", "install", "lab", "--dry-run", "--json").stdout
    )
    assert [i.path for i in report.plan] == [f"{LAB}/WowLab.toc"]


# ─── doctor ──────────────────────────────────────────────────────────────────


def test_doctor_without_the_addon_names_it_only_in_the_next_steps(flavor: Path) -> None:
    state = _lab_state()
    assert state.status == "not_installed" and state.expected == INTERFACE
    # The flavor's lines say nothing about it; only the next steps (M16-37) do.
    text = _doctor_text()
    assert text.count("Lab-addon") == 1
    assert f"Next steps: Lab-addon: not installed. Close the game, then run `{REINSTALL}`." in text


def test_doctor_with_a_current_addon(flavor: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    state = _lab_state()
    assert state.status == "current"
    assert state.installed == (INTERFACE,) and state.expected == INTERFACE
    assert state.version == wowlab_core.__version__
    text = _doctor_text()
    assert "Lab-addon: installed, made for this client (## Interface: 16001)" in text
    assert REINSTALL not in text


def test_doctor_names_the_reinstall_after_a_client_update_constructed(
    root: Path, flavor: Path
) -> None:
    ok("addon", "install", "lab", "--yes")
    _set_version(root, "1.61.2.70001")
    state = _lab_state()
    assert state.status == "stale"
    assert state.installed == (INTERFACE,) and state.expected == 16102
    text = _doctor_text()
    assert (
        "Lab-addon: made for another client version (## Interface: 16001; this client is "
        "16102), so the game may list it as out of date and not load it (unless 'Load out "
        "of date AddOns' is ticked), and then no new records are saved. Close the game and run "
        f"`{REINSTALL}` to update it."
    ) in text
    # A stale addon is advice, not a problem: it does not make doctor fail.
    assert all("Lab-addon" not in p for p in _doctor_json().problems)
    # The advice works: after the reinstall doctor finds it current.
    ok("addon", "install", "lab", "--yes")
    assert _lab_state().status == "current"


def test_doctor_a_build_only_update_needs_no_reinstall_constructed(
    root: Path, flavor: Path
) -> None:
    ok("addon", "install", "lab", "--yes")
    _set_version(root, "1.60.1.70500")
    assert _lab_state().status == "current"
    assert REINSTALL not in _doctor_text()


def test_doctor_an_unfilled_toc_is_stale_constructed(flavor: Path) -> None:
    (flavor / LAB).mkdir(parents=True)
    (flavor / LAB / "WowLab.toc").write_bytes((SOURCE / "WowLab.toc").read_bytes())
    state = _lab_state()
    assert state.status == "stale" and state.installed == ()
    text = _doctor_text()
    assert "made for another client version" not in text
    assert (
        "Lab-addon: its WowLab.toc has no usable ## Interface: value, so the game may treat "
        "it as out of date and not load it (unless 'Load out of date AddOns' is ticked), and "
        f"then no new records are saved. Close the game and run `{REINSTALL}`."
    ) in text


@pytest.mark.parametrize("with_main", [True, False], ids=["beside-WowLab.toc", "alone"])
def test_doctor_a_suffixed_toc_makes_it_unknown_constructed(flavor: Path, with_main: bool) -> None:
    """The client may load a suffixed `WowLab_<x>.toc` instead of
    `WowLab.toc`, so doctor does not judge `WowLab.toc` alone."""
    if with_main:
        ok("addon", "install", "lab", "--yes")
    else:
        (flavor / LAB).mkdir(parents=True)
    (flavor / LAB / "WowLab_Mainline.toc").write_bytes(b"## Interface: 1\n")
    state = _lab_state()
    assert state.status == "unknown"
    assert state.installed == ((INTERFACE,) if with_main else ())
    assert state.detail == (
        f"{LAB}/ also holds WowLab_Mainline.toc, which the client may load instead of WowLab.toc"
    )
    assert "WowLab_Mainline.toc, which the client may load instead" in _doctor_text()


def test_doctor_a_toc_that_is_not_wowlabs_is_named_neutrally_constructed(
    flavor: Path,
) -> None:
    """Any other `*.toc` keeps the state unknown, but only a `WowLab_<x>.toc`
    is said to be one the client may load instead."""
    ok("addon", "install", "lab", "--yes")
    (flavor / LAB / "Other.toc").write_bytes(b"## Interface: 1\n")
    (flavor / LAB / "wowlab-Classic.TOC").write_bytes(b"## Interface: 1\n")
    state = _lab_state()
    assert state.status == "unknown"
    assert state.detail == (
        f"{LAB}/ also holds wowlab-Classic.TOC, which the client may load instead of "
        "WowLab.toc; and Other.toc, which is not wowlab's"
    )


def test_other_tocs_skip_wowlab_toc_in_any_case_constructed(tmp_path: Path) -> None:
    """On a case-insensitive file system a lone `wowlab.toc` is WowLab.toc,
    not another TOC (constructed: the listing is checked directly, as this
    file system may be case-sensitive)."""
    (tmp_path / "wowlab.toc").write_bytes(b"## Interface: 1\n")
    (tmp_path / "WOWLAB.TOC.bak").write_bytes(b"")
    assert addoninstall._other_tocs(tmp_path) == []
    (tmp_path / "WowLab_Mainline.toc").write_bytes(b"")
    assert addoninstall._other_tocs(tmp_path) == ["WowLab_Mainline.toc"]


def test_doctor_names_the_real_reason_it_could_not_look_constructed(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permission error on the way to the folder is said as one, not as a
    link (constructed: injected at `longpath.lstat`, as root ignores modes)."""
    ok("addon", "install", "lab", "--yes")
    real = longpath.lstat

    def lstat(path: str | os.PathLike[str]) -> os.stat_result:
        if Path(path).name == "AddOns":
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(longpath, "lstat", lstat)
    (chosen,) = install.discover(flavor.parent).flavors
    state = addoninstall.lab_addon_state(chosen)
    assert state.status == "unknown"
    assert (
        state.detail == f"wowlab could not look at the way to {LAB}/WowLab.toc: Permission denied"
    )


def test_doctor_unknown_when_the_version_gives_no_interface_constructed(
    root: Path, flavor: Path
) -> None:
    ok("addon", "install", "lab", "--yes")
    _set_version(root, "1.160.1.70001")
    state = _lab_state()
    assert state.status == "unknown" and state.expected is None
    assert state.installed == (INTERFACE,)
    assert state.detail and "nothing was changed" not in state.detail
    assert "wowlab cannot tell whether it fits this client" in _doctor_text()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_doctor_never_reads_through_a_linked_toc_constructed(tmp_path: Path, flavor: Path) -> None:
    outside = tmp_path / "outside.toc"
    outside.write_bytes(b"## Interface: 16001\n")
    (flavor / LAB).mkdir(parents=True)
    try:
        (flavor / LAB / "WowLab.toc").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not permitted here")
    state = _lab_state()
    assert state.status == "unknown" and state.installed == ()
    assert state.detail is not None and "regular file" in state.detail


def test_doctor_reads_and_writes_nothing_in_the_addon_folder(root: Path, flavor: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    folder = flavor / LAB
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}
    _doctor_json()
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()} == before


def test_doctor_redact_keeps_the_status_constructed(root: Path, flavor: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    _set_version(root, "1.61.2.70001")
    state = _lab_state("--redact")
    assert state.status == "stale"
    assert state.installed == (INTERFACE,) and state.expected == 16102


# ─── char list and char show ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("saved", "installed", "older"),
    [
        (SAVED_BY, SAVED_BY, False),
        (SAVED_BY, "1.60.1.70010", True),
        (SAVED_BY, "1.60.1.69913", False),
        # The whole version is compared, not the build number alone.
        (SAVED_BY, "1.60.2.100", True),
        ("1.60.2.100", SAVED_BY, False),
        (SAVED_BY, "2.0.0.1", True),
        (None, SAVED_BY, None),
        (SAVED_BY, None, None),
        (SAVED_BY, "1.60.1", None),
        (SAVED_BY, "1.60.x.1", None),
    ],
)
def test_older_client_compares_the_whole_version(
    saved: str | None, installed: str | None, older: bool | None
) -> None:
    assert labaddon.older_client(saved, installed) is older


def _list(*args: str) -> cli.CharListReport:
    return cli.CharListReport.model_validate_json(ok("char", "list", "--json", *args).stdout)


def _rows(report: cli.CharListReport) -> dict[str, labaddon.CharacterFile]:
    return {e.character: e for e in report.characters if e.no_lab_record is None}


def test_char_list_on_the_captured_install_flags_nothing(flavor: Path) -> None:
    """The captured records were saved by 1.60.1.70009, newer than the
    captured `.build.info`'s 1.60.1.69913: not older, so no flag."""
    report = _list()
    assert report.installed_version == VERSION
    rows = _rows(report)
    assert {r.client_build for r in rows.values()} == {SAVED_BY}
    assert {r.older_client for r in rows.values()} == {False}
    assert "older than the installed" not in ok("char", "list").stdout


@pytest.mark.parametrize("brief", [False, True], ids=["full", "brief"])
def test_char_list_flags_records_an_older_client_saved_constructed(
    root: Path, flavor: Path, brief: bool
) -> None:
    _set_version(root, "1.60.2.70100")
    report = _list(*(["--brief"] if brief else []))
    assert report.installed_version == "1.60.2.70100"
    rows = _rows(report)
    assert set(rows) >= {FIRST, SECOND}
    assert all(r.older_client is True for r in rows.values())
    text = ok("char", "list").stdout
    flagged = [ln for ln in text.splitlines() if "older than the installed client" in ln]
    assert len(flagged) == len(rows)
    for line in flagged:
        assert f"saved by client {SAVED_BY}" in line
        assert line.endswith("; older than the installed client 1.60.2.70100")


def test_char_list_without_an_installed_version_flags_nothing_constructed(
    root: Path, flavor: Path
) -> None:
    _set_version(root, "")
    report = _list()
    assert report.installed_version is None
    assert {r.older_client for r in _rows(report).values()} == {None}


def test_char_show_flags_a_record_an_older_client_saved_constructed(
    root: Path, flavor: Path
) -> None:
    _set_version(root, "1.60.1.70010")
    data = json.loads(ok("char", "show", "--character", FIRST, "--json").stdout)
    assert data["installed_version"] == "1.60.1.70010"
    assert data["older_client"] is True
    # The lab-addon is not installed in the captured tree: text and JSON agree.
    assert data["lab_addon"]["status"] == "not_installed"
    text = " ".join(ok("char", "show", "--character", FIRST).stdout.split())
    assert (
        "This record was saved by an older client than the installed 1.60.1.70010, and the "
        f"lab-addon is not installed. Close the game, run `{REINSTALL}`, then, with the "
        "lab-addon enabled for this character, log in on it, then log out or /reload, to "
        "save a fresh one."
    ) in text


@pytest.mark.parametrize(
    ("version", "status"),
    [("1.60.1.70010", "current"), ("1.61.2.70001", "stale")],
    ids=["build-only-update", "patch-update"],
)
def test_char_show_json_carries_the_addon_state_constructed(
    root: Path, flavor: Path, version: str, status: str
) -> None:
    """`char show --json` gives the same `lab_addon` state `doctor --json`
    gives for the flavor, which the text's advice rests on."""
    ok("addon", "install", "lab", "--yes")
    _set_version(root, version)
    report = cli.CharShowReport.model_validate_json(
        ok("char", "show", "--character", FIRST, "--json").stdout
    )
    assert report.lab_addon is not None and report.lab_addon.status == status
    assert report.lab_addon == _lab_state()


def test_char_show_advice_with_a_current_addon_constructed(root: Path, flavor: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    _set_version(root, "1.60.1.70010")  # a build-only update: the addon still fits
    text = " ".join(ok("char", "show", "--character", FIRST).stdout.split())
    assert (
        "This record was saved by an older client than the installed 1.60.1.70010: with the "
        "lab-addon enabled for this character, log in on it, then log out or /reload, to "
        "save a fresh one."
    ) in text
    assert REINSTALL not in text


def test_char_show_advice_with_a_stale_addon_constructed(root: Path, flavor: Path) -> None:
    ok("addon", "install", "lab", "--yes")
    _set_version(root, "1.61.2.70001")
    text = " ".join(ok("char", "show", "--character", FIRST).stdout.split())
    assert (
        "This record was saved by an older client than the installed 1.61.2.70001, and the "
        "installed lab-addon is out of date for this client, so the game may not load it "
        f"(`wowlab doctor` says why). Close the game, run `{REINSTALL}`, then, with the "
        "lab-addon enabled for this character, log in on it, then log out or /reload, to "
        "save a fresh one."
    ) in text


def test_char_show_on_the_captured_install_flags_nothing(flavor: Path) -> None:
    report = cli.CharShowReport.model_validate_json(
        ok("char", "show", "--character", SECOND, "--json").stdout
    )
    assert report.installed_version == VERSION and report.older_client is False
    assert "older client" not in ok("char", "show", "--character", SECOND).stdout
