"""disclosure: "What leaves your machine", said once (M16-07).

`docs/LAB_PLAN.md` §18.1 item 2. The one statement of what wowlab sends over
the network. Its text lives here and nowhere else: `doctor`'s first-run
intro prints `lines()`, and README.md and docs/INSTALL.md carry
`render_markdown()` between the `<!-- network:begin -->` and
`<!-- network:end -->` markers, rewritten by `scripts/gen_network_block.py`.
A drift test (`lab/core/tests/test_disclosure.py`) fails when any copy
differs.

The words are player copy (`.claude/rules/player-copy.md`): commands in
backticks, which read the same in a terminal and in Markdown, and no ticket,
ADR, module or doc name. Nothing here names a flavor, a product code or a
version (L6).
"""

from __future__ import annotations

__all__ = ["BEGIN", "END", "HEAD", "ROWS", "has_block", "lines", "render_doc", "render_markdown"]

BEGIN = "<!-- network:begin -->"
END = "<!-- network:end -->"

HEAD = (
    "What leaves your machine. Nothing is uploaded: these three are wowlab's only "
    "network traffic, and no other command makes a request."
)

ROWS: tuple[str, ...] = (
    "`wowlab doctor --check-builds` downloads the public list of game versions from "
    "wago.tools, a community-run public site of game data, and looks for your installed "
    "versions in it; nothing from your install is sent. Without `--check-builds`, "
    "`doctor` makes no network request.",
    "`wowlab db2` and `wowlab looks` download public game data from wago.tools: its list "
    "of game versions, and tables for one version at a time. A table request names that "
    "version: your installed one, or the one you give with `--build`. `wowlab names "
    "fetch` downloads the tables that name races, classes, specs and professions the same "
    "way, for each version you have installed, and only the ones not already downloaded.",
    "A dressing-room link opens in your own browser, and only when you click it, "
    "carrying your character's race, body type, class, level and equipped item and "
    "bonus ids to that site: public game ids only, never a name. wowlab itself makes no "
    "request to it.",
)


def lines(indent: str = "") -> list[str]:
    """The block as terminal lines: the head after `indent`, then one `- `
    line per row, indented two spaces past it."""
    under = " " * len(indent)
    return [indent + HEAD, *(f"{under}  - {row}" for row in ROWS)]


def render_markdown() -> str:
    """The block as Markdown, markers included, ending in a newline."""
    body = [BEGIN, f"**{HEAD}**", "", *(f"- {row}" for row in ROWS), END]
    return "\n".join(body) + "\n"


def has_block(text: str) -> bool:
    """Whether `text` holds exactly one `BEGIN` line and one `END` line
    after it: a document meant to carry the block that has lost it is
    stale, not up to date."""
    return text.count(BEGIN) == 1 and text.count(END) == 1 and text.index(BEGIN) < text.index(END)


def render_doc(text: str) -> str:
    """`text` with every marked block replaced by `render_markdown()`.

    Each `BEGIN` line must be followed by an `END` line; the lines from one
    to the other, both included, are replaced. A document with an unpaired
    marker raises ValueError, so a broken copy is never silently left."""
    out: list[str] = []
    rest = text
    while True:
        start = rest.find(BEGIN)
        if start < 0:
            if END in rest:
                raise ValueError(f"{END} without {BEGIN}")
            out.append(rest)
            return "".join(out)
        stop = rest.find(END, start)
        if stop < 0:
            raise ValueError(f"{BEGIN} without {END}")
        if BEGIN in rest[start + len(BEGIN) : stop]:
            raise ValueError(f"{BEGIN} twice before {END}")
        if END in rest[:start]:
            raise ValueError(f"{END} without {BEGIN}")
        after = stop + len(END)
        if rest[after : after + 1] == "\n":
            after += 1
        out.append(rest[:start])
        out.append(render_markdown())
        rest = rest[after:]
