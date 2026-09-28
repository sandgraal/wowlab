"""The combat log: a streaming tokenizer (docs/LAB_PLAN.md §6.8, M10-13).

Grammar per docs/LAB_FORMATS.md §8 and its amendment of 2026-09-28, which
records what the one real Forever log shows. The Forever client does write a
usable combat log (M10-03), so this module is graded on that capture, and on
constructed lines for what it lacks. There are no event semantics in Wave 1:
a line becomes a timestamp, an event name and a list of fields, and nothing
here knows what any field means.

Read-only (L1): files are opened ``"rb"``, one read at a time, and closed
between polls. Nothing is written anywhere.

Lines. A line ends at LF. Its ending is ``"\\r\\n"`` when a CR precedes the
LF, ``"\\n"`` otherwise, and ``""`` for a last line with no break. A lone CR
stays in the line's text. Bytes are decoded as UTF-8 with
``surrogateescape``, so a byte that is not UTF-8 survives as a lone surrogate
and encodes back to the same byte. Every line is yielded, in order, as a
:class:`Record` or an :class:`Unparsed`; each keeps its text (``raw``), its
ending and its byte offset in the file, so for a file with no line over
:data:`MAX_LINE_BYTES` the lines rebuild it byte for byte.

A line over :data:`MAX_LINE_BYTES` (hostile input; no real line is near it)
is yielded as :class:`Unparsed` holding its first ``MAX_LINE_BYTES`` bytes,
and the rest of it is skipped, so memory stays bounded.

Record grammar (anything else is :class:`Unparsed`, with a reason and, where
there is one, the column):

- ``<timestamp>`` two spaces ``<event>`` ``,`` ``<field>`` ``,`` ...
- The timestamp is kept as written. It is checked for shape only, never as a
  date or a time: the committed fixtures carry timestamps shifted by the
  capture tool (docs/LAB_PLAN.md §8, amendment 2026-09-24), and a record's
  timestamp is not a real time. Accepted: ``M/D[/YYYY] H:MM:SS[.f][offset]``
  with a one- or two-digit month, day and hour, padded or not, an optional
  four-digit year (older retail logs omit it **[verify]**), any number of
  fraction digits, and an optional UTC-offset suffix ``+h``/``-h``, with or
  without minutes (``-4``, ``+5:30``, ``+0530``). The real log shows
  ``M/D/YYYY HH:MM:SS.mmm-4``; a positive, half-hour or UTC offset is
  **[verify]**.
- The event name is the first field; it must be a bare run of ASCII letters,
  digits and ``_``.
- A field is a bare token, a quoted string or a group. A bare token is kept
  as text (``nil``, ``0x511``, ``-1``, ``1.60.1``, ``Player-...``, and the
  empty token between two commas); nothing is converted to a number.
- A quoted string starts with ``"``. The client's escaping of a quote inside
  a quoted string is not known (no real line has one) **[verify]**, so no
  escape is interpreted: the string ends at the first ``"`` followed by
  ``,``, ``]``, ``)`` or the end of the line, and everything between the
  quotes is the value, verbatim. A quote or comma inside the string is
  therefore kept, unless the quote is directly followed by one of those four.
- A group is ``[...]`` or ``(...)`` holding fields (possibly none), nested to
  at most :data:`MAX_DEPTH` levels, and becomes a :class:`Group` whose
  ``items`` are its fields (nested lists). ``COMBATANT_INFO`` is the record
  known to carry them (community documentation; no real fixture yet
  **[verify]**).

Following. :func:`follow` yields lines as the client appends them. The
client flushes in batches, so a partial last line is buffered until its line
break arrives. It survives truncation (the file shrinks, or the bytes before
the read position change), replacement (a different file under the same
name) and rotation (a new ``WoWCombatLog*.txt`` in the same folder), and
yields a :class:`Following` whenever it starts on a file. A partial line left
behind by any of those three is yielded as :class:`Unparsed`, never as a
record, since its end never came.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import Annotated, BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Discriminator

__all__ = [
    "MAX_DEPTH",
    "MAX_LINE_BYTES",
    "Bracket",
    "Ending",
    "Entry",
    "FieldValue",
    "Following",
    "Group",
    "Quoted",
    "Record",
    "Unparsed",
    "find_logs",
    "follow",
    "is_log_name",
    "newest_log",
    "read_log",
    "tail",
    "tokenize",
    "tokenize_line",
]

MAX_LINE_BYTES = 1 << 20
"""A longer line is yielded as :class:`Unparsed`, truncated to this size."""

MAX_DEPTH = 32
"""Deepest group nesting a record may have; deeper is :class:`Unparsed`."""

_CHUNK = 1 << 20  # bytes read per open of a file
_FINGERPRINT = 64  # bytes before the read position checked for a rewrite

Ending = Literal["\r\n", "\n", ""]
Bracket = Literal["[", "("]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Quoted(_Model):
    """A quoted string field; ``text`` is what stood between the quotes."""

    text: str


class Group(_Model):
    """A ``[...]`` or ``(...)`` group; ``items`` are its fields, in order."""

    bracket: Bracket
    items: tuple[FieldValue, ...]


type FieldValue = str | Quoted | Group
"""A bare token (as written), a quoted string, or a group."""

Group.model_rebuild()


class Record(_Model):
    """A line that tokenized: ``<timestamp>  <event>,<fields...>``."""

    kind: Literal["record"] = "record"
    offset: int  # byte offset of the line in its file
    timestamp: str  # as written; shifted in the committed fixtures, never a real time
    event: str
    fields: tuple[FieldValue, ...]  # after the event name
    raw: str  # the line's text without its ending
    ending: Ending


class Unparsed(_Model):
    """A line the grammar does not cover, kept whole (L4)."""

    kind: Literal["unparsed"] = "unparsed"
    offset: int
    raw: str
    ending: Ending
    reason: str
    column: int | None = None  # 0-based position in ``raw`` where tokenizing stopped


type Entry = Annotated[Record | Unparsed, Discriminator("kind")]


class Following(_Model):
    """Yielded by :func:`follow` when it starts reading a file.

    ``start``: the first file. ``rotated``: a combat log that was not in the
    folder before. ``truncated``: the same file became shorter, or its bytes
    before the read position changed; read again from the start.
    ``replaced``: a different file now has the same name; read from the start.
    """

    kind: Literal["following"] = "following"
    path: Path
    reason: Literal["start", "rotated", "truncated", "replaced"]


# ─── one line ────────────────────────────────────────────────────────────────

_TIMESTAMP = re.compile(
    r"\d{1,2}/\d{1,2}(?:/\d{4})? \d{1,2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{1,2}(?::?\d{2})?)?"
)
_EVENT = re.compile(r"[A-Za-z0-9_]+")
_BARE = re.compile(r'[^,\[\]()"]*')
_SPECIAL = re.compile(r'["\[\]()]')
_GROUPING = re.compile(r"[\[\]()]")
_OPENER: dict[str, Bracket] = {"[": "[", "(": "("}
_CLOSER = {"[": "]", "(": ")"}
_AFTER_QUOTE = frozenset({",", "]", ")"})


class _TokenError(Exception):
    def __init__(self, reason: str, column: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.column = column


def _fields(body: str, base: int) -> list[FieldValue]:
    """The comma-separated fields of ``body``; ``base`` is its column in the line."""
    if not _SPECIAL.search(body):
        return list[FieldValue](body.split(","))
    if not _GROUPING.search(body):
        quick = _quoted_fields(body)
        if quick is not None:
            return quick
    return _scan(body, base)


def _quoted_fields(body: str) -> list[FieldValue] | None:
    """Fields of a line with quotes and no groups, or None to let :func:`_scan`
    find the error. The same rule as the scanner: with no ``]`` or ``)`` in
    the line, a quoted string closes at the first later ``"`` that ends a
    comma-separated piece."""
    pieces = body.split(",")
    n = len(pieces)
    out: list[FieldValue] = []
    i = 0
    while i < n:
        piece = pieces[i]
        if piece.startswith('"'):
            j = i
            text = piece
            while len(text) < 2 or not text.endswith('"'):
                j += 1
                if j == n:
                    return None
                text = text + "," + pieces[j]
            out.append(Quoted(text=text[1:-1]))
            i = j + 1
        elif '"' in piece:
            return None
        else:
            out.append(piece)
            i += 1
    return out


def _scan(body: str, base: int) -> list[FieldValue]:
    """The general tokenizer: quoted strings and nested groups."""
    n = len(body)
    top: list[FieldValue] = []
    stack: list[tuple[Bracket, int, list[FieldValue], list[FieldValue]]] = []
    current = top
    i = 0
    while True:
        # One field at i.
        c = body[i] if i < n else ""
        if c == '"':
            j = i + 1
            while True:
                k = body.find('"', j)
                if k == -1:
                    raise _TokenError("a quoted string is not closed", base + i)
                if k + 1 == n or body[k + 1] in _AFTER_QUOTE:
                    break
                j = k + 1
            current.append(Quoted(text=body[i + 1 : k]))
            i = k + 1
        elif c in _OPENER:
            if len(stack) >= MAX_DEPTH:
                raise _TokenError(f"groups nested deeper than {MAX_DEPTH}", base + i)
            items: list[FieldValue] = []
            stack.append((_OPENER[c], i, current, items))
            current = items
            i += 1
            if i < n and body[i] == _CLOSER[c]:
                pass  # an empty group; closed below
            else:
                continue
        else:
            m = _BARE.match(body, i)
            assert m is not None  # the pattern matches the empty string
            j = m.end()
            if j < n and body[j] in '"[(':
                raise _TokenError(f"{body[j]!r} inside a bare field", base + j)
            current.append(body[i:j])
            i = j
        # After a field: close groups, then a comma or the end.
        while i < n and body[i] in "])":
            if not stack:
                raise _TokenError(f"{body[i]!r} closes no group", base + i)
            opener, _, parent, items = stack.pop()
            if body[i] != _CLOSER[opener]:
                raise _TokenError(f"{body[i]!r} closes a group opened with {opener!r}", base + i)
            parent.append(Group(bracket=opener, items=tuple(items)))
            current = parent
            i += 1
        if i == n:
            if stack:
                raise _TokenError(
                    f"a group opened with {stack[-1][0]!r} is not closed", base + stack[-1][1]
                )
            return top
        if body[i] != ",":
            raise _TokenError(
                f"{body[i]!r} where a comma or the end of the line should be", base + i
            )
        i += 1


def tokenize_line(text: str, *, offset: int = 0, ending: Ending = "") -> Record | Unparsed:
    """One line's text (without its ending) as a :class:`Record`, or :class:`Unparsed`."""
    sep = text.find("  ")
    if sep == -1:
        return Unparsed(
            offset=offset, raw=text, ending=ending, reason="no two spaces after a timestamp"
        )
    timestamp = text[:sep]
    if not _TIMESTAMP.fullmatch(timestamp):
        return Unparsed(
            offset=offset,
            raw=text,
            ending=ending,
            reason="the text before the two spaces is not a timestamp",
            column=0,
        )
    try:
        fields = _fields(text[sep + 2 :], sep + 2)
    except _TokenError as stop:
        return Unparsed(
            offset=offset, raw=text, ending=ending, reason=stop.reason, column=stop.column
        )
    event = fields[0]
    if not isinstance(event, str) or not _EVENT.fullmatch(event):
        return Unparsed(
            offset=offset,
            raw=text,
            ending=ending,
            reason="the first field is not an event name",
            column=sep + 2,
        )
    return Record(
        offset=offset,
        timestamp=timestamp,
        event=event,
        fields=tuple(fields[1:]),
        raw=text,
        ending=ending,
    )


