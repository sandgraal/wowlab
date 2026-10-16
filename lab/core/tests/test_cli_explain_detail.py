"""`wowlab explain`'s per-path detail (M13-23, docs/LAB_PLAN.md §15.6):
`stat`, `snapshot`, `views` and `hints`.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`
with its flavor folder named as its provenance row says (`_classic_beta_`),
as `test_cli.py` does. `WOWLAB_WOW_ROOT` points at the copy, the user data
directory (the snapshot store) is redirected into `tmp_path`, and
`psutil.process_iter` is replaced by an empty table. Nothing reads or
writes a real install.

Inputs labelled `constructed` (L8) are boundary and hostile cases: a
character `bindings-cache.wtf` copied from the account's (the capture had
none), a SavedVariables file whose name holds ESC and U+202E, a file or
folder replaced by a link, a path outside the install, and a store that
cannot be read.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
import typer.main
from typer.testing import CliRunner

from wowlab_core import cli, filemap, install, pathfacts, snapshot

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
CHARACTER = "1/Labcharb-Labrealmd"  # <realm folder>/<character folder>
SV_DIR = f"WTF/Account/{ACCOUNT}/SavedVariables"
SYNDICATOR = f"{SV_DIR}/Syndicator.lua"

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture(autouse=True)
def idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process table with no client in it."""
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))


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


