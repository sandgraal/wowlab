"""`wowlab ui` in the companion design (M18-03; docs/design/COMPANION.md).

The shell is the companion's and the views are the ones before it, moved:

- `page.css` declares every token of COMPANION.md's token table with its
  value, in one dark theme (no `prefers-color-scheme` block), and no colour
  literal outside custom-property declarations;
- the header holds the mark, the name, the five sections in the design's
  order, and the game being read; the game is named for people by the
  script, never shown as a flavor folder (`test_ui_flavor_picker.py`);
- every view's intro is a closed **About this data** disclosure at the end
  of its view, and no sentence of the views before M18-03 is dropped;
- the mark is inline, without `xmlns`, and coloured through its class.

The contrast of every pair is graded in `test_ui_contrast.py`; the badges
and the font stacks in `test_ui_explorer.py`; the fonts in
`test_ui_fonts.py`. Replaces `test_ui_wellspring.py`, whose design
COMPANION.md supersedes.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from wowlab_core import ui

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"


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


def _root() -> dict[str, str]:
    out: dict[str, str] = {}
    for media, head, decls in _rules(_css()):
        if head == ":root":
            assert not media, media
            out.update({k: v for k, v in decls.items() if k.startswith("--")})
    return out


# The token table of COMPANION.md ("Tokens"), copied: name -> value.
_DESIGN_TOKENS = {
    "bg": "#0d1015",
    "bar": "#11151c",
    "surface": "#151a22",
    "line": "#222a35",
    "line-strong": "#2c3644",
    "text": "#e7eaee",
    "muted": "#9aa4b1",
    "faint": "#8a94a1",
    "accent": "#f4c15d",
    "ok": "#4ade80",
    "link": "#7cc4ff",
    "prof": "#6ee7b7",
}


# ─── the tokens ──────────────────────────────────────────────────────────────


def test_the_sheet_declares_every_design_token_with_its_value() -> None:
    root = _root()
    for name, value in _DESIGN_TOKENS.items():
        assert root.get(f"--{name}", "").lower() == value.lower(), (name, root.get(f"--{name}"))


def test_one_dark_theme() -> None:
    """COMPANION.md: one dark theme. No light block, and the page says so."""
    css = _css()
    assert "prefers-color-scheme" not in css
    root = [d for m, h, d in _rules(css) if h == ":root"]
    assert len(root) == 1 and root[0]["color-scheme"] == "dark"
    assert '<meta name="color-scheme" content="dark">' in _template("page.html")


def test_the_page_and_its_cards_use_the_design_grounds() -> None:
    paints = {head: decls for media, head, decls in _rules(_css()) if not media}
    assert paints["html"]["background"] == "var(--bg)"
    assert paints[".banner"]["background"] == "var(--bar)"
    card = paints[".panel"]
    assert card["background-color"] == "var(--surface)"
    assert card["border"] == "1px solid var(--line)"
    assert card["border-radius"] == "var(--radius-card)"
    assert _root()["--radius-card"] == "14px" and _root()["--radius-row"] == "10px"
    body = paints["body"]
    assert body["font-family"] == "var(--font-text)" and body["font-size"] == "15px"


def test_the_primary_button_is_the_accent() -> None:
    rules = [(head, decls) for media, head, decls in _rules(_css()) if not media]
    paints = dict(rules)
    primary = paints["#run, #looks-write, #confirm"]
    assert primary["background"] == "var(--accent)"
    assert primary["color"] == "var(--on-accent)"
    assert primary["min-height"] == "44px"


_LITERAL = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix)\(",
)
_NAMED = re.compile(
    r"(?<![-\w])(?:white|black|red|green|blue|gray|grey|silver|gold|orange|yellow|purple|teal)"
    r"(?![-\w(])",
    flags=re.IGNORECASE,
)


def _colour_literals(css: str) -> list[str]:
    """Every colour literal in a declaration that is not a custom property:
    a hex code, a colour function, or a common named colour (font family
    names in quotes are skipped). `Highlight`, a system colour, is the
    forced-colours outline's and is allowed."""
    found: list[str] = []
    for media, head, decls in _rules(css):
        for name, value in decls.items():
            if name.startswith("--"):
                continue
            unquoted = re.sub(r'"[^"]*"', "", value)
            for hit in _LITERAL.findall(unquoted) + _NAMED.findall(unquoted):
                found.append(f"{media} {head} {{ {name}: {value} }} ({hit})")
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
    assert _colour_literals(':root { --x: #fff; } .y { font-family: "Tan Serif"; }') == []


