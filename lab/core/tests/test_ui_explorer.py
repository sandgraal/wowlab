"""The explorer view of `wowlab ui`, first stage (M13-25, §15.6, ADR-0029).

The page is served by the real server over the real CLI, and the explorer's
data comes from the runs it makes: `install show`, `tree --explain` and
`explain`, each with `--json`. The install is the captured tree
(`fixtures/macos/`) copied into `tmp_path` under a stand-in home folder, with
its flavor folder named as its provenance row says (`_classic_beta_`), and
`WOWLAB_WOW_ROOT` pointing at the copy. HOME and the user data folders point
into `tmp_path`. Nothing reads or writes a real install.

The hostile-name cases are `constructed` (L8, hostile input): no real capture
holds a file named `</script>`, `<img onerror=…>`, or one with U+2028 or
U+202E. Characters outside printable ASCII are spelled with `chr`.

What a browser does with the page is checked by reading the page: the CSP
hashes, no external URL, text written through `textContent` only, and no
file-map facts in the script (§14.5 D3). The owner's end-to-end check in a
real browser is reported in the PR.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import filemap, ui

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
SV_FILE = "WTF/Account/90000001#6/SavedVariables/RareScanner.lua"

LSEP = chr(0x2028)  # LINE SEPARATOR
RLO = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE
SCRIPT_CLOSE = "</script>"
IMG = "<img src=x onerror=alert(1)>"
HOSTILE_SV = [
    # `</script>` holds a slash, so it spans a folder `<` and a file `script>.lua`.
    "<",
    IMG + ".lua",
    "line" + LSEP + "sep.lua",
    RLO + "aul.exe.lua",
]
HOSTILE_ADDONS = [IMG, "line" + LSEP + "sep", RLO + "aul.exe"]

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"


# ─── serving ─────────────────────────────────────────────────────────────────


def _copy_install(root: Path) -> Path:
    root.mkdir(parents=True)
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    return root


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(folder))
    for name in ("APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(folder / name.lower()))
    return folder


@pytest.fixture
def install(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _copy_install(home / "Games" / "World of Warcraft")
    monkeypatch.setenv("WOWLAB_WOW_ROOT", str(root))
    return root


@pytest.fixture
def hostile(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if os.name == "nt":
        pytest.skip("Windows forbids < and > in file names")
    root = _copy_install(home / "hostile_constructed")
    flavor = root / FLAVOR
    saved = flavor / "WTF" / "Account" / "90000001#6" / "SavedVariables"
    for name in HOSTILE_SV:
        if name == "<":
            (saved / "<").mkdir()
            (saved / "<" / "script>.lua").write_text("X = 1\n", encoding="utf-8")
        else:
            (saved / name).write_text("X = 1\n", encoding="utf-8")
    for name in HOSTILE_ADDONS:
        folder = flavor / "Interface" / "AddOns" / name
        folder.mkdir()
        (folder / (name + ".toc")).write_text("## Interface: 1\n", encoding="utf-8")
    monkeypatch.setenv("WOWLAB_WOW_ROOT", str(root))
    return root


@pytest.fixture
def server(home: Path) -> Iterator[Any]:
    served = ui.make_server()
    thread = threading.Thread(target=served.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield served
    finally:
        served.shutdown()
        served.server_close()
        thread.join(10)


class _Reply:
    def __init__(self, status: int, headers: http.client.HTTPMessage, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def _send(server: Any, method: str, path: str, body: bytes | None = None) -> _Reply:
    port = int(server.port)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    try:
        headers = {"Host": f"127.0.0.1:{port}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, f"{path}?token={server.token}", body=body, headers=headers)
        response = connection.getresponse()
        return _Reply(response.status, response.headers, response.read())
    finally:
        connection.close()


def _run(server: Any, command: list[str], params: dict[str, Any]) -> _Reply:
    body = json.dumps({"command": command, "params": params}).encode("utf-8")
    return _send(server, "POST", "/api/run", body)


def _run_json(server: Any, command: list[str], params: dict[str, Any]) -> Any:
    reply = _run(server, command, params)
    assert reply.status == 200, reply.body[:300]
    result = reply.json()
    assert result["exit_code"] == 0, result["stderr"]
    assert not result["timed_out"] and not result["truncated"]
    return json.loads(result["stdout"])


def _page(server: Any) -> _Reply:
    reply = _send(server, "GET", "/")
    assert reply.status == 200
    return reply


def _script() -> str:
    return (_TEMPLATES / "page.js").read_text(encoding="utf-8")


def _inline(body: str, tag: str) -> list[str]:
    return re.findall(rf"<{tag}>(.*?)</{tag}>", body, flags=re.DOTALL)


def _sha(text: str) -> str:
    return (
        "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"
    )


def _directives(csp: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in csp.split(";"):
        words = part.split()
        if words:
            out[words[0].lower()] = words[1:]
    return out


# ─── the page: CSP, no external URL, text only ───────────────────────────────


def test_the_explorer_page_is_served_with_a_csp_that_hashes_it(server: Any) -> None:
    reply = _page(server)
    assert reply.headers["Content-Type"].startswith("text/html")
    body = reply.body.decode("utf-8")
    assert 'id="explorer-view"' in body and 'id="tree"' in body and 'id="explain"' in body
    scripts = _inline(body, "script")
    styles = _inline(body, "style")
    assert len(scripts) == 1 and len(styles) == 1
    assert body.count("<script") == 1 and body.count("<style") == 1
    directives = _directives(reply.headers["Content-Security-Policy"])
    assert directives["default-src"] == ["'none'"]
    assert directives["script-src"] == [_sha(scripts[0])]
    assert directives["style-src"] == [_sha(styles[0])]
    assert directives["connect-src"] == ["'self'"]
    assert directives["frame-ancestors"] == ["'none'"]
    assert "font-src" not in directives
    flat = " ".join(" ".join(v) for v in directives.values())
    assert "unsafe" not in flat and "data:" not in flat and "http" not in flat
    # The page is the explorer first: its view is not hidden, the commands are.
    assert re.search(r'<section id="explorer-view"[^>]*\bhidden\b', body) is None
    assert re.search(r'<section id="commands-view"[^>]*\bhidden\b', body) is not None


_EXTERNAL = [
    "://",
    "url(",
    "@import",
    "@font-face",
    "<link",
    "<img",
    "<iframe",
    "<object",
    "<embed",
    "src=",
    "href=",
    "srcset",
    "xmlns",
    "xlink",
    "//cdn",
    "fonts.",
    "data:image",
    "data:font",
    "data:text",
    "data:application",
]


def test_no_external_url_appears_in_the_page(server: Any) -> None:
    body = _page(server).body.decode("utf-8").lower()
    for marker in _EXTERNAL:
        assert marker not in body, marker
    # Every request the script makes goes to the server's own API, by path.
    script = _script()
    assert script.count("fetch(") == script.count('fetch(api("/api/')
    assert set(re.findall(r'fetch\(api\("([^"]+)"\)', script)) == {"/api/commands", "/api/run"}
    for sink in (
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "sendBeacon",
        "import(",
        "new Image",
    ):
        assert sink not in script, sink


_HTML_SINKS = [
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "createContextualFragment",
    "DOMParser",
    "srcdoc",
    "eval(",
    "Function(",
    "setTimeout(",
    "setInterval(",
    ".src",
    ".href",
    "location.assign",
    "window.open",
    'setAttribute("on',
    'setAttribute("style',
    ".style.",
]


def test_the_script_writes_text_only(server: Any) -> None:
    script = _script()
    for sink in _HTML_SINKS:
        assert sink not in script, sink
    # Attributes the script sets are a closed set, none of them a URL or a handler.
    names = set(re.findall(r'setAttribute\("([^"]+)"', script))
    assert names <= {
        "aria-pressed",
        "aria-selected",
        "aria-expanded",
        "aria-level",
        "aria-label",
        "role",
    }


def test_the_script_holds_no_file_map_logic() -> None:
    """§14.5 D3: no file-map row, Edit cell or verdict wording in the script;
    the level of a badge and its words come from the CLI's JSON."""
    script = _script()
    loaded = filemap.load()
    for entry in loaded.entries:
        assert f'"{entry.id}"' not in script, entry.id
        assert entry.what not in script, entry.id
        if len(entry.written_by) >= 12:
            assert entry.written_by not in script, entry.id
        for pattern in entry.match:
            if "*" in pattern:
                assert pattern not in script, pattern
    # `kind` is the tree item's file-or-folder field, not a verdict level.
    levels_free = script.replace('item.kind === "folder"', "")
    for level in ("gate", "no", "folder", "not_applicable", "unclassified"):
        assert f'"{level}"' not in levels_free, level
        assert f"'{level}'" not in script, level
    for words in ("write gate", "server may replace", "Blizzard", "snapshot restore"):
        assert words not in script, words


