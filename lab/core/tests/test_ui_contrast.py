"""Colour contrast and layout rules of `wowlab ui`'s style sheet (M16-30;
EV-28, from a11y:F5, F6, F8, F9, F10 and designer:F11 of the October 2026
outside evaluation).

The page's style is `page.css` plus the item-quality rules the server makes
from the shipped `gear_slots.toml` (`ui._colour_rules`). This file parses
that text, with no browser, and grades:

- every text colour against every surface it can sit on, at 4.5:1, or 3:1
  for large text (WCAG 1.4.3: at least 24 px, or 18.66 px bold), and every
  focus ring against the surfaces around it at 3:1 (WCAG 1.4.11), in every
  colour scheme the sheet declares;
- the reflow rules (the tab strip wraps, the Commands panes stack on a
  narrow window), a forced-colours rule for every selection state, and the
  heading order of each tab panel.

The palette is read, never listed. A scheme is the `:root` block (named by
its own `color-scheme`) and each `@media (prefers-color-scheme: …)` block
that overrides it; every custom property is resolved per scheme. Surfaces
are read from the rules too: a region (the parchment detail pane, the gear
table, which stays dark in both schemes, and the rest of the page, called
the chrome here) has the backgrounds of its root rules and of every rule in
it that paints a background and leaves the text colour to another rule. A
rule that sets a text colour is graded on its own background when it paints
one. Otherwise it is graded on every surface of its region, and also on the
ground of each container that holds it (a rule that paints a background and
sets its own text colour: its descendant selectors, and the few children
`_CHILDREN` names by class) and, for a state such as `:disabled`, on the
ground its plain rule paints. A hover or pressed state that only paints a
ground is graded with its plain rule's text colour. A new token or a new
scheme is graded without a change here; a container whose children are not
its descendant selectors needs a row in `_CHILDREN`.

A browser check of the reflow at 320 CSS px is at the end; it runs only when
Node can load Playwright and launch Chromium (CI has neither, and a browser
in CI needs an ADR: outside evaluation §5, row 8).
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import test_ui_explorer as first_stage

from wowlab_core import ui

home = first_stage.home
install = first_stage.install
server = first_stage.server

_TEMPLATES = Path(ui.__file__).resolve().parent / "templates"

# ─── reading the sheet ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rule:
    media: str  # the enclosing @media condition, "" at the top level
    selectors: tuple[str, ...]
    decls: dict[str, str]


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _block_end(css: str, start: int) -> int:
    """The index of the `}` that closes the `{` at `start`."""
    depth = 0
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise AssertionError("unbalanced braces in the style sheet")


def _parse(css: str, media: str = "") -> list[Rule]:
    rules: list[Rule] = []
    pos = 0
    text = _strip_comments(css)
    while True:
        brace = text.find("{", pos)
        if brace < 0:
            assert not text[pos:].strip(), text[pos:]
            return rules
        prelude = text[pos:brace].strip()
        end = _block_end(text, brace)
        inner = text[brace + 1 : end]
        if prelude.startswith("@media"):
            condition = prelude[len("@media") :].strip()
            rules += _parse(inner, f"{media} and {condition}" if media else condition)
        else:
            assert not prelude.startswith("@"), prelude
            decls: dict[str, str] = {}
            for part in inner.split(";"):
                if ":" in part:
                    name, value = part.split(":", 1)
                    decls[name.strip().lower()] = value.strip()
            selectors = tuple(s.strip() for s in prelude.split(",") if s.strip())
            rules.append(Rule(media, selectors, decls))
        pos = end + 1


def _sheet() -> str:
    return (_TEMPLATES / "page.css").read_text(encoding="utf-8") + ui._colour_rules()


def _rules() -> list[Rule]:
    return _parse(_sheet())


# ─── schemes and colours ─────────────────────────────────────────────────────

Rgb = tuple[float, float, float]


def _schemes(rules: list[Rule]) -> dict[str, dict[str, str]]:
    """Each colour scheme's custom properties: the top-level `:root`, named
    by its `color-scheme`, and each `prefers-color-scheme` block over it."""
    base: dict[str, str] = {}
    for rule in rules:
        if not rule.media and ":root" in rule.selectors:
            base.update(rule.decls)
    default = base.get("color-scheme", "").split()
    assert default, "the :root block names no color-scheme"
    schemes = {default[0]: base}
    for rule in rules:
        found = re.search(r"prefers-color-scheme:\s*([a-z]+)", rule.media)
        if found and ":root" in rule.selectors:
            schemes[found.group(1)] = {**base, **rule.decls}
    return schemes


def _hex(text: str) -> Rgb:
    return _hex_alpha(text)[0]


def _hex_alpha(text: str) -> tuple[Rgb, float]:
    """`#rgb`, `#rgba`, `#rrggbb` or `#rrggbbaa`."""
    digits = text.lstrip("#")
    if len(digits) in (3, 4):
        digits = "".join(c * 2 for c in digits)
    assert len(digits) in (6, 8), f"not a hex colour: {text}"
    rgb = (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
    return rgb, (int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0)


_COLOUR = re.compile(
    r"var\((--[\w-]+)\)|#[0-9a-fA-F]+\b|rgba?\([^)]*\)|(-?[A-Za-z][\w-]*)(\()?|-?[\d.]+[a-z%]*|[\s,/()]+"
)
# What a colour-bearing value may hold besides colours: gradient functions
# and their words, border and outline words, and the keywords that paint
# nothing. Anything else (a named colour, `hsl()`, `oklch()`, `color-mix()`,
# a system colour outside forced colours) fails the read, so a colour the
# grade cannot read is never left ungraded.
_GRADIENTS = {
    "linear-gradient",
    "radial-gradient",
    "repeating-linear-gradient",
    "repeating-radial-gradient",
}
_WORDS = {
    "none", "transparent", "inherit", "initial", "unset", "currentcolor",
    "solid", "dotted", "dashed", "double", "groove", "ridge", "inset", "outset", "auto",
    "to", "at", "top", "bottom", "left", "right", "center", "ellipse", "circle",
    "closest-side", "closest-corner", "farthest-side", "farthest-corner",
    "fixed", "scroll", "local", "no-repeat", "repeat", "repeat-x", "repeat-y", "cover", "contain",
    "border-box", "padding-box", "content-box", "thin", "medium", "thick",
}  # fmt: skip


def _colours(value: str, scheme: dict[str, str]) -> list[tuple[Rgb, float]]:
    """Every colour in a declaration's value, gradient stops included, as
    (rgb, alpha), `var()` resolved in the scheme. The keywords that paint
    nothing give nothing; any colour syntax this reader does not know
    raises, so it cannot pass unread."""
    out: list[tuple[Rgb, float]] = []
    pos = 0
    while pos < len(value):
        found = _COLOUR.match(value, pos)
        assert found, f"cannot read {value[pos:]!r} in {value!r}"
        pos = found.end()
        token = found.group(0)
        if found.group(1):
            name = found.group(1)
            assert name in scheme, f"{name} is used but not declared"
            out += _colours(scheme[name], scheme)
        elif token.startswith("#"):
            out.append(_hex_alpha(token))
        elif token.startswith("rgb"):
            parts = [p.strip() for p in token[token.index("(") + 1 : -1].split(",")]
            assert len(parts) in (3, 4), f"cannot read {token!r}"
            rgb = (float(parts[0]), float(parts[1]), float(parts[2]))
            out.append((rgb, float(parts[3]) if len(parts) == 4 else 1.0))
        elif found.group(2):
            word = found.group(2).lower()
            known = word in _GRADIENTS if found.group(3) else word in _WORDS
            assert known, f"cannot read the colour {token!r} in {value!r}"
    return out


def test_constructed_colour_syntax_the_reader_does_not_know_raises() -> None:
    assert _colours("#0a73611f", {}) == [((10, 115, 97), 0x1F / 255)]
    assert _colours("#fff8", {}) == [((255, 255, 255), 0x88 / 255)]
    assert _colours("2px solid none", {}) == []
    for value in (
        "white",
        "hsl(40 50% 50%)",
        "oklch(0.7 0.1 80)",
        "color-mix(in srgb, #fff, #000)",
        "Highlight",
    ):
        with pytest.raises(AssertionError):
            _colours(value, {})


def _over(top: tuple[Rgb, float], under: Rgb) -> Rgb:
    rgb, alpha = top
    return (
        rgb[0] * alpha + under[0] * (1 - alpha),
        rgb[1] * alpha + under[1] * (1 - alpha),
        rgb[2] * alpha + under[2] * (1 - alpha),
    )


def _luminance(rgb: Rgb) -> float:
    def channel(c: float) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a: Rgb, b: Rgb) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_contrast_formula_matches_wcag() -> None:
    assert round(_ratio((0, 0, 0), (255, 255, 255)), 2) == 21.0
    assert round(_ratio((255, 255, 255), (255, 255, 255)), 2) == 1.0
    # The evaluation's own figure (a11y:F5): dark gold on the parchment.
    assert round(_ratio(_hex("#e3bb55"), _hex("#e4d0a2")), 2) == 1.2


# ─── regions and surfaces ────────────────────────────────────────────────────

# Each region: the selector words that put a rule in it, and the rules whose
# background is the region's own. The first region whose word a selector
# holds wins; the chrome holds the rest.
_REGIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # The detail pane: its own rules, and the hint and preview boxes the
    # script draws inside it.
    "parchment": ((".parchment", "#explain", ".hint", ".preview"), (".parchment",)),
    "gear": ((".gear-",), (".gear-table",)),
    "chrome": ((), ("html", "body", ".banner", ".panel")),
}
# Painted surfaces that hold none of the page's text: the Looks frame shows a
# page with its own style sheet, graded with that page.
_NOT_TEXT_SURFACES = (".looks-frame",)
_BACKGROUND = ("background", "background-color")


def _region(selector: str) -> str:
    for name, (words, _roots) in _REGIONS.items():
        if any(word in selector for word in words):
            return name
    return "chrome"


def _forced(rule: Rule) -> bool:
    return "forced-colors" in rule.media


def _background(rule: Rule, scheme: dict[str, str]) -> list[tuple[Rgb, float]]:
    out: list[tuple[Rgb, float]] = []
    for name in _BACKGROUND:
        if name in rule.decls:
            out += _colours(rule.decls[name], scheme)
    return out


def _flatten(paints: list[tuple[Rgb, float]], unders: list[Rgb]) -> list[Rgb]:
    """Opaque surfaces from paints; a translucent paint is laid over each
    surface under it."""
    out: list[Rgb] = []
    for paint in paints:
        if paint[1] >= 1.0:
            out.append(paint[0])
        else:
            out += [_over(paint, under) for under in unders]
    return out


_STATE = re.compile(r"(?::[\w-]+(?:\([^)]*\))?|\[[^\]]*\])+$")


def _state_text(rules: list[Rule], selector: str) -> str | None:
    """For a state of one element (`.x:hover`, `.x[aria-pressed="true"]`),
    the text colour its plain rule (`.x`) sets, if one does."""
    plain = _STATE.sub("", selector)
    if plain == selector or not plain:
        return None
    colour = None
    for rule in rules:
        if not rule.media and plain in rule.selectors and "color" in rule.decls:
            colour = rule.decls["color"]
    return colour


def _painters(rules: list[Rule]) -> Iterator[tuple[Rule, str]]:
    """Every top-level selector that paints a background and sets no text
    colour, outside the frame that holds no text of the page's."""
    for rule in rules:
        if rule.media or "color" in rule.decls or not any(n in rule.decls for n in _BACKGROUND):
            continue
        for selector in rule.selectors:
            if not any(word in selector for word in _NOT_TEXT_SURFACES):
                yield rule, selector


