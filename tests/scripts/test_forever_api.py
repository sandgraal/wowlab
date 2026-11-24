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


@pytest.mark.live
def test_live_fetch_and_generate_reproduce_the_committed_list(tmp_path: Path) -> None:
    """Owner-run, by hand, never in CI (ADR-0012; `-m 'not live'` is the
    default): download every pinned file from GitHub into a temp folder,
    generate, and compare with the committed list and the committed client
    lists (M22-18) byte for byte.

        uv run pytest tests/scripts/test_forever_api.py -m live -p no:cacheprovider
    """
    fa.fetch(tmp_path)
    assert fa.generate(tmp_path) == _committed()
    assert fa.generate_lists(tmp_path) == fa.read_lists()


def test_the_addon_s_own_globals_are_the_ones_it_writes_whole() -> None:
    """Four names, none of them the client's: the SavedVariables and the slash
    command. (Read from the property, so this file names none of them.)"""
    std = fa.STD.read_text(encoding="utf-8")
    own = set(fa.std_globals(std)) - fa.client_globals(std)
    assert len(own) == 4
    assert all(name.startswith(("WowLab", "SLASH_WOWLAB")) for name in own)


def test_constructed_std_reads_each_name_s_property() -> None:
    std = (
        "---\nbase: lua51\nglobals:\n  Own:\n    property: full-write\n"
        "  C_X.Y:\n    property: read-only\n  F:\n    args: []\n"
    )
    assert fa.std_globals(std) == {"Own": "full-write", "C_X.Y": "read-only", "F": None}
    assert fa.client_globals(std) == {"C_X.Y", "F"}


# --- the client's lists (M22-18) -----------------------------------------------------

# Constructed: the pinned wow-ui-source files' shapes and Ketho's GlobalStrings,
# a few lines each, with one of each thing the reader leaves out.
UI_CONSTRUCTED: dict[str, bytes] = {
    fa.VERSION_FILE: b"1.60.1.71037",
    fa.SLASH_FILES[0]: (
        b'SLASH_COMMAND = {\n\tCAST = "CAST",\n\tDUMP = "DUMP",\n\tORPHAN = "ORPHAN",\n};\n\n'
        b"function SlashCommandUtil.CheckAddSlashCommand(commandKey, category, callback)\n"
        b"\tSlashCommandUtil.CheckAddSlashCommand(commandKey, category, callback);\nend\n"
        b"SlashCommandUtil.CheckAddSecureSlashCommand(SLASH_COMMAND.CAST, "
        b"SLASH_COMMAND_CATEGORY.COMBAT, function(msg)\nend);\n"
        b"SlashCommandUtil.CheckAddSlashCommand(SLASH_COMMAND.ORPHAN, "
        b"SLASH_COMMAND_CATEGORY.X, function(msg)\nend);\n"
        b"SlashCommandUtil.CheckAddSlashCommand(SLASH_COMMAND.MISSING, "
        b"SLASH_COMMAND_CATEGORY.X, function(msg)\nend);\n"
        b'SLASH_LOCAL1 = "/local";\n'
    ),
    fa.SLASH_FILES[1]: (
        b"SlashCommandUtil.CheckAddSlashCommand(SLASH_COMMAND.DUMP, "
        b"SLASH_COMMAND_CATEGORY.DEBUG_COMMAND, function(msg)\nend);\n"
        b"local add = SlashCommandUtil.CheckAddSlashCommand;\n"
    ),
    fa.REGISTRY_FILE: b'local cmdString = _G["SLASH_"..index..i];\n',
    fa.CHAT_CONSTANTS_FILE: b'\t\t\t\t{ Name = "MaxChatChannels", Type = "number", Value = 2 },\n',
    fa.ENCODING_FILE: (
        b"local EncodingUtilConstants =\n{\n\tTables =\n\t{\n\t\t{\n"
        b'\t\t\tName = "EncodingLimits",\n\t\t\tType = "Constants",\n\t\t\tValues =\n\t\t\t{\n'
        b'\t\t\t\t{ Name = "EncodingStackSizeLimit", Type = "number", Value = 100 },\n'
        b"\t\t\t},\n\t\t},\n"
        b'\t\t{ Name = "Other", Type = "Enumeration", Fields = {} },\n'
        b"\t},\n\tPredicates =\n\t{\n\t},\n};\n\n"
        b"APIDocumentation:AddDocumentationTable(EncodingUtilConstants);"
    ),
    fa.BINDING_FILES[0]: (
        b"<Bindings>\n"
        b'\t<!-- <Binding name="GONE" category="BINDING_HEADER_MOVEMENT"></Binding> -->\n'
        b'\t<Binding name="MOVEFORWARD" runOnUp="true" category="BINDING_HEADER_MOVEMENT">\n'
        b'\t\tif ( keystate == "down" ) then MoveForwardStart(); end\n\t</Binding>\n'
        b'\t<Binding name="NOLABEL" header="MISC"/>\n'
        b'\t<Binding name="Open Report" category="PTR"/>\n'
        b'\t<ModifiedClick action="SELFCAST" default="ALT"/>\n'
        b"</Bindings>\n"
    ),
    fa.BINDING_FILES[1]: (
        b'<Bindings>\n\t<Binding name="MOVEFORWARD" category="Other Words"/>\n'
        b'\t<Binding name="PADBUTTON" category="Pad Buttons"/>\n</Bindings>\n'
    ),
    **dict.fromkeys(fa.BINDING_FILES[2:], b"<Bindings>\n</Bindings>\n"),
    fa.KEYBINDINGS_FILE: b'<Ui>\n\t<Frame name="SettingsKeybindingSpacerTemplate"/>\n</Ui>\n',
}
STRINGS_CONSTRUCTED: dict[str, bytes] = {
    "enUS.lua": (
        b'_G["101_Name"] = "x";\n'
        b'SLASH_CAST1 = "/cast";\nSLASH_CAST2 = "/spell";\nSLASH_DUMP1 = "/dump";\n'
        b'SLASH_SAY1 = "/s";\nSLASH_STOPWATCH_PARAM_PLAY1 = "play";\n'
        b'EMOTE1_CMD1 = "/agree";\nEMOTE1_TOKEN = "AGREE";\n'
        b'BINDING_NAME_MOVEFORWARD = "Move \\"Forward\\"";\n'
        b'BINDING_HEADER_MOVEMENT = "Movement Keys";\n'
        b"NOT_A_STRING = 1;\n"
    ),
    "deDE.lua": b'SLASH_CAST3 = "/wirken";\nSLASH_COUNTDOWN2 = "/compte \xc3\xa0 rebours";\n',
}