def ok(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, (args, result.stdout, result.stderr, result.exception)
    return result


def explained(*args: str) -> cli.ExplainReport:
    return cli.ExplainReport.model_validate_json(ok("explain", *args, "--json").stdout)


def _state(root: Path) -> dict[str, tuple[bytes | None, int]]:
    out: dict[str, tuple[bytes | None, int]] = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[str(p)] = (
            p.read_bytes() if p.is_file() and not p.is_symlink() else None,
            st.st_mtime_ns,
        )
    return out


# ─── the view table ──────────────────────────────────────────────────────────

# The modules that decode a file for an existing read command, as the file
# map's Module column names them. `layout` and `process` list and name
# things; they decode no file's content.
DECODERS = ("`luadata`", "`wtfconfig`", "`install`", "`toc`", "`combatlog`")

# One path per entry with a view: (base, path, kind), checked to classify as
# that entry.
SAMPLES: dict[str, tuple[str, str, filemap.Kind]] = {
    "build-info": ("root", ".build.info", "file"),
    "flavor-folder": ("flavor", "", "dir"),
    "flavor-info": ("flavor", ".flavor.info", "file"),
    "config-wtf": ("flavor", "WTF/Config.wtf", "file"),
    "account-blizzard-savedvariables": (
        "flavor",
        f"WTF/Account/{ACCOUNT}/SavedVariables.lua",
        "file",
    ),
    "account-savedvariables": ("flavor", SYNDICATOR, "file"),
    "character-savedvariables": (
        "flavor",
        f"WTF/Account/{ACCOUNT}/{CHARACTER}/SavedVariables/WowLab.lua",
        "file",
    ),
    "savedvariables-backup": ("flavor", f"{SV_DIR}/RareScanner.lua.bak", "file"),
    "account-config-cache": ("flavor", f"WTF/Account/{ACCOUNT}/config-cache.wtf", "file"),
    "account-bindings-cache": ("flavor", f"WTF/Account/{ACCOUNT}/bindings-cache.wtf", "file"),
    "account-macros-cache": ("flavor", f"WTF/Account/{ACCOUNT}/macros-cache.txt", "file"),
    "character-wtf-config": (
        "flavor",
        f"WTF/Account/{ACCOUNT}/{CHARACTER}/config-cache.wtf",
        "file",
    ),
    "addons-folder": ("flavor", "Interface/AddOns", "dir"),
    "addon": ("flavor", "Interface/AddOns/DBM-Brawlers/DBM-Brawlers.toc", "file"),
    "blizzard-addon-export": ("flavor", "Interface/AddOns/Blizzard_Foo/Blizzard_Foo.toc", "file"),
    "combat-log": ("flavor", "Logs/WoWCombatLog-040126_021630.txt", "file"),
}
CHARACTER_WTF = ("config-cache.wtf", "bindings-cache.wtf", "macros-cache.txt")


def test_every_entry_an_existing_command_decodes_has_a_view() -> None:
    fm = filemap.load()
    ids = {e.id for e in fm.entries}
    assert set(cli._VIEWS) <= ids
    decoded = {e.id for e in fm.entries if any(d in e.module for d in DECODERS)}
    assert decoded, "the Module column names no decoder"
    assert decoded <= set(cli._VIEWS), sorted(decoded - set(cli._VIEWS))
    assert set(SAMPLES) == set(cli._VIEWS)


def _leaf(command: list[str]) -> Any:
    node: Any = typer.main.get_command(cli.app)
    for name in command:
        node = node.commands[name]
    assert not hasattr(node, "commands"), command
    return node


def _all_views(root: str | None) -> list[tuple[str, cli.ExplainView]]:
    fm = filemap.load()
    found: list[tuple[str, cli.ExplainView]] = []
    for entry_id, (base, path, kind) in SAMPLES.items():
        parts = tuple(p for p in path.split("/") if p)
        samples = [parts]
        if entry_id == "character-wtf-config":
            samples = [(*parts[:-1], name) for name in CHARACTER_WTF]
        for sample in samples:
            assert fm.classify(sample, base, kind) == fm.entry(entry_id), (entry_id, sample)  # type: ignore[arg-type]
            at = cli._At(
                parts=sample,
                absolute="/" + "/".join(("install", FLAVOR, *sample)),
                flavor=FLAVOR if base == "flavor" else None,
                folder=kind == "dir",
                newest_log=sample[-1] if entry_id == "combat-log" else None,
            )
            views, note = cli._views(entry_id, at, root)
            assert views, (entry_id, sample)
            assert note is None
            assert all(v.does and v.does != "decode it" for v in views)
            found.extend((entry_id, v) for v in views)
    return found


@pytest.mark.parametrize("root", [None, "/install root/World of Warcraft"])
def test_every_view_argv_parses_against_the_click_tree(root: str | None) -> None:
    """Each view's argv is a leaf of the tree, parses with Click to exactly
    its `params` (everything else at its default), and is a command the UI
    runs: no `--yes`, no `log tail --follow`, no `looks page --out`."""
    for entry_id, view in _all_views(root):
        assert view.argv[: len(view.command)] == view.command
        leaf = _leaf(view.command)
        declared = {opt for p in leaf.params for opt in getattr(p, "opts", ())}
        assert "--yes" not in declared, (entry_id, view.command)
        assert "--json" in declared
        ctx = leaf.make_context(view.command[-1], list(view.argv[len(view.command) :]))
        for name, value in ctx.params.items():
            if name in view.params:
                assert str(value) == view.params[name], (entry_id, view.argv, name)
            else:  # everything else is left at its default
                default = next(p.default for p in leaf.params if p.name == name)
                assert value == default, (entry_id, view.argv, name)
        assert set(view.params) <= set(ctx.params)
        # What the UI refuses (ADR-0029 amendment 2026-10-15) is never a view.
        assert view.command != ["ui"]
        assert ctx.params.get("follow") in (None, False)
        assert "out" not in view.params
        if root is not None:
            assert view.params["root"] == root


def test_each_view_decodes_the_captured_file(root: Path, flavor: Path) -> None:
    """Every view `explain` gives for a captured file runs and exits 0."""
    char = flavor / "WTF" / "Account" / ACCOUNT / CHARACTER
    shutil.copy2(  # constructed: the capture had no character bindings
        flavor / "WTF" / "Account" / ACCOUNT / "bindings-cache.wtf", char / "bindings-cache.wtf"
    )
    paths = [
        ".build.info",
        FLAVOR,
        f"{FLAVOR}/.flavor.info",
        "WTF/Config.wtf",
        SYNDICATOR,
        f"WTF/Account/{ACCOUNT}/1/Labchard-Labrealmg/SavedVariables/WowLab.lua",
        f"{SV_DIR}/RareScanner.lua.bak",
        f"WTF/Account/{ACCOUNT}/config-cache.wtf",
        f"WTF/Account/{ACCOUNT}/bindings-cache.wtf",
        f"WTF/Account/{ACCOUNT}/macros-cache.txt",
        *(f"WTF/Account/{ACCOUNT}/{CHARACTER}/{n}" for n in CHARACTER_WTF),
        "Interface/AddOns/",
        "Interface/AddOns/DBM-Brawlers/DBM-Brawlers.toc",
        "Logs/WoWCombatLog-040126_021630.txt",
    ]
    seen = set()
    for path in paths:
        report = explained(path)
        assert report.views, path
        assert isinstance(report.result, cli.layout.Classified)
        seen.add(report.result.entry.id)
        for view in report.views:
            out = ok(*view.argv, "--json").stdout
            assert out.strip(), (path, view.argv)
    assert seen == set(SAMPLES) - {"account-blizzard-savedvariables", "blizzard-addon-export"}


def test_with_root_every_view_and_hint_names_the_install(root: Path) -> None:
    report = explained("WTF/Config.wtf", "--root", str(root))
    assert report.views[0].params["root"] == str(root)
    assert all(h.command.count(f"--root {shlex.quote(str(root))}") == 1 for h in report.hints)
    without = explained("WTF/Config.wtf")
    assert all("root" not in v.params for v in without.views)
    assert all("--root" not in h.command for h in without.hints)


# ─── stat and snapshot facts ─────────────────────────────────────────────────


def test_stat_is_the_paths_own(root: Path, flavor: Path) -> None:
    st = (flavor / SYNDICATOR).lstat()
    report = explained(SYNDICATOR)
    assert report.stat is not None
    assert report.stat.kind == "file"
    assert report.stat.size == st.st_size
    assert report.stat.mtime_ns == st.st_mtime_ns
    assert report.stat.mtime.endswith("Z")
    folder = explained("WTF/")
    assert folder.stat is not None
    assert folder.stat.kind == "folder"
    assert folder.stat.size is None
    absent = explained(f"{FLAVOR}/Logs/WoWCombatLog-092726_120000.txt")
    assert absent.stat is None


def _restore_hints(report: cli.ExplainReport) -> list[cli.ExplainHint]:
    return [h for h in report.hints if h.command.startswith("wowlab snap restore ")]


def test_snapshot_facts_follow_the_file_on_disk(root: Path, flavor: Path) -> None:
    before = explained(SYNDICATOR)
    assert before.snapshot is not None
    assert (before.snapshot.snapshot_id, before.snapshot.holds, before.snapshot.error) == (
        None,
        None,
        None,
    )
    assert before.snapshot.captured is True
    assert not _restore_hints(before)
    assert any(h.command == f"wowlab snap create --flavor {FLAVOR}" for h in before.hints)
    assert (
        "  In the latest snapshot: none; no snapshot of this flavor folder has been taken\n"
        in ok("explain", SYNDICATOR).stdout
    )

    ok("snap", "create", "-m", "older")
    first = snapshot.SnapshotStore().list()[-1]
    (flavor / SYNDICATOR).write_bytes((flavor / SYNDICATOR).read_bytes() + b"\n")
    ok("snap", "create", "-m", "newer")
    latest = snapshot.SnapshotStore().list()[-1]
    assert latest.id != first.id

    same = explained(SYNDICATOR)
    assert same.snapshot is not None
    assert (same.snapshot.snapshot_id, same.snapshot.created_at, same.snapshot.label) == (
        latest.id,
        latest.created_at,
        "newer",
    )
    assert (same.snapshot.holds, same.snapshot.path) == (True, SYNDICATOR)
    assert (same.snapshot.live, same.snapshot.differs) == ("same", False)
    assert not _restore_hints(same)
    assert not any(h.command.startswith("wowlab snap create") for h in same.hints)

    # Same size, other bytes: the hash, not the size, tells.
    data = bytearray((flavor / SYNDICATOR).read_bytes())
    data[0] ^= 1
    (flavor / SYNDICATOR).write_bytes(bytes(data))
    differs = explained(SYNDICATOR)
    assert differs.snapshot is not None
    assert (differs.snapshot.live, differs.snapshot.differs) == ("differs", True)
    [hint] = _restore_hints(differs)
    assert hint.argv == ["snap", "restore", latest.id, "--paths", SYNDICATOR]
    assert hint.command == f"wowlab snap restore {latest.id} --paths {shlex.quote(SYNDICATOR)}"
    assert hint.shell == "posix"
    text = ok("explain", SYNDICATOR).stdout
    assert (
        f'  In the latest snapshot ({latest.id}, {latest.created_at}, "newer"): '
        "the file on disk differs\n" in text
    )
    assert f"  hint: {hint.command}  # {hint.does}\n" in text
    size = (flavor / SYNDICATOR).stat().st_size
    assert f"  on disk:      {size:,} bytes, modified " in text

    # The hint is a command the CLI takes as it is.
    plan = ok(*hint.argv, "--dry-run", "--json").stdout
    assert SYNDICATOR in plan

    (flavor / SYNDICATOR).unlink()
    missing = explained(SYNDICATOR)
    assert missing.snapshot is not None
    assert (missing.snapshot.live, missing.snapshot.differs) == ("missing", True)
    assert _restore_hints(missing)


def test_a_file_the_latest_snapshot_lacks_gets_no_restore_hint(root: Path, flavor: Path) -> None:
    """Domain review 4: the file was deleted and a snapshot taken since."""
    ok("snap", "create", "-m", "with it")
    held = snapshot.SnapshotStore().list()[-1]
    (flavor / SYNDICATOR).unlink()
    ok("snap", "create", "-m", "without it")
    latest = snapshot.SnapshotStore().list()[-1]
    report = explained(SYNDICATOR)
    assert report.snapshot is not None
    assert (report.snapshot.snapshot_id, report.snapshot.holds) == (latest.id, False)
    assert report.snapshot.last_held is not None
    assert report.snapshot.last_held.id == held.id
    assert not _restore_hints(report)
    assert not any(h.command.startswith("wowlab snap ") for h in report.hints)
    text = ok("explain", SYNDICATOR).stdout
    assert "not held, the file was not there when it was taken" in text
    assert f"the latest snapshot that holds it is {held.id}" in text


def test_a_file_snapshots_never_capture_says_so(root: Path, flavor: Path) -> None:
    """Domain review 11: `Logs/` is outside what `snap create` captures."""
    ok("snap", "create")
    log = "Logs/WoWCombatLog-040126_021630.txt"
    report = explained(log)
    assert report.snapshot is not None
    assert (report.snapshot.captured, report.snapshot.snapshot_id) == (False, None)
    text = ok("explain", log).stdout
    assert (
        "  In the latest snapshot: not held; snapshots capture WTF/, Interface/, Fonts/ "
        "(Screenshots/ with --screenshots) only\n" in text
    )


def test_a_damaged_manifest_is_said_not_skipped(root: Path, flavor: Path) -> None:
    """Domain review 1. Constructed: a manifest file cut short."""
    ok("snap", "create")
    manifests = snapshot.SnapshotStore().manifests_dir
    [good] = list(manifests.iterdir())
    damaged = manifests / ("20200101T000000.000000Z-00000000" + good.suffix)
    damaged.write_bytes(good.read_bytes()[:40])
    # Not held anywhere readable: never "no snapshot holds it", no snap create.
    rel = f"WTF/Account/{ACCOUNT}/new-constructed.txt"
    (flavor / rel).write_bytes(b"x")
    report = explained(rel)
    assert report.snapshot is not None
    assert (report.snapshot.holds, report.snapshot.damaged) == (False, 1)
    assert not any(h.command.startswith("wowlab snap ") for h in report.hints)
    text = ok("explain", rel).stdout
    assert "no snapshot of this flavor folder holds it" not in text
    assert "no readable snapshot holds it; 1 snapshot manifest(s) could not be read" in text
    assert "so this may be incomplete" in ok("explain", SYNDICATOR).stdout


def test_restore_hints_carry_the_caveats_of_the_row(root: Path, flavor: Path) -> None:
    server = f"WTF/Account/{ACCOUNT}/config-cache.wtf"
    bak = f"{SV_DIR}/RareScanner.lua.bak"
    ok("snap", "create")
    for rel in (server, bak, SYNDICATOR):
        (flavor / rel).write_bytes((flavor / rel).read_bytes() + b"\n")
    [hint] = _restore_hints(explained(server))
    assert hint.does.endswith(
        "; the server may replace it at the next login (sync settings: wowlab doctor)"
    )
    assert "again" not in hint.does
    [hint] = _restore_hints(explained(bak))
    assert hint.does.endswith("; the client writes this file and replaces it at its next save")
    [hint] = _restore_hints(explained(SYNDICATOR))
    assert "server" not in hint.does and "client writes" not in hint.does


def test_a_combat_log_gets_log_tail_only_when_it_is_the_newest(root: Path, flavor: Path) -> None:
    """Domain review 3. Constructed: a second, newer combat log."""
    old = "Logs/WoWCombatLog-040126_021630.txt"
    assert explained(old).views[0].command == ["log", "tail"]
    new = flavor / "Logs" / "WoWCombatLog-050126_010101.txt"
    shutil.copy2(flavor / old, new)
    later = (flavor / old).stat().st_mtime_ns + 10**9
    os.utime(new, ns=(later, later))
    report = explained(old)
    assert report.views == []
    assert report.views_note == (
        "`wowlab log tail` reads only the newest combat log (WoWCombatLog-050126_010101.txt), "
        "not this one"
    )
    assert f"  views:        {report.views_note}\n" in ok("explain", old).stdout
    assert explained(f"Logs/{new.name}").views[0].command == ["log", "tail"]


def test_a_file_no_command_reads_says_so(root: Path) -> None:
    """Domain review 6 and 12: AddOns.txt, and a file in an addon that is not
    a TOC, get no view and a note."""
    addons_txt = f"WTF/Account/{ACCOUNT}/Labrealmb Partb Partc Partd/Labchard/AddOns.txt"
    report = explained(addons_txt)
    assert isinstance(report.result, cli.layout.Classified)
    assert report.result.entry.id == "character-addons-txt"
    assert (report.views, report.views_note) == ([], "no wowlab command reads this file")
    assert "  views:        no wowlab command reads this file\n" in ok("explain", addons_txt).stdout
    lua = explained("Interface/AddOns/DBM-Brawlers/DBM-Brawlers.lua-constructed")
    assert lua.views == []
    assert lua.views_note is not None
    assert lua.views_note.startswith("no wowlab command reads this file")
    assert explained("Interface/AddOns/DBM-Brawlers/").views_note is None


def test_hints_are_quoted_for_the_shell_of_the_machine(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Domain review 7: Windows quoting is the C runtime's, `list2cmdline`."""
    hostile = ["a b", 'say "hi"', "back\\slash\\", 'q\\"x', "", "Kel'Thuzad", "#6", "\t"]
    assert cli._windows_join(hostile) == subprocess.list2cmdline(hostile)
    ok("snap", "create")
    (flavor / SYNDICATOR).write_bytes((flavor / SYNDICATOR).read_bytes() + b"\n")
    monkeypatch.setattr(cli, "_shell", lambda: "windows")
    report = explained(SYNDICATOR, "--root", str(root))
    assert report.hints
    for hint in report.hints:
        assert hint.shell == "windows"
        assert hint.command == subprocess.list2cmdline(["wowlab", *hint.argv])


def test_explain_hashes_only_the_named_path_and_writes_nothing(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok("snap", "create")
    opened: list[Path] = []
    real = snapshot.open_regular_file

    def spy(path: Path, **kw: Any) -> Any:
        opened.append(Path(path))
        return real(path, **kw)

    monkeypatch.setattr(snapshot, "open_regular_file", spy)
    before = _state(root)
    explained(SYNDICATOR)
    ok("explain", SYNDICATOR)
    assert _state(root) == before
    in_install = [p for p in opened if root in p.parents]
    assert in_install == [flavor / SYNDICATOR] * 2


def test_a_folder_gets_no_snapshot_facts_and_no_restore_hint(root: Path) -> None:
    ok("snap", "create")
    for path in (
        "WTF/",
        f"WTF/Account/{ACCOUNT}/SavedVariables/",
        "Interface/AddOns/DBM-Brawlers/",
    ):
        report = explained(path)
        assert report.verdict.restorable is None
        assert report.snapshot is None
        assert not _restore_hints(report)
        assert not any(h.command.startswith("wowlab snap create") for h in report.hints)
    assert explained("Interface/AddOns/").views[0].command == ["addons", "list"]


needs_symlink = pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="links")


@needs_symlink
def test_a_link_or_a_path_inside_one_gets_no_detail(
    root: Path, flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed: a SavedVariables file and an addon folder replaced by links."""
    ok("snap", "create")
    outside = tmp_path / "outside-constructed"
    outside.mkdir()
    shutil.copy2(flavor / SYNDICATOR, outside / "Syndicator.lua")
    (flavor / SYNDICATOR).unlink()
    (flavor / SYNDICATOR).symlink_to(outside / "Syndicator.lua")
    addon = flavor / "Interface" / "AddOns" / "DBM-Brawlers"
    shutil.move(addon, outside / "DBM-Brawlers")
    addon.symlink_to(outside / "DBM-Brawlers")
    opened: list[Path] = []
    real = snapshot.open_regular_file
    monkeypatch.setattr(
        snapshot, "open_regular_file", lambda p, **kw: (opened.append(Path(p)), real(p, **kw))[1]
    )
    for path in (SYNDICATOR, "Interface/AddOns/DBM-Brawlers/DBM-Brawlers.toc"):
        report = explained(path)
        assert report.verdict.restorable is None
        assert (report.stat, report.snapshot, report.views, report.hints) == (None, None, [], [])
        text = ok("explain", path).stdout
        assert "hint:" not in text
        assert "In the latest snapshot" not in text
    assert opened == []


def test_a_path_outside_the_install_gets_no_detail(root: Path, tmp_path: Path) -> None:
    """Constructed: an absolute path beside the install."""
    elsewhere = tmp_path / "elsewhere-constructed.lua"
    elsewhere.write_bytes(b"x = 1\n")
    report = explained(str(elsewhere))
    assert (report.stat, report.snapshot, report.views, report.hints) == (None, None, [], [])


def test_an_unreadable_store_is_said_not_guessed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Constructed: the store refuses to list."""

    def refuse(self: snapshot.SnapshotStore) -> Any:
        raise snapshot.StoreLocationError("constructed: the store is a link")

    monkeypatch.setattr(snapshot.SnapshotStore, "list_lenient", refuse)
    report = explained(SYNDICATOR)
    assert report.snapshot == pathfacts.SnapshotFacts(
        snapshot_id=None,
        created_at=None,
        label=None,
        holds=None,
        path=None,
        live=None,
        differs=None,
        last_held=None,
        captured=True,
        damaged=0,
        error="constructed: the store is a link",
    )
    assert not any(h.command.startswith("wowlab snap ") for h in report.hints)
    assert "the snapshot store could not be read" in ok("explain", SYNDICATOR).stdout


# ─── hints are safe ──────────────────────────────────────────────────────────


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    return resolved == root.resolve() or root.resolve() in resolved.parents


def _paths_in(hint: cli.ExplainHint, flavor: Path) -> list[Path]:
    argv = hint.argv
    assert shlex.split(hint.command) == ["wowlab", *argv]
    found = [Path(a) for a in argv if Path(a).is_absolute()]
    found += [flavor / argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--paths"]
    return found


def test_hints_name_only_paths_inside_the_install(root: Path, flavor: Path) -> None:
    ok("snap", "create")
    for p in flavor.rglob("*"):
        if p.is_file():
            p.write_bytes(p.read_bytes() + b"\n")
    checked = 0
    for p in [root / ".build.info", *flavor.rglob("*")]:
        rel = str(p.relative_to(root))
        report = explained(rel)
        for hint in report.hints:
            assert hint.command.startswith("wowlab ")
            for named in _paths_in(hint, flavor):
                assert _inside(named, root), (rel, hint.command)
                checked += 1
        for view in report.views:
            for a in view.argv:
                if Path(a).is_absolute():
                    assert _inside(Path(a), root), (rel, view.argv)
    assert checked > 10


def test_hints_print_through_safe(root: Path, flavor: Path) -> None:
    """Constructed: SavedVariables files whose names hold ESC (the write gate
    refuses the name, so only the view's hint names it) and U+202E (a
    restore hint names it too)."""
    ok("snap", "create")  # neither file is in it yet
    for name, restorable in (("Evil\x1b[31mRed.lua", False), ("Evil\u202eRed.lua", True)):
        rel = f"{SV_DIR}/{name}"
        shutil.copy2(flavor / SYNDICATOR, flavor / rel)
        ok("snap", "create")
        (flavor / rel).write_bytes((flavor / rel).read_bytes() + b"\n")
        report = explained(rel)
        assert report.verdict.restorable is restorable
        assert bool(_restore_hints(report)) is restorable
        assert all(name in h.command for h in report.hints)  # the JSON keeps the name
        text = ok("explain", rel).stdout
        assert "\x1b" not in text
        assert "\u202e" not in text
        hint_lines = [line for line in text.splitlines() if line.startswith("  hint: ")]
        assert len(hint_lines) == len(report.hints)
        for line, hint in zip(hint_lines, report.hints, strict=True):
            assert line == "  hint: " + cli._safe(f"{hint.command}  # {hint.does}")
            assert "\\x1b" in line or "\\xe2\\x80\\xae" in line


# ─── the text output ─────────────────────────────────────────────────────────


def _old_text(report: cli.ExplainReport) -> list[str]:
    """`explain`'s text as it was before M13-23, built from the report."""
    result = report.result
    if isinstance(result, cli.layout.Unclassified):
        where = f" ({result.flavor_folder})" if result.flavor_folder else ""
        return [f"{report.query}: no file-map row{where}: {result.reason}"]
    e = result.entry
    base = f"in flavor folder {result.flavor_folder}" if result.base == "flavor" else "install root"
    return [
        f"{result.path or '.'} ({base})",
        f"  what:         {e.what.replace('**', '')}",
        f"  written by:   {e.written_by.replace('**', '')}",
        f"  edit:         {report.verdict.words}",
        f"  tier:         {cli.tier_words(e.tier)}",
        f"  read by:      {e.module}",
        f"  file-map row: {e.id} ({e.doc_path})",
    ]


@pytest.mark.parametrize(
    "path",
    [
        "WTF/Config.wtf",
        SYNDICATOR,
        "WTF/",
        ".build.info",
        "Interface/AddOns/DBM-Brawlers/DBM-Brawlers.toc",
        "unknown-constructed.txt",
    ],
)
def test_text_keeps_every_line_it_had_and_adds_only_the_new_ones(
    root: Path, flavor: Path, path: str
) -> None:
    ok("snap", "create")
    (flavor / "WTF" / "Config.wtf").write_bytes(b'SET constructed "1"\n')
    report = explained(path)
    lines = ok("explain", path).stdout.splitlines()
    old = [cli._safe(line) for line in _old_text(report)]
    assert lines[: len(old)] == old
    new = lines[len(old) :]
    expected: list[str] = []
    if report.stat is not None:
        size = report.stat.size
        what = f"{size:,} byte" + ("" if size == 1 else "s") if size is not None else "a folder"
        expected.append(f"  on disk:      {what}, modified {report.stat.mtime}")
    if report.snapshot is not None:
        captured = "WTF/, Interface/, Fonts/ (Screenshots/ with --screenshots)"
        expected.append(f"  {cli._snapshot_line(report.snapshot, captured)}")
    if report.views_note is not None:
        expected.append(f"  views:        {report.views_note}")
    expected += [f"  hint: {h.command}  # {h.does}" for h in report.hints]
    assert new == [cli._safe(line) for line in expected]


def test_another_case_spelling_reads_the_file_as_spelled_on_disk(root: Path, flavor: Path) -> None:
    """Domain review 2. Constructed: queries spelled in another case; skipped
    where the temporary volume is case-sensitive."""
    if not (flavor / SYNDICATOR.lower()).exists():
        pytest.skip("the temporary volume is case-sensitive")
    ok("snap", "create")
    (flavor / SYNDICATOR).write_bytes((flavor / SYNDICATOR).read_bytes() + b"\n")
    typed = f"WTF/account/{ACCOUNT}/savedvariables/syndicator.lua"
    report = explained(typed)
    assert report.snapshot is not None
    assert (report.snapshot.live, report.snapshot.path) == ("differs", SYNDICATOR)
    [hint] = _restore_hints(report)
    assert hint.argv[-2:] == ["--paths", SYNDICATOR]  # the spelling the snapshot holds
    assert report.views[0].params["file"] == str(flavor / SYNDICATOR)
    macros = explained(f"WTF/account/{ACCOUNT}/{CHARACTER.lower()}/MACROS-CACHE.TXT")
    [view] = macros.views
    assert view.params["character"] == CHARACTER
    ok(*view.argv, "--json")