def _surfaces(rules: list[Rule], scheme: dict[str, str]) -> dict[str, list[Rgb]]:
    """Every region's surfaces in one scheme: the backgrounds of its root
    rules, then of every rule in it that paints a background and sets no text
    colour, translucent ones over the root's. A painted state of an element
    whose plain rule sets the text colour (a hover, a pressed button) is not
    a shared surface: `_text_pairs` grades that colour on it."""
    out: dict[str, list[Rgb]] = {}
    for name, (_words, roots) in _REGIONS.items():
        base: list[Rgb] = []
        for rule in rules:
            if rule.media and "prefers-color-scheme" not in rule.media:
                continue
            if any(s in roots for s in rule.selectors):
                base += [c for c, a in _background(rule, scheme) if a >= 1.0]
        assert base, f"the {name} region has no background"
        found = list(base)
        for rule, selector in _painters(rules):
            if selector in roots or _region(selector) != name:
                continue
            if _state_text(rules, selector) is not None:
                continue
            found += _flatten(_background(rule, scheme), base)
        out[name] = sorted(set(found))
    return out


# ─── text size ───────────────────────────────────────────────────────────────

_HEADING_EM = {"h1": 2.0, "h2": 1.5, "h3": 1.17, "h4": 1.0}


def _font(rules: list[Rule], rule: Rule, selector: str) -> dict[str, str]:
    """The font size and weight of a selector: those of the top-level rules
    for the same element with fewer qualifiers (`.gear-name` for
    `.gear-name.colour-x`), then its own rule's."""
    out: dict[str, str] = {}
    for other in rules:
        if other.media:
            continue
        for plain in other.selectors:
            if plain != selector and selector.startswith(plain) and selector[len(plain)] in ".:[":
                out.update({k: v for k, v in other.decls.items() if k.startswith("font-")})
    out.update({k: v for k, v in rule.decls.items() if k.startswith("font-")})
    return out


