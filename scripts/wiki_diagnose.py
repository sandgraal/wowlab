"""Print a redacted diagnosis of the wiki-daily Claude step (M12-14).

    uv run python scripts/wiki_diagnose.py FILE [--summary PATH]
        [--workspace DIR] [--temp DIR] [--home DIR]

FILE is the execution file of `anthropics/claude-code-action`
(`claude-execution-output.json`, the action's `execution_file` output): a
JSON array of every Agent SDK message of the run, unsanitised, tool inputs
and tool results included. The action prints none of it unless
`show_full_output` is on, and that prints all of it, in a public log. Run
38653894587 failed with `is_error: true` after 16 turns and nothing else to
go on; this prints a bounded extract instead:

- the final result message's `subtype`, `is_error` and `num_turns`, and its
  `result` text (or its `errors`, for an error subtype), at most 1,500
  characters;
- for each `tool_result` with `is_error: true`, the name of the tool that
  produced it and the first 300 characters of its content, at most 10.

Every string printed is first put in the form `check_wiki` checks (entities
decoded, NFKC, format characters removed), its control characters dropped,
and the runner's own paths (`--workspace`, `--temp`, `--home`) shortened to
labels. It then goes through `check_wiki.personal_kinds`, the matcher the
gate's report lines use: a word that matches is replaced by the kinds it
matched; a line that still matches after that is replaced whole. Nothing is
quoted that the gate would not quote.

Writes the extract to stdout (every line indented, inside a stop-commands
block, so no text can start a workflow command) and, with `--summary`,
appends it to that file as Markdown (each text inside a fence longer than any
it contains). Reads FILE only. Exit 0 whatever FILE holds, even when it is
missing or malformed; 2 on a usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from check_wiki import PERSONAL_SUFFIX, allowed_terms, normalise, personal_kinds  # noqa: E402

RESULT_LIMIT = 1_500
TOOL_LIMIT = 300
MAX_TOOL_ERRORS = 10
NAME_LIMIT = 80
# Text is cut to this many times its limit before the matchers run, so a
# huge tool result costs no more than a long one.
PRE_CUT = 20
MAX_FILE_BYTES = 64 * 1024 * 1024

_SUBTYPE = re.compile(r"^[a-z_]{1,40}$")
_LINE_BREAKS = re.compile(r"\r\n|[\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]")


@dataclass
class Diagnosis:
    """What gets printed; every string already redacted and bounded."""

    notes: list[str] = field(default_factory=list)
    subtype: str = "(none)"
    is_error: str = "(none)"
    num_turns: str = "(none)"
    result: str | None = None
    tool_errors: list[tuple[str, str]] = field(default_factory=list)
    tool_error_count: int = 0


@dataclass
class Redactor:
    """Shortens runner paths, drops control characters, withholds personal data."""

    paths: list[tuple[str, str]] = field(default_factory=list)
    terms: frozenset[str] = field(default_factory=allowed_terms)

    def clean(self, text: str) -> str:
        text = normalise(_LINE_BREAKS.sub("\n", text))
        for path, label in self.paths:
            text = text.replace(path, label)
        text = text.replace("\t", " ")
        return "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch) != "Cc")

    def line(self, line: str) -> str:
        if not personal_kinds(line, self.terms):
            return line
        words = []
        for word in line.split(" "):
            kinds = personal_kinds(word, self.terms)
            words.append(f"[withheld: {_kinds(kinds)}]" if kinds else word)
        line = " ".join(words)
        kinds = personal_kinds(line, self.terms)
        if kinds:
            return f"[line withheld: {_kinds(kinds)} {PERSONAL_SUFFIX}]"
        return line

    def text(self, text: str, limit: int) -> str:
        text = self.clean(text[: limit * PRE_CUT])
        text = "\n".join(self.line(line) for line in text.split("\n"))
        if len(text) > limit:
            text = text[:limit] + f"… ({len(text) - limit} more characters)"
        return text


def _kinds(kinds: list[str]) -> str:
    return ", ".join(sorted(set(kinds)))


def _content_text(content: Any) -> str:
    """A tool result's content as text: a string, or a list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, dict):
                parts.append(f"[{str(block.get('type', 'unknown'))[:20]} block]")
        return "\n".join(parts)
    return "" if content is None else f"[{type(content).__name__}]"


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    inner = message.get("message")
    content = inner.get("content") if isinstance(inner, dict) else None
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)]


