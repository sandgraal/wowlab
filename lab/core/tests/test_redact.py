"""`wowlab_core.redact` (M13-09, docs/LAB_PLAN.md §15.2).

Every input here is `constructed` (L8): identity strings, home folders in
macOS, Windows and Linux forms, and paths with spaces, planted in text, which
the redactor must leave without. The module is also the scrub tool's source
of identifier patterns; the last tests hold both to that.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import unicodedata
from pathlib import Path

import pytest

from wowlab_core import redact
from wowlab_core.redact import ACCOUNT, BATTLETAG, CHARACTER, EMAIL, GUID, HOME, NAME, REALM

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "lab_capture.py"
SOURCE = Path(redact.__file__).resolve()


@pytest.mark.parametrize(
    ("home", "text", "expected"),
    [
        # macOS, with a space in the user's folder name.
        (
            "/Users/Jaina Proudmoore",
            "Install: /Users/Jaina Proudmoore/Games/World of Warcraft (found by --root)",
            f"Install: {HOME}/Games/World of Warcraft (found by --root)",
        ),
        # Windows, either separator, any casing, and the long-path prefix.
        (
            r"C:\Users\Jaina Proudmoore",
            r"C:\Users\Jaina Proudmoore\AppData\Local\wowlab and c:/users/jaina proudmoore/x "
            r"and \\?\C:\Users\Jaina Proudmoore\Games",
            rf"{HOME}\AppData\Local\wowlab and {HOME}/x and \\?\{HOME}\Games",
        ),
        # Linux, and a Wine prefix inside it.
        (
            "/home/jaina",
            "/home/jaina/.wine/drive_c/Program Files (x86)/World of Warcraft",
            f"{HOME}/.wine/drive_c/Program Files (x86)/World of Warcraft",
        ),
        # A trailing separator on the home folder changes nothing.
        ("/home/jaina/", "/home/jaina/x", f"{HOME}/x"),
    ],
)
def test_constructed_known_home_folder_is_replaced(home: str, text: str, expected: str) -> None:
    assert redact.Redactor(homes=[home])(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/Users/Thrall Durotan/Games/wow", f"{HOME}/Games/wow"),
        (r"C:\Users\Thrall Durotan\Games\wow", rf"{HOME}\Games\wow"),
        (r"D:/Users/thrall/wow", f"{HOME}/wow"),
        ("/home/thrall/wow", f"{HOME}/wow"),
        ("/var/home/thrall/wow", f"/var{HOME}/wow"),
        (r"C:\Documents and Settings\Thrall\wow", rf"{HOME}\wow"),
        ("~/.wine/drive_c/users/thrall/Documents", f"~/.wine/drive_c{HOME}/Documents"),
        # A home folder that ends the text takes the rest of its line with it:
        # over-redaction, never a leak.
        ("not found at /Users/Thrall Durotan (searched)\nnext", f"not found at {HOME}\nnext"),
    ],
)
def test_constructed_any_home_shaped_path_is_replaced_without_knowing_the_home(
    text: str, expected: str
) -> None:
    assert redact.Redactor()(text) == expected


def test_constructed_a_known_home_does_not_claim_a_longer_folder_name() -> None:
    replace = redact.Redactor(homes=["/opt/people/bob"])
    assert replace("/opt/people/bob/x") == f"{HOME}/x"
    assert replace("/opt/people/bobby/x") == "/opt/people/bobby/x"
    # The filesystem root and a bare drive name nobody; they are never replaced.
    assert redact.Redactor(homes=["/", "C:\\"])("/x C:\\y") == "/x C:\\y"


def test_constructed_battletags_emails_and_guids() -> None:
    jaina = "Ja" + chr(0xEF) + "na"  # LATIN SMALL LETTER I WITH DIAERESIS
    text = (
        f"{jaina}#12345 Proudfoot#2468, mail jaina.p+wow@example.co.uk; "
        "Player-1403-0A1B2C3D Guild-1403-00ABCDEF BNetAccount-0-0000AB12 "
        "ClubFinder-1-12AB-34CD player-9-abc"
    )
    out = redact.Redactor()(text)
    assert out == (f"{BATTLETAG} {BATTLETAG}, mail {EMAIL}; {GUID} {GUID} {GUID} {GUID} {GUID}"), (
        out
    )


def test_constructed_battletag_goes_before_a_name_that_is_also_its_name() -> None:
    out = redact.Redactor(characters=["Proudfoot"])("Proudfoot#2468 and Proudfoot")
    assert out == f"{BATTLETAG} and {CHARACTER}"


def test_constructed_names_in_any_casing_and_normal_form() -> None:
    name = "J" + chr(0xE1) + "ina"  # composed: LATIN SMALL LETTER A WITH ACUTE
    decomposed = unicodedata.normalize("NFD", name)
    replace = redact.Redactor(
        accounts=["WOWACCOUNT1"], characters=[name, "Bo"], realms=["Area 52"], names=["Ironpaw"]
    )
    text = (
        f"{name} {name.upper()} {decomposed} WTF/Account/wowaccount1/Area 52/{name}/x "
        "Area52 area-52 Area_52 AREA 52 Bo bo Bob Bo-Area52 xIronpawx"
    )
    assert replace(text) == (
        f"{CHARACTER} {CHARACTER} {CHARACTER} WTF/Account/{ACCOUNT}/{REALM}/{CHARACTER}/x "
        f"{REALM} {REALM} {REALM} {REALM} {CHARACTER} {CHARACTER} Bob {CHARACTER}-{REALM} "
        f"x{NAME}x"
    )


def test_constructed_realm_spellings_follow_realm_forms() -> None:
    replace = redact.Redactor(realms=["Kel'Thuzad"])
    for form in redact.realm_forms("Kel'Thuzad"):
        assert replace(f"<{form}>") == f"<{REALM}>", form


def test_constructed_placeholders_are_never_matched_again() -> None:
    replace = redact.Redactor(characters=["Home", "Realm"], homes=["/Users/home"])
    assert replace("/Users/home/x Home Realm") == f"{HOME}/x {CHARACTER} {CHARACTER}"
    assert replace(replace("/Users/home/x Home")) == replace("/Users/home/x Home")


def test_constructed_bytes_that_were_not_utf8_survive() -> None:
    lone = "folder-" + chr(0xDC80) + " Proudfoot#2468"  # surrogateescape of byte 0x80
    assert redact.Redactor()(lone) == "folder-" + chr(0xDC80) + f" {BATTLETAG}"


def test_redact_imports_the_standard_library_only() -> None:
    """scripts/lab_capture.py loads this file by path and must run on the
    standard library alone."""
    imported: set[str] = set()
    for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports: the file is also loaded by path"
            if node.module:
                imported.add(node.module.split(".")[0])
    assert imported <= sys.stdlib_module_names, imported - sys.stdlib_module_names


def test_the_scrub_tool_takes_its_identifier_patterns_from_the_package() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("lab_capture_for_redact_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    assert Path(module.REDACT_SOURCE).resolve() == SOURCE
    for name in (
        "IDENTITY_CVARS",
        "EMAIL_RE",
        "BATTLETAG_RE",
        "GUID_RE",
        "ACCOUNT_GUID_RE",
        "GUILD_GUID_RE",
        "CLUB_GUID_RE",
        "GUID_NAME_RE",
        "EMBEDDED_MIN_CHARS",
    ):
        mine, theirs = getattr(redact, name), getattr(module, name)
        assert getattr(mine, "pattern", mine) == getattr(theirs, "pattern", theirs), name
        assert f"\n{name} = " not in source and f"\n{name}: " in source, (
            name
        )  # imported, not redefined
    assert module._realm_forms("Area 52") == redact.realm_forms("Area 52")
    assert "def _realm_forms" not in source
