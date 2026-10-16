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
import re
import shlex
import shutil
import unicodedata
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
        "  In the latest snapshot: none; no snapshot of this flavor folder has been taken "
        "in this install\n" in ok("explain", SYNDICATOR).stdout
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
    assert "no snapshot of this flavor folder in this install holds it" not in text
    assert "no readable snapshot holds it; 1 snapshot manifest(s) could not be read" in text
    assert "In the latest readable snapshot (" in text
    assert "so this may be incomplete" in ok("explain", SYNDICATOR).stdout
    # An older damaged manifest leaves the restore hint; a newer one withholds it.
    (flavor / SYNDICATOR).write_bytes((flavor / SYNDICATOR).read_bytes() + b"\n")
    older = explained(SYNDICATOR)
    assert older.snapshot is not None
    assert older.snapshot.damaged_newer is False
    assert _restore_hints(older)
    damaged.rename(manifests / ("29990101T000000.000000Z-00000000" + good.suffix))
    newer = explained(SYNDICATOR)
    assert newer.snapshot is not None
    assert (newer.snapshot.damaged, newer.snapshot.damaged_newer) == (1, True)
    assert newer.snapshot.live == "differs"
    assert not _restore_hints(newer)


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
    assert (report.views, report.views_note) == ([], "no wowlab command shows this file's contents")
    assert (
        "  views:        no wowlab command shows this file's contents\n"
        in ok("explain", addons_txt).stdout
    )
    lua = explained("Interface/AddOns/DBM-Brawlers/DBM-Brawlers.lua-constructed")
    assert lua.views == []
    assert lua.views_note is not None
    assert lua.views_note.startswith("no wowlab command shows this file's contents")
    assert explained("Interface/AddOns/DBM-Brawlers/").views_note is None