def test_the_explorer_runs_only_listed_read_commands(server: Any) -> None:
    listed = _send(server, "GET", "/api/commands").json()
    offered = {tuple(c["path"]): c for c in listed["commands"]}
    used = {
        tuple(json.loads("[" + inside + "]"))
        for inside in re.findall(r"runJson\(\[([^\]]*)\]", _script())
    }
    assert used == {("install", "show"), ("tree",), ("explain",)}
    for path in used:
        assert path in offered, path
        assert offered[path]["excluded"] is None, path


def test_the_command_list_carries_the_home_folder_for_tilde(server: Any, home: Path) -> None:
    listed = _send(server, "GET", "/api/commands").json()
    assert listed["home"] == str(Path.home()) == str(home)
    assert listed["sep"] == os.sep


# ─── the data the explorer draws ─────────────────────────────────────────────


def test_the_tree_and_details_the_explorer_draws(server: Any, install: Path, home: Path) -> None:
    shown = _run_json(server, ["install", "show"], {})
    assert [f["folder"] for f in shown["flavors"]] == [FLAVOR]

    tree = _run_json(server, ["tree"], {"explain": True, "flavor": FLAVOR})
    assert tree["flavor_folder"] == FLAVOR
    assert tree["install_root"].startswith(str(home) + os.sep)  # shown as "~"
    paths = {item["path"]: item for item in tree["items"]}
    # The first view's two open folders hold listed paths.
    assert any(p.startswith("WTF/") for p in paths)
    assert any(p.startswith("Interface/AddOns/") for p in paths)
    for item in tree["items"]:
        verdict = item["verdict"]
        assert verdict["level"] in ("gate", "no", "folder", "not_applicable", "unclassified")
        assert verdict["words"]
    assert any(i["verdict"]["level"] == "no" for i in tree["items"])  # "filter to no"

    # Detail, asked the way the page asks: the absolute path, root and flavor.
    for rel, entry_id in (("WTF/Config.wtf", "config-wtf"), (SV_FILE, "account-savedvariables")):
        assert rel in paths
        target = tree["install_root"] + os.sep + FLAVOR + "/" + rel
        report = _run_json(
            server, ["explain"], {"path": target, "root": tree["install_root"], "flavor": FLAVOR}
        )
        result = report["result"]
        assert result["status"] == "classified"
        assert result["path"] == rel
        assert result["entry"]["id"] == entry_id
        assert result["entry"]["what"] and result["entry"]["written_by"]
        assert report["verdict"] == paths[rel]["verdict"]