def _large(font: dict[str, str], selector: str) -> bool:
    """Large text in WCAG's sense: 24 px, or 18.66 px bold. A heading
    selector with no size of its own has the browser's default size and
    weight."""
    tail = re.split(r"[\s>+~]+", selector.strip())[-1]
    element = re.match(r"[a-z0-9]+", tail)
    heading = element.group(0) if element and element.group(0) in _HEADING_EM else None
    size = font.get("font-size")
    if size and size.endswith("rem"):
        px = float(size[:-3]) * 16
    elif heading:
        px = _HEADING_EM[heading] * 16
    else:
        px = 16.0
    weight = font.get("font-weight", "bold" if heading else "normal")
    bold = weight == "bold" or (weight.isdigit() and int(weight) >= 700)
    return px >= 24 or (bold and px >= 18.66)


# ─── the grades ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Pair:
    scheme: str
    selector: str
    what: str
    fore: Rgb
    back: Rgb
    need: float

    @property
    def ratio(self) -> float:
        return _ratio(self.fore, self.back)


# A container paints its own ground and sets its own text colour, and holds
# other rules' text: its descendant selectors, and these children, which the
# script puts inside it under a class of their own.
_CHILDREN = {
    ".jt": re.compile(r"\.jt-(?!raw\b)"),
    ".macro-body": re.compile(r"\.macro-line\b"),
    "pre": re.compile(r"#(stdout|stderr)$"),
}


