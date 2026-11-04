"""The Wellspring fonts in `wowlab ui`'s own style (M16-51, ADR-0035).

Twelve WOFF2 files and two SIL Open Font License texts live in
`wowlab_core/ui/templates/fonts/`, copied unchanged from the URLs of
ADR-0035's table and its amendment of 2026-10-26. The sizes and SHA-256 of
all fourteen are held here, copied from the ADR, and nothing else may sit in
the folder. The served shell carries one `@font-face` per font file, its
bytes as a base64 `data:` URL after `page.css` and the colour rules, and the
CSP gains exactly `font-src data:`. The wheel and the sdist carry the
fourteen files byte for byte.

`scripts/check_fonts.py` compares the same pins with the bytes the URLs
serve; it is run by hand, never here (ADR-0012). The test below only checks
that its pins are these.

The page is served by the real server; HOME and the user data folders point
into `tmp_path`, and nothing reads or writes an install.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import importlib.util
import re
import tarfile
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import lookspage, ui

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / "lab" / "core" / "src" / "wowlab_core" / "ui" / "templates"
FONTS = TEMPLATES / "fonts"

# ADR-0035's table, in its order (file names from the amendment of 2026-10-26):
# file -> (family, style, weight, subset, bytes, SHA-256).
FONT_PINS: dict[str, tuple[str, str, str, str, int, str]] = {
    "alegreya-latin.woff2": (
        "Alegreya",
        "normal",
        "400 900",
        "latin",
        43476,
        "2431eca3ed8ed66e713cf7093d170d77a083a793a5f19a615506d63770abd973",
    ),
    "alegreya-latin-ext.woff2": (
        "Alegreya",
        "normal",
        "400 900",
        "latin-ext",
        37856,
        "16b24f84da5ac12dc79ea1060e3fd4e299f19146f607021f81bcf0b8123fe5bd",
    ),
    "alegreya-italic-latin.woff2": (
        "Alegreya",
        "italic",
        "400 900",
        "latin",
        44456,
        "1846c307495f922d3fc00ce3c32aaaba8eefc7c348425a8244a7ffc63ddc0993",
    ),
    "alegreya-italic-latin-ext.woff2": (
        "Alegreya",
        "italic",
        "400 900",
        "latin-ext",
        38316,
        "3b6ec2f8e48aff730aaebeb52423ba2f7967c5b80b1727907e4b1154786fd54c",
    ),
    "alegreya-sc-500-latin.woff2": (
        "Alegreya SC",
        "normal",
        "500",
        "latin",
        30880,
        "98b4a138b6117d621a122a7c8590aa945720e4953d926b921a61e0d3f96a02c9",
    ),
    "alegreya-sc-500-latin-ext.woff2": (
        "Alegreya SC",
        "normal",
        "500",
        "latin-ext",
        31944,
        "fae2927855146328875e379a3f0dcd5ab26629413347838b32190ea441b1a4b7",
    ),
    "alegreya-sc-700-latin.woff2": (
        "Alegreya SC",
        "normal",
        "700",
        "latin",
        30660,
        "988821cdcc03a1c0f7587143e4d921582595c5bcd803061863f7c3be7dd0d4b8",
    ),
    "alegreya-sc-700-latin-ext.woff2": (
        "Alegreya SC",
        "normal",
        "700",
        "latin-ext",
        31656,
        "42e3b3a35260c3626cb1e5fb71a134da3e856a8820d4ab757261fc383aa762f5",
    ),
    "ibm-plex-mono-400-latin.woff2": (
        "IBM Plex Mono",
        "normal",
        "400",
        "latin",
        14708,
        "08949f728dc52d528e69b1667d15c89a5686a4ee9a296ff90983985f99c380f7",
    ),
    "ibm-plex-mono-400-latin-ext.woff2": (
        "IBM Plex Mono",
        "normal",
        "400",
        "latin-ext",
        13348,
        "6bc0f226a5b7884a8170e3f62c63d7675609d4631bdc5931b5cdab81821f00eb",
    ),
    "ibm-plex-mono-500-latin.woff2": (
        "IBM Plex Mono",
        "normal",
        "500",
        "latin",
        14888,
        "01d285447409c8a588692162439a038b8cbd7871309ee20267b0d2d91c6e8e22",
    ),
    "ibm-plex-mono-500-latin-ext.woff2": (
        "IBM Plex Mono",
        "normal",
        "500",
        "latin-ext",
        13432,
        "6bb06407c97584b0867a959e05e8874693bfeb8c317de190811c51598f2d99ea",
    ),
}
# The amendment's licence pins: file -> (bytes, SHA-256).
LICENCE_PINS: dict[str, tuple[int, str]] = {
    "OFL-Alegreya.txt": (4395, "f6f60d5d4cf4f4b1fc4e41353c897a2f5a16e6396c0cd8fa8bdfd2f4586a9a68"),
    "OFL-IBMPlexMono.txt": (
        4456,
        "7e6b2818edbd8f6a01ae80641cc8f16a51080d08fb4e532be3a0b6f74adb07da",
    ),
}
PINNED_FONT_SHA256 = frozenset(pin[5] for pin in FONT_PINS.values())

# Google's stylesheet's ranges for the two subsets, as served for these
# families on 2026-10-27 (css2 API, a WOFF2-capable user agent).
RANGES = {
    "latin": (
        "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, "
        "U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, "
        "U+FEFF, U+FFFD"
    ),
    "latin-ext": (
        "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, "
        "U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C4, "
        "U+2113, U+2C60-2C7F, U+A720-A7FF"
    ),
}

# One served `@font-face`, whole: a family of the table, a style, a weight,
# one `src` that is a base64 WOFF2 `data:` URL and nothing else, and a range
# written only with `U+`, hex digits, commas, spaces and dashes.
FONT_FACE = re.compile(
    r"@font-face \{"
    r' font-family: "(Alegreya|Alegreya SC|IBM Plex Mono)";'
    r" font-style: (normal|italic);"
    r" font-weight: ([0-9]{3}(?: [0-9]{3})?);"
    r' src: url\(data:font/woff2;base64,([A-Za-z0-9+/]+={0,2})\) format\("woff2"\);'
    r" unicode-range: ([U+0-9A-F, -]+); \}"
)


def without_font_faces(body: str) -> str:
    """`body` with its `@font-face` rules taken out, after checking they are
    exactly twelve whole rules of `FONT_FACE`, each with one `data:` URL that
    decodes to one of the twelve pinned files, each file once. What is left
    is graded as if the fonts were not there (`test_ui_explorer.py`)."""
    assert body.lower().count("@font-face") == 12
    found = list(FONT_FACE.finditer(body))
    assert len(found) == 12
    digests = [
        hashlib.sha256(base64.b64decode(m.group(4), validate=True)).hexdigest() for m in found
    ]
    assert sorted(digests) == sorted(PINNED_FONT_SHA256)
    return FONT_FACE.sub("", body)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ─── the files ───────────────────────────────────────────────────────────────


def test_the_fonts_folder_holds_exactly_the_fourteen_pinned_files() -> None:
    assert sorted(p.name for p in FONTS.iterdir()) == sorted([*FONT_PINS, *LICENCE_PINS])
    for name, (*_, size, sha) in FONT_PINS.items():
        path = FONTS / name
        assert path.is_file() and not path.is_symlink(), name
        assert (path.stat().st_size, _sha_file(path)) == (size, sha), name
        assert path.read_bytes()[:4] == b"wOF2", name
    for name, (size, sha) in LICENCE_PINS.items():
        path = FONTS / name
        assert path.is_file() and not path.is_symlink(), name
        assert (path.stat().st_size, _sha_file(path)) == (size, sha), name
        assert b"SIL OPEN FONT LICENSE Version 1.1" in path.read_bytes(), name
    # ADR-0035, Decision 1: "Total: 345,620 bytes."
    assert sum(pin[4] for pin in FONT_PINS.values()) == 345620


def test_the_hand_run_check_holds_the_same_pins() -> None:
    spec = importlib.util.spec_from_file_location(
        "check_fonts", ROOT / "scripts" / "check_fonts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    script = {pin.name: (pin.size, pin.sha256) for pin in module.PINS}
    held = {name: (pin[4], pin[5]) for name, pin in FONT_PINS.items()} | LICENCE_PINS
    assert script == held
    assert [pin.name for pin in module.PINS][:12] == list(FONT_PINS)
    for pin in module.PINS:
        assert pin.url.startswith(
            ("https://fonts.gstatic.com/s/", "https://raw.githubusercontent.com/google/fonts/")
        )
    assert module.FONTS == FONTS


def test_the_hand_run_check_compares_source_pin_and_package_constructed() -> None:
    spec = importlib.util.spec_from_file_location(
        "check_fonts", ROOT / "scripts" / "check_fonts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pin = module.PINS[0]
    good = (FONTS / pin.name).read_bytes()
    assert module.check(pin, good, good) == []
    assert len(module.check(pin, good + b"x", good + b"x")) == 1
    assert len(module.check(pin, good, None)) == 1
    assert len(module.check(pin, good, good[:-1])) == 1


def test_the_library_table_is_the_adrs() -> None:
    assert [row[0] for row in ui._FONTS] == list(FONT_PINS)
    for name, family, style, weight, ranges in ui._FONTS:
        pin = FONT_PINS[name]
        assert (family, style, weight, ranges) == (pin[0], pin[1], pin[2], RANGES[pin[3]])


# ─── the served shell ────────────────────────────────────────────────────────


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path))
    for name in ("APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    served = ui.make_server()
    thread = threading.Thread(target=served.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield served
    finally:
        served.shutdown()
        served.server_close()
        thread.join(10)


def _get_shell(server: Any) -> tuple[str, str]:
    port = int(server.port)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        connection.request("GET", f"/?token={server.token}", headers={"Host": f"127.0.0.1:{port}"})
        reply = connection.getresponse()
        body = reply.read()
        assert reply.status == 200
        return body.decode("utf-8"), reply.getheader("Content-Security-Policy") or ""
    finally:
        connection.close()


def _hash(text: str) -> str:
    return (
        "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"
    )


def test_the_served_shell_carries_one_font_face_per_file_and_font_src_data(
    server: Any,
) -> None:
    body, csp = _get_shell(server)
    styles = re.findall(r"<style>(.*?)</style>", body, flags=re.DOTALL)
    scripts = re.findall(r"<script>(.*?)</script>", body, flags=re.DOTALL)
    assert len(styles) == 1 and len(scripts) == 1
    style = styles[0]
    # page.css, then the colour rules, then the font rules.
    head = (TEMPLATES / "page.css").read_text(encoding="utf-8") + ui._colour_rules()
    assert style.startswith(head)
    rules = style[len(head) :]
    faces = list(FONT_FACE.finditer(rules))
    assert len(faces) == 12 and style.count("@font-face") == 12
    assert FONT_FACE.sub("", rules).strip() == (
        "/* the Wellspring fonts, from this package's fonts/ folder (SIL OFL 1.1) */"
    )
    for match, (name, pin) in zip(faces, FONT_PINS.items(), strict=True):
        family, font_style, weight, b64, ranges = match.groups()
        assert (family, font_style, weight, ranges) == (pin[0], pin[1], pin[2], RANGES[pin[3]])
        data = base64.b64decode(b64, validate=True)
        assert data == (FONTS / name).read_bytes(), name
        assert (len(data), hashlib.sha256(data).hexdigest()) == (pin[4], pin[5]), name
    # The CSP: every directive as before M16-51, and `font-src data:`.
    directives = {}
    for part in csp.split(";"):
        words = part.split()
        if words:
            assert words[0] not in directives
            directives[words[0]] = words[1:]
    assert directives == {
        "default-src": ["'none'"],
        "script-src": [_hash(scripts[0]), _hash(lookspage._SCRIPT)],
        "style-src": [_hash(style), _hash(lookspage._STYLE)],
        "font-src": ["data:"],
        "connect-src": ["'self'"],
        "img-src": ["'self'"],
        "frame-ancestors": ["'none'"],
        "base-uri": ["'none'"],
        "form-action": ["'none'"],
    }
    assert csp == ui._shell()[1]


def test_the_check_on_font_faces_refuses_a_stranger_constructed() -> None:
    """Constructed: the helper the explorer's `_EXTERNAL` check uses passes
    the real style and fails one with a rule changed or added."""
    style = ui._font_rules()
    assert "@font-face" not in without_font_faces(style)
    first = FONT_FACE.search(style)
    assert first is not None
    other = base64.b64encode(b"wOF2 not a pinned file").decode()
    changed = style.replace(first.group(4), other, 1)
    with pytest.raises(AssertionError):
        without_font_faces(changed)
    with pytest.raises(AssertionError):
        without_font_faces(style + "@font-face { src: url(https://example.invalid/x.woff2); }")
    swapped = style.replace(first.group(0), first.group(0).replace("format(", "format( "), 1)
    with pytest.raises(AssertionError):
        without_font_faces(swapped)


# ─── the wheel and the sdist ─────────────────────────────────────────────────


def _pin(text: str, name: str) -> str:
    found = re.findall(rf"^{re.escape(name)}==(\S+)", text, flags=re.MULTILINE)
    assert len(found) == 1, (name, found)
    return str(found[0])


def test_the_dev_hatchling_is_the_release_builds_pin() -> None:
    """The wheel test below builds with the `hatchling` of the dev group
    (installed by `uv sync`, locked with hashes in uv.lock), so it is the one
    the release builds with (`lab/core/build-constraints.txt`)."""
    import tomllib
    from importlib.metadata import version

    release = _pin(
        (ROOT / "lab" / "core" / "build-constraints.txt").read_text("utf-8"), "hatchling"
    )
    dev = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["dependency-groups"]["dev"]
    assert f"hatchling=={release}" in dev
    assert version("hatchling") == release


def test_the_wheel_and_sdist_carry_the_fourteen_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Builds both as the release does (`.github/workflows/release.yml`): the
    sdist from the checkout, then the wheel from the sdist. The pinned build
    backend runs in this process, as installed from the dev group, so the
    build makes no request (ADR-0012)."""
    from hatchling import build as backend

    out = tmp_path / "dist"
    out.mkdir()
    monkeypatch.chdir(ROOT / "lab" / "core")
    sdist = out / backend.build_sdist(str(out))
    unpacked = tmp_path / "unpacked"
    with tarfile.open(sdist) as tar:
        tar.extractall(unpacked, filter="data")
    (project,) = unpacked.iterdir()
    monkeypatch.chdir(project)
    wheel = out / backend.build_wheel(str(out))
    expected = {name: (FONTS / name).read_bytes() for name in [*FONT_PINS, *LICENCE_PINS]}
    with zipfile.ZipFile(wheel) as whl:
        prefix = "wowlab_core/ui/templates/fonts/"
        members = {n[len(prefix) :]: n for n in whl.namelist() if n.startswith(prefix)}
        assert sorted(members) == sorted(expected)
        for name, data in expected.items():
            assert whl.read(members[name]) == data, name
    with tarfile.open(sdist) as tar:
        marker = "/src/wowlab_core/ui/templates/fonts/"
        files = {
            m.name.split(marker, 1)[1]: m
            for m in tar.getmembers()
            if marker in m.name and m.isfile()
        }
        assert sorted(files) == sorted(expected)
        for name, data in expected.items():
            handle = tar.extractfile(files[name])
            assert handle is not None
            assert handle.read() == data, name


# ─── the server grader's CSP reading, narrowed (test_ui_server.py) ───────────


@pytest.mark.parametrize(
    ("font_src", "ok"),
    [
        ("font-src data:", True),
        ("FONT-SRC DATA:", True),
        ("font-src 'self'", True),
        ("font-src data: 'self'", False),
        ("font-src 'self' data:", False),
        ("font-src data: data:", False),
        ("font-src data:font/woff2", False),
        ("font-src *", False),
        ("font-src https:", False),
        ("img-src data:", False),
        ("style-src data:", False),
        ("default-src data:", False),
    ],
)
def test_the_server_csp_reading_accepts_data_only_as_the_whole_font_src_constructed(
    font_src: str, ok: bool
) -> None:
    from test_ui_server import _csp_problems

    frame = "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    value = f"default-src 'none'; {font_src}; {frame}"
    assert (_csp_problems(value) == []) is ok, value