# ─── lines from bytes ────────────────────────────────────────────────────────


def _too_long(data: bytes, offset: int, ending: Ending) -> Unparsed:
    return Unparsed(
        offset=offset,
        raw=data[:MAX_LINE_BYTES].decode("utf-8", "surrogateescape"),
        ending=ending,
        reason=f"a line longer than {MAX_LINE_BYTES} bytes; only its start is kept",
    )


def _line(data: bytes, offset: int, ending: Ending) -> Record | Unparsed:
    if len(data) > MAX_LINE_BYTES:
        return _too_long(data, offset, ending)
    return tokenize_line(data.decode("utf-8", "surrogateescape"), offset=offset, ending=ending)


class _Splitter:
    """Bytes in, lines out; a partial last line waits for its line break."""

    def __init__(self, offset: int = 0) -> None:
        self.pending = b""
        self.start = offset  # file offset of pending[0]
        self.skipping = False  # inside a too-long line, dropping bytes to its LF

    def feed(self, chunk: bytes) -> list[Entry]:
        out: list[Entry] = []
        data = self.pending + chunk if self.pending else chunk
        pos = 0
        if self.skipping:
            nl = data.find(b"\n")
            if nl == -1:
                self.start += len(data)
                self.pending = b""
                return out
            pos = nl + 1
            self.skipping = False
        while True:
            nl = data.find(b"\n", pos)
            if nl == -1:
                break
            if nl > pos and data[nl - 1] == 0x0D:
                out.append(_line(data[pos : nl - 1], self.start + pos, "\r\n"))
            else:
                out.append(_line(data[pos:nl], self.start + pos, "\n"))
            pos = nl + 1
        rest = data[pos:]
        if len(rest) > MAX_LINE_BYTES:
            out.append(_too_long(rest, self.start + pos, ""))
            self.skipping = True
            self.start += len(data)
            self.pending = b""
        else:
            self.start += pos
            self.pending = rest
        return out

    def end(self) -> list[Entry]:
        """At the end of a file: the unterminated last line, tokenized."""
        rest, self.pending = self.pending, b""
        start, self.start = self.start, self.start + len(rest)
        return [_line(rest, start, "")] if rest else []

    def abandon(self, why: str) -> list[Entry]:
        """The file went away under a partial line: yield it, never as a record."""
        rest, self.pending = self.pending, b""
        self.skipping = False
        if not rest:
            return []
        return [
            Unparsed(
                offset=self.start,
                raw=rest.decode("utf-8", "surrogateescape"),
                ending="",
                reason=f"the file was {why} before this line's break was written",
            )
        ]


