"""`wowlab ui` in the Wellspring design (M16-52; docs/design/WELLSPRING.md).

The redesign restyles the page and changes nothing a player reads or the
script touches:

- `page.css` declares every token of `docs/design/wellspring-tokens.json`
  except the `quality-*` reference colours, with that file's value per theme
  (Warband under `:root`, Bastion under `prefers-color-scheme: light`), and
  no colour literal outside those custom-property declarations;
- the classes and ids of `page.html` and `page.js` are those of `main`
  before M16-52, apart from the crest's replacement by the sigil;
- the text a player reads in `page.html` is unchanged, word for word;
- the sigil is inline, without `xmlns`, and coloured through classes.

The contrast of every pair the design names is graded in
`test_ui_contrast.py`; the badges and the font stacks in `test_ui_explorer.py`.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from wowlab_core import ui

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"
_TOKENS = Path(__file__).resolve().parents[3] / "docs" / "design" / "wellspring-tokens.json"


def _template(name: str) -> str:
    return (_TEMPLATES / name).read_text(encoding="utf-8")


def _css() -> str:
    return re.sub(r"/\*.*?\*/", "", _template("page.css"), flags=re.DOTALL)


def _block_end(css: str, opened: int) -> int:
    depth = 0
    for at in range(opened, len(css)):
        if css[at] == "{":
            depth += 1
        elif css[at] == "}":
            depth -= 1
            if depth == 0:
                return at
    raise AssertionError("unbalanced CSS")


def _rules(css: str, media: str = "") -> list[tuple[str, str, dict[str, str]]]:
    """(media, selector text, declarations) for every rule, in order."""
    out: list[tuple[str, str, dict[str, str]]] = []
    at = 0
    while True:
        opened = css.find("{", at)
        if opened < 0:
            assert not css[at:].strip(), css[at:]
            return out
        head = css[at:opened].strip()
        end = _block_end(css, opened)
        inner = css[opened + 1 : end]
        if head.startswith("@media"):
            out += _rules(inner, head[len("@media") :].strip())
        else:
            assert not head.startswith("@"), head
            decls: dict[str, str] = {}
            for part in inner.split(";"):
                if ":" in part:
                    name, value = part.split(":", 1)
                    decls[name.strip()] = " ".join(value.split())
            out.append((media, head, decls))
        at = end + 1


def _themes() -> dict[str, dict[str, str]]:
    """The custom properties each theme sees: Warband, the top-level `:root`;
    Bastion, Warband's overridden by the light block's `:root`."""
    rules = _rules(_css())
    warband: dict[str, str] = {}
    light: dict[str, str] = {}
    for media, head, decls in rules:
        if head != ":root":
            continue
        props = {k: v for k, v in decls.items() if k.startswith("--")}
        if not media:
            warband.update(props)
        else:
            assert re.fullmatch(r"\(\s*prefers-color-scheme:\s*light\s*\)", media), media
            light.update(props)
    assert warband and light
    return {"warband": warband, "bastion": {**warband, **light}}


def _design() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(_TOKENS.read_text(encoding="utf-8"))
    return loaded


def _expected() -> dict[str, dict[str, str]]:
    """Every token of the design but `quality-*`, as `--name` and the text
    `page.css` declares for it, per theme."""
    design = _design()
    out: dict[str, dict[str, str]] = {"warband": {}, "bastion": {}}

    def put(name: str, value: Any) -> None:
        for theme in out:
            text = value[theme] if isinstance(value, dict) else value
            alias = re.fullmatch(r"\{([\w-]+)\}", text)
            out[theme][f"--{name}"] = f"var(--{alias.group(1)})" if alias else text

    for token in design["color"]["tokens"]:
        if not token["name"].startswith("quality-"):
            put(token["name"], token["value"])
    for group in ("spacing", "radius", "ornament", "shadow"):
        for token in design[group]["tokens"]:
            put(token["name"], token["value"])
    for family, stack in design["type"]["families"].items():
        put(f"font-{family}", stack)
    return out


def test_the_sheet_declares_every_design_token_with_its_value_per_theme() -> None:
    themes = _themes()
    expected = _expected()
    assert len(expected["warband"]) > 60
    for theme, tokens in expected.items():
        declared = themes[theme]
        assert set(declared) == set(tokens), (theme, set(declared) ^ set(tokens))
        for name, value in tokens.items():
            assert declared[name].lower() == value.lower(), (theme, name, declared[name], value)


def test_the_quality_colours_are_not_declared() -> None:
    """The `quality-*` tokens are a reference: the server draws item names
    from the shipped gear table, and the sheet declares none of them, under
    any name (M13-72 checks the values as well)."""
    css = _css()
    assert "--quality" not in css
    for token in _design()["color"]["tokens"]:
        if token["name"].startswith("quality-"):
            assert token["value"].lower() not in css.lower(), token["name"]


# The CSS named colours (CSS Color 4), which a value could hold as a literal.
_NAMED = {
    "aliceblue",
    "antiquewhite",
    "aqua",
    "aquamarine",
    "azure",
    "beige",
    "bisque",
    "black",
    "blanchedalmond",
    "blue",
    "blueviolet",
    "brown",
    "burlywood",
    "cadetblue",
    "chartreuse",
    "chocolate",
    "coral",
    "cornflowerblue",
    "cornsilk",
    "crimson",
    "cyan",
    "darkblue",
    "darkcyan",
    "darkgoldenrod",
    "darkgray",
    "darkgreen",
    "darkgrey",
    "darkkhaki",
    "darkmagenta",
    "darkolivegreen",
    "darkorange",
    "darkorchid",
    "darkred",
    "darksalmon",
    "darkseagreen",
    "darkslateblue",
    "darkslategray",
    "darkslategrey",
    "darkturquoise",
    "darkviolet",
    "deeppink",
    "deepskyblue",
    "dimgray",
    "dimgrey",
    "dodgerblue",
    "firebrick",
    "floralwhite",
    "forestgreen",
    "fuchsia",
    "gainsboro",
    "ghostwhite",
    "gold",
    "goldenrod",
    "gray",
    "green",
    "greenyellow",
    "grey",
    "honeydew",
    "hotpink",
    "indianred",
    "indigo",
    "ivory",
    "khaki",
    "lavender",
    "lavenderblush",
    "lawngreen",
    "lemonchiffon",
    "lightblue",
    "lightcoral",
    "lightcyan",
    "lightgoldenrodyellow",
    "lightgray",
    "lightgreen",
    "lightgrey",
    "lightpink",
    "lightsalmon",
    "lightseagreen",
    "lightskyblue",
    "lightslategray",
    "lightslategrey",
    "lightsteelblue",
    "lightyellow",
    "lime",
    "limegreen",
    "linen",
    "magenta",
    "maroon",
    "mediumaquamarine",
    "mediumblue",
    "mediumorchid",
    "mediumpurple",
    "mediumseagreen",
    "mediumslateblue",
    "mediumspringgreen",
    "mediumturquoise",
    "mediumvioletred",
    "midnightblue",
    "mintcream",
    "mistyrose",
    "moccasin",
    "navajowhite",
    "navy",
    "oldlace",
    "olive",
    "olivedrab",
    "orange",
    "orangered",
    "orchid",
    "palegoldenrod",
    "palegreen",
    "paleturquoise",
    "palevioletred",
    "papayawhip",
    "peachpuff",
    "peru",
    "pink",
    "plum",
    "powderblue",
    "purple",
    "rebeccapurple",
    "red",
    "rosybrown",
    "royalblue",
    "saddlebrown",
    "salmon",
    "sandybrown",
    "seagreen",
    "seashell",
    "sienna",
    "silver",
    "skyblue",
    "slateblue",
    "slategray",
    "slategrey",
    "snow",
    "springgreen",
    "steelblue",
    "tan",
    "teal",
    "thistle",
    "tomato",
    "turquoise",
    "violet",
    "wheat",
    "white",
    "whitesmoke",
    "yellow",
    "yellowgreen",
}
_LITERAL = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix)\(",
)


def _colour_literals(css: str) -> list[str]:
    """Every colour literal in a declaration that is not a custom property:
    a hex code, a colour function, or a named colour (font family names in
    quotes are skipped)."""
    found: list[str] = []
    for media, head, decls in _rules(css):
        for name, value in decls.items():
            if name.startswith("--"):
                continue
            unquoted = re.sub(r'"[^"]*"', "", value)
            for hit in _LITERAL.findall(unquoted):
                found.append(f"{media} {head} {{ {name}: {value} }} ({hit})")
            for word in re.findall(r"(?<![-\w])[A-Za-z]+(?![-\w(])", unquoted):
                if word.lower() in _NAMED:
                    found.append(f"{media} {head} {{ {name}: {value} }} ({word})")
    return found


def test_no_colour_literal_outside_the_token_declarations() -> None:
    assert _colour_literals(_css()) == []


def test_constructed_a_colour_literal_in_a_rule_is_caught() -> None:
    for rule in (
        ".x { color: #fff; }",
        ".x { background: rgba(0, 0, 0, 0.5); }",
        ".x { border: 1px solid white; }",
        ".x { box-shadow: 0 0 2px oklch(0.7 0.1 80); }",
    ):
        assert _colour_literals(_css() + rule), rule
    # a custom property may hold one; a font named in quotes is no colour
    assert _colour_literals(':root { --x: #fff; } .y { font-family: "Tan Serif"; }') == []


# ─── the classes, ids and words of the page ──────────────────────────────────


def _html_names(html: str) -> tuple[set[str], set[str]]:
    classes: set[str] = set()
    for value in re.findall(r'\bclass="([^"]*)"', html):
        classes.update(value.split())
    return classes, set(re.findall(r'\bid="([^"]+)"', html))


def _js_names(script: str) -> tuple[set[str], set[str]]:
    """The classes and ids the script reads or writes: `className`,
    `classList`, `charLine`'s class, `querySelector` selectors and `byId`.
    A class built by concatenation is kept as its literal prefix."""
    classes: set[str] = set()
    ids = set(re.findall(r'byId\(\s*"([^"]+)"\s*\)', script))
    for line in re.findall(r"className\s*=\s*([^;]+);", script):
        for literal in re.findall(r'"([^"]*)"', line):
            classes.update(literal.split())
    classes.update(re.findall(r'classList\.(?:add|toggle|remove|contains)\(\s*"([^"]+)"', script))
    classes.update(re.findall(r'charLine\([^;]*?,\s*"([a-z][\w-]*)"\)', script))
    for selector in re.findall(r'querySelector(?:All)?\(\s*"([^"]+)"', script):
        classes.update(re.findall(r"\.([\w-]+)", selector))
        ids.update(re.findall(r"#([\w-]+)", selector))
    return classes, ids


# As on `main` before M16-52 (b085218).
_HTML_CLASSES_BEFORE = frozenset(
    [
        "banner",
        "banner-text",
        "char-dressing",
        "char-filters",
        "char-panel",
        "char-panels",
        "char-read-only",
        "check",
        "commands-body",
        "confirm-box",
        "crest",
        "dressing-items",
        "dressing-link",
        "dressing-note",
        "hints",
        "intro",
        "jt",
        "jt-raw",
        "looks-frame",
        "looks-where",
        "panel",
        "panes",
        "parchment",
        "path",
        "preview-box",
        "snapshot",
        "tab",
        "tabs",
        "toolbar",
        "tree",
        "tree-panel",
        "tree-root",
        "unseen",
        "verdict",
    ]
)
_HTML_IDS_BEFORE = frozenset(
    [
        "char-dressing",
        "char-dressing-items",
        "char-dressing-link",
        "char-dressing-note",
        "char-gear",
        "char-gear-panel",
        "char-header",
        "char-keys",
        "char-keys-disagree",
        "char-keys-filter",
        "char-keys-panel",
        "char-keys-title",
        "char-macros",
        "char-macros-findings",
        "char-macros-panel",
        "char-macros-title",
        "char-panels",
        "char-picker",
        "char-status",
        "character-view",
        "commands",
        "commands-view",
        "confirm",
        "confirm-box",
        "confirm-note",
        "copy-status",
        "detail",
        "explain",
        "explain-facts",
        "explain-hints",
        "explain-path",
        "explain-preview",
        "explain-snapshot",
        "explain-status",
        "explain-title",
        "explain-verdict",
        "explorer-status",
        "explorer-view",
        "fields",
        "flavor",
        "flavor-box",
        "gaps-body",
        "gaps-only",
        "gaps-status",
        "gaps-view",
        "help",
        "level-filter",
        "looks-frame",
        "looks-status",
        "looks-view",
        "looks-where",
        "looks-write",
        "result-raw",
        "result-tree",
        "run",
        "status",
        "stderr",
        "stdout",
        "tab-character",
        "tab-commands",
        "tab-explorer",
        "tab-gaps",
        "tab-looks",
        "text-filter",
        "title",
        "tree",
        "tree-root",
    ]
)
_JS_CLASSES_BEFORE = frozenset(
    [
        "badge",
        "badge-",
        "badge-link",
        "badge-placeholder",
        "badge-server",
        "char-cannot",
        "char-count",
        "char-error",
        "char-facts",
        "char-line",
        "char-none",
        "char-notes",
        "char-row-notes",
        "char-slots",
        "char-sources",
        "char-table",
        "char-unreached",
        "copy",
        "dressing-ids",
        "error",
        "field",
        "field-refused",
        "folder",
        "gap-catch-all",
        "gap-examples",
        "gap-group",
        "gap-head",
        "gap-intro",
        "gap-link",
        "gap-not-entered",
        "gap-place",
        "gap-places",
        "gap-section",
        "gear-detail",
        "gear-empty",
        "gear-level",
        "gear-link",
        "gear-name",
        "gear-quality",
        "gear-row",
        "gear-row-empty",
        "gear-slot",
        "gear-table",
        "hint",
        "hint-command",
        "hint-does",
        "hint-note",
        "hint-shell",
        "jt",
        "jt-",
        "jt-children",
        "jt-count",
        "jt-key",
        "jt-leaf",
        "jt-more",
        "jt-node",
        "jt-value",
        "key-command",
        "key-disagree-mark",
        "key-disagrees",
        "key-does",
        "key-key",
        "key-row",
        "key-slot",
        "key-source",
        "macro",
        "macro-body",
        "macro-cannot",
        "macro-clean",
        "macro-facts",
        "macro-finding",
        "macro-findings",
        "macro-key",
        "macro-line",
        "macro-list",
        "macro-name",
        "macro-placement",
        "macro-reason",
        "macro-slots",
        "marks",
        "name",
        "needed",
        "note",
        "panel",
        "preview",
        "preview-does",
        "preview-error",
        "preview-head",
        "preview-note",
        "preview-out",
        "preview-tree",
        "row",
        "selected",
        "snapshot-label",
        "twisty",
        "twisty-gap",
        "unseen",
    ]
)
_JS_IDS_BEFORE = frozenset(
    [
        "char-dressing",
        "char-dressing-items",
        "char-dressing-link",
        "char-dressing-note",
        "char-gear",
        "char-header",
        "char-keys",
        "char-keys-disagree",
        "char-keys-filter",
        "char-macros",
        "char-macros-findings",
        "char-picker",
        "char-status",
        "commands",
        "confirm",
        "confirm-box",
        "confirm-note",
        "copy-status",
        "explain-facts",
        "explain-hints",
        "explain-path",
        "explain-preview",
        "explain-snapshot",
        "explain-status",
        "explain-title",
        "explain-verdict",
        "explorer-status",
        "fields",
        "flavor",
        "flavor-box",
        "gaps-body",
        "gaps-only",
        "gaps-status",
        "help",
        "level-filter",
        "looks-frame",
        "looks-status",
        "looks-where",
        "looks-write",
        "result-raw",
        "result-tree",
        "run",
        "status",
        "stderr",
        "stdout",
        "tab-character",
        "tab-commands",
        "tab-explorer",
        "tab-gaps",
        "tab-looks",
        "text-filter",
        "title",
        "tree",
        "tree-root",
    ]
)
_SIGIL_CLASSES = frozenset({"sigil", "ring", "tick", "core", "fissure"})


def test_the_classes_and_ids_are_those_of_main_but_the_sigil() -> None:
    html_classes, html_ids = _html_names(_template("page.html"))
    assert html_classes == (_HTML_CLASSES_BEFORE - {"crest"}) | _SIGIL_CLASSES
    assert html_ids == _HTML_IDS_BEFORE
    js_classes, js_ids = _js_names(_template("page.js"))
    assert js_classes == _JS_CLASSES_BEFORE
    assert js_ids == _JS_IDS_BEFORE
    # no sigil class collides with one the script uses
    assert not _SIGIL_CLASSES & js_classes


class _Words(HTMLParser):
    """The text nodes of the page outside `style` and `script`, whitespace
    collapsed, and the attributes a player reads (`aria-label`, `title`,
    `placeholder`)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("style", "script"):
            self.skip += 1
        for name, value in attrs:
            if name in ("aria-label", "title", "placeholder") and value:
                self.out.append(f"[{name}] {value}")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script"):
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.out.append(" ".join(data.split()))