def _containers(rules: list[Rule]) -> Iterator[tuple[Rule, str]]:
    for rule in rules:
        if rule.media or "color" not in rule.decls or not any(n in rule.decls for n in _BACKGROUND):
            continue
        for selector in rule.selectors:
            if not any(word in selector for word in _NOT_TEXT_SURFACES):
                yield rule, selector


def _grounds(
    rules: list[Rule], selector: str, scheme: dict[str, str], surfaces: dict[str, list[Rgb]]
) -> list[Rgb]:
    """The grounds a colour rule's text sits on besides its region's
    surfaces: the ground of every container that holds it, and, for a state
    (`#run:disabled`), the ground its plain rule (`#run`) paints."""
    out: list[Rgb] = []
    for rule, outer in _containers(rules):
        children = _CHILDREN.get(outer)
        inside = re.match(re.escape(outer) + r"\s*[\s>]\s*\S", selector) is not None
        if inside or (children is not None and children.match(selector)):
            out += _flatten(_background(rule, scheme), surfaces[_region(outer)])
    plain = _STATE.sub("", selector)
    if plain and plain != selector:
        for rule in rules:
            if not rule.media and plain in rule.selectors:
                out += _flatten(_background(rule, scheme), surfaces[_region(plain)])
    return out


def _text_pairs(rules: list[Rule] | None = None) -> list[Pair]:
    rules = _rules() if rules is None else rules
    pairs: list[Pair] = []
    for scheme_name, scheme in _schemes(rules).items():
        surfaces = _surfaces(rules, scheme)
        for rule in rules:
            if _forced(rule) or "color" not in rule.decls:
                continue
            found = re.search(r"prefers-color-scheme:\s*([a-z]+)", rule.media)
            if rule.media and not found:
                continue
            if found and found.group(1) != scheme_name:
                continue
            fores = _colours(rule.decls["color"], scheme)
            for selector in rule.selectors:
                if selector.startswith("::") and selector != "::placeholder":
                    continue
                region = surfaces[_region(selector)]
                own = _background(rule, scheme)
                if own:
                    backs = _flatten(own, region)
                else:
                    backs = sorted(set(region + _grounds(rules, selector, scheme, surfaces)))
                need = 3.0 if _large(_font(rules, rule, selector), selector) else 4.5
                for fore, _alpha in fores:
                    for back in backs:
                        pairs.append(Pair(scheme_name, selector, "text", fore, back, need))
        for rule, selector in _painters(rules):
            text = _state_text(rules, selector)
            if text is None:
                continue
            backs = _flatten(_background(rule, scheme), surfaces[_region(selector)])
            for fore, _alpha in _colours(text, scheme):
                for back in backs:
                    pairs.append(Pair(scheme_name, selector, "text", fore, back, 4.5))
    return pairs