def diagnose(messages: list[Any], redactor: Redactor) -> Diagnosis:
    out = Diagnosis()
    tools: dict[str, str] = {}
    result: dict[str, Any] | None = None
    for message in messages:
        if not isinstance(message, dict):
            continue
        kind = message.get("type")
        if kind == "assistant":
            for block in _blocks(message):
                if block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                    tools[block["id"]] = str(block.get("name", "unknown"))
        elif kind == "user":
            for block in _blocks(message):
                if block.get("type") != "tool_result" or block.get("is_error") is not True:
                    continue
                out.tool_error_count += 1
                if len(out.tool_errors) < MAX_TOOL_ERRORS:
                    name = tools.get(str(block.get("tool_use_id")), "unknown")
                    content = _content_text(block.get("content"))
                    out.tool_errors.append(
                        (
                            redactor.text(name, NAME_LIMIT).replace("\n", " "),
                            redactor.text(content, TOOL_LIMIT),
                        )
                    )
        elif kind == "result":
            result = message
    if result is None:
        out.notes.append("no result message in the execution file")
        return out
    subtype = result.get("subtype")
    out.subtype = (
        subtype if isinstance(subtype, str) and _SUBTYPE.match(subtype) else "(unexpected)"
    )
    is_error = result.get("is_error")
    out.is_error = str(is_error).lower() if isinstance(is_error, bool) else "(unexpected)"
    turns = result.get("num_turns")
    is_int = isinstance(turns, int) and not isinstance(turns, bool)
    out.num_turns = str(turns) if is_int else "(unexpected)"
    text = result.get("result")
    errors = result.get("errors")
    if isinstance(text, str):
        out.result = redactor.text(text, RESULT_LIMIT)
    elif isinstance(errors, list):
        out.result = redactor.text("\n".join(str(e) for e in errors), RESULT_LIMIT)
    return out


def load(path: Path) -> list[Any] | str:
    """The file's messages, or a note saying why there are none."""
    if not path.is_file():
        return "no execution file (the Claude step stopped before writing one)"
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return f"the execution file is {size} bytes, over the {MAX_FILE_BYTES}-byte limit"
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return "the execution file is not valid JSON"
    if not isinstance(data, list):
        return "the execution file is not a JSON array"
    return data


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"~+|`+", text)), default=0)
    return "~" * max(3, longest + 1)


def render_log(d: Diagnosis) -> list[str]:
    lines = ["Claude step diagnosis (redacted; the log is public)"]
    lines += [f"note: {n}" for n in d.notes]
    lines.append(f"subtype: {d.subtype}  is_error: {d.is_error}  num_turns: {d.num_turns}")
    if d.result is not None:
        lines.append("result:")
        lines += ["    " + line for line in d.result.split("\n")]
    shown = len(d.tool_errors)
    lines.append(f"tool errors: {d.tool_error_count} (first {shown} shown)")
    for i, (name, content) in enumerate(d.tool_errors, 1):
        lines.append(f"  {i}. {name}")
        lines += ["    " + line for line in content.split("\n")]
    return ["  " + line for line in lines]


def render_summary(d: Diagnosis) -> str:
    out = ["### Claude step diagnosis (redacted)", ""]
    out += [f"- note: {n}" for n in d.notes]
    out.append(f"- subtype `{d.subtype}`, is_error `{d.is_error}`, num_turns `{d.num_turns}`")
    out.append(f"- tool errors: {d.tool_error_count} (first {len(d.tool_errors)} shown)")
    out.append("")
    if d.result is not None:
        fence = _fence(d.result)
        out += ["Result:", "", fence, d.result, fence, ""]
    for i, (name, content) in enumerate(d.tool_errors, 1):
        fence = _fence(name + content)
        out += [f"Tool error {i}:", "", fence, name, content, fence, ""]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("file", type=Path)
    parser.add_argument("--summary", type=Path, help="append Markdown to this file")
    parser.add_argument("--workspace", help="shown as <workspace>")
    parser.add_argument("--temp", help="shown as <runner-temp>")
    parser.add_argument("--home", help="shown as ~")
    args = parser.parse_args(argv)

    labels = [(args.workspace, "<workspace>"), (args.temp, "<runner-temp>"), (args.home, "~")]
    paths = [(p.rstrip("/"), label) for p, label in labels if p and p.rstrip("/")]
    redactor = Redactor(paths=sorted(paths, key=lambda pl: -len(pl[0])))

    loaded = load(args.file)
    diagnosis = Diagnosis(notes=[loaded]) if isinstance(loaded, str) else diagnose(loaded, redactor)

    token = secrets.token_hex(16)
    print(f"::stop-commands::{token}")
    for line in render_log(diagnosis):
        print(line)
    print(f"::{token}::")
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(render_summary(diagnosis))
    return 0


if __name__ == "__main__":
    sys.exit(main())