def test_a_request_without_the_token_is_refused(server: Any) -> None:
    port = int(server.port)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        assert response.status == 403
        assert json.loads(response.read()) == {"error": "token"}
    finally:
        connection.close()


# ─── hostile names (constructed) ─────────────────────────────────────────────


def test_hostile_names_pass_through_escaped_constructed(server: Any, hostile: Path) -> None:
    reply = _run(server, ["tree"], {"explain": True, "flavor": FLAVOR})
    assert reply.status == 200
    assert reply.headers["Content-Type"].startswith("application/json")
    assert reply.headers["X-Content-Type-Options"] == "nosniff"
    # Escaped in the body: no markup, no raw line separator or override.
    for raw in ("<", ">", "&", LSEP, RLO):
        assert raw.encode("utf-8") not in reply.body, repr(raw)
    result = reply.json()
    assert result["exit_code"] == 0, result["stderr"]
    paths = {item["path"]: item for item in json.loads(result["stdout"])["items"]}
    saved = "WTF/Account/90000001#6/SavedVariables/"
    expected = [saved + SCRIPT_CLOSE + ".lua"]
    expected += [saved + name for name in HOSTILE_SV if name != "<"]
    expected += ["Interface/AddOns/" + name for name in HOSTILE_ADDONS]
    for path in expected:
        assert path in paths, repr(path)
        assert paths[path]["verdict"]["words"]
    # The page never carries data: names reach it only as JSON read by the script.
    page = _page(server).body.decode("utf-8")
    for name in (SCRIPT_CLOSE + ".lua", IMG, LSEP, RLO):
        assert name not in page, repr(name)


def test_a_hostile_name_explains_constructed(server: Any, hostile: Path) -> None:
    root = str(hostile)
    rel = "WTF/Account/90000001#6/SavedVariables/" + SCRIPT_CLOSE + ".lua"
    reply = _run(
        server,
        ["explain"],
        {"path": root + os.sep + FLAVOR + "/" + rel, "root": root, "flavor": FLAVOR},
    )
    assert reply.status == 200
    assert b"</script" not in reply.body.lower()
    report = json.loads(reply.json()["stdout"])
    assert report["verdict"]["level"] == "unclassified"
    assert report["result"]["path"] == rel


def test_the_page_shows_control_characters_visibly() -> None:
    """U+202E and U+2028 in a name are shown as a code, not applied, and
    names are isolated from the text around them."""
    script = _script()
    assert r"\p{Cc}\p{Cf}\p{Zl}\p{Zp}" in script
    css = (_TEMPLATES / "page.css").read_text(encoding="utf-8")
    assert css.count("unicode-bidi: isolate") >= 3


def test_the_json_escape_keeps_the_text_constructed() -> None:
    """The extra escapes change bytes, never the decoded text."""
    text = SCRIPT_CLOSE + IMG + "&" + LSEP + chr(0x2029) + RLO + '"\\'
    encoded = ui._json_text(text)
    assert json.loads(b'"' + encoded + b'"') == text
    assert b"<" not in encoded and b">" not in encoded and b"&" not in encoded