_FOCUS = re.compile(r":focus(-visible)?\b")


def _ring_colour(rule: Rule, scheme: dict[str, str]) -> list[tuple[Rgb, float]]:
    if "outline-color" in rule.decls:
        return _colours(rule.decls["outline-color"], scheme)
    if "outline" in rule.decls:
        return _colours(rule.decls["outline"], scheme)
    return []


def _focus_pairs() -> list[Pair]:
    """The focus ring each region draws: the ring of a focus rule scoped to
    the region when there is one, else the page-wide one; graded against
    every surface of the region, since the ring sits outside the control."""
    rules = _rules()
    pairs: list[Pair] = []
    for scheme_name, scheme in _schemes(rules).items():
        surfaces = _surfaces(rules, scheme)
        focus = [
            (selector, rule)
            for rule in rules
            if not rule.media and _ring_colour(rule, scheme)
            for selector in rule.selectors
            if _FOCUS.search(selector)
        ]
        page_wide = [(s, r) for s, r in focus if _FOCUS.sub("", s).strip() == ""]
        assert page_wide, "no page-wide focus ring"
        for region, backs in surfaces.items():
            words = _REGIONS[region][0]
            scoped = [(s, r) for s, r in focus if any(w in s for w in words) and " " in s.strip()]
            for selector, rule in scoped or page_wide:
                for fore, _alpha in _ring_colour(rule, scheme):
                    for back in backs:
                        pairs.append(
                            Pair(scheme_name, selector, f"focus ring ({region})", fore, back, 3.0)
                        )
    return pairs


