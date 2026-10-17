"""The file map's `addon-executable` row against the write gate (M13-28).

An executable, script or bundle under `Interface/AddOns/` used to match the
`addon` row, so `explain` called it editable through the write gate, which
never writes one (L7). The `addon-executable` row comes first and must match
exactly the paths `guard._check_rel` refuses as executables: any component
for which `guard._is_executable` is true. The parity table below is
hand-written plus generated from guard's own suffix list when this module
loads, so a suffix guard gains is exercised without editing this file.

Every path here is constructed (L8: boundary cases, labelled `constructed`).
The `explain` tests copy the captured tree (`fixtures/macos/`) into
`tmp_path`, as `test_cli_explain_detail.py` does, add constructed files to
one addon folder, redirect the user data directory and replace the process
table. Nothing reads or writes a real install.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, filemap, guard, install

ROW = "addon-executable"
ADDON = "Interface/AddOns/DBM-Brawlers"  # an addon folder in the capture

# Paths relative to the flavor folder, all under `Interface/AddOns/`.
HAND_WRITTEN = [
    # The ticket's own examples.
    f"{ADDON}/bin/helper.exe",
    f"{ADDON}/run.bat",
    f"{ADDON}/Helper.app/Contents/MacOS/x",
    f"{ADDON}/lib.bundle/y",
    f"{ADDON}/libfoo.so.1",
    # Letter case, depth, and where in the path the executable part sits.
    f"{ADDON}/HELPER.EXE",
    f"{ADDON}/tools/Run.Cmd",
    f"{ADDON}/a/b/c/d/e/f/x.dll",
    f"{ADDON}/x.dylib",
    f"{ADDON}/Lib.Framework/Versions/A/Lib",
    f"{ADDON}/Lib.framework/",
    f"{ADDON}/Helper.app/",
    f"{ADDON}/example.com/readme.txt",
    "Interface/AddOns/Foo.app/Foo.toc",
    "Interface/AddOns/Foo.app/",
    "Interface/AddOns/run.bat",
    "Interface/AddOns/Blizzard_Foo/x.exe",
    "Interface/AddOns/Blizzard_Foo/Blizzard_Foo.toc",
    # Versioned shared libraries, and names that only look like one.
    f"{ADDON}/libfoo.so",
    f"{ADDON}/libfoo.so.1.2.3",
    f"{ADDON}/libfoo.SO.10",
    f"{ADDON}/libfoo.so.1/x.lua",
    f"{ADDON}/libfoo.so.{chr(0x0661)}",  # an Arabic-Indic digit: guard's `\d` takes it
    f"{ADDON}/libfoo.so.1a",
    f"{ADDON}/libfoo.so.x",
    f"{ADDON}/libfoo.so1",
    f"{ADDON}/libfoo.so.1.lua",
    f"{ADDON}/libfoo.so..1",
    # Case folding beyond ASCII: U+017F folds to `s`, U+212A to `k`.
    f"{ADDON}/x.{chr(0x017F)}h",
    f"{ADDON}/x.ln{chr(0x212A)}",
    f"{ADDON}/x.{chr(0xFF45)}{chr(0xFF58)}{chr(0xFF45)}",  # fullwidth letters do not fold to ASCII
    f"{ADDON}/x.{chr(0x00DF)}h",
    # Ordinary addon contents, and suffixes that only contain a listed one.
    f"{ADDON}/{ADDON.rsplit('/', 1)[-1]}.toc",
    f"{ADDON}/Libs/x.lua",
    f"{ADDON}/media/x.tga",
    f"{ADDON}/x.exe.txt",
    f"{ADDON}/x.json",
    f"{ADDON}/x.jsx",
    f"{ADDON}/x.shader",
    f"{ADDON}/exe",
    f"{ADDON}/app/x.lua",
    f"{ADDON}/x.lnkx",
    f"{ADDON}/xexe",
    # Rows ahead of this one: file-browser metadata.
    f"{ADDON}/._run.bat",
    f"{ADDON}/.DS_Store",
    # Folders.
    f"{ADDON}/",
    f"{ADDON}/bin/",
    "Interface/AddOns/",
]


def _from_guard_table() -> list[str]:
    """One file, one folder and one path inside a folder per guard suffix,
    in two letter cases, plus the near misses either side of each suffix."""
    out: list[str] = []
    for suffix in guard._EXECUTABLE_SUFFIXES:
        for s in (suffix, suffix.upper()):
            out.append(f"{ADDON}/bin/a b{s}")
            out.append(f"{ADDON}/Thing{s}/")
            out.append(f"{ADDON}/Thing{s}/Contents/x.lua")
        out.append(f"{ADDON}/a{suffix}x")
        out.append(f"{ADDON}/a{suffix[:-1]}")
        out.append(f"{ADDON}/{suffix[1:]}")
    return out


PATHS = list(dict.fromkeys([*HAND_WRITTEN, *_from_guard_table()]))


def _parts(path: str) -> tuple[str, ...]:
    return tuple(p for p in path.split("/") if p)


def _guard_says_executable(path: str) -> bool:
    return any(guard._is_executable(p) for p in _parts(path))


def test_the_copied_rule_equals_guards() -> None:
    assert filemap._EXECUTABLE_SUFFIXES == guard._EXECUTABLE_SUFFIXES
    assert filemap._VERSIONED_SHARED_LIBRARY.pattern == guard._VERSIONED_SHARED_LIBRARY.pattern
    assert filemap._VERSIONED_SHARED_LIBRARY.flags == guard._VERSIONED_SHARED_LIBRARY.flags


def test_the_table_has_both_answers() -> None:
    executable = [p for p in PATHS if _guard_says_executable(p)]
    assert len(executable) > 100
    assert len(PATHS) - len(executable) > 50


@pytest.mark.parametrize("path", PATHS, ids=lambda p: f"constructed-{p!r}")
def test_executable_row_agrees_with_the_write_gate(path: str) -> None:
    fm = filemap.load()
    row = fm.entry(ROW)
    parts = _parts(path)
    expected = _guard_says_executable(path)
    for kind in ("file", "dir", "any"):
        assert row.matches(parts, kind) is expected, (path, kind)
        found = fm.classify(parts, "flavor", kind)
        if expected:
            # This row, or a `no` row ahead of it (file-browser metadata).
            assert found is not None and found.edit == "no", (path, kind, found)
            assert not found.gated
        else:
            assert found is None or found.id != ROW, (path, kind)


@pytest.mark.parametrize("path", PATHS, ids=lambda p: f"constructed-{p!r}")
def test_executable_row_agrees_with_the_gates_path_check(path: str) -> None:
    """The same, judged by `guard._check_rel` itself: on a path the gate
    would otherwise accept as a file, it refuses exactly the ones the row
    matches."""
    rel = path.rstrip("/")
    try:
        guard._check_rel(rel, allow_executable=True)
    except guard.PathNotAllowedError:
        return  # refused for another reason; the executable rule is not what decides
    try:
        guard._check_rel(rel)
    except guard.PathNotAllowedError:
        refused = True
    else:
        refused = False
    assert filemap.load().entry(ROW).matches(_parts(rel), "file") is refused


def test_the_row_comes_before_the_addon_row() -> None:
    ids = [e.id for e in filemap.load().entries]
    assert ids.index(ROW) < ids.index("addon")
    assert ids.index(ROW) < ids.index("blizzard-addon-export")


def test_the_row_does_not_reach_outside_the_addons_folder() -> None:
    row = filemap.load().entry(ROW)
    for path in ("Wow.exe", "Interface/Foo.exe", "WTF/x.bat", "Fonts/x.dll", "Interface/AddOns"):
        assert not row.matches(_parts(path), "any"), path


def test_gaps_still_need_not_enter_an_addon_folder() -> None:
    """The row has a `**` ahead of its last segment, so it never claims to
    cover a folder; the `addon` row still covers each addon folder."""
    fm = filemap.load()
    assert fm.covers(_parts(ADDON), "flavor")
    assert fm.covers(("Interface", "AddOns", "Foo.app"), "flavor")


# ─── explain ─────────────────────────────────────────────────────────────────

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)

runner = CliRunner()


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    assert (root / FLAVOR / ADDON).is_dir()
    return root / FLAVOR


def _explain(rel: str) -> cli.ExplainReport:
    result = runner.invoke(cli.app, ["explain", rel, "--json"])
    assert result.exit_code == 0, (rel, result.stdout, result.stderr, result.exception)
    return cli.ExplainReport.model_validate_json(result.stdout)


@pytest.mark.parametrize(
    "name",
    ["bin/helper.exe", "run.bat", "tools/Build.CMD", "lib/x.dll", "lib/libx.dylib"],
    ids=lambda n: f"constructed-{n}",
)
def test_explain_says_no_for_an_executable_in_an_addon(flavor: Path, name: str) -> None:
    path = flavor / ADDON / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ constructed, not a real executable\n")
    rel = f"{ADDON}/{name}"
    report = _explain(rel)
    assert isinstance(report.result, cli.layout.Classified)
    assert report.result.entry.id == ROW
    assert report.verdict.level == "no"
    assert report.verdict.restorable is False
    assert report.verdict.words == "no; wowlab never writes it"
    text = runner.invoke(cli.app, ["explain", rel])
    assert text.exit_code == 0, text.stdout
    assert "  edit:         no; wowlab never writes it\n" in text.stdout
    assert "write gate" not in text.stdout.split("  edit:")[1].splitlines()[0]


def test_explain_says_no_inside_an_app_bundle(flavor: Path) -> None:
    inner = flavor / ADDON / "Helper.app" / "Contents" / "MacOS" / "Helper"
    inner.parent.mkdir(parents=True)
    inner.write_bytes(b"constructed\n")
    for rel in (f"{ADDON}/Helper.app", f"{ADDON}/Helper.app/Contents/MacOS/Helper"):
        report = _explain(rel)
        assert isinstance(report.result, cli.layout.Classified)
        assert report.result.entry.id == ROW, rel
        assert report.verdict.level == "no", rel


def test_explain_still_says_gate_for_an_ordinary_addon_file(flavor: Path) -> None:
    report = _explain(f"{ADDON}/DBM-Brawlers.toc")
    assert isinstance(report.result, cli.layout.Classified)
    assert report.result.entry.id == "addon"
    assert report.verdict.level == "gate"