def _words(html: str) -> list[str]:
    parser = _Words()
    parser.feed(html)
    return parser.out


# The words of `page.html` on `main` before M16-52 (b085218), in order.
_WORDS_BEFORE = (
    "wowlab",
    "wowlab",
    "What every file in your game folder is, who writes it, and whether wowlab may change it. Reading never changes anything. Six commands can change game files; each shows a plan and runs only when you confirm, after a snapshot.",
    "[aria-label] Views",
    "Explorer",
    "Gaps",
    "Commands",
    "Character",
    "Looks",
    "Open flavor folder",
    "Verdict",
    "All",
    "Find",
    "[placeholder] part of a path",
    "Gaps only",
    "[aria-label] Files in the install",
    "Pick a file or folder",
    "Gaps",
    "Paths in the install that no file-map row describes, grouped under the row of the folder they are in and ranked by how many files they hold, then paths only a catch-all row describes, folders labelled whole and not looked inside, and links, which are never followed. Read only.",
    "Runs the commands of the",
    "wowlab",
    "you started, on this machine only. Reading never changes anything. Six commands can change game files; each shows a plan and runs only when you confirm, after a snapshot. They are",
    "snap restore",
    ",",
    "undo",
    ",",
    "profile apply",
    ",",
    "sv merge",
    ",",
    "addon install",
    "and",
    "addon remove",
    ", and",
    "wowlab undo",
    "reverses each of them. Two of them refuse to run when something changed after the plan:",
    "undo",
    ", when a newer change has been journaled, and",
    "sv merge",
    ", when its target file changed. The others apply what is there when you confirm, after the snapshot that",
    "wowlab undo",
    "restores from. The plan changes nothing; press Confirm under it to run the command for real.",
    "snap gc",
    "and",
    "doctor",
    "also ask first (",
    "--yes",
    ") and change only wowlab's own store. The commands that only read, or save only to wowlab's own data (snapshots, profiles, saved looks, the db2 cache, pages), run directly.",
    "[aria-label] Commands",
    "Pick a command",
    "Run",
    "Confirm",
    "Output as printed",
    "Character",
    "One character as the lab-addon last saved it: the file it wrote at logout or",
    "/reload",
    ", never the session in progress, beside the key bindings and macros the client last wrote. Read only: this tab runs",
    "wowlab char list",
    ",",
    "wowlab char gear",
    ",",
    "wowlab char binds",
    "and",
    "wowlab char macros",
    "and nothing else. An item's name is its link's own text, and nothing is fetched.",
    "See it dressed",
    "opens an outside dressing room in your own browser, and only when you click it; this page makes no request to it.",
    "Character",
    "See it dressed",
    "Keys",
    "Find",
    "[placeholder] key, command or what it does",
    "Disagreements only",
    "[aria-label] Key bindings",
    "Read only in this version: this panel changes no binding. Changing keys from here is planned for a later wave.",
    "Macros",
    "Findings only",
    "Read only in this version: this panel changes no macro. Changing macros from here is planned for a later wave.",
    "Looks",
    "The page",
    "wowlab looks page",
    "writes: the races, body types, classes and customization options of the build's tables, and your saved looks checked against that build. Write the page runs that command, which writes",
    "pages/looks.html",
    "in wowlab's own data folder and never touches your game folder, then shows that file here. It runs in a closed frame: only its own script, no network requests, and nothing it can reach on this page.",
    "Write the page",
    "[title] The looks page",
)


