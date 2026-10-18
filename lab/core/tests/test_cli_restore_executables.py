"""`wowlab snap restore` and the addon executables a snapshot holds (M13-38).

An executable under `Interface/AddOns/` has the file-map row
`addon-executable` (Edit `no`, M13-28), and the write gate never writes,
restores or deletes one. A whole restore skips one that changed or went
missing, lists it in `RestoreReport.skipped` by its row, and restores the
rest by name; `--paths` naming one is refused with the gate's reason and a
note on what the owner can do. An executable the gate would refuse for
another reason too (a reserved name), or one outside `AddOns/`, still
refuses the whole restore, as the gate does.

The install is the captured tree as `test_cli.py` builds it (copied into
`tmp_path`, the user data directory redirected, the process table a fake).
Inputs labelled `constructed` in the test names (L8): an addon's
`helper.sh`, `gone.dll`, `aux.sh`, `.DS_Store` and `._helper.sh`, a
`tool.sh` under `WTF/`, which the capture does not have, a symbolic link to a
file outside the install or a folder where `helper.sh` was, a snapshot
manifest forged to spell `helper.sh` as `HELPER.sh` (re-fingerprinted so the
store accepts it), and an edited copy of the captured `.toc` standing for a
later session. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_cli import (
    _json,
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard
from wowlab_core.snapshot import Manifest, SnapshotStore, manifest_bytes, tree_fingerprint

ADDON = "Interface/AddOns/DBM-Brawlers"
TOC = f"{ADDON}/DBM-Brawlers.toc"
HELPER = f"{ADDON}/helper.sh"
GONE = f"{ADDON}/gone.dll"
HELPER_BYTES = b"#!/bin/sh\necho constructed\n"
HELPER_LATER = b"#!/bin/sh\necho constructed later\n"
GONE_BYTES = b"MZconstructed dll"
NOTE = (
    "wowlab never writes, restores or deletes an executable, not even with --paths: "
    "name the other files with --paths, or reinstall the addon to get its executable back."
)


def _snapshot() -> Manifest:
    made = ok("snap", "create", "-m", "constructed", "--json")
    return Manifest.model_validate_json(made.stdout)


def _later(flavor: Path) -> bytes:
    """The `.toc` edited since the snapshot; returns its bytes at the snapshot."""
    toc = flavor / TOC
    saved = toc.read_bytes()
    toc.write_bytes(saved + b"## X-Constructed: 1\n")
    return saved


def _executables_snapshot(flavor: Path) -> tuple[Manifest, bytes]:
    """`helper.sh` and `gone.dll` in the snapshot; since then `helper.sh`
    changed, `gone.dll` was removed, and the `.toc` was edited."""
    (flavor / HELPER).write_bytes(HELPER_BYTES)
    (flavor / GONE).write_bytes(GONE_BYTES)
    a = _snapshot()
    held = {e.path.split("/", 1)[1] for e in a.entries}
    assert {HELPER, GONE, TOC} <= held
    (flavor / HELPER).write_bytes(HELPER_LATER)
    (flavor / GONE).unlink()
    return a, _later(flavor)


def test_whole_restore_skips_a_changed_and_a_missing_executable_constructed(
    root: Path, flavor: Path
) -> None:
    a, saved_toc = _executables_snapshot(flavor)

    plan = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert [i.path for i in plan.plan] == [TOC]
    assert [(s.path, s.entry_id) for s in plan.skipped] == [
        (GONE, "addon-executable"),
        (HELPER, "addon-executable"),
    ]
    assert not plan.applied and plan.dry_run

    text = _plain(ok("snap", "restore", a.id, "--dry-run").stdout)
    lines = text.splitlines()
    (header,) = [ln for ln in lines if ln.startswith("  Skipped ")]
    assert header == (
        "  Skipped 2 executable(s) whose content changed or that are missing on disk "
        '(file-map Edit "no"): wowlab never writes, restores or deletes an executable, '
        "not even with --paths; reinstall the addon to get one back:"
    )
    assert "name one with --paths" not in text
    at = lines.index(header)
    assert lines[at + 1 : at + 3] == [
        f"    {GONE}  [addon-executable]",
        f"    {HELPER}  [addon-executable]",
    ]

    done = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert done.applied and [i.path for i in done.plan] == [TOC]
    assert (flavor / TOC).read_bytes() == saved_toc
    assert (flavor / HELPER).read_bytes() == HELPER_LATER
    assert not (flavor / GONE).exists()
    (record,) = guard.history()
    assert record.state == "committed"
    assert [p.path for p in record.paths] == [TOC]


@pytest.mark.parametrize("rel", [HELPER, GONE], ids=["changed", "missing"])
def test_paths_naming_an_executable_is_refused_with_what_to_do_constructed(
    root: Path, flavor: Path, rel: str
) -> None:
    a, _ = _executables_snapshot(flavor)
    for extra in ([], ["--json"], ["--dry-run"]):
        result = run("snap", "restore", a.id, "--paths", rel, "--yes", *extra)
        assert result.exit_code == 3, (result.stdout, result.stderr)
        err = _plain(result.stderr).splitlines()
        assert err[0] == (
            f"wowlab: refused by the write gate: {rel!r} is an executable; guard never writes one"
        )
        assert err[1] == NOTE
        assert result.stdout == ""
    assert guard.history() == ()


def test_paths_naming_the_other_files_restores_them_constructed(root: Path, flavor: Path) -> None:
    a, saved_toc = _executables_snapshot(flavor)
    ok("snap", "restore", a.id, "--paths", TOC, "--yes")
    assert (flavor / TOC).read_bytes() == saved_toc
    assert (flavor / HELPER).read_bytes() == HELPER_LATER


def test_executables_and_other_edit_no_files_are_listed_apart_constructed(
    root: Path, flavor: Path
) -> None:
    ds = f"{ADDON}/.DS_Store"
    (flavor / ds).write_bytes(b"constructed metadata")
    a, _ = _executables_snapshot(flavor)
    (flavor / ds).write_bytes(b"constructed metadata, later")
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert [(s.path, s.entry_id) for s in report.skipped] == [
        (ds, "os-metadata"),
        (GONE, "addon-executable"),
        (HELPER, "addon-executable"),
    ]
    text = _plain(ok("snap", "restore", a.id, "--dry-run").stdout)
    headers = [ln for ln in text.splitlines() if ln.startswith("  Skipped ")]
    assert len(headers) == 2
    assert headers[0].startswith('  Skipped 1 file(s) wowlab leaves alone (file-map Edit "no"')
    assert "name one with --paths to restore it" in headers[0]
    assert headers[1].startswith("  Skipped 2 executable(s)")
    assert "name one with --paths" not in headers[1]
    ok("snap", "restore", a.id, "--yes")
    assert (flavor / ds).read_bytes() == b"constructed metadata, later"


def test_only_changed_executables_change_nothing_constructed(root: Path, flavor: Path) -> None:
    (flavor / HELPER).write_bytes(HELPER_BYTES)
    a = _snapshot()
    (flavor / HELPER).write_bytes(HELPER_LATER)
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert report.plan == [] and not report.applied
    assert [s.path for s in report.skipped] == [HELPER]
    text = _plain(ok("snap", "restore", a.id, "--yes").stdout)
    assert "Nothing to restore but the skipped files; nothing was changed." in text
    assert guard.history() == ()


def test_an_unchanged_executable_is_not_listed_constructed(root: Path, flavor: Path) -> None:
    (flavor / HELPER).write_bytes(HELPER_BYTES)
    a = _snapshot()
    saved = _later(flavor)
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert report.applied and report.skipped == []
    assert (flavor / TOC).read_bytes() == saved


@pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot create a file named aux.sh")
def test_a_changed_reserved_name_executable_still_refuses_constructed(
    root: Path, flavor: Path
) -> None:
    """The gate refuses `aux.sh` for its name, not only as an executable;
    skipping it would hide that refusal, so the whole restore is refused."""
    aux = flavor / ADDON / "aux.sh"
    aux.write_bytes(HELPER_BYTES)
    a = _snapshot()
    aux.write_bytes(HELPER_LATER)
    _later(flavor)
    result = run("snap", "restore", a.id, "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "reserved device name" in _plain(result.stderr)
    assert guard.history() == ()


def test_a_changed_executable_outside_addons_still_refuses_constructed(
    root: Path, flavor: Path
) -> None:
    """Only the `addon-executable` row is skipped; elsewhere the gate's
    refusal stands, and the --paths note does not mention an addon."""
    tool = "WTF/tool.sh"
    (flavor / tool).write_bytes(HELPER_BYTES)
    a = _snapshot()
    (flavor / tool).write_bytes(HELPER_LATER)
    _later(flavor)
    result = run("snap", "restore", a.id, "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert f"{tool!r} is an executable; guard never writes one" in _plain(result.stderr)
    named = run("snap", "restore", a.id, "--paths", tool, "--yes")
    assert named.exit_code == 3
    assert _plain(named.stderr).splitlines()[1] == (
        "wowlab never writes, restores or deletes an executable, not even with --paths: "
        "name the other files with --paths."
    )
    assert guard.history() == ()


def test_help_says_paths_never_restores_an_executable() -> None:
    text = " ".join(_plain(ok("snap", "restore", "--help").stdout).split())
    assert (
        "A named path is restored even when its file-map row says wowlab leaves it alone, "
        "except an executable, which never is." in text.replace("│ ", "").replace(" │", "")
    )


# ─── what is not a plain change still refuses, as the gate does ─────────────


def _refused(a: Manifest) -> None:
    for args in (["--dry-run"], ["--yes"], ["--yes", "--json"]):
        result = run("snap", "restore", a.id, *args)
        assert result.exit_code == 3, (args, result.stdout, result.stderr)
        assert "is an executable; guard never writes one" in _plain(result.stderr)
    assert guard.history() == ()


def test_a_link_where_the_executable_was_still_refuses_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    (flavor / HELPER).write_bytes(HELPER_BYTES)
    a = _snapshot()
    outside = tmp_path / "outside-constructed.sh"
    outside.write_bytes(HELPER_LATER)
    (flavor / HELPER).unlink()
    try:
        (flavor / HELPER).symlink_to(outside)
    except OSError:
        pytest.skip("this machine does not let a test create a symbolic link")
    _later(flavor)
    _refused(a)
    assert (flavor / HELPER).is_symlink()


def test_a_folder_where_the_executable_was_still_refuses_constructed(
    root: Path, flavor: Path
) -> None:
    (flavor / HELPER).write_bytes(HELPER_BYTES)
    a = _snapshot()
    (flavor / HELPER).unlink()
    (flavor / HELPER).mkdir()
    _later(flavor)
    _refused(a)


def test_a_missing_executable_whose_folder_is_gone_still_refuses_constructed(
    root: Path, flavor: Path
) -> None:
    """Missing is skipped only from real folders that are still there."""
    tool = f"{ADDON}/tools/build.sh"
    (flavor / tool).parent.mkdir()
    (flavor / tool).write_bytes(HELPER_BYTES)
    a = _snapshot()
    (flavor / tool).unlink()
    (flavor / tool).parent.rmdir()
    _later(flavor)
    _refused(a)


def _forge_case(base: Manifest) -> str:
    """`base` with `helper.sh` spelled `HELPER.sh`, re-fingerprinted."""
    entries = tuple(
        sorted(
            (
                e.model_copy(update={"path": e.path.replace("/helper.sh", "/HELPER.sh")})
                for e in base.entries
            ),
            key=lambda e: e.path,
        )
    )
    fingerprint = tree_fingerprint(base.subtrees, base.excluded, entries)
    forged = Manifest(
        **{
            **base.model_dump(),
            "id": f"20260921T130000.000000Z-{fingerprint}",
            "created_at": "2026-09-21T13:00:00.000000Z",
            "label": "constructed: forged",
            "entries": entries,
        }
    )
    store = SnapshotStore()
    (store.manifests_dir / f"{forged.id}.json").write_bytes(manifest_bytes(forged))
    assert store.show(forged.id) == forged, "the store accepts it; refusing is guard's job"
    return forged.id


def test_a_forged_case_variant_of_an_executable_still_refuses_constructed(
    root: Path, flavor: Path
) -> None:
    (flavor / HELPER).write_bytes(HELPER_BYTES)
    forged = SnapshotStore().show(_forge_case(_snapshot()))
    (flavor / HELPER).write_bytes(HELPER_LATER)
    _later(flavor)
    _refused(forged)


def test_a_changed_appledouble_of_an_executable_is_listed_with_the_executables_constructed(
    root: Path, flavor: Path
) -> None:
    """`._helper.sh` (row `os-metadata`) is refused by the gate for its name,
    so it is skipped as an executable is and its header offers no --paths."""
    meta = f"{ADDON}/._helper.sh"
    (flavor / meta).write_bytes(b"\x00\x05\x16\x07constructed")
    a, _ = _executables_snapshot(flavor)
    (flavor / meta).write_bytes(b"\x00\x05\x16\x07constructed, later")
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert [(s.path, s.entry_id) for s in report.skipped] == [
        (meta, "os-metadata"),
        (GONE, "addon-executable"),
        (HELPER, "addon-executable"),
    ]
    text = _plain(ok("snap", "restore", a.id, "--dry-run").stdout)
    (header,) = [ln for ln in text.splitlines() if ln.startswith("  Skipped ")]
    assert header.startswith("  Skipped 3 executable(s) whose content changed")
    assert "name one with --paths" not in text
