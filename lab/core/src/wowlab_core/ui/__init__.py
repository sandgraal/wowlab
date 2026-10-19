"""ui: the `wowlab ui` server, a local UI over the CLI (ADR-0029, §15.6, M13-24).

One `http.server.ThreadingHTTPServer`, bound to `127.0.0.1` on a port the
OS picks, serving one page shell and two JSON endpoints for as long as
`wowlab ui` runs. It runs CLI commands as subprocesses of the same `wowlab`,
with `--json`, and writes nothing itself: no file, no log line.

Every request is checked, in this order, before anything else is done with
it (a POST body included):
- `Host` is exactly `127.0.0.1:<port>` or `localhost:<port>`;
- an `Origin`, when present, is exactly `http://` plus that `Host`;
- the query parameter `token` is the server's token
  (`secrets.token_urlsafe(32)`), which appears only in the URL `wowlab ui`
  opens: never in a response, a header, a cookie, a log, or a child's argv
  or environment.
A refusal answers 403 `{"error": "host" | "origin" | "token"}`. Every
response, refusals and errors included, carries the same
Content-Security-Policy, which allows the server itself and the page's own
inline script and style by hash, and nothing else, and no CORS header. The
page shell is template text in this package (`templates/`), assembled into
one document: a `<script src>` would be a request without the token. Its
style has one part that is not template text: the rules that draw an item
name in its quality colour, made from the shipped `gear_slots.toml` (the
Character tab, M14-08), so neither the script nor the stylesheet holds a
colour table.

`GET /api/commands` lists the leaves of the Click tree read at start, with
the home folder and path separator so the page can show home as `~`. `POST
/api/run` runs one leaf. Its argv is built only from the parameters the leaf
declares: options as `--name=value`, so a value is never read as an option,
arguments after `--`, flags given or not, never through a shell. Refused
(403 `excluded`, ADR-0029 amendment and owner rulings of 2026-10-15): a leaf
that declares `--yes` (read from the tree) whose plan run has none of the
three shapes below (none on 2026-10-19), `ui` itself, `log tail` with
`follow` (a run must end) and `looks page` with `out` (it can create a file
anywhere outside an install).

The confirm step (ADR-0029 "Writes", §16.1, M14-02, M14-03). A leaf that
declares `--yes` is never run with `--yes` from a form: its `yes` (and
`dry_run`, and for the declined shape `expect`) parameter is the server's,
not the page's. A request with no `confirm` makes the *plan run*, whose
shape the server reads from what the leaf declares, never from a list: a
leaf that declares `--dry-run` (`snap gc`, `snap restore`, `profile apply`,
`addon install`, `addon remove`) runs with `--dry-run --json`, and its
marker is a JSON report with `dry_run: true`, exit 0; `doctor` runs with
`--json`, and its marker is `first_run.outcome == "not_asked"` with
`first_run.chosen` set, exit 0; a leaf with no `--dry-run` that declares
`--expect` and whose pin field the CLI exports in `cli.EXPECT_FIELDS`
(`undo`, `sv merge`: the *declined* shape) runs with `--json` alone, and its
marker is exit 1 with stderr's last line exactly `cli.CONFIRM_DECLINED`
(`_confirm` got no answer on the closed stdin) and stdout one JSON report
whose pin field holds a well-formed value. Only a whole run with its shape's
marker gets a one-time token (`secrets`), bound here to the exact argv it
confirms, spent on first use and kept only in memory. A request carrying
`confirm: <token>` spends it, rebuilds the plan argv from its own `command`
and `params`, and runs the bound confirmed argv only when the two plan argvs
are equal: the plan argv with `--dry-run` taken out and `--yes` added as its
own element after `--json` (and before any `--`, so it is never read as an
argument), and for the declined shape `--expect=<pin>` after it, with the
pin read here from the plan's own report: `undo` is refused (exit 3) if
another record was journaled after the plan, `sv merge` if its target file
changed (its source is not pinned; M13-74). The dry-run shape carries no
pin: its confirmed run applies what is there at confirm time, after the
pre-write snapshot. An unknown, spent or mismatched token answers 403
`confirm` and runs nothing. A confirmed run's result names the journal
record its report names, with the `wowlab undo` hint, and a confirmed run
killed at its limit says what to do (`_KILLED_NOTE`, or
`_KILLED_STORE_NOTE` for `snap gc` and `doctor`). The server writes
nothing itself: the confirmed run is the CLI's, and every change it causes
is the CLI's (L2).

At most `MAX_CONCURRENT_RUNS` run at once (503
`busy` past that). Each run has a timeout and an output cap, read as a stream
and counted on the encoded response. The run's whole process tree,
grandchildren included, is killed at exactly three points: when the run
passes its timeout, when its output reaches the cap, and when the server
closes while the run is in progress. The kill uses the OS and lists no
processes: the run's process group on POSIX, its Job Object on Windows
(`_Tree`). No process other than a run is ever opened or signalled. A run
that exits on its own is not killed. On POSIX, anything it started and left
running outlives it. On Windows, its job is closed after every run, which
kills whatever is left. None of the commands the server offers starts a
process.

The run is `[sys.executable, "-P", "-m", "wowlab_core", ...]`: the running
interpreter by absolute path, never a PATH lookup, with the working
directory kept off its import path.
"""

from __future__ import annotations

import base64
import codecs
import collections
import contextlib
import functools
import hashlib
import http.server
import importlib.resources
import json
import math
import os
import re
import secrets
import signal
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, override

from wowlab_core.ui import _winjob

__all__ = [
    "DEFAULT_OUTPUT_CAP_BYTES",
    "DEFAULT_TIMEOUT_S",
    "MAX_BODY_BYTES",
    "MAX_CONCURRENT_RUNS",
    "MAX_PENDING_CONFIRMS",
    "UiServer",
    "make_server",
    "static_files",
]

DEFAULT_TIMEOUT_S: float = 120.0
"""How long one run may take before its process tree is killed."""

DEFAULT_OUTPUT_CAP_BYTES: int = 16 * 1024 * 1024
"""How many bytes one run may print, stdout and stderr together."""

MAX_BODY_BYTES: int = 64 * 1024
"""The largest POST body the server reads."""