def test_no_sentence_of_the_page_changed() -> None:
    assert _words(_template("page.html")) == list(_WORDS_BEFORE)


# ─── the sigil ───────────────────────────────────────────────────────────────


def test_the_sigil_is_inline_and_follows_the_theme() -> None:
    """The crest is replaced by the Wellspring sigil, inline in the banner,
    hidden from screen readers, without `xmlns`, and with no colour of its
    own: each part takes its token through its class."""
    html = _template("page.html")
    banner = html[html.index('<header class="banner">') : html.index("</header>")]
    svgs = re.findall(r"<svg\b[^>]*>.*?</svg>", banner, flags=re.DOTALL)
    assert len(svgs) == 1 and html.count("<svg") == 1
    svg = svgs[0]
    assert re.match(r'<svg class="sigil"[^>]*aria-hidden="true"[^>]*focusable="false"', svg), svg
    assert "xmlns" not in svg and "crest" not in html
    assert not re.search(r"\b(?:fill|stroke|style|color)=", svg), (
        "the sigil has no colour of its own"
    )
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", html)
    for part in ("ring", "tick", "core", "fissure"):
        assert f'class="{part}"' in svg, part
    paints = {head: decls for media, head, decls in _rules(_css()) if not media}
    assert paints[".sigil .ring"]["stroke"] == "var(--brass)"
    assert paints[".sigil .tick"]["stroke"] == "var(--brass-deep)"
    assert paints[".sigil .core"]["fill"] == "var(--wellspring)"
    assert paints[".sigil .fissure"]["stroke"] == "var(--wellspring)"