def test_the_quality_colours_are_not_declared() -> None:
    """The server draws item names from the shipped gear table; the sheet
    declares no quality colour under any name."""
    css = _css()
    assert "--quality" not in css and "quality-" not in css


# ─── the header ──────────────────────────────────────────────────────────────


def _header() -> str:
    html = _template("page.html")
    return html[html.index('<header class="banner">') : html.index("</header>")]


def test_the_header_has_the_mark_the_name_and_five_sections_in_order() -> None:
    header = _header()
    assert re.search(r'<h1 class="brand"><svg class="mark"[^>]*>.*?</svg>wowlab</h1>', header)
    sections = re.findall(
        r'<button id="sec-([a-z]+)" class="sec" type="button" role="tab" '
        r'aria-selected="(true|false)" aria-controls="([a-z -]+)">([A-Za-z]+)</button>',
        header,
    )
    assert [(s[0], s[3]) for s in sections] == [
        ("characters", "Characters"),
        ("backups", "Backups"),
        ("addons", "Addons"),
        ("keybinds", "Keybinds"),
        ("advanced", "Advanced"),
    ]
    assert [s[1] for s in sections] == ["true", "false", "false", "false", "false"]
    html = _template("page.html")
    for _name, _on, controls, _label in sections:
        for view in controls.split():
            assert f'<section id="{view}" role="tabpanel"' in html, view
    # The game being read: a dot, a line with one game version, the picker
    # with several. Neither holds a folder name in the shell.
    assert '<span class="game-dot" aria-hidden="true"></span>' in header
    assert '<span id="game-name" class="game-name" hidden></span>' in header
    assert "_" not in re.sub(r'(?:id|class|aria-[a-z]+)="[^"]*"', "", header)


def test_the_mark_is_inline_and_coloured_through_its_class() -> None:
    html = _template("page.html")
    svgs = re.findall(r"<svg\b[^>]*>.*?</svg>", html, flags=re.DOTALL)
    assert len(svgs) == 1 and svgs[0] in _header()
    svg = svgs[0]
    assert re.match(r'<svg class="mark"[^>]*aria-hidden="true"[^>]*focusable="false"', svg), svg
    assert "xmlns" not in svg
    assert not re.search(r"\b(?:fill|stroke|style|color)=", svg), (
        "the mark has no colour of its own"
    )
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", html)
    paints = {head: decls for media, head, decls in _rules(_css()) if not media}
    assert paints[".mark"]["stroke"] == "var(--accent)"
    assert paints[".mark"]["fill"] == "none"
    assert paints[".brand"]["font-family"] == "var(--font-display)"


def test_each_section_with_several_views_has_its_own_tabs() -> None:
    html = _template("page.html")
    rows = re.findall(
        r'<nav id="([a-z]+)-tabs" class="tabs" role="tablist"[^>]*>(.*?)</nav>', html, re.S
    )
    assert [(name, re.findall(r'id="tab-([a-z]+)"', body)) for name, body in rows] == [
        ("characters", ["account", "character"]),
        ("advanced", ["explorer", "gaps", "commands", "looks"]),
    ]
    script = _template("page.js")
    assert 'characters: ["account", "character"],' in script
    assert 'advanced: ["explorer", "gaps", "commands", "looks"],' in script


# ─── About this data ─────────────────────────────────────────────────────────