def _pins(files: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}


@pytest.fixture
def ui_constructed(tmp_path: Path) -> tuple[Path, dict[str, str], dict[str, str]]:
    for path, data in UI_CONSTRUCTED.items():
        (tmp_path / fa.UI_DIR / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / fa.UI_DIR / path).write_bytes(data)
    (tmp_path / fa.STRINGS_DIR).mkdir()
    for name, data in STRINGS_CONSTRUCTED.items():
        (tmp_path / fa.STRINGS_DIR / name).write_bytes(data)
    return tmp_path, _pins(UI_CONSTRUCTED), _pins(STRINGS_CONSTRUCTED)


def test_constructed_slash_list_joins_registrations_strings_emotes_and_channels() -> None:
    slash = fa.collect_slash(UI_CONSTRUCTED, STRINGS_CONSTRUCTED)
    assert slash.entries == {
        ("/cast", "registered", "CAST"): {"enUS"},
        ("/spell", "registered", "CAST"): {"enUS"},
        ("/wirken", "registered", "CAST"): {"deDE"},
        ("/dump", "registered", "DUMP"): {"enUS"},
        ("/s", "named", "SAY"): {"enUS"},
        ("/local", "named", "LOCAL"): {"*"},  # the code's own string: every language's
        ("/agree", "emote", "AGREE"): {"enUS"},
        ("/1", "channel", "CHANNEL"): {"*"},
        ("/2", "channel", "CHANNEL"): {"*"},
    }
    assert slash.languages == {"deDE", "enUS"}
    # One line per (text, source, key), with its languages; `*` for every one.
    assert slash.lines()[:3] == [
        "/1\tchannel\tCHANNEL\t*",
        "/2\tchannel\tCHANNEL\t*",
        "/agree\temote\tAGREE\tenUS",
    ]
    slash.add("/cast", "registered", "CAST", "deDE")
    slash.add("/cast", "named", "OTHER", "deDE")
    assert [line for line in slash.lines() if line.startswith("/cast\t")] == [
        "/cast\tregistered\tCAST\t*",
        "/cast\tnamed\tOTHER\tdeDE",
    ]
    # Everything left out is said, with where it is.
    assert slash.not_read == [
        f"{fa.SLASH_FILES[0]} line 14: registers SLASH_COMMAND.MISSING, which the table does "
        "not hold",
        f"{fa.SLASH_FILES[1]} line 3: local add = SlashCommandUtil.CheckAddSlashCommand;",
        'GlobalStrings/enUS.lua: 1 lines not of the form NAME = "text";',
        "GlobalStrings/deDE.lua: SLASH_COUNTDOWN2 = '/compte à rebours', not one word",
        "1 strings named like a command whose text has no leading / (a command's parameters, "
        "such as the stopwatch's), left out",
        "ORPHAN: registered, and no client string read names a text for it",
    ]