def _powershell_split(line: str) -> list[str]:
    """The arguments PowerShell makes of `line`, for the subset
    `_powershell_join` writes: bare words of safe characters, and single-
    quoted strings in which a doubled quote character stands for one."""
    quotes = "\u0027\u2018\u2019\u201a\u201b"
    out: list[str] = []
    i = 0
    while i < len(line):
        if line[i] == " ":
            i += 1
            continue
        if line[i] in quotes:
            i += 1
            text = []
            while True:
                c = line[i]
                if c in quotes:
                    if i + 1 < len(line) and line[i + 1] in quotes:
                        text.append(line[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                text.append(c)
                i += 1
            out.append("".join(text))
        else:
            j = line.find(" ", i)
            j = len(line) if j < 0 else j
            word = line[i:j]
            assert re.fullmatch(r"[A-Za-z0-9_./:=+@%\\-]+", word), word
            assert not word.startswith("@"), word
            out.append(word)
            i = j
    return out


HOSTILE = [
    "a b",
    "Kel'Thuzad",
    "Kel\u2019Thuzad",
    "a&b",
    "a;b",
    "a,b",
    "$env:x",
    "`n",
    "(x)",
    "{x}",
    "#6",
    "@x",
    "",
    "C:\\World of Warcraft\\_retail_",
    'say "hi"',
    "--paths",
    "WTF/Account/1#1/SavedVariables/A.lua",
]


def test_powershell_quoting_keeps_every_argument_whole() -> None:
    """Domain review round 2, item 3: anything outside a safe set is single-
    quoted, a quote character doubled, so PowerShell (Windows Terminal's
    default) reads back the argv. Constructed hostile arguments."""
    line = cli._powershell_join(HOSTILE)
    assert _powershell_split(line) == HOSTILE
    for word in ("--paths", "WTF/Account/x/A.lua", "C:\\x"):
        assert cli._powershell_join([word]) == word
    assert cli._powershell_join(["Kel'Thuzad"]) == "'Kel''Thuzad'"


def test_hints_are_quoted_for_the_shell_of_the_machine(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok("snap", "create")
    (flavor / SYNDICATOR).write_bytes((flavor / SYNDICATOR).read_bytes() + b"\n")
    posix = explained(SYNDICATOR, "--root", str(root))
    assert posix.hints
    for hint in posix.hints:
        assert hint.shell == "posix"
        assert shlex.split(hint.command) == ["wowlab", *hint.argv]
    monkeypatch.setattr(cli, "_shell", lambda: "powershell")
    report = explained(SYNDICATOR, "--root", str(root))
    assert report.hints
    for hint in report.hints:
        assert hint.shell == "powershell"
        assert _powershell_split(hint.command) == ["wowlab", *hint.argv]


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
        confirmed=None,
        live=None,
        differs=None,
        last_held=None,
        captured=True,
        damaged=0,
        damaged_newer=False,
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


def test_the_verdict_judges_the_spelling_on_disk(root: Path, flavor: Path) -> None:
    """Domain review round 2, item 2: one file, one answer, however typed;
    `query` and `result` stay as typed. Constructed: a lower-case spelling;
    skipped where the temporary volume is case-sensitive."""
    bak = f"{SV_DIR}/RareScanner.lua.bak"
    if not (flavor / bak.lower()).exists():
        pytest.skip("the temporary volume is case-sensitive")
    exact = explained(bak)
    typed = explained(bak.lower())
    assert typed.query == bak.lower()
    assert typed.result.path == bak.lower()
    assert typed.verdict == exact.verdict
    assert typed.verdict.restorable is True
    ok("snap", "create")
    (flavor / bak).write_bytes((flavor / bak).read_bytes() + b"\n")
    [hint] = _restore_hints(explained(bak.lower()))
    assert hint.argv[-2:] == ["--paths", bak]


def test_another_unicode_normalization_names_the_same_file(root: Path, flavor: Path) -> None:
    """Domain review round 2, item 1. Constructed: a character folder stored
    in NFD (as macOS volumes may hand back) typed in NFC; skipped where the
    temporary volume does not fold normalization."""
    nfd = unicodedata.normalize("NFD", "Ëlfé")
    nfc = unicodedata.normalize("NFC", "Ëlfé")
    char = flavor / "WTF" / "Account" / ACCOUNT / "1" / nfd / "SavedVariables"
    char.mkdir(parents=True)
    shutil.copy2(flavor / SYNDICATOR, char / "Syndicator.lua")
    if nfd not in [e.name for e in char.parent.parent.iterdir()]:
        pytest.skip("the temporary volume normalizes names when it stores them")
    if not (char.parent.parent / nfc).exists():
        pytest.skip("the temporary volume does not fold Unicode normalization")
    ok("snap", "create")
    (char / "Syndicator.lua").write_bytes((char / "Syndicator.lua").read_bytes() + b"\n")
    stored = f"WTF/Account/{ACCOUNT}/1/{nfd}/SavedVariables/Syndicator.lua"
    report = explained(f"WTF/Account/{ACCOUNT}/1/{nfc}/SavedVariables/Syndicator.lua")
    assert report.snapshot is not None
    assert (report.snapshot.holds, report.snapshot.live, report.snapshot.path) == (
        True,
        "differs",
        stored,
    )
    [hint] = _restore_hints(report)
    assert hint.argv[-2:] == ["--paths", stored]
    assert not any(h.argv[:2] == ["snap", "create"] for h in report.hints)


def test_a_deleted_file_typed_in_another_case_is_still_held(root: Path, flavor: Path) -> None:
    """Domain review round 3, item 1: neither spelling exists, so the folder
    above decides. Constructed: a lower-case query of a deleted file; skipped
    where the temporary volume is case-sensitive."""
    if not (flavor / SYNDICATOR.lower()).exists():
        pytest.skip("the temporary volume is case-sensitive")
    ok("snap", "create")
    (flavor / SYNDICATOR).unlink()
    typed = f"WTF/Account/{ACCOUNT}/SavedVariables/syndicator.lua"
    report = explained(typed)
    assert report.snapshot is not None
    assert (report.snapshot.holds, report.snapshot.confirmed, report.snapshot.live) == (
        True,
        True,
        "missing",
    )
    assert report.snapshot.path == SYNDICATOR
    [hint] = _restore_hints(report)
    assert hint.argv[-2:] == ["--paths", SYNDICATOR]
    assert not any(h.argv[:2] == ["snap", "create"] for h in report.hints)
    plan = ok(*hint.argv, "--dry-run", "--json").stdout
    assert SYNDICATOR in plan


def test_an_unknown_folding_is_said_and_offers_nothing(tmp_path: Path) -> None:
    """Domain review round 3, item 1. Constructed: folders whose names have
    no letters, so none on the way can show whether the volume folds case."""
    (tmp_path / "1" / "2").mkdir(parents=True)
    volume = pathfacts._Volume(tmp_path)
    assert volume.same("1/2/abc.lua", "1/2/ABC.lua") is None
    assert volume.same("1/2/abc.lua", "1/2/abd.lua") is False
    facts = pathfacts.SnapshotFacts(
        snapshot_id="20261016T000000.000000Z-00000000",
        created_at="2026-10-16T00:00:00.000000Z",
        label="",
        holds=True,
        path="WTF/ABC.lua",
        confirmed=False,
        live="missing",
        differs=True,
        last_held=None,
        captured=True,
        damaged=0,
        damaged_newer=False,
        error=None,
    )
    line = cli._snapshot_line(facts, "WTF/")
    assert "it holds WTF/ABC.lua, which could not be confirmed to be this file" in line
    verdict = cli.verdict_of(filemap.load().entry("config-wtf"), "flavor", "WTF/Config.wtf")
    hints = cli._hints(verdict, facts, FLAVOR, None, [])
    assert hints == []


@pytest.mark.skipif(os.name == "nt", reason="links")
def test_a_spelling_through_a_folder_link_is_never_the_same_entry(tmp_path: Path) -> None:
    """Code review round 3, item 2. Constructed: a real folder and a link to
    it under another case spelling; skipped where the volume folds case."""
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "f.lua").write_bytes(b"x")
    if (tmp_path / "REAL").exists():
        pytest.skip("the temporary volume folds case")
    (tmp_path / "REAL").symlink_to("real")
    volume = pathfacts._Volume(tmp_path)
    assert volume.same("real/f.lua", "REAL/f.lua") is False
    assert volume.same("REAL/f.lua", "real/f.lua") is False


# ─── M13-32: the leftovers from M13-23's final review ───────────────────────


def test_a_fold_wider_than_case_is_unknown_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M13-32 item 1. Constructed: the gate's fold takes "Straße" for
    "STRASSE", which no upper/lower-case probe can show a volume does. Even
    on a volume that folds every case and normalization difference (stood in
    here, so this runs on any system), the answer is unknown."""
    (tmp_path / "Ab").mkdir()
    monkeypatch.setattr(pathfacts._Volume, "_folds", lambda self, parts, difference: True)
    volume = pathfacts._Volume(tmp_path)
    assert volume.same("Ab/Straße.lua", "Ab/STRASSE.lua") is None
    assert volume.same("Ab/straße.lua", "Ab/Strasse.lua") is None
    # Differences the probes can show are still answered.
    assert volume.same("Ab/abc.lua", "Ab/ABC.lua") is True
    assert volume.same("Ab/é.lua", "Ab/é.lua") is True


@pytest.mark.parametrize(
    ("case", "normalization", "expected"),
    [
        pytest.param(True, False, False, id="constructed-ntfs-folds-case-only"),
        pytest.param(False, True, False, id="constructed-folds-normalization-only"),
        pytest.param(True, True, True, id="constructed-apfs-folds-both"),
        pytest.param(True, None, None, id="constructed-normalization-unknown"),
        pytest.param(None, None, None, id="constructed-both-unknown"),
    ],
)
def test_case_and_normalization_both_differing_need_both_folded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: bool | None,
    normalization: bool | None,
    expected: bool | None,
) -> None:
    """M13-32 item 1. Constructed: "É" (NFC) against "e" + U+0301 (NFD,
    lower case) differ in case and in normalization. A volume that folds
    case but keeps normalization apart (NTFS) holds them as two names, so
    the case probe alone never confirms them. The volume's answers are stood
    in, so this runs on any system."""
    (tmp_path / "Ab").mkdir()
    answers = {"case": case, "normalization": normalization}
    asked: list[str] = []

    def folds(self: pathfacts._Volume, parts: tuple[str, ...], difference: str) -> bool | None:
        asked.append(difference)
        return answers[difference]

    monkeypatch.setattr(pathfacts._Volume, "_folds", folds)
    volume = pathfacts._Volume(tmp_path)
    upper_nfc = unicodedata.normalize("NFC", "Ab/É.lua")
    lower_nfd = unicodedata.normalize("NFD", "Ab/é.lua")
    assert volume.same(upper_nfc, lower_nfd) is expected
    assert sorted(asked) == ["case", "normalization"]


def test_a_case_variant_never_changes_length() -> None:
    """M13-32 item 1. Constructed names: the case probe tries a spelling that
    differs letter for letter, never "SS" for "ß"."""
    assert pathfacts._case_variant("Ab") == "aB"
    assert pathfacts._case_variant("ß") is None
    assert pathfacts._case_variant("Straße") == "straße"
    assert pathfacts._case_variant("12") is None


def _manifest(root: Path, sid: str, entries: tuple[snapshot.Entry, ...]) -> snapshot.Manifest:
    return snapshot.Manifest(
        id=sid,
        created_at=f"{sid[:4]}-{sid[4:6]}-{sid[6:8]}T00:00:00.000000Z",
        install_root=str(root),
        flavor_folder="0",
        subtrees=("0/1",),
        entries=entries,
    )


class _Listed:
    """A store that lists the given manifests (constructed)."""

    def __init__(self, manifests: tuple[snapshot.Manifest, ...]) -> None:
        self.manifests = manifests

    def list_lenient(self) -> snapshot.SnapshotListing:
        return snapshot.SnapshotListing(manifests=self.manifests, invalid=())


def test_an_older_snapshot_named_for_an_unconfirmed_match_says_so(tmp_path: Path) -> None:
    """M13-32 item 3. Constructed: an older snapshot holds `ABC.lua`, the
    latest holds nothing there, and `abc.lua` is asked about. Neither name is
    on disk and every folder on the way is named in digits, so nothing shows
    whether the drive treats the two as one. The older snapshot is still
    named, with its spelling and the same caveat the held case carries."""
    (tmp_path / "0" / "1" / "2").mkdir(parents=True)
    held = snapshot.Entry(path="0/1/2/ABC.lua", sha256="0" * 64, size=1)
    older = _manifest(tmp_path, "20261001T000000.000000Z-00000001", (held,))
    latest = _manifest(tmp_path, "20261002T000000.000000Z-00000002", ())
    store = _Listed((older, latest))
    facts = pathfacts.snapshot_facts(
        store,  # type: ignore[arg-type]
        tmp_path,
        "0",
        "1/2/abc.lua",
        ["1"],
    )
    assert (facts.snapshot_id, facts.holds) == (latest.id, False)
    assert facts.last_held is not None and facts.last_held.id == older.id
    assert (facts.last_held_path, facts.last_held_confirmed) == ("1/2/ABC.lua", False)
    line = cli._snapshot_line(facts, "WTF/")
    assert line.endswith(
        f"the latest snapshot that holds it is {older.id}, {older.created_at}, as 1/2/ABC.lua, "
        "which could not be confirmed to be this file (neither name is on disk, and nothing "
        "on this drive shows whether it treats the two names as one)"
    )
    # Its exact spelling is confirmed, and the line is the one from before.
    exact = pathfacts.snapshot_facts(
        store,  # type: ignore[arg-type]
        tmp_path,
        "0",
        "1/2/ABC.lua",
        ["1"],
    )
    assert (exact.last_held_path, exact.last_held_confirmed) == ("1/2/ABC.lua", True)
    assert cli._snapshot_line(exact, "WTF/").endswith(
        f"the latest snapshot that holds it is {older.id}, {older.created_at}"
    )


def test_the_unconfirmed_clause_reads_as_the_review_asked() -> None:
    """M13-32 item 2. Constructed facts: the wording of the clause M13-23
    added, and only that clause."""
    facts = pathfacts.SnapshotFacts(
        snapshot_id="20261016T000000.000000Z-00000000",
        created_at="2026-10-16T00:00:00.000000Z",
        label="",
        holds=True,
        path="WTF/ABC.lua",
        confirmed=False,
        live="missing",
        differs=True,
        last_held=None,
        captured=True,
        damaged=0,
        damaged_newer=False,
        error=None,
    )
    assert cli._snapshot_line(facts, "WTF/") == (
        "In the latest snapshot (20261016T000000.000000Z-00000000, "
        "2026-10-16T00:00:00.000000Z): it holds WTF/ABC.lua, which could not be confirmed "
        "to be this file (neither name is on disk, and nothing on this drive shows whether "
        "it treats the two names as one); the file on disk is missing"
    )


CLOUD_PLACEHOLDER = 0x9000001A  # IO_REPARSE_TAG_CLOUD_A: no name-surrogate bit
DEDUPLICATED = 0x80000013  # IO_REPARSE_TAG_DEDUP: no name-surrogate bit
MOUNT_POINT = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT, a junction: name surrogate


class _ReparseStat:
    """An `lstat` result as Windows gives it for a reparse point: the real
    entry's mode, size and identity, with the reparse attribute and tag."""

    def __init__(self, real: os.stat_result, tag: int) -> None:
        self.st_mode = real.st_mode
        self.st_size = real.st_size
        self.st_mtime_ns = real.st_mtime_ns
        self.st_mtime = real.st_mtime
        self.st_dev = real.st_dev
        self.st_ino = real.st_ino
        self.st_file_attributes = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
        self.st_reparse_tag = tag


@pytest.mark.parametrize(
    ("tag", "link"),
    [
        pytest.param(CLOUD_PLACEHOLDER, False, id="constructed-onedrive-placeholder"),
        pytest.param(DEDUPLICATED, False, id="constructed-deduplicated"),
        pytest.param(MOUNT_POINT, True, id="constructed-junction"),
    ],
)
def test_only_a_name_surrogate_reparse_point_is_a_link(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch, tag: int, link: bool
) -> None:
    """M13-32 item 5. Constructed: the account folder on the way to
    `Syndicator.lua` is a Windows reparse point, its attributes stood in so
    this runs on any system. A cloud placeholder or a deduplicated file holds
    real content and is no link; a junction (name surrogate) is one, as
    snapshot, `gaps` and `tree --install` count them."""
    account = flavor / "WTF" / "Account" / ACCOUNT
    real_lstat = Path.lstat

    def lstat(self: Path) -> Any:
        st = real_lstat(self)
        return _ReparseStat(st, tag) if self == account else st

    monkeypatch.setattr(Path, "lstat", lstat)
    folder, is_link, placeholder = cli._path_kind(
        root,
        cli.layout.Layout.for_flavor(install.discover(root).flavors[0], root).classify(SYNDICATOR),
        SYNDICATOR,
    )
    assert (folder, is_link, placeholder) == (False, link, not link)
    report = explained(SYNDICATOR)
    if link:
        assert report.verdict.restorable is None
        assert report.stat is None
    else:
        # Walked and described, but the write gate refuses every reparse
        # point, so nothing promises a write (coordinator ruling on #215).
        assert report.verdict.restorable is False
        assert report.stat is not None and report.stat.kind == "file"


def test_a_file_under_a_onedrive_placeholder_promises_no_write(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M13-32, the ruling on #215. Constructed: the account folder is a
    OneDrive cloud placeholder (attributes stood in, so this runs on any
    system), and `Syndicator.lua` differs from the snapshot's copy. It gets
    the usual words with the placeholder clause, `restorable` False and no
    `snap restore` or `snap create` hint, because `guard` refuses to write
    through any reparse point."""
    ok("snap", "create")
    data = bytearray((flavor / SYNDICATOR).read_bytes())
    data[0] ^= 1
    (flavor / SYNDICATOR).write_bytes(bytes(data))
    account = flavor / "WTF" / "Account" / ACCOUNT
    real_lstat = Path.lstat

    def lstat(self: Path) -> Any:
        st = real_lstat(self)
        return _ReparseStat(st, CLOUD_PLACEHOLDER) if self == account else st

    monkeypatch.setattr(Path, "lstat", lstat)
    report = explained(SYNDICATOR)
    assert report.verdict.level == "gate"
    assert report.verdict.restorable is False
    assert report.verdict.words == (
        "yes, through wowlab's write gate only, with the client closed; a cloud placeholder "
        "or deduplicated file, or inside one: wowlab's write gate does not write through these"
    )
    assert report.snapshot is not None and report.snapshot.live == "differs"
    assert not any(h.argv[:1] == ["snap"] for h in report.hints), [h.command for h in report.hints]
    text = ok("explain", SYNDICATOR).stdout
    assert f"  edit:         {report.verdict.words}\n" in text
    assert "snap restore" not in text
