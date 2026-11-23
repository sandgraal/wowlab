"""scripts/forever_api.py: the addon's API list at the launch build (M21-09).

No test fetches anything (ADR-0012): `fetch` is exercised only with a
replaced `urlopen`. Ketho/BlizzardInterfaceResources' files are not in the
repository (only the names read from them), so the reader is graded on
constructed chunks shaped like them, labelled `constructed`, and the
committed list and README are graded as they are.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "forever_api.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("forever_api", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fa = _load()

# Constructed: the six files' shapes, a few names each.
CONSTRUCTED = {
    "GlobalAPI.lua": (
        b'local GlobalAPI = {\n\t"GetBuildInfo",\n\t"C_Timer.After",\n\t"C_Secrets.HasSecretRestrictions",\n}\n\n'
        b'local LuaAPI = {\n\t"date",\n\t"bit.band",\n}\n\nreturn {GlobalAPI, LuaAPI}\n'
    ),
    "FrameXML.lua": (
        b'local FrameXML = {\n\t"CreateColor",\n\t"AchievementUtil.IsFeatOfStrength",\n}\n\n'
        b'local LoadOnDemand = {\n\t"MacroFrame_Show",\n\t"CreateColor",\n}\n\n'
        b"return {FrameXML, LoadOnDemand}\n"
    ),
    "Frames.lua": (
        b'local Frames = {\n\t"GameTooltip",\n}\n\nlocal LoadOnDemand = {\n\t"MacroFrame",\n}\n\n'
        b"return {Frames, LoadOnDemand}\n"
    ),
    "LuaEnum.lua": (
        b"Enum = {\n\tBindingSet = {\n\t\tDefault = 0,\n\t\tAccount = 1,\n\t},\n\tMask = {\n\t\tA = 0x1,\n\t},\n}\n\n"
        b"Constants = {\n\tMacroConsts = {\n\t\tMAX_ACCOUNT_MACROS = 120,\n\t},\n}\n\n"
        b"LE_EXPANSION_CLASSIC = 0\nNUM_LE_X = -1\n"
    ),
    "Events.lua": (
        b'local Events = {\n\tAddOns = {\n\t\t"ADDON_LOADED",\n\t},\n\tUnit = {\n\t\t"UNIT_NAME_UPDATE",\n\t},\n}\n\n'
        b"return Events\n"
    ),
    "WidgetAPI.lua": (
        b'local WidgetAPI = {\n\tFrame = {\n\t\tinherits = {"Region"},\n\t\tmethods = {\n\t\t\t"RegisterEvent",\n\t\t},\n'
        b'\t\thandlers = {\n\t\t\t"OnEvent",\n\t\t},\n\t},\n\tMaskTexture = {\n\t\tinherits = {},\n\t\tmethods = {\n\t\t},\n\t},\n}\n\n'
        b"return WidgetAPI\n"
    ),
}


def _write(folder: Path, files: dict[str, bytes]) -> dict[str, str]:
    for name, data in files.items():
        (folder / name).write_bytes(data)
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}


@pytest.fixture
def constructed(tmp_path: Path) -> Iterator[tuple[Path, dict[str, str]]]:
    yield tmp_path, _write(tmp_path, CONSTRUCTED)


# --- the reader ---------------------------------------------------------------------


def test_constructed_chunk_reads_locals_and_return() -> None:
    chunk = fa.read_chunk("x.lua", b'local A = {\n\t"x",\n}\nlocal B = {}\nreturn {A, B}\n')
    assert chunk == {"A": ["x"], "B": {}}


def test_constructed_chunk_without_locals_is_plain_assignments() -> None:
    assert fa.read_chunk("x.lua", b"A = 1\nB = { C = 0x2 }\n") == {"A": 1, "B": {"C": 2}}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b'local A = {"x"}\nreturn B\n', "returns ['B'], declared ['A']"),
        (b'local A = {"x"}\nlocal B = {}\nreturn {B, A}\n', "returns ['B', 'A']"),
        (b'local A = {"x"}\n', "returns none of them"),
        (b'local A = {"x"}\nreturn A\nB = 1\n', "after the closing return"),
        (b"local function f() end\nreturn f\n", "does not take"),
        (b"local A\nreturn A\n", "does not take"),
        (b"return {}\n", "does not take"),
    ],
    ids=lambda value: "constructed" if isinstance(value, bytes) else str(value)[:20],
)
def test_constructed_statement_shapes_are_refused(data: bytes, message: str) -> None:
    with pytest.raises(fa.ApiError, match=re.escape(message)):
        fa.read_chunk("x.lua", data)


@pytest.mark.parametrize(
    "data",
    [
        b'local A = {\n\t"x",\n\tos.exit(),\n}\nreturn A\n',
        b"local A = function() end\nreturn A\n",
        b"local A = { B = C }\nreturn A\n",
        b"A = 1 + 2\n",
        b"local A = setmetatable({}, {})\nreturn A\n",
    ],
    ids=[
        "constructed-call",
        "constructed-function",
        "constructed-bare-name",
        "constructed-arith",
        "constructed-metatable",
    ],
)
def test_constructed_code_is_refused_by_luadata_not_run(data: bytes) -> None:
    with pytest.raises(fa.ApiError):
        fa.read_chunk("x.lua", data)


def test_constructed_error_points_at_the_real_line_and_column() -> None:
    """Blanking `local` and `return` keeps offsets: the error names line 3,
    column 2, where the call is in the file as written."""
    with pytest.raises(fa.ApiError, match=r"line 3, column 2"):
        fa.read_chunk("x.lua", b'local A = {\n\t"x",\n\tos.exit(),\n}\nreturn A\n')


# --- the sections -------------------------------------------------------------------


def test_constructed_six_files_give_four_sections(constructed: tuple[Path, dict[str, str]]) -> None:
    folder, _ = constructed
    sections = fa.collect({name: (folder / name).read_bytes() for name in CONSTRUCTED})
    assert sections[fa.GLOBAL] == {
        "GetBuildInfo",
        "C_Timer.After",
        "C_Secrets.HasSecretRestrictions",
        "date",
        "bit.band",
        "CreateColor",
        "AchievementUtil.IsFeatOfStrength",
        "GameTooltip",
        "Enum",
        "Enum.BindingSet",
        "Enum.Mask",
        "Constants",
        "Constants.MacroConsts",
        "LE_EXPANSION_CLASSIC",
        "NUM_LE_X",
    }
    # `CreateColor` is in both FrameXML lists; it counts as always defined.
    assert sections[fa.GLOBAL_LOD] == {"MacroFrame_Show", "MacroFrame"}
    assert sections[fa.EVENT] == {"ADDON_LOADED", "UNIT_NAME_UPDATE"}
    assert sections[fa.WIDGET_METHOD] == {"Frame:RegisterEvent"}


@pytest.mark.parametrize(
    ("file", "data", "message"),
    [
        (
            "Events.lua",
            b'local Events = {\n\tA = {"lower_case"},\n}\nreturn Events\n',
            "not a name",
        ),
        (
            "GlobalAPI.lua",
            b'local GlobalAPI = {"a b"}\nlocal LuaAPI = {}\nreturn {GlobalAPI, LuaAPI}\n',
            "not a name",
        ),
        ("GlobalAPI.lua", b'local GlobalAPI = {"a"}\nreturn GlobalAPI\n', "top-level names"),
        ("LuaEnum.lua", b'Enum = {}\nLE_X = "text"\n', "not a whole number"),
        ("LuaEnum.lua", b"Enum = { A = 1 }\n", "not a table"),
        (
            "WidgetAPI.lua",
            b"local WidgetAPI = { F = { methods = {} } }\nreturn WidgetAPI\n",
            "fields",
        ),
        (
            "Frames.lua",
            b"local Frames = { 1 }\nlocal LoadOnDemand = {}\nreturn {Frames, LoadOnDemand}\n",
            "not a list of strings",
        ),
    ],
    ids=lambda value: "constructed" if isinstance(value, bytes) else str(value)[:24],
)
def test_constructed_shape_changes_are_refused(file: str, data: bytes, message: str) -> None:
    files = dict(CONSTRUCTED)
    files[file] = data
    with pytest.raises(fa.ApiError, match=re.escape(message)):
        fa.collect(files)


def test_constructed_generate_writes_marked_sorted_files(
    constructed: tuple[Path, dict[str, str]],
) -> None:
    folder, pins = constructed
    texts = fa.generate(folder, pins)
    assert set(texts) == {f"{section}.txt" for section in fa.SECTIONS}
    for text in texts.values():
        assert text.startswith(fa.GENERATED_MARK + "\n")
        assert f"# commit: {fa.COMMIT}\n" in text and f"# build: {fa.BUILD}\n" in text
    names = [line for line in texts["global.txt"].splitlines() if line and not line.startswith("#")]
    assert names == sorted(set(names))
    header, sections = fa.parse_list(texts)
    assert header == {"commit": fa.COMMIT, "build": fa.BUILD}
    assert sections[fa.EVENT] == {"ADDON_LOADED", "UNIT_NAME_UPDATE"}


def test_constructed_generate_refuses_bytes_other_than_the_pin(
    constructed: tuple[Path, dict[str, str]],
) -> None:
    folder, pins = constructed
    (folder / "Events.lua").write_bytes(CONSTRUCTED["Events.lua"] + b"\n")
    with pytest.raises(fa.ApiError, match=re.escape("Events.lua: SHA-256")):
        fa.generate(folder, pins)


def test_fetch_refuses_bytes_other_than_the_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No network: `urlopen` is replaced, and a body that is not the pinned
    file is refused before anything is written."""
    asked: list[str] = []

    def fake_urlopen(url: str, timeout: float) -> io.BytesIO:
        asked.append(url)
        return io.BytesIO(b"constructed: not the pinned file")

    monkeypatch.setattr(fa.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(fa.ApiError, match="SHA-256"):
        fa.fetch(tmp_path / "out")
    assert asked == [fa.RAW_URL.format(commit=fa.COMMIT, name=next(iter(fa.PINS)))]
    assert fa.COMMIT in asked[0]
    assert list((tmp_path / "out").iterdir()) == []


# --- the committed list and the README ------------------------------------------------


def _committed() -> dict[str, str]:
    return fa.read_list()


def test_committed_list_is_marked_generated_and_names_the_pin() -> None:
    texts = _committed()
    for file, text in texts.items():
        assert text.startswith(fa.GENERATED_MARK + "\n"), file
        for name, digest in fa.PINS.items():
            assert f"# sha256: {digest}  Resources/{name}\n" in text, (file, name)
        names = [line for line in text.splitlines() if line and not line.startswith("#")]
        assert names == sorted(set(names)), f"{file}: not sorted and unique"
    header, sections = fa.parse_list(texts)
    assert header == {"commit": fa.COMMIT, "build": fa.BUILD}
    assert all(sections.values())


def test_committed_list_files_stay_under_the_size_cap() -> None:
    """The repository refuses an added file over 512 KB (.pre-commit-config.yaml)."""
    for section in fa.SECTIONS:
        assert (fa.OUT / f"{section}.txt").stat().st_size < 480 * 1024, section


def test_readme_agrees_with_the_committed_list() -> None:
    """What `make lint-lua` runs, on the files as committed."""
    problems = fa.check(
        _committed(),
        fa.STD.read_text(encoding="utf-8"),
        fa.README.read_text(encoding="utf-8"),
    )
    assert problems == []


def test_constructed_flipped_baseline_is_reported() -> None:
    readme = fa.README.read_text(encoding="utf-8")
    row = re.search(r"^\| `GetBuildInfo` \|.*\| yes \|$", readme, flags=re.M)
    assert row is not None
    flipped = readme.replace(row.group(0), row.group(0)[: -len("| yes |")] + "| no |")
    problems = fa.check(_committed(), fa.STD.read_text(encoding="utf-8"), flipped)
    assert problems == ["GetBuildInfo: README Baseline 'no'; the list at 1.60.1.70938 says yes"]


def test_constructed_missing_row_commit_and_build_are_reported() -> None:
    readme = fa.README.read_text(encoding="utf-8")
    readme = re.sub(r"^\| `GetBuildInfo` \|.*$\n", "", readme, flags=re.M)
    readme = readme.replace(fa.COMMIT, "<commit>").replace(fa.BUILD, "<build>")
    problems = fa.check(_committed(), fa.STD.read_text(encoding="utf-8"), readme)
    assert problems == [
        "GetBuildInfo: no row in the README API table",
        f"README does not name the list's commit {fa.COMMIT}",
        f"README does not name the list's build {fa.BUILD}",
    ]


def test_namespace_table_is_known_through_its_members() -> None:
    """`C_Secrets` is not a line of the list, only its functions are."""
    texts = _committed()
    assert "\nC_Secrets\n" not in texts["global.txt"]
    assert "\nC_Secrets.HasSecretRestrictions\n" in texts["global.txt"]
    std = "globals:\n  C_Secrets:\n    property: read-only\n"
    readme = f"{fa.COMMIT} {fa.BUILD}\n| `C_Secrets` | x | [verify] | no |\n"
    assert fa.check(texts, std, readme) == [
        "C_Secrets: README Baseline 'no'; the list at 1.60.1.70938 says yes"
    ]


def test_constructed_list_files_that_disagree_are_refused() -> None:
    texts = dict(_committed())
    texts["event.txt"] = texts["event.txt"].replace(fa.BUILD, "1.60.1.1")
    with pytest.raises(fa.ApiError, match="different commits or builds"):
        fa.parse_list(texts)
    texts = dict(_committed())
    texts["event.txt"] = texts["event.txt"].split("\n", 1)[1]
    with pytest.raises(fa.ApiError, match="generated mark"):
        fa.parse_list(texts)


def test_check_command_exits_zero_on_the_repository(capsys: pytest.CaptureFixture[str]) -> None:
    assert fa.main(["check"]) == 0
    assert capsys.readouterr().err == ""