def test_constructed_registry_that_no_longer_looks_up_slash_strings_is_refused() -> None:
    files = dict(UI_CONSTRUCTED)
    files[fa.REGISTRY_FILE] = b"local cmd = hash[name];\n"
    with pytest.raises(fa.ApiError, match="no longer looks a command up"):
        fa.collect_slash(files, STRINGS_CONSTRUCTED)


def test_constructed_bindings_keep_the_first_file_and_say_what_is_left_out() -> None:
    english, _ = fa.read_strings("enUS.lua", STRINGS_CONSTRUCTED["enUS.lua"])
    rows, not_read = fa.collect_bindings(UI_CONSTRUCTED, english)
    got = [(r.name, r.category, r.category_label, r.label, r.file) for r in rows]
    assert got == [
        (
            "MOVEFORWARD",
            "BINDING_HEADER_MOVEMENT",
            "Movement Keys",
            'Move "Forward"',
            "Blizzard_FrameXML/Bindings_Camelot.xml",
        ),
        ("NOLABEL", "", "", "", "Blizzard_FrameXML/Bindings_Camelot.xml"),
        ("PADBUTTON", "Pad Buttons", "Pad Buttons", "", "Blizzard_Commentator/Bindings.xml"),
    ]
    assert not_read == [
        "Blizzard_SettingsDefinitions_Frame/Keybindings.xml: frame templates only, no binding",
        "Blizzard_FrameXML/Bindings_Camelot.xml: 1 bindings inside a comment, left out",
        "Blizzard_FrameXML/Bindings_Camelot.xml: 1 ModifiedClick lines (modified clicks, not keys)",
        "Blizzard_FrameXML/Bindings_Camelot.xml: a binding named 'Open Report', not a plain "
        "command name",
        "2 commands with no English BINDING_NAME string (label empty)",
    ]


@pytest.mark.parametrize(
    ("file", "data", "message"),
    [
        (fa.BINDING_FILES[0], b"<Bindings><Script>x</Script></Bindings>", "does not take"),
        (fa.KEYBINDINGS_FILE, b'<Ui><Binding name="X"/></Ui>', "now holds a Binding"),
    ],
    ids=["constructed-element", "constructed-keybindings"],
)
def test_constructed_binding_file_shapes_are_refused(file: str, data: bytes, message: str) -> None:
    files = dict(UI_CONSTRUCTED)
    files[file] = data
    with pytest.raises(fa.ApiError, match=message):
        fa.collect_bindings(files, {})


def test_constructed_limits_are_read_as_data() -> None:
    assert fa.collect_limits(UI_CONSTRUCTED) == [("EncodingStackSizeLimit", 100, "number")]


_CLOSE = b"APIDocumentation:AddDocumentationTable(A);"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"local A =\n{\n\tB = 1,\n};\n\nAPIDocumentation:AddDocumentationTable(C);", "names"),
        (b"local A =\n{\n};\n", "not one local table"),
        (b"local A =\n{\n};\n" + _CLOSE + b"\nx = 1\n", "after"),
        (b"local A =\n{\n\tB = os.exit(),\n};\n" + _CLOSE, "line 3"),
        (b"local A =\n{\n\tB = FULLNAME_MAX_CHAR,\n};\n" + _CLOSE, "line 3"),
    ],
    ids=[
        "constructed-other-name",
        "constructed-no-call",
        "constructed-after",
        "constructed-call",
        "constructed-bare-name",
    ],
)
def test_constructed_documentation_that_is_not_data_is_refused(data: bytes, message: str) -> None:
    with pytest.raises(fa.ApiError, match=message):
        fa.read_documentation("x.lua", data)