def tokenize(data: bytes, *, offset: int = 0) -> Iterator[Entry]:
    """Every line of ``data``, in order; an unterminated last line is tokenized too."""
    splitter = _Splitter(offset)
    yield from splitter.feed(data)
    yield from splitter.end()


def read_log(path: Path | str) -> Iterator[Entry]:
    """Every line of a combat log file, read in chunks (a log can be large)."""
    splitter = _Splitter()
    with Path(path).open("rb") as f:
        while chunk := f.read(_CHUNK):
            yield from splitter.feed(chunk)
    yield from splitter.end()


def tail(path: Path | str, n: int) -> tuple[list[Entry], int]:
    """The last ``n`` complete lines of a file, and the offset just after them.

    A partial last line (the client mid-flush) is not among them; the offset
    is where it starts, so ``follow(path, offset=...)`` picks it up once its
    line break arrives. Reads backwards from the end, not the whole file.
    """
    if n < 0:
        raise ValueError("n must be zero or more")
    with Path(path).open("rb") as f:
        size = os.fstat(f.fileno()).st_size
        start = size
        buf = b""
        cap = (n + 1) * (MAX_LINE_BYTES + 2) + _CHUNK
        while start > 0:
            step = min(_CHUNK, start)
            start -= step
            f.seek(start)
            buf = f.read(step) + buf
            if buf.count(b"\n") > n or len(buf) >= cap:
                break
    last = buf.rfind(b"\n")
    if last == -1:  # no line break at all: the only line is still being written
        return [], 0 if start == 0 else size
    end = start + last + 1
    body = buf[: last + 1]
    if start > 0:  # the window starts inside a line: drop that line
        first = body.find(b"\n")
        if first == len(body) - 1:
            return [], end
        body = body[first + 1 :]
        start += first + 1
    if n == 0:
        return [], end
    lines = _Splitter(start).feed(body)
    return lines[-n:], end