MAX_CONCURRENT_RUNS: int = 4
"""Runs in progress at once; one more is refused with 503 `busy`."""

_LOOPBACK = "127.0.0.1"
_READ_CHUNK = 64 * 1024
# How long a request may sit on a socket operation; an idle connection
# (a browser's speculative one) is dropped after this.
_SOCKET_TIMEOUT_S = 30.0
# How long the readers get to reach end of file once the run has ended.
_DRAIN_GRACE_S = 2.0
# How long server_close() waits for runs in progress to be killed.
_CLOSE_WAIT_S = 10.0
_POLL_S = 0.05

# A `--yes` leaf whose plan run has none of §16.1's three shapes is refused
# until it declares what a plan of it is (none does on 2026-10-19).
_EXCLUDED_YES = "--yes with no plan run the UI can confirm"
_EXCLUDED_UI = "the UI itself"

_JSON = "--json"
_YES = "--yes"
_DRY_RUN = "--dry-run"
_EXPECT = "--expect"
_DOCTOR = ("doctor",)
# The plan-run shapes (§16.1), as `/api/commands` names them. Which one a
# `--yes` leaf has is read from what it declares (`_confirm_shape`); the set
# of leaves is the tree's, never listed here.
_SHAPE_DRY_RUN = "dry_run"
_SHAPE_DOCTOR = "doctor"
_SHAPE_DECLINED = "declined"
# What a pin (the `--expect` value read from a declined plan's report) may
# be: a journal record id or a SHA-256 fit it, and it never starts with `-`.
_PIN = re.compile(r"\A[0-9A-Za-z][0-9A-Za-z._-]{0,127}\Z")
# Refusal code for an unknown, spent or mismatched confirm token.
_CONFIRM_REFUSED = "confirm"
# The longest confirm token read; `secrets.token_urlsafe(32)` is 43 characters.
_MAX_CONFIRM_TOKEN = 128

MAX_PENDING_CONFIRMS: int = 32
"""Confirm tokens kept at once; minting one more forgets the oldest."""
# Parameters refused on a leaf that otherwise runs, by leaf path: whether
# only a true value is refused (else any value is), and why. Owner rulings
# of 2026-10-15.
_REFUSED_PARAMS: Mapping[tuple[str, ...], Mapping[str, tuple[bool, str]]] = {
    ("log", "tail"): {"follow": (True, "a run must end")},
    ("looks", "page"): {"out": (False, "it can create a file anywhere outside an install")},
}


# ─── the page shell ──────────────────────────────────────────────────────────


def _template(name: str) -> str:
    return (importlib.resources.files(__name__) / "templates" / name).read_text(encoding="utf-8")