@pytest.mark.parametrize(
    ("body", "text"),
    [('a\\"b', 'a"b'), ("a\\\\b", "a\\b"), ("\\65", "A"), ("\\200", None), ("\\z", None)],
    ids=[
        "constructed-quote",
        "constructed-backslash",
        "constructed-decimal",
        "constructed-byte",
        "constructed-z",
    ],
)
def test_constructed_escapes(body: str, text: str | None) -> None:
    assert fa.unescape(body) == text


def test_constructed_generate_lists_writes_three_marked_files(
    ui_constructed: tuple[Path, dict[str, str], dict[str, str]],
) -> None:
    folder, ui_pins, string_pins = ui_constructed
    texts = fa.generate_lists(folder, ui_pins, string_pins)
    assert set(texts) == set(fa.LIST_FILES)
    assert fa.check_lists(texts) == []
    slash = texts[fa.SLASH_LIST]
    assert "\n/agree\temote\tAGREE\tenUS\n" in slash and "# not read: ORPHAN: registered" in slash
    assert f"# sha256: {string_pins['deDE.lua']}  GlobalStrings/deDE.lua\n" in slash
    assert texts[fa.LIMIT_LIST].endswith("\nEncodingStackSizeLimit\t100\tnumber\n")
    # A file other than its pin, and a version other than the pinned build, are refused.
    (folder / fa.UI_DIR / fa.VERSION_FILE).write_bytes(b"1.60.1.1")
    with pytest.raises(fa.ApiError, match="SHA-256"):
        fa.generate_lists(folder, ui_pins, string_pins)
    ui_pins[fa.VERSION_FILE] = hashlib.sha256(b"1.60.1.1").hexdigest()
    with pytest.raises(fa.ApiError, match="not the pinned build"):
        fa.generate_lists(folder, ui_pins, string_pins)


def test_committed_client_lists_pass_check_and_name_the_pins() -> None:
    texts = fa.read_lists()
    assert fa.check_lists(texts) == []
    for file, text in texts.items():
        assert text.startswith(fa.GENERATED_MARK + "\n"), file
        assert f"# ui-commit: {fa.UI_COMMIT}\n" in text and f"# ui-build: {fa.UI_BUILD}\n" in text
        assert (fa.LISTS / file).stat().st_size < 480 * 1024, file
    for path, digest in fa.UI_PINS.items():
        if path.endswith(".xml"):
            assert f"# sha256: {digest}  {path}\n" in texts[fa.BINDING_LIST], path


@pytest.mark.parametrize(
    ("file", "old", "new", "problem"),
    [
        (fa.SLASH_LIST, "/cast\tregistered", "/cast\tsomewhere", "the source 'somewhere'"),
        (fa.SLASH_LIST, "\n/cast\t", "\ncast\t", "is not a slash command text"),
        (fa.SLASH_LIST, "\tCAST\t*\n", "\tCAST\tGerman\n", "are not language codes"),
        (fa.BINDING_LIST, "\nTOGGLEWORLDMAP\t", "\nToggleWorldMap\t", "not a command name"),
        (fa.LIMIT_LIST, "\t100\t", "\tmany\t", "is not a whole number"),
        (fa.LIMIT_LIST, f"# ui-build: {fa.UI_BUILD}", "# ui-build: 1.60.1.1", "ui-build"),
        (fa.BINDING_LIST, fa.GENERATED_MARK, "# edited", "not the generated mark"),
    ],
    ids=[
        "constructed-source",
        "constructed-text",
        "constructed-languages",
        "constructed-name",
        "constructed-value",
        "constructed-build",
        "constructed-mark",
    ],
)
def test_constructed_edits_to_the_client_lists_are_reported(
    file: str, old: str, new: str, problem: str
) -> None:
    texts = dict(fa.read_lists())
    assert old in texts[file]
    texts[file] = texts[file].replace(old, new, 1)
    problems = fa.check_lists(texts)
    assert any(problem in p for p in problems), problems