def _failures(pairs: list[Pair]) -> list[str]:
    def css(rgb: Rgb) -> str:
        return "#" + "".join(f"{round(c):02x}" for c in rgb)

    return sorted(
        {
            f"{p.scheme}: {p.selector} {p.what} {css(p.fore)} on {css(p.back)} "
            f"is {p.ratio:.2f}:1, needs {p.need}:1"
            for p in pairs
            if p.ratio < p.need
        }
    )


def test_the_sheet_declares_two_schemes_and_every_region_has_surfaces() -> None:
    rules = _rules()
    schemes = _schemes(rules)
    assert set(schemes) == {"dark", "light"}
    for scheme in schemes.values():
        surfaces = _surfaces(rules, scheme)
        assert all(surfaces.values()), surfaces


def test_every_text_colour_meets_aa_on_every_surface_in_both_schemes() -> None:
    pairs = _text_pairs()
    assert {p.scheme for p in pairs} == {"dark", "light"}
    assert len(pairs) > 100
    assert _failures(pairs) == []


def test_every_focus_ring_meets_three_to_one_in_both_schemes() -> None:
    pairs = _focus_pairs()
    assert {p.scheme for p in pairs} == {"dark", "light"}
    assert {p.what for p in pairs} >= {"focus ring (parchment)", "focus ring (chrome)"}
    assert _failures(pairs) == []


def test_the_parchment_ring_is_ink() -> None:
    """a11y:F5: on the parchment the ring is the ink colour, not the gold."""
    rules = _rules()
    rings = [
        rule.decls.get("outline-color", rule.decls.get("outline", ""))
        for rule in rules
        if not rule.media and ".parchment :focus-visible" in rule.selectors
    ]
    assert rings and all("var(--ink)" in ring for ring in rings)


def test_the_grader_sees_a_failing_pair_constructed() -> None:
    """The grade fails when it should: the gold ring the evaluation measured
    on the parchment (a11y:F5), put back, is caught."""
    rules = _rules()
    scheme = _schemes(rules)["dark"]
    gold = _colours("var(--gold)", scheme)[0][0]
    parchment = _surfaces(rules, scheme)["parchment"]
    bad = [Pair("dark", ":focus-visible", "ring", gold, back, 3.0) for back in parchment]
    assert len(_failures(bad)) == len(set(parchment))


# ─── reflow, forced colours, headings ────────────────────────────────────────