def test_the_banner_has_the_heraldry_band_and_the_name_in_display() -> None:
    paints = {head: decls for media, head, decls in _rules(_css()) if not media}
    assert paints[".banner"]["border-top"] == "4px solid var(--heraldry)"
    name = paints[".banner h1"]
    assert name["font-family"] == "var(--font-display)"
    assert name["font-size"] == "40px" and name["color"] == "var(--brass)"


def test_the_codex_is_the_raised_surface_with_a_heraldry_edge() -> None:
    paints = {head: decls for media, head, decls in _rules(_css()) if not media}
    codex = paints[".parchment"]
    assert codex["background-color"] == "var(--surface-raised)"
    assert codex["border-top"] == "3px solid var(--heraldry)"
    title = paints[".parchment h2"]
    assert title["font-family"] == "var(--font-display)" and title["font-size"] == "28px"


def test_the_buttons_are_primary_secondary_and_one_seal() -> None:
    """`#run` and `#looks-write` are primary (a `wellspring` fill, the glow
    only on hover or focus), `.copy` is secondary, and `#confirm` is the seal,
    the one standing glow of the Commands view."""
    rules = [(head, decls) for media, head, decls in _rules(_css()) if not media]
    paints = dict(rules)
    primary = paints["#run, #looks-write"]
    assert primary["background"] == "var(--wellspring)"
    assert primary["color"] == "var(--on-wellspring)"
    assert "box-shadow" not in primary
    secondary = next(d for h, d in rules if ".copy" in [s.strip() for s in h.split(",")])
    assert secondary["color"] == "var(--brass)"
    assert secondary["border"] == "1px solid var(--rule-strong)"
    seal = paints["#confirm"]
    assert seal["background"] == "var(--wellspring)"
    assert seal["box-shadow"] == "var(--shadow-glow)"
    standing = [h for h, d in rules if d.get("box-shadow") == "var(--shadow-glow)" and ":" not in h]
    assert standing == ["#confirm"], standing


def test_the_keys_table_scrolls_inside_its_own_box() -> None:
    """M16-53's reflow rule is kept."""
    paints = {head: decls for media, head, decls in _rules(_css()) if not media}
    assert paints["#char-keys"] == {"max-width": "100%", "overflow-x": "auto"}