def _views(html: str) -> dict[str, str]:
    found = re.finditer(
        r'<section id="([a-z]+-view)" role="tabpanel".*?(?=<section id="[a-z]+-view" role="tabpanel"|</main>)',
        html,
        flags=re.DOTALL,
    )
    return {m.group(1): m.group(0) for m in found}


_ABOUT = re.compile(
    r'<details class="about"><summary>About this data</summary>\n<p class="(?:intro|patch-day-intro)">'
    r"(?:(?!</p>).)+</p>\n</details>\n</section>\n$",
    flags=re.DOTALL,
)


def test_every_view_ends_with_one_closed_about_this_data() -> None:
    html = _template("page.html")
    views = _views(html)
    assert list(views) == [
        "account-view",
        "character-view",
        "backups-view",
        "addons-view",
        "explorer-view",
        "gaps-view",
        "commands-view",
        "looks-view",
    ]
    for name, body in views.items():
        assert body.count('<details class="about">') == 1, name
        assert _ABOUT.search(body), name
        assert "<details open" not in body and 'class="about" open' not in body, name
    # No intro paragraph is left outside a disclosure.
    outside = re.sub(r'<details class="about">.*?</details>', "", html, flags=re.DOTALL)
    assert 'class="intro"' not in outside and "patch-day-intro" not in outside


class _Words(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("style", "script"):
            self.skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script"):
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip and data.strip():
            self.out.append(" ".join(data.split()))


def _text(html: str) -> str:
    parser = _Words()
    parser.feed(html)
    return " ".join(parser.out)


# The prose of `page.html` before M18-03 that M18-03 moved under About this
# data, as a player reads it. The Account tab's intro is split: its backup
# sentence went to Backups.
_KEPT = (
    "What every file in your game folder is, who writes it, and whether wowlab may change it. "
    "Reading never changes anything. Six commands can change game files; each shows a plan and "
    "runs only when you confirm, after a snapshot.",
    "Every character folder in your game folder, in every flavor, as the lab-addon last saved "
    "it: at logout or /reload , never the session in progress. This tab reads with wowlab char "
    "list --summary . Pick a character to open it in the Character tab.",
    "Only Take a backup now writes, with wowlab snap create , and only into wowlab's own store, "
    "never your game folder.",
    "Addons the game may skip, and saved data no installed addon declares, as wowlab reads them "
    "from your files. Reads only. Pick a line to see its file in the Explorer.",
    "Paths in the install that no file-map row describes, grouped under the row of the folder "
    "they are in and ranked by how many files they hold, then paths only a catch-all row "
    "describes, folders labelled whole and not looked inside, and links, which are never "
    "followed. Read only.",
    "Runs the commands of the wowlab you started, on this machine only.",
    "The commands that only read, or save only to wowlab's own data (snapshots, profiles, saved "
    "looks, the db2 cache, pages), run directly.",
    "One character as the lab-addon last saved it: the file it wrote at logout or /reload , "
    "never the session in progress, beside the key bindings and macros the client last wrote, "
    "and, under History, its earlier saves that your backups hold.",
    "See it dressed opens an outside dressing room in your own browser, and only when you click "
    "it; this page makes no request to it.",
    "It downloads the build's public game tables from wago.tools when wowlab does not have them "
    "yet, sending your game version, then writes pages/looks.html in wowlab's own data folder, "
    "never touching your game folder, and shows that file here.",
)


def test_no_sentence_is_dropped_and_each_sits_in_a_disclosure() -> None:
    html = _template("page.html")
    abouts = " ".join(
        _text(d) for d in re.findall(r'<details class="about">.*?</details>', html, flags=re.DOTALL)
    )
    for sentence in _KEPT:
        assert sentence in abouts, sentence


def test_the_keys_table_scrolls_inside_its_own_box() -> None:
    """M16-53's reflow rule is kept."""
    paints = {head: decls for media, head, decls in _rules(_css()) if not media}
    assert paints["#char-keys"] == {"max-width": "100%", "overflow-x": "auto"}