# ─── files in a Logs folder ──────────────────────────────────────────────────


def is_log_name(name: str) -> bool:
    """A combat log's file name, ``WoWCombatLog*.txt`` (case folded; LAB_FILE_MAP)."""
    folded = name.casefold()
    return folded.startswith("wowcombatlog") and folded.endswith(".txt")


def find_logs(directory: Path | str) -> list[Path]:
    """The combat logs directly in ``directory``, oldest first by modification
    time, then by name. Lists the folder and stats its entries; opens nothing."""
    found: list[tuple[int, str, Path]] = []
    with os.scandir(directory) as it:
        for entry in it:
            if not is_log_name(entry.name):
                continue
            try:
                if not entry.is_file():
                    continue
                mtime = entry.stat().st_mtime_ns
            except OSError:
                continue
            found.append((mtime, entry.name, Path(entry.path)))
    return [p for _, _, p in sorted(found)]


def newest_log(directory: Path | str) -> Path | None:
    """The combat log in ``directory`` written last, or None."""
    logs = find_logs(directory)
    return logs[-1] if logs else None


# ─── following ───────────────────────────────────────────────────────────────


class _Follower:
    """The state of :func:`follow`; each step reads at most one chunk."""

    def __init__(self, directory: Path, current: Path | None, offset: int | None) -> None:
        self.directory = directory
        self.current = current
        self.seen = {p.name for p in find_logs(directory)}
        self.identity: tuple[int, int] | None = None
        self.pos = 0
        self.tail = b""  # the last bytes before pos, to notice a rewrite
        self.splitter = _Splitter(0)
        self.start_offset = offset

    def _switch(self, path: Path, offset: int) -> None:
        self.current = path
        self.identity = None
        self.pos = offset
        self.tail = b""
        self.splitter = _Splitter(offset)

    def _open_first(self) -> Iterator[Entry | Following]:
        """Position on the first file: ``offset``, or its end when None."""
        assert self.current is not None
        path = self.current
        with path.open("rb") as f:
            st = os.fstat(f.fileno())
            offset = st.st_size if self.start_offset is None else self.start_offset
            offset = min(offset, st.st_size)
            self._switch(path, offset)
            self.identity = (st.st_dev, st.st_ino)
            if offset:
                keep = min(_FINGERPRINT, offset)
                f.seek(offset - keep)
                self.tail = f.read(keep)
        yield Following(path=path, reason="start")

    def step(self) -> tuple[list[Entry | Following], bool]:
        """What one poll found, and whether it made progress."""
        out: list[Entry | Following] = []
        if self.current is None:
            return self._rotate(out)
        try:
            with self.current.open("rb") as f:
                chunk = self._read(f, out)
        except FileNotFoundError:
            return self._rotate(out)
        if chunk:
            self.pos += len(chunk)
            self.tail = (self.tail + chunk)[-_FINGERPRINT:]
            out.extend(self.splitter.feed(chunk))
            return out, True
        if out:
            return out, True
        return self._rotate(out)

    def _read(self, f: BinaryIO, out: list[Entry | Following]) -> bytes:
        """The next chunk of the open current file, after checking that it is
        still the file, and the content, read so far."""
        assert self.current is not None
        st = os.fstat(f.fileno())
        identity = (st.st_dev, st.st_ino)
        if self.identity is None:
            self.identity = identity
        elif identity != self.identity:
            out.extend(self.splitter.abandon("replaced"))
            self._switch(self.current, 0)
            self.identity = identity
            out.append(Following(path=self.current, reason="replaced"))
        if self.pos > st.st_size or not self._same_tail(f):
            out.extend(self.splitter.abandon("truncated"))
            self._switch(self.current, 0)
            self.identity = identity
            out.append(Following(path=self.current, reason="truncated"))
        f.seek(self.pos)
        return f.read(_CHUNK)

    def _same_tail(self, f: BinaryIO) -> bool:
        if not self.tail:
            return True
        f.seek(self.pos - len(self.tail))
        return f.read(len(self.tail)) == self.tail

    def _rotate(self, out: list[Entry | Following]) -> tuple[list[Entry | Following], bool]:
        """At the end of the current file: switch to a log that is new in the folder."""
        try:
            logs = find_logs(self.directory)
        except FileNotFoundError:
            return out, False
        names = {p.name for p in logs}
        new = [p for p in logs if p.name not in self.seen]
        self.seen = names
        if not new:
            return out, bool(out)
        target = new[-1]
        out.extend(self.splitter.abandon("rotated"))
        self._switch(target, 0)
        out.append(Following(path=target, reason="rotated"))
        return out, True


def follow(
    path: Path | str,
    *,
    offset: int | None = None,
    poll_interval: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> Generator[Entry | Following, None, None]:
    """Yield lines as the client appends them, forever (close the generator to stop).

    ``path`` is a combat log file, or a ``Logs`` folder, in which case the
    newest combat log in it is followed (and, if there is none yet, the first
    one that appears). The first file is read from ``offset`` (a line
    start, such as the offset :func:`tail` returns), or from its end when
    ``offset`` is None. Files that appear later are read from the start.

    A :class:`Following` is yielded when a file is started: ``start`` for the
    first, then ``rotated``, ``truncated`` or ``replaced`` as described on
    that class. Rotation is noticed only at the end of the current file, so a
    file's lines are all yielded before the next file's. When nothing new was
    found, ``sleep(poll_interval)`` is called before the next look.
    """
    path = Path(path)
    if path.is_dir():
        directory, current = path, newest_log(path)
    elif path.is_file():
        directory, current = path.parent, path
    else:
        raise FileNotFoundError(f"no such file or folder: {path}")
    follower = _Follower(directory, current, offset)
    if current is not None:
        yield from follower._open_first()
    while True:
        found, progressed = follower.step()
        yield from found
        if not progressed:
            sleep(poll_interval)