def _hash_source(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


def _colour_rules() -> str:
    """One rule per colour code of the shipped slot table that has a colour:
    an item name of that code (class `gear.colour_class`) is drawn in
    `gear.colour_rgb`'s colour. Only the shipped table is read, never the
    player's (the page reads nothing from the home folder): a code only the
    player's table names is drawn in the text colour. Both helpers return
    only `[0-9a-z_-]` and hex digits, so a rule cannot carry anything else."""
    from wowlab_core import gear

    table = gear.shipped_slot_table()
    rules = ["/* item names in their quality colour, from the shipped gear_slots.toml */"]
    for code in table.quality:
        name, rgb = gear.colour_class(code), gear.colour_rgb(code, table)
        if name is not None and rgb is not None:
            rules.append(f".gear-name.{name} {{ color: #{rgb}; }}")
    return "\n".join(rules) + "\n"


@functools.cache
def _shell() -> tuple[bytes, str]:
    """The page (its script and style inline) and the CSP that allows them."""
    style = _template("page.css") + _colour_rules()
    script = _template("page.js")
    page = (
        _template("page.html")
        .replace("<!--wowlab:style-->", style)
        .replace("<!--wowlab:script-->", script)
    )
    csp = "; ".join(
        [
            "default-src 'none'",
            f"script-src {_hash_source(script)}",
            f"style-src {_hash_source(style)}",
            "connect-src 'self'",
            "img-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'none'",
        ]
    )
    return page.encode("utf-8"), csp


def static_files() -> Mapping[str, bytes]:
    """Every URL path served as a static file, with its exact bytes. All of
    it is template text from this package's `templates/` folder, but for the
    colour rules made from the shipped slot table (`_colour_rules`)."""
    return {"/": _shell()[0]}


# ─── the Click tree ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Param:
    """One declared parameter of a leaf, as the argv builder reads it."""

    name: str
    kind: str  # "argument", "option" or "flag"
    multiple: bool
    opt: str  # the option text a value is attached to, or the flag's; "" for an argument
    required: bool
    help: str
    supported: bool  # False: a shape this builder does not pass (any value is refused)


@dataclass(frozen=True)
class _Leaf:
    path: tuple[str, ...]
    help: str
    params: Mapping[str, _Param]
    json_opt: str | None
    excluded: str | None
    # The plan-run shape of a `--yes` leaf that goes through the confirm step
    # (`_SHAPE_DRY_RUN`, `_SHAPE_DOCTOR` or `_SHAPE_DECLINED`); None for
    # every other leaf.
    confirm: str | None = None
    # For `_SHAPE_DECLINED`: the field of the plan's report whose value the
    # confirmed run passes back as `--expect` (`cli.EXPECT_FIELDS`).
    pin: str | None = None


def _opts(param: Any) -> list[str]:
    return [*getattr(param, "opts", ()), *getattr(param, "secondary_opts", ())]


@functools.cache
def _cli_contract() -> tuple[str, Mapping[tuple[str, ...], str]]:
    """What the CLI exports for the declined shape (§16.1): the line
    `_confirm` ends a declined run with, and the report field each command
    that declares `--expect` names its pin in."""
    from wowlab_core import cli

    return cli.CONFIRM_DECLINED, dict(cli.EXPECT_FIELDS)


def _value_option(param: Any, opt: str) -> bool:
    """Whether `param` is an option that takes exactly one value at `opt`."""
    return (
        not getattr(param, "is_flag", False)
        and getattr(param, "param_type_name", "") == "option"
        and opt in getattr(param, "opts", ())
        and getattr(param, "nargs", 1) == 1
        and not getattr(param, "multiple", False)
        and not getattr(param, "count", False)
    )


def _plain_flag(param: Any, opt: str) -> bool:
    """Whether `param` is a flag that giving `opt` alone turns on: no
    `--no-…` twin, no count, no other flag value."""
    return (
        bool(getattr(param, "is_flag", False))
        and opt in getattr(param, "opts", ())
        and not getattr(param, "secondary_opts", ())
        and getattr(param, "flag_value", True) is True
        and not getattr(param, "count", False)
    )


def _confirm_shape(
    path: tuple[str, ...],
    json_opt: str | None,
    yes: Any,
    dry_run: Any | None,
    expect: Any | None,
) -> str | None:
    """The plan-run shape of a `--yes` leaf, read from what it declares
    (§16.1), or None when it has none (it is then excluded): a leaf with no
    `--json` to read a marker from, or whose `--yes` is not a plain flag,
    has none. Else: a plain `--dry-run` flag is the dry-run shape (`--dry-run
    --json`); `doctor` is its own; a leaf with no `--dry-run` that declares
    a one-value `--expect` and whose report field for it the CLI exports
    (`cli.EXPECT_FIELDS`) is the declined shape (`--json` alone, ended by
    `_confirm`'s `cli.CONFIRM_DECLINED`). Nothing else."""
    if json_opt is None or not _plain_flag(yes, _YES):
        return None
    if dry_run is not None:
        return _SHAPE_DRY_RUN if _plain_flag(dry_run, _DRY_RUN) else None
    if path == _DOCTOR:
        return _SHAPE_DOCTOR
    if expect is not None and _value_option(expect, _EXPECT) and path in _cli_contract()[1]:
        return _SHAPE_DECLINED
    return None


def _read_param(param: Any) -> _Param:
    name = str(param.name)
    help_text = str(getattr(param, "help", None) or "")
    required = bool(getattr(param, "required", False))
    nargs = getattr(param, "nargs", 1)
    if getattr(param, "param_type_name", "") == "argument":
        return _Param(name, "argument", nargs == -1, "", required, help_text, nargs in (1, -1))
    opts = [str(o) for o in getattr(param, "opts", ())]
    long = [o for o in opts if o.startswith("--")]
    opt = long[0] if long else (opts[0] if opts else "")
    if getattr(param, "is_flag", False):
        supported = (
            not getattr(param, "secondary_opts", ())
            and getattr(param, "flag_value", True) is True
            and not getattr(param, "count", False)
            and bool(opt)
        )
        return _Param(name, "flag", False, opt, required, help_text, supported)
    supported = nargs == 1 and not getattr(param, "count", False) and bool(long)
    multiple = bool(getattr(param, "multiple", False))
    return _Param(name, "option", multiple, opt, required, help_text, supported)


def _leaves(command: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    if hasattr(command, "list_commands"):
        from typer._click import Context

        ctx = Context(command)
        for name in command.list_commands(ctx):
            sub = command.get_command(ctx, name)
            if sub is not None:
                yield from _leaves(sub, (*path, str(name)))
    else:
        yield path, command


def _read_tree(root: Any) -> dict[tuple[str, ...], _Leaf]:
    leaves: dict[tuple[str, ...], _Leaf] = {}
    for path, command in _leaves(root):
        params: dict[str, _Param] = {}
        json_opt: str | None = None
        yes: list[Any] = []
        dry_run: list[Any] = []
        expect: list[Any] = []
        for param in getattr(command, "params", ()):
            opts = _opts(param)
            if _YES in opts:
                yes.append(param)
            if _DRY_RUN in opts:
                dry_run.append(param)
            if _EXPECT in opts:
                expect.append(param)
            if _JSON in opts:
                json_opt = _JSON
                continue  # the server adds it itself; it is not a parameter here
            params[str(param.name)] = _read_param(param)
        excluded = None
        confirm = None
        pin = None
        if yes:
            if len(yes) == 1 and len(dry_run) <= 1 and len(expect) <= 1:
                confirm = _confirm_shape(
                    path,
                    json_opt,
                    yes[0],
                    dry_run[0] if dry_run else None,
                    expect[0] if expect else None,
                )
            if confirm is None:
                excluded = _EXCLUDED_YES
            else:
                # The server adds these itself, as it adds `--json`: the
                # plan run never carries `--yes`, and only a token adds it,
                # with the `--expect` value read from the plan's own report.
                own = [*yes, *(dry_run if confirm == _SHAPE_DRY_RUN else ())]
                if confirm == _SHAPE_DECLINED:
                    own += expect
                    pin = _cli_contract()[1][path]
                for param in own:
                    params.pop(str(param.name), None)
        if path == ("ui",):
            excluded = _EXCLUDED_UI
            confirm = None
            pin = None
        help_text = str(getattr(command, "help", None) or "").strip()
        leaves[path] = _Leaf(path, help_text, params, json_opt, excluded, confirm, pin)
    return leaves


def _home() -> str:
    """The home folder, so the page can show it as `~` (§15.6); "" when it
    cannot be read or is the file system root."""
    try:
        home = str(Path.home())
    except (RuntimeError, KeyError, OSError):
        return ""
    return "" if home in ("", os.sep) else home


def _describe(leaf: _Leaf) -> dict[str, Any]:
    refused = _REFUSED_PARAMS.get(leaf.path, {})
    return {
        "path": list(leaf.path),
        "help": leaf.help,
        "excluded": leaf.excluded,
        "confirm": leaf.confirm,
        "params": [
            {
                "name": p.name,
                "label": p.opt or p.name,
                "kind": p.kind,
                "multiple": p.multiple,
                "required": p.required,
                "help": p.help,
                "refused": refused[p.name][1] if p.name in refused else None,
            }
            for p in leaf.params.values()
            if p.supported
        ],
    }


# ─── building the argv ───────────────────────────────────────────────────────


class _RefusedError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _text(value: object) -> str:
    """A single value as argv text: a str or an int (never a bool)."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _RefusedError(403, "bad_value")
    text = str(value)
    if "\x00" in text:
        raise _RefusedError(403, "bad_value")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate from JSON
        raise _RefusedError(403, "bad_value") from None
    return text


@dataclass(frozen=True)
class _Argv:
    """One run's argv after the `wowlab` prefix, from declared parameters
    only, in the three places the server puts its own options: `head` (the
    leaf's path and the options given), then the server's options, then
    `tail` (`--` and the arguments, or nothing)."""

    head: tuple[str, ...]
    json_opt: str | None
    tail: tuple[str, ...]
    shape: str | None

    def _json(self) -> list[str]:
        return [self.json_opt] if self.json_opt is not None else []

    def plan(self) -> list[str]:
        """What runs for a request with no token: for a leaf of the dry-run
        shape `--dry-run --json`, else `--json` alone; never `--yes`, never
        `--expect`."""
        dry_run = [_DRY_RUN] if self.shape == _SHAPE_DRY_RUN else []
        return [*self.head, *dry_run, *self._json(), *self.tail]

    def confirmed(self, pin: str | None = None) -> list[str]:
        """The plan with `--dry-run` taken out and `--yes` added as its own
        element after `--json`: the last element when there is no `--`. For
        the declined shape, `--expect=<pin>` follows `--yes`, with the pin
        read from the plan's report; it has no confirmed run without one."""
        if self.shape is None:
            raise ValueError("only a leaf of the confirm step has a confirmed run")
        if self.shape != _SHAPE_DECLINED:
            return [*self.head, *self._json(), _YES, *self.tail]
        if pin is None or not _PIN.fullmatch(pin):
            raise ValueError("a confirmed run of the declined shape needs its plan's pin")
        return [*self.head, *self._json(), _YES, f"{_EXPECT}={pin}", *self.tail]


def _build_argv(leaf: _Leaf, params: Mapping[str, object]) -> list[str]:
    """The argv after the `wowlab` prefix, from declared parameters only (for
    a leaf of the confirm step, its plan run)."""
    return _build(leaf, params).plan()


def _build(leaf: _Leaf, params: Mapping[str, object]) -> _Argv:
    """`_build_argv` in its parts, so the plan and its confirmed run are the
    same request's."""
    if leaf.excluded is not None:
        raise _RefusedError(403, "excluded")
    for key in params:
        if key not in leaf.params:
            raise _RefusedError(403, "unknown_parameter")
    refused = _REFUSED_PARAMS.get(leaf.path, {})
    for key, (only_true, _) in refused.items():
        if key in params and (not only_true or params[key] is True):
            raise _RefusedError(403, "excluded")
    options: list[str] = []
    arguments: list[str] = []
    skipped_argument = False
    for name, param in leaf.params.items():
        if name not in params:
            if param.kind == "argument":
                skipped_argument = True
            continue
        value = params[name]
        if not param.supported:
            raise _RefusedError(403, "bad_value")
        if param.kind == "flag":
            if not isinstance(value, bool):
                raise _RefusedError(403, "bad_value")
            if value:
                options.append(param.opt)
            continue
        if param.multiple:
            if not isinstance(value, list):
                raise _RefusedError(403, "bad_value")
            values = [_text(v) for v in value]
        else:
            values = [_text(value)]
        if param.kind == "argument":
            if skipped_argument:
                # An earlier argument left out would shift this one into its place.
                raise _RefusedError(403, "bad_value")
            arguments.extend(values)
        else:
            options.extend(f"{param.opt}={v}" for v in values)
    tail = ("--", *arguments) if arguments else ()
    return _Argv((*leaf.path, *options), leaf.json_opt, tail, leaf.confirm)


# ─── running one command ─────────────────────────────────────────────────────


# Characters escaped in a run's output even though JSON allows them raw: the
# HTML-significant ones and the two line separators that end a JavaScript
# line (M13-25), so the body never holds `</script>` or a raw U+2028.
_JSON_EXTRA_ESCAPES = str.maketrans(
    {
        "<": "\\u003c",
        ">": "\\u003e",
        "&": "\\u0026",
        chr(0x2028): "\\u2028",
        chr(0x2029): "\\u2029",
    }
)


def _json_text(text: str) -> bytes:
    """`text` as the inside of a JSON string, UTF-8 (`ensure_ascii=False`,
    plus `_JSON_EXTRA_ESCAPES`): what it costs in the response body."""
    inside = json.dumps(text, ensure_ascii=False)[1:-1]
    return inside.translate(_JSON_EXTRA_ESCAPES).encode("utf-8")


class _Capture:
    """stdout and stderr of one run, read as streams and kept already encoded
    as the inside of JSON strings: at most `cap` bytes of response body,
    both together. A byte that is not UTF-8 becomes U+FFFD (3 bytes), and an
    escaped character costs its escape. Past the cap, reading stops and
    `full` is set; memory stays near the cap."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.lock = threading.Lock()
        self.pieces: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
        self.used = 0
        self.truncated = False
        self.full = threading.Event()

    def _keep(self, name: str, text: str) -> bool:
        """Keep what fits of `text`; False once the cap is reached."""
        if not text:
            return True
        encoded = _json_text(text)
        with self.lock:
            room = self.cap - self.used
            if len(encoded) <= room:
                self.pieces[name].append(encoded)
                self.used += len(encoded)
                return True
            # The longest prefix that fits, cut between characters.
            low, high = 0, len(text)
            while low < high:
                mid = (low + high + 1) // 2
                if len(_json_text(text[:mid])) <= room:
                    low = mid
                else:
                    high = mid - 1
            fitted = _json_text(text[:low])
            self.pieces[name].append(fitted)
            self.used += len(fitted)
            self.truncated = True
            self.full.set()
            return False

    def pump(self, stream: IO[bytes], name: str) -> None:
        read = getattr(stream, "read1", stream.read)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                chunk = read(_READ_CHUNK)
                if not self._keep(name, decoder.decode(chunk, final=not chunk)):
                    return
                if not chunk:
                    return
        except (OSError, ValueError):
            return

    def body(self, name: str) -> list[bytes]:
        with self.lock:
            return list(self.pieces[name])


@dataclass
class _Run:
    exit_code: int
    stdout: list[bytes]  # the inside of a JSON string, UTF-8
    stderr: list[bytes]
    timed_out: bool
    truncated: bool
    # For a leaf of the confirm step: what the page is offered (`_Confirm`).
    confirm: Mapping[str, object] | None = None

    def response(self) -> list[bytes]:
        """The JSON body, in pieces, never joined into one copy."""
        head = (
            f'{{"exit_code": {self.exit_code}, "timed_out": {json.dumps(self.timed_out)}, '
            f'"truncated": {json.dumps(self.truncated)}, "stdout": "'
        )
        end = b'"}'
        if self.confirm is not None:
            offer = json.dumps(self.confirm, ensure_ascii=True).translate(_JSON_EXTRA_ESCAPES)
            end = b'", "confirm": ' + offer.encode("ascii") + b"}"
        return [head.encode("ascii"), *self.stdout, b'", "stderr": "', *self.stderr, end]

    def report(self) -> dict[str, Any] | None:
        """The JSON object the run printed on stdout, when it exited 0, whole
        (no timeout, nothing cut), and stdout is exactly one JSON object;
        else None. A marker of the dry-run or doctor shape is read only from
        this."""
        if self.exit_code != 0:
            return None
        return self._object()

    def declined(self) -> dict[str, Any] | None:
        """The declined shape's marker (§16.1): the run exited 1, whole, its
        stderr's last line is exactly `cli.CONFIRM_DECLINED` (what `_confirm`
        prints when it gets no answer), and stdout is exactly one JSON
        object, the report printed with it; that report, else None. Exit 1
        without that last line (unresolved conflicts, an error), and every
        other exit, are not the marker."""
        if self.exit_code != 1:
            return None
        stderr = self._text(self.stderr)
        if stderr is None:
            return None
        lines = stderr.replace("\r\n", "\n").split("\n")
        if len(lines) < 2 or lines[-1] != "" or lines[-2] != _cli_contract()[0]:
            return None
        return self._object()

    def _object(self) -> dict[str, Any] | None:
        if self.timed_out or self.truncated:
            return None
        text = self._text(self.stdout)
        if text is None:
            return None
        try:
            value = json.loads(text)
        except (ValueError, RecursionError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _text(pieces: list[bytes]) -> str | None:
        """What one stream printed: the pieces are the inside of a JSON
        string (`_json_text`)."""
        try:
            text = json.loads(b'"' + b"".join(pieces) + b'"')
        except (ValueError, RecursionError):
            return None
        return text if isinstance(text, str) else None


@dataclass(frozen=True)
class _Offer:
    """What a plan run ended with, read in Python (§14.5 D3): whether it
    ended with its shape's marker, the sentence the page shows beside the
    plan, the parameter the player must fill to be offered Confirm, and for
    the declined shape the pin its confirmed run passes as `--expect`."""

    marked: bool
    note: str | None = None
    needs: str | None = None
    pin: str | None = None


def _offer(leaf: _Leaf, run: _Run) -> _Offer:
    """The plan run's outcome by its shape's marker (§16.1). Anything that is
    not the marker offers nothing: for the dry-run and doctor shapes an exit
    other than 0 (a refusal, exit 3, included) or output that is not one
    whole JSON object; for the declined shape anything but exit 1 ended by
    `_confirm`'s line with a report that names a well-formed pin."""
    if leaf.confirm == _SHAPE_DECLINED:
        return _declined_offer(leaf, run.declined())
    report = run.report()
    if report is None:
        return _Offer(False)
    if leaf.confirm == _SHAPE_DRY_RUN:
        if report.get("dry_run") is not True:
            return _Offer(False)
        return _Offer(True, _dry_run_note(leaf, report))
    if leaf.confirm == _SHAPE_DOCTOR:
        first = report.get("first_run")
        if not isinstance(first, Mapping) or first.get("outcome") != "not_asked":
            return _Offer(False)
        chosen = first.get("chosen")
        if isinstance(chosen, str) and chosen:
            return _Offer(True, f"Confirm takes a first snapshot of {chosen} into wowlab's store.")
        if chosen is None and "flavor" in leaf.params:
            # Several flavors and none named: a confirmed run would exit 2
            # (`firstrun.run` returns `choose_flavor` under `--yes`).
            return _Offer(
                False,
                "Several flavors are installed: name one in --flavor and run the plan "
                "again to be offered Confirm.",
                "flavor",
            )
    return _Offer(False)


def _declined_offer(leaf: _Leaf, report: Mapping[str, Any] | None) -> _Offer:
    """A declined plan (`undo`, `sv merge`): offered only when its report
    names the pin in the field the CLI exports for it; the UI never confirms
    one of these without `--expect` (§16.1)."""
    if report is None or leaf.pin is None:
        return _Offer(False)
    pin = report.get(leaf.pin)
    if not isinstance(pin, str) or not _PIN.fullmatch(pin):
        return _Offer(False)
    check = _PIN_CHECKS.get(
        leaf.pin, "if that no longer matches by then, it is refused (exit 3) and nothing is changed"
    )
    return _Offer(
        True,
        "The plan is what the command printed on stderr before it asked. Confirm runs it "
        f"with --yes and --expect {pin}: {check}.",
        pin=pin,
    )


# What `--expect` checks, by the report field its pin is read from, in the
# words the offer uses: exactly what the CLI compares, nothing more. `sv
# merge` pins its target only, not its source (M13-74 follows that up).
_PIN_CHECKS: Mapping[str, str] = {
    "undone": (
        "if another change has been journaled since this plan, it is refused (exit 3) and "
        "nothing is undone"
    ),
    "target_sha256": (
        "if the target file has changed by then, it is refused (exit 3) and nothing is written"
    ),
}

# The sentence beside a confirmed run that was killed (its time limit or its
# output cap) before it ended. M14-03 decides this case (§16.1): a `guard`
# transaction cannot roll back in a process that was killed, but the lock is
# the OS's and goes with the process, the journal record is written before
# the first change, and the snapshot taken before it holds every file.
_KILLED_NOTE = (
    "It was stopped before it finished, so it may have changed some files and not others. "
    "A change through the write gate releases its lock when its process ends, and the "
    "snapshot it took before its first change holds every file as it was: show the plan of "
    "undo, and if the record it names is this command's (state open), Confirm there puts "
    "back what it changed."
)
# The same for a leaf of the step that changes only wowlab's own store and
# never goes through the write gate: it takes no pre-write snapshot and
# journals nothing, so there is nothing to undo.
_KILLED_STORE_NOTE = (
    "It was stopped before it finished. It changes only wowlab's own store, never your "
    "game files, and its lock was released when its process ended: run it again."
)
# The leaves of the step whose confirmed run writes only the store (§16.1:
# `snap gc` removes unreferenced objects, `doctor --yes` takes a first
# snapshot). Any other leaf of the step gets `_KILLED_NOTE`.
_STORE_ONLY: frozenset[tuple[str, ...]] = frozenset({("snap", "gc"), _DOCTOR})


def _confirmed_note(leaf: _Leaf, run: _Run) -> str | None:
    """The sentence beside a confirmed run (§16.1): the journal record it
    made and the `wowlab undo` hint, when its report names one; what to do
    when it was killed, by whether it writes the install; else nothing
    (`snap gc` and `doctor` make no record, and a refusal is shown as the
    CLI printed it)."""
    if run.timed_out or run.truncated:
        return _KILLED_STORE_NOTE if leaf.path in _STORE_ONLY else _KILLED_NOTE
    report = run.report()
    record = report.get("transaction") if report is not None else None
    if isinstance(record, str) and _PIN.fullmatch(record):
        return f"Journaled as {record}. `wowlab undo` reverses it."
    return None


def _dry_run_note(leaf: _Leaf, report: Mapping[str, Any]) -> str | None:
    """The sentence beside a dry-run plan, from its report. `snap gc`: what
    it found to remove, or that there is nothing; Confirm is offered either
    way (a confirmed run that finds nothing changes nothing)."""
    if leaf.path == ("snap", "gc"):
        found = report.get("unreferenced")
        if isinstance(found, list) and not found:
            return "Nothing to remove. Confirm runs it anyway, and it changes nothing."
        if isinstance(found, list):
            return (
                f"{len(found)} stored object(s) nothing keeps. Confirm removes what "
                "nothing keeps when it runs; a snapshot taken meanwhile keeps its own."
            )
    return None


_KILL = getattr(signal, "SIGKILL", signal.SIGTERM)


class _Tree:
    """How a run is started so that it and everything it starts can be
    killed together, without listing processes.

    `kill` is called at exactly three points: when the run passes its
    timeout, when its output reaches the cap, and when the server closes
    while the run is in progress. It is always called before the run is
    reaped.

    POSIX: the run leads a new session and process group
    (`start_new_session`). `kill` signals that group while the run's
    unreaped pid still holds the group id. A descendant that calls `setsid`
    itself leaves the group and is not killed. After a run exits on its own,
    nothing is signalled, because the group id is no longer reserved. Any
    descendant still running then outlives the run, and that is a known
    residual. The `wowlab` CLI never forks, and none of the offered commands
    starts a process.

    Windows: the run is created suspended and resumed inside a Job Object
    (`_winjob`) that kills everything in it when terminated or closed;
    `end` closes it after every run, so nothing the run started outlives
    it."""

    def __init__(self) -> None:
        self.job: _winjob.Job | None = None

    def popen_kwargs(self) -> dict[str, Any]:
        if os.name == "posix":
            return {"start_new_session": True}
        self.job = _winjob.Job()
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | _winjob.CREATE_SUSPENDED
        return {"creationflags": flags}

    def started(self, process: subprocess.Popen[bytes]) -> None:
        if self.job is not None:
            self.job.adopt(process)

    def kill(self, process: subprocess.Popen[bytes]) -> None:
        """Kill the run and its descendants. Call only before it is reaped."""
        if self.job is not None:
            self.job.terminate()
        elif sys.platform != "win32" and process.returncode is None:
            with contextlib.suppress(OSError):
                os.killpg(process.pid, _KILL)
        with contextlib.suppress(OSError):
            process.kill()

    def end(self) -> None:
        if self.job is not None:
            self.job.close()
            self.job = None


# ─── the server ──────────────────────────────────────────────────────────────


def _positive_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout_s must be a number of seconds")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("timeout_s must be finite and above zero")
    return seconds


def _positive_bytes(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("output_cap_bytes must be an int")
    if value <= 0:
        raise ValueError("output_cap_bytes must be above zero")
    return value


def _commands_body(leaves: Mapping[tuple[str, ...], _Leaf]) -> bytes:
    """The `/api/commands` body. The home folder is data from the machine,
    like a file name: escaped as a run's output is. `<`, `>` and `&` occur
    only inside JSON strings, so translating the whole document is safe."""
    return (
        json.dumps(
            {
                "commands": [_describe(leaf) for leaf in leaves.values()],
                "home": _home(),
                "sep": os.sep,
            },
            ensure_ascii=True,
        )
        .translate(_JSON_EXTRA_ESCAPES)
        .encode("ascii")
    )


class UiServer(http.server.ThreadingHTTPServer):
    """The `wowlab ui` server. Built by `make_server`, bound and not yet
    serving."""

    daemon_threads = True
    token: str
    url: str
    wowlab_argv: Sequence[str]

    def __init__(
        self,
        leaves: Mapping[tuple[str, ...], _Leaf],
        wowlab_argv: Sequence[str],
        timeout_s: float,
        output_cap_bytes: int,
    ) -> None:
        self.leaves = leaves
        self.wowlab_argv = tuple(wowlab_argv)
        self.timeout_s = timeout_s
        self.output_cap_bytes = output_cap_bytes
        self.token = secrets.token_urlsafe(32)
        self._runs_lock = threading.Lock()
        self._runs: set[int] = set()
        self._closing = threading.Event()
        self._idle = threading.Condition(self._runs_lock)
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)
        # Confirm tokens not yet spent: token -> (the plan argv it was minted
        # for, the confirmed argv it runs). In memory only, so they expire
        # with the server.
        self._confirms: collections.OrderedDict[str, tuple[tuple[str, ...], tuple[str, ...]]]
        self._confirms = collections.OrderedDict()
        self._confirms_lock = threading.Lock()
        super().__init__((_LOOPBACK, 0), _Handler)
        self.port = int(self.server_address[1])
        self.url = f"http://{_LOOPBACK}:{self.port}/?token={self.token}"
        self.hosts = frozenset({f"{_LOOPBACK}:{self.port}", f"localhost:{self.port}"})
        self.commands_body = _commands_body(leaves)

    @override
    def server_bind(self) -> None:
        """Bind without `HTTPServer.server_bind`'s `socket.getfqdn()`, a
        name lookup that can stall startup."""
        socketserver.TCPServer.server_bind(self)
        self.server_name = _LOOPBACK
        self.server_port = int(self.server_address[1])

    @override
    def handle_error(self, request: Any, client_address: Any) -> None:
        """Print nothing: the default prints a traceback that can hold the
        request line, and with it the token."""

    @override
    def server_close(self) -> None:
        """Stop accepting, then kill every run in progress with its whole
        tree and wait (bounded) for them to end."""
        self._closing.set()
        with self._confirms_lock:
            self._confirms.clear()
        super().server_close()
        deadline = time.monotonic() + _CLOSE_WAIT_S
        with self._idle:
            while self._runs and time.monotonic() < deadline:
                self._idle.wait(_POLL_S)

    def run(self, argv: list[str]) -> _Run:
        """One run of `wowlab` with `argv`, bounded in time and output."""
        with self._slot():
            return self._start(argv)

    @contextlib.contextmanager
    def _slot(self) -> Iterator[None]:
        if not self._slots.acquire(blocking=False):
            raise _RefusedError(503, "busy")
        try:
            yield
        finally:
            self._slots.release()

    def run_leaf(self, leaf: _Leaf, params: Mapping[str, object], token: str | None) -> _Run:
        """One request's run. With no token, the leaf's own run, or its plan
        run for a leaf of the confirm step, which mints a token only when it
        ends with its shape's marker. With a token, the confirmed run that
        token was minted for, and only when this request's plan argv is the
        one it was minted for; else 403 `confirm`, and nothing runs."""
        if token is None:
            built = _build(leaf, params)
            plan = built.plan()
            run = self.run(plan)
            if leaf.confirm is not None:
                offer = _offer(leaf, run)
                # The confirmed argv is made here, from the plan's own report
                # (its pin), never from the request that confirms it.
                minted = self._mint(plan, built.confirmed(offer.pin)) if offer.marked else None
                run.confirm = {
                    "stage": "plan",
                    "token": minted,
                    "note": offer.note,
                    "needs": offer.needs,
                }
            return run
        # The slot first, so that a busy server does not spend a token.
        with self._slot():
            bound = self._spend(token)
            if bound is None:
                raise _RefusedError(403, _CONFIRM_REFUSED)
            if leaf.confirm is None:
                # An excluded leaf is refused as it is without a token.
                code = "excluded" if leaf.excluded is not None else _CONFIRM_REFUSED
                raise _RefusedError(403, code)
            shown, confirmed = bound
            if tuple(_build(leaf, params).plan()) != shown:
                raise _RefusedError(403, _CONFIRM_REFUSED)
            run = self._start(list(confirmed))
        run.confirm = {
            "stage": "confirmed",
            "token": None,
            "note": _confirmed_note(leaf, run),
            "needs": None,
        }
        return run

    def _mint(self, plan: Sequence[str], confirmed: Sequence[str]) -> str:
        """A one-time token for `confirmed`, bound to the plan it was shown as."""
        token = secrets.token_urlsafe(32)
        with self._confirms_lock:
            self._confirms[token] = (tuple(plan), tuple(confirmed))
            while len(self._confirms) > MAX_PENDING_CONFIRMS:
                self._confirms.popitem(last=False)
        return token

    def _spend(self, token: str) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        """The argvs a token was minted for, forgotten as they are read: a
        token is spent the first time it is presented, matched or not."""
        with self._confirms_lock:
            return self._confirms.pop(token, None)

    def _start(self, argv: list[str]) -> _Run:
        capture = _Capture(self.output_cap_bytes)
        tree = _Tree()
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            with self._runs_lock:
                if self._closing.is_set():
                    raise _RefusedError(503, "closing")
                # stdin is a pipe whose writing end is closed at once: the
                # run reads end of file and is not a terminal on every
                # platform. DEVNULL is not enough: on Windows the NUL device
                # is a character device, and `isatty()` is True for it, so a
                # command that asks only at a terminal would think it could.
                process = subprocess.Popen(
                    [*self.wowlab_argv, *argv],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    shell=False,
                    **tree.popen_kwargs(),
                )
                assert process.stdin is not None
                with contextlib.suppress(OSError):
                    process.stdin.close()
                self._runs.add(process.pid)
            try:
                tree.started(process)
                return self._watch(process, tree, capture)
            finally:
                with self._idle:
                    self._runs.discard(process.pid)
                    self._idle.notify_all()
        finally:
            tree.end()

    def _watch(self, process: subprocess.Popen[bytes], tree: _Tree, capture: _Capture) -> _Run:
        assert process.stdout is not None and process.stderr is not None
        readers = [
            threading.Thread(target=capture.pump, args=(process.stdout, "stdout"), daemon=True),
            threading.Thread(target=capture.pump, args=(process.stderr, "stderr"), daemon=True),
        ]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + self.timeout_s
        timed_out = False
        ended = False
        while True:
            try:
                process.wait(_POLL_S)
                ended = True
                break
            except subprocess.TimeoutExpired:
                pass
            if capture.full.is_set() or self._closing.is_set():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
        if not ended:
            # Not reaped yet, so its pid and process group are still its own.
            tree.kill(process)
            process.wait()
        # Windows: whatever the run left behind dies with its job here.
        tree.end()
        for reader in readers:
            reader.join(_DRAIN_GRACE_S)
        if not any(reader.is_alive() for reader in readers):
            process.stdout.close()
            process.stderr.close()
        # A reader still alive here means a descendant still holds the pipe
        # (POSIX: one left behind by a run that exited on its own, or one
        # that called `setsid`). The reader is abandoned and stops reading
        # at the cap.
        return _Run(
            exit_code=int(process.returncode),
            stdout=capture.body("stdout"),
            stderr=capture.body("stderr"),
            timed_out=timed_out,
            truncated=capture.truncated,
        )


# ─── requests ────────────────────────────────────────────────────────────────

_DIGITS = re.compile(r"[0-9]+")


class _Handler(http.server.BaseHTTPRequestHandler):
    """One request. Nothing here logs, and no response echoes the request."""

    server: UiServer
    server_version = "wowlab"
    sys_version = ""
    protocol_version = "HTTP/1.0"
    timeout = _SOCKET_TIMEOUT_S

    # ── output ──

    @override
    def version_string(self) -> str:
        return self.server_version

    @override
    def log_message(self, format: str, *args: Any) -> None:
        """Nothing is logged: a request line holds the token."""

    @override
    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", _shell()[1])
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    def _reply(self, status: int, body: bytes, content_type: str) -> None:
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("ascii")
        self._reply(status, body, "application/json")

    def _error(self, status: int, code: str) -> None:
        self._json(status, {"error": code})

    @override
    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """A bare JSON error. The default page quotes the request line,
        which holds the token."""
        names = {400: "bad_request", 404: "not_found", 408: "timeout", 414: "too_long"}
        self._error(code, names.get(code, "error"))

    # ── checks ──

    def _check(self) -> str | None:
        """Host, Origin and the token, in that order; the refusal code, or None."""
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or hosts[0] not in self.server.hosts:
            return "host"
        origins = self.headers.get_all("Origin")
        if origins is not None and (len(origins) != 1 or origins[0] != "http://" + hosts[0]):
            return "origin"
        query = self.path.partition("?")[2].partition("#")[0]
        try:
            fields = urllib.parse.parse_qs(query, keep_blank_values=True, max_num_fields=32)
        except ValueError:
            return "token"
        given = fields.get("token", [])
        if len(given) != 1:
            return "token"
        expected = self.server.token.encode("ascii")
        if not secrets.compare_digest(given[0].encode("utf-8", "surrogatepass"), expected):
            return "token"
        return None

    def _route(self) -> str:
        return self.path.partition("?")[0].partition("#")[0]

    def _checked(self) -> str | None:
        """The request's path once it passed the checks, else None (refused)."""
        refusal = self._check()
        if refusal is not None:
            self._error(403, refusal)
            return None
        return self._route()

    # ── methods ──

    def do_GET(self) -> None:
        path = self._checked()
        if path is None:
            return
        files = static_files()
        if path in files:
            self._reply(200, files[path], "text/html; charset=utf-8")
        elif path == "/api/commands":
            self._reply(200, self.server.commands_body, "application/json")
        elif path == "/api/run":
            self._error(405, "method_not_allowed")
        else:
            self._error(404, "not_found")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_OPTIONS(self) -> None:
        if self._checked() is not None:
            self._error(405, "method_not_allowed")

    def do_POST(self) -> None:
        path = self._checked()
        if path is None:
            return
        if path != "/api/run":
            if path in static_files():
                self._error(405, "method_not_allowed")
            else:
                self._error(404, "not_found")
            return
        body = self._body()
        if body is None:
            return
        try:
            request = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            self._error(400, "bad_request")
            return
        try:
            run = self._run(request)
        except _RefusedError as refused:
            self._error(refused.status, refused.code)
            return
        except OSError:
            self._error(500, "run_failed")
            return
        pieces = run.response()
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(sum(len(piece) for piece in pieces)))
        self.send_header("Connection", "close")
        self.end_headers()
        for piece in pieces:
            self.wfile.write(piece)

    def _body(self) -> bytes | None:
        """The POST body, read only once it is known to be small enough."""
        if self.headers.get_all("Transfer-Encoding"):
            self._error(400, "bad_request")
            return None
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or not _DIGITS.fullmatch(lengths[0].strip()):
            self._error(411 if not lengths else 400, "bad_request")
            return None
        digits = lengths[0].strip()
        if len(digits) > 12 or int(digits) > MAX_BODY_BYTES:
            self._error(413, "too_large")
            return None
        length = int(digits)
        body = self.rfile.read(length)
        if len(body) != length:
            self._error(400, "bad_request")
            return None
        return body

    def _run(self, request: object) -> _Run:
        if not isinstance(request, dict):
            raise _RefusedError(400, "bad_request")
        command = request.get("command")
        params = request.get("params", {})
        if not isinstance(command, list) or not all(isinstance(p, str) for p in command):
            raise _RefusedError(400, "bad_request")
        if not isinstance(params, dict):
            raise _RefusedError(400, "bad_request")
        token = request.get("confirm")
        if "confirm" in request and (
            not isinstance(token, str) or not 0 < len(token) <= _MAX_CONFIRM_TOKEN
        ):
            raise _RefusedError(400, "bad_request")
        leaf = self.server.leaves.get(tuple(command))
        if leaf is None:
            if token is not None:
                self.server._spend(token)  # presented, so spent
            raise _RefusedError(403, "not_a_command")
        return self.server.run_leaf(leaf, params, token)


# ─── the entry point ─────────────────────────────────────────────────────────


def _default_wowlab_argv() -> list[str]:
    """The running interpreter by absolute path, never a name on PATH, with
    the working directory kept off the import path (`-P`)."""
    if not sys.executable or not Path(sys.executable).is_absolute():
        raise RuntimeError("the running interpreter has no absolute path to start runs with")
    return [sys.executable, "-P", "-m", "wowlab_core"]


def make_server(
    *,
    command: Any = None,
    wowlab_argv: Sequence[str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES,
) -> UiServer:
    """The `wowlab ui` server on `127.0.0.1` at a free port, bound and not
    yet serving: the caller runs `serve_forever()`, then `shutdown()` and
    `server_close()`.

    `command` is the root Click command whose leaves may run (`None`: the
    CLI's own). `wowlab_argv` starts "the same `wowlab`" (`None`: this
    interpreter, `-P -m wowlab_core`). `timeout_s` and `output_cap_bytes`
    bound every run and cannot be turned off."""
    seconds = _positive_seconds(timeout_s)
    cap = _positive_bytes(output_cap_bytes)
    if command is None:
        import typer

        from wowlab_core import cli

        command = typer.main.get_command(cli.app)
    if wowlab_argv is None:
        wowlab_argv = _default_wowlab_argv()
    if isinstance(wowlab_argv, str) or not wowlab_argv:
        raise TypeError("wowlab_argv must be a non-empty sequence of strings")
    return UiServer(_read_tree(command), [str(a) for a in wowlab_argv], seconds, cap)