def _declared(selector: str, media_word: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for rule in _rules():
        if selector in rule.selectors and (
            media_word in rule.media if media_word else not rule.media
        ):
            out.update(rule.decls)
    return out


def test_the_tabs_wrap() -> None:
    assert _declared(".tabs").get("flex-wrap") == "wrap"


def test_the_commands_panes_stack_below_40rem() -> None:
    narrow = [r for r in _rules() if re.search(r"max-width:\s*40rem", r.media)]
    stacked = {s: r.decls for r in narrow for s in r.selectors}
    assert stacked.get(".commands-body", {}).get("flex-direction") == "column"
    assert stacked.get("#commands", {}).get("min-width") == "0"


def test_forced_colours_mark_every_selection() -> None:
    """a11y:F9: under forced colours a background or an inset shadow is
    dropped, so each selected state (the tree row, the picked command, the
    open tab) draws a system-colour outline instead."""
    forced = [r for r in _rules() if "forced-colors: active" in r.media]
    marked = {s for r in forced if "Highlight" in r.decls.get("outline", "") for s in r.selectors}
    page_js = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    for selector in (
        ".row.selected",
        '#commands button[aria-pressed="true"]',
        '.tab[aria-selected="true"]',
    ):
        assert selector in marked, selector
    # each is a state the script really sets
    assert '"selected"' in page_js and '"aria-pressed", "true"' in page_js


class _Headings:
    """The heading levels inside each tab panel of the page shell, in order."""

    def __init__(self, html: str) -> None:
        self.panels: dict[str, list[int]] = {}
        sections = re.finditer(
            r'<section id="([a-z-]+)" role="tabpanel".*?(?=<section id="[a-z-]+" role="tabpanel"|</main>)',
            html,
            flags=re.DOTALL,
        )
        for found in sections:
            self.panels[found.group(1)] = [
                int(h) for h in re.findall(r"<h([1-6])\b", found.group(0))
            ]


def test_each_tab_panel_starts_at_h2_and_skips_no_level() -> None:
    """a11y:F10: under the page's one H1, each panel's headings start at H2
    in the shell itself, before any script draws one, and go down one level
    at a time."""
    html = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
    assert len(re.findall(r"<h1\b", html)) == 1
    panels = _Headings(html).panels
    assert set(panels) == {
        "explorer-view",
        "gaps-view",
        "commands-view",
        "character-view",
        "looks-view",
    }
    for name, levels in panels.items():
        assert levels and levels[0] == 2, (name, levels)
        for before, after in itertools.pairwise(levels):
            assert after <= before + 1, (name, levels)


def test_the_quality_word_is_drawn_beside_the_name() -> None:
    """a11y:F8: the quality is a visible word next to the gear name, from the
    record's own `quality`, not a hidden span or a tooltip."""
    page_js = (_TEMPLATES / "page.js").read_text(encoding="utf-8")
    body = page_js[page_js.index("function gearRow(") : page_js.index("function gearDetail(")]
    assert "gear-quality" in body
    assert "slot.quality" in body
    assert "unseen(" not in body


# ─── the reflow at 320 CSS px, in a browser ──────────────────────────────────

# Opens the page at 320 CSS px in Chromium through Playwright, visits every
# tab in both colour schemes (a file picked, a command picked, a character's
# item opened), and prints per state the document's scrollWidth and
# clientWidth and the elements that stick out past the right edge.
_REFLOW_JS = r"""
"use strict";

// One link per scheme: the link `wowlab ui` opens works once (M16-42).
const urls = { dark: process.argv[2], light: process.argv[3] };
let playwright;
try {
  playwright = require("playwright");
} catch (error) {
  console.log(JSON.stringify({ skip: "Playwright is not installed for Node: " + error.message }));
  process.exit(0);
}

async function measure(page, state) {
  return page.evaluate((name) => {
    const root = document.documentElement;
    const wide = [];
    for (const el of document.querySelectorAll("body *")) {
      const box = el.getBoundingClientRect();
      if (box.width && box.right > root.clientWidth + 0.5 && el.offsetParent !== null) {
        wide.push((el.id ? "#" + el.id : el.tagName.toLowerCase() + "." + el.className) + " right=" + Math.round(box.right));
      }
    }
    const detail = document.querySelector("#detail").getBoundingClientRect().width;
    return { state: name, scrollWidth: root.scrollWidth, clientWidth: root.clientWidth, detailWidth: detail, wide: wide.slice(0, 8) };
  }, state);
}

async function main() {
  let browser;
  try {
    browser = await playwright.chromium.launch();
  } catch (error) {
    console.log(JSON.stringify({ skip: "Chromium does not launch: " + String(error.message).split("\n")[0] }));
    return;
  }
  const out = [];
  try {
    for (const scheme of ["dark", "light"]) {
      const context = await browser.newContext({ viewport: { width: 320, height: 800 }, colorScheme: scheme });
      const page = await context.newPage();
      await page.goto(urls[scheme]);
      await page.waitForSelector("#tree li", { timeout: 60000 });
      await page.locator("#tree .name").first().click();
      await page.waitForFunction(() => document.querySelector("#explain-facts").children.length > 0, null, { timeout: 60000 });
      out.push(await measure(page, scheme + " explorer"));

      await page.click("#tab-gaps");
      await page.waitForFunction(() => document.querySelector("#gaps-body").children.length > 0 || document.querySelector("#gaps-status").textContent, null, { timeout: 60000 });
      out.push(await measure(page, scheme + " gaps"));

      await page.click("#tab-commands");
      await page.waitForSelector("#commands button:enabled", { timeout: 60000 });
      await page.locator("#commands button:enabled").first().click();
      out.push(await measure(page, scheme + " commands"));

      await page.click("#tab-character");
      await page.waitForSelector("#char-gear .gear-name", { timeout: 60000 });
      await page.locator("#char-gear .gear-name").first().click();
      out.push(await measure(page, scheme + " character"));

      await page.click("#tab-looks");
      out.push(await measure(page, scheme + " looks"));
      await context.close();
    }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ states: out }));
}

main().catch((error) => {
  console.log(JSON.stringify({ error: String(error && error.stack ? error.stack : error) }));
});
"""


def test_nothing_is_wider_than_a_320px_window(tmp_path: Path, install: Path, server: Any) -> None:
    """a11y:F6, designer:F11 (WCAG 1.4.10): at 320 CSS px, the width of a
    page at 400% zoom, no tab scrolls sideways (`scrollWidth ==
    clientWidth`), in either scheme. The page is the real server's, over the
    captured install the explorer tests use. Skipped unless Node can load
    Playwright and launch Chromium."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    script = tmp_path / "m16-30-reflow.js"
    script.write_text(_REFLOW_JS, encoding="utf-8")
    # A second server for the second scheme: each opened link works once (M16-42).
    light = ui.make_server()
    thread = threading.Thread(target=light.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        done = subprocess.run(
            [node, str(script), server.url, light.url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
            check=False,
        )
    finally:
        light.shutdown()
        light.server_close()
        thread.join(10)
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout.strip().splitlines()[-1])
    if "skip" in out:
        pytest.skip(out["skip"])
    assert "error" not in out, out["error"]
    states = out["states"]
    assert len(states) == 10
    wide = [s for s in states if s["scrollWidth"] != s["clientWidth"]]
    assert wide == [], wide
    assert all(s["clientWidth"] == 320 for s in states), states
    # The Commands form sits under the list at its full width, not in a
    # 40 px column beside it.
    commands = [s for s in states if s["state"].endswith(" commands")]
    assert len(commands) == 2 and all(s["detailWidth"] >= 250 for s in commands), commands


def test_constructed_a_container_ground_does_not_hang_on_another_rule() -> None:
    """`.jt-number` sits on the `.jt` box's ground. Giving `.toolbar select`
    (another rule that paints the same token) a text colour of its own takes
    that ground out of the chrome's shared surfaces, but `.jt-number` is
    still graded on it, as a child of `.jt`."""
    rules = _rules()
    scheme = _schemes(rules)["dark"]
    field = _colours(next(r for r in rules if ".jt" in r.selectors).decls["background"], scheme)[0][
        0
    ]
    changed = [
        Rule(r.media, r.selectors, {**r.decls, "color": "var(--field-text)"})
        if ".toolbar select" in r.selectors
        else r
        for r in rules
    ]
    assert field in _surfaces(rules, scheme)["chrome"]
    assert field not in _surfaces(changed, scheme)["chrome"]
    for version in (rules, changed):
        backs = {
            p.back
            for p in _text_pairs(version)
            if p.scheme == "dark" and p.selector == ".jt-number"
        }
        assert field in backs


def test_constructed_a_colour_only_state_is_graded_on_its_own_ground() -> None:
    """`#run:disabled` sets only a colour; it is graded on the ground `#run`
    paints, in each scheme."""
    rules = _rules()
    for name, scheme in _schemes(rules).items():
        run = next(r for r in rules if "#run" in r.selectors)
        ground = _colours(run.decls["background"], scheme)[0][0]
        backs = {
            p.back for p in _text_pairs(rules) if p.scheme == name and p.selector == "#run:disabled"
        }
        assert ground in backs, name
