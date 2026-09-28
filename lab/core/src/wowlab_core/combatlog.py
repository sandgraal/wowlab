"""The combat log: a streaming tokenizer (docs/LAB_PLAN.md §6.8, M10-13).

Grammar per docs/LAB_FORMATS.md §8 and its amendment of 2026-09-28, which
records what the one real Forever log shows. The Forever client does write a
usable combat log (M10-03), so this module is graded on that capture, and on
constructed lines for what it lacks. There are no event semantics in Wave 1:
a line becomes a timestamp, an event name and a list of fields, and nothing
here knows what any field means.

Read-only (L1): files are opened read-only (on POSIX also non-blocking, so a
FIFO under a log's name cannot hang a read), must be regular files, are read
one chunk at a time, and are closed between polls. Nothing is written
anywhere.

Lines. A line ends at LF. Its ending is ``"\\r\\n"`` when a CR precedes the
LF, ``"\\n"`` otherwise, and ``""`` for a last line with no break. A lone CR
stays in the line's text. Bytes are decoded as UTF-8 with
``surrogateescape``, so a byte that is not UTF-8 survives as a lone surrogate
and encodes back to the same byte. Every line is yielded, in order, as a
:class:`Record` or an :class:`Unparsed`; each keeps its text (``raw``), its
ending and its byte offset in the file, so for a file with no line over
:data:`MAX_LINE_BYTES` the lines rebuild it byte for byte.

The one exception to that (docs/LAB_PLAN.md §6.8, amendment 2026-09-28): a
line over :data:`MAX_LINE_BYTES` (hostile input; no real line is near it) is
yielded as :class:`Unparsed` holding only its first ``MAX_LINE_BYTES`` bytes,
with ``truncated`` set, its full byte ``length`` and its real ending, and the
rest of it is skipped, so memory stays bounded. The same line gives the same
entry however the file is chunked.

Record grammar (anything else is :class:`Unparsed`, with a reason and, where
there is one, the column):

- ``<timestamp>`` two spaces ``<event>`` ``,`` ``<field>`` ``,`` ...
- The timestamp is kept as written: the client's local clock time for the
  event, followed by the UTC offset where the client writes one. It is
  checked for shape only, never parsed as a date or a time. In a live log it
  is the real local time. In the committed fixtures the capture tool shifts
  it (docs/LAB_PLAN.md §8, amendment 2026-09-24), so nothing here or in the
  tests treats it as real. Accepted: ``M/D[/YYYY] H:MM:SS[.f][offset]`` with
  a one- or two-digit month, day and hour, padded or not, an optional
  four-digit year (older retail logs have neither the year nor the offset,
  ``M/D H:MM:SS.mmm``, per community documentation **[verify]**; whether
  month comes before day on non-US clients is **[verify]**, and nothing here
  depends on it), any number of fraction digits, and an optional UTC-offset
  suffix ``+h``/``-h``, with or without minutes (``-4``, ``+5:30``,
  ``+0530``). The real log shows ``M/D/YYYY HH:MM:SS.mmm-4``. The half-hour
  shapes accepted are guesses; how the client writes a half-hour or zero
  offset is unknown **[verify]**, and a shape not accepted makes every line
  Unparsed, which is reported, not hidden.
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

Tokenizing is linear in the line's length on every path, hostile ones
included.

Following. :func:`follow` yields lines as the client appends them. The
client flushes in batches, so a partial last line is buffered until its line
break arrives. It survives truncation (the file shrinks, or the bytes before
the read position change), replacement (a different file under the same
name) and rotation (new ``WoWCombatLog*.txt`` files in the same folder, each
read in turn, oldest first), and yields a :class:`Following` whenever it
starts on a file. A partial line left behind by any of those three is
yielded as :class:`Unparsed`, never as a record, since its end never came.
"""

from __future__ import annotations

import os
import re
import stat
import sys
import time
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import Annotated, BinaryIO, Literal, cast

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
    "NotARegularFileError",
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
_CR = 0x0D

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
    timestamp: (
        str  # as written: the client's local time (shifted in the committed fixtures); never parsed
    )
    event: str
    fields: tuple[FieldValue, ...]  # after the event name
    raw: str  # the line's text without its ending
    ending: Ending


class Unparsed(_Model):
    """A line the grammar does not cover, kept whole (L4) unless ``truncated``."""

    kind: Literal["unparsed"] = "unparsed"
    offset: int
    raw: str  # the line's text without its ending; its first MAX_LINE_BYTES when truncated
    ending: Ending
    reason: str
    column: int | None = None  # 0-based position in ``raw`` where tokenizing stopped
    length: int  # bytes in the whole line, without its ending
    truncated: bool = False  # ``raw`` holds only the line's first MAX_LINE_BYTES bytes


type Entry = Annotated[Record | Unparsed, Discriminator("kind")]


class Following(_Model):
    """Yielded by :func:`follow` when it starts reading a file.

    ``start``: the first file (from the folder, the first log to appear in
    it if it had none). ``rotated``: a combat log that was not in the folder
    before. ``truncated``: the same file became shorter, or its bytes before
    the read position changed; read again from the start. ``replaced``: a
    different file now has the same name; read from the start.
    """

    kind: Literal["following"] = "following"
    path: Path
    reason: Literal["start", "rotated", "truncated", "replaced"]


class NotARegularFileError(OSError):
    """A log's name that is not a regular file (a FIFO, a device, a folder)."""


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
    comma-separated piece. Linear: each piece is looked at once, and a quoted
    string's pieces are joined once."""
    pieces = body.split(",")
    n = len(pieces)
    out: list[FieldValue] = []
    i = 0
    while i < n:
        piece = pieces[i]
        if piece.startswith('"'):
            if len(piece) >= 2 and piece.endswith('"'):
                out.append(Quoted(text=piece[1:-1]))
                i += 1
                continue
            j = i + 1
            while j < n and not pieces[j].endswith('"'):
                j += 1
            if j == n:
                return None
            out.append(Quoted(text=",".join(pieces[i : j + 1])[1:-1]))
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


def _byte_length(text: str) -> int:
    try:
        return len(text.encode("utf-8", "surrogateescape"))
    except UnicodeEncodeError:  # a surrogate that did not come from a byte
        return len(text.encode("utf-8", "surrogatepass"))


def tokenize_line(
    text: str, *, offset: int = 0, ending: Ending = "", length: int | None = None
) -> Record | Unparsed:
    """One line's text (without its ending) as a :class:`Record`, or :class:`Unparsed`.

    ``length`` is the line's size in bytes, if the caller knows it; it is
    only used for an :class:`Unparsed`, and computed from ``text`` if not given.
    """

    def unparsed(reason: str, column: int | None = None) -> Unparsed:
        return Unparsed(
            offset=offset,
            raw=text,
            ending=ending,
            reason=reason,
            column=column,
            length=_byte_length(text) if length is None else length,
        )

    sep = text.find("  ")
    if sep == -1:
        return unparsed("no two spaces after a timestamp")
    timestamp = text[:sep]
    if not _TIMESTAMP.fullmatch(timestamp):
        return unparsed("the text before the two spaces is not a timestamp", 0)
    try:
        fields = _fields(text[sep + 2 :], sep + 2)
    except _TokenError as stop:
        return unparsed(stop.reason, stop.column)
    event = fields[0]
    if not isinstance(event, str) or not _EVENT.fullmatch(event):
        return unparsed("the first field is not an event name", sep + 2)
    return Record(
        offset=offset,
        timestamp=timestamp,
        event=event,
        fields=tuple(fields[1:]),
        raw=text,
        ending=ending,
    )


# ─── lines from bytes ────────────────────────────────────────────────────────

_TOO_LONG = f"a line longer than {MAX_LINE_BYTES} bytes; only its start is kept"


def _too_long(head: bytes, offset: int, ending: Ending, length: int) -> Unparsed:
    return Unparsed(
        offset=offset,
        raw=head[:MAX_LINE_BYTES].decode("utf-8", "surrogateescape"),
        ending=ending,
        reason=_TOO_LONG,
        length=length,
        truncated=True,
    )


def _line(data: bytes, offset: int, ending: Ending) -> Record | Unparsed:
    if len(data) > MAX_LINE_BYTES:
        return _too_long(data, offset, ending, len(data))
    return tokenize_line(
        data.decode("utf-8", "surrogateescape"), offset=offset, ending=ending, length=len(data)
    )


class _Long:
    """A line over MAX_LINE_BYTES whose break has not been seen yet."""

    def __init__(self, head: bytes, offset: int, length: int) -> None:
        self.head = head[:MAX_LINE_BYTES]
        self.offset = offset
        self.length = length  # bytes so far, a trailing CR included
        self.last_cr = head.endswith(b"\r")


class _Splitter:
    """Bytes in, lines out; a partial last line waits for its line break.

    Memory is bounded: a pending line over MAX_LINE_BYTES keeps only its head
    and a count, and is yielded when its break arrives, with its real ending,
    exactly as if the whole line had been read at once.
    """

    def __init__(self, offset: int = 0) -> None:
        self.pending = b""
        self.start = offset  # file offset of pending[0], or of the next byte fed
        self.long: _Long | None = None

    def feed(self, chunk: bytes) -> list[Entry]:
        out: list[Entry] = []
        pos = 0
        if self.long is not None:
            long = self.long
            nl = chunk.find(b"\n")
            if nl == -1:
                long.length += len(chunk)
                if chunk:
                    long.last_cr = chunk[-1] == _CR
                self.start += len(chunk)
                return out
            cr = chunk[nl - 1] == _CR if nl else long.last_cr
            length = long.length + nl - (1 if cr else 0)
            out.append(_too_long(long.head, long.offset, "\r\n" if cr else "\n", length))
            self.long = None
            self.start += nl + 1
            pos = nl + 1
        data = self.pending + chunk[pos:] if self.pending else chunk[pos:]
        pos = 0
        while True:
            nl = data.find(b"\n", pos)
            if nl == -1:
                break
            if nl > pos and data[nl - 1] == _CR:
                out.append(_line(data[pos : nl - 1], self.start + pos, "\r\n"))
            else:
                out.append(_line(data[pos:nl], self.start + pos, "\n"))
            pos = nl + 1
        rest = data[pos:]
        if len(rest) > MAX_LINE_BYTES:
            self.long = _Long(rest, self.start + pos, len(rest))
            self.start += len(data)
            self.pending = b""
        else:
            self.start += pos
            self.pending = rest
        return out

    def _take(self) -> tuple[bytes, int, int, bool] | None:
        """The unterminated line held, as (head, offset, length, truncated)."""
        if self.long is not None:
            long, self.long = self.long, None
            return long.head, long.offset, long.length, True
        if not self.pending:
            return None
        rest, self.pending = self.pending, b""
        start, self.start = self.start, self.start + len(rest)
        return rest, start, len(rest), False

    def end(self) -> list[Entry]:
        """At the end of a file: the unterminated last line, tokenized (``ending=""``)."""
        held = self._take()
        if held is None:
            return []
        head, offset, length, truncated = held
        if truncated:
            return [_too_long(head, offset, "", length)]
        return [_line(head, offset, "")]

    def abandon(self, why: str) -> list[Entry]:
        """The file went away under a partial line: yield it, never as a record."""
        held = self._take()
        if held is None:
            return []
        head, offset, length, truncated = held
        return [
            Unparsed(
                offset=offset,
                raw=head[:MAX_LINE_BYTES].decode("utf-8", "surrogateescape"),
                ending="",
                reason=f"the file was {why} before this line's break was written",
                length=length,
                truncated=truncated,
            )
        ]


def _open_log(path: Path) -> BinaryIO:
    """``path`` opened read-only; on POSIX also non-blocking, so opening a FIFO
    returns at once. Raises :class:`NotARegularFileError` for anything but a
    regular file."""
    if sys.platform == "win32":
        f = cast(BinaryIO, path.open("rb"))
    else:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            f = os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
    try:
        mode = os.fstat(f.fileno()).st_mode
        if not stat.S_ISREG(mode):
            raise NotARegularFileError(f"not a regular file: {path}")
    except BaseException:
        f.close()
        raise
    return f


def tokenize(data: bytes, *, offset: int = 0) -> Iterator[Entry]:
    """Every line of ``data``, in order; an unterminated last line is tokenized too."""
    splitter = _Splitter(offset)
    yield from splitter.feed(data)
    yield from splitter.end()


def read_log(path: Path | str) -> Iterator[Entry]:
    """Every line of a combat log file, read in chunks (a log can be large)."""
    splitter = _Splitter()
    with _open_log(Path(path)) as f:
        while chunk := f.read(_CHUNK):
            yield from splitter.feed(chunk)
    yield from splitter.end()


def _newlines_before(f: BinaryIO, end: int, want: int) -> tuple[list[int], int]:
    """Up to ``want`` offsets of LF bytes before ``end``, the nearest first,
    reading backwards in blocks; and how far back the search went (0 when it
    reached the start of the file). Memory: one block and the offsets."""
    found: list[int] = []
    pos = end
    while pos > 0 and len(found) < want:
        step = min(_CHUNK, pos)
        pos -= step
        f.seek(pos)
        block = f.read(step)
        k = len(block)
        while len(found) < want:
            k = block.rfind(b"\n", 0, k)
            if k == -1:
                break
            found.append(pos + k)
    return found, pos


def tail(path: Path | str, n: int) -> tuple[list[Entry], int]:
    """The last ``n`` complete lines of a file, and the offset just after them.

    A partial last line (the client mid-flush) is not among them; the offset
    is where it starts, however long it is, so ``follow(path, offset=...)``
    picks it up once its line break arrives. Reads backwards from the end to
    find where those lines start, then reads just those lines forwards, a
    block at a time, never the whole file into memory.
    """
    if n < 0:
        raise ValueError("n must be zero or more")
    with _open_log(Path(path)) as f:
        size = os.fstat(f.fileno()).st_size
        found, _ = _newlines_before(f, size, 1)
        if not found:  # no line break at all: the only line is still being written
            return [], 0
        last = found[0]
        end = last + 1
        if n == 0:
            return [], end
        before, _ = _newlines_before(f, last, n)
        start = before[n - 1] + 1 if len(before) == n else 0
        splitter = _Splitter(start)
        entries: list[Entry] = []
        f.seek(start)
        remaining = end - start
        while remaining > 0:
            chunk = f.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            entries.extend(splitter.feed(chunk))
    return entries[-n:], end


# ─── files in a Logs folder ──────────────────────────────────────────────────


def is_log_name(name: str) -> bool:
    """A combat log's file name, ``WoWCombatLog*.txt`` (case folded; LAB_FILE_MAP)."""
    folded = name.casefold()
    return folded.startswith("wowcombatlog") and folded.endswith(".txt")


def find_logs(directory: Path | str) -> list[Path]:
    """The combat logs directly in ``directory``, oldest first by modification
    time, then by name. Only regular files count; a symbolic link does not.
    Lists the folder and stats its entries; opens nothing."""
    found: list[tuple[int, str, Path]] = []
    with os.scandir(directory) as it:
        for entry in it:
            if not is_log_name(entry.name):
                continue
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                mtime = entry.stat(follow_symlinks=False).st_mtime_ns
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
        self.started = current is not None
        self.seen = {p.name for p in find_logs(directory)}
        self.queue: list[Path] = []  # new logs not read yet, oldest first
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
        with _open_log(path) as f:
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
            with _open_log(self.current) as f:
                chunk = self._read(f, out)
        except FileNotFoundError:
            return self._rotate(out)
        except NotARegularFileError:
            # Something else now has the log's name: stop following it.
            out.extend(self.splitter.abandon("replaced"))
            self.current = None
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
        """At the end of the current file: switch to the oldest log not read yet."""
        try:
            logs = find_logs(self.directory)
        except FileNotFoundError:
            logs = None
        if logs is not None:
            self.queue.extend(p for p in logs if p.name not in self.seen)
            self.seen = {p.name for p in logs}
        if not self.queue:
            return out, bool(out)
        target = self.queue.pop(0)
        out.extend(self.splitter.abandon("rotated"))
        self._switch(target, 0)
        out.append(Following(path=target, reason="rotated" if self.started else "start"))
        self.started = True
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
    one that appears, reported as ``start``). The first file is read from
    ``offset`` (a line start, such as the offset :func:`tail` returns), or
    from its end when ``offset`` is None. Files that appear later are read
    from the start.

    A :class:`Following` is yielded when a file is started: ``start`` for the
    first, then ``rotated``, ``truncated`` or ``replaced`` as described on
    that class. Rotation is noticed only at the end of the current file, so a
    file's lines are all yielded before the next file's, and when several new
    logs have appeared they are read in turn, oldest first. Any new regular
    file named ``WoWCombatLog*.txt`` counts, including one another tool
    writes there, such as a log uploader's split or archived copies
    **[verify]**. If the current file's name comes to hold something other
    than a regular file, it is no longer followed. When nothing new was
    found, ``sleep(poll_interval)`` is called before the next look.
    """
    path = Path(path)
    if path.is_dir():
        directory, current = path, newest_log(path)
    elif path.is_file():
        directory, current = path.parent, path
    elif path.exists():
        raise NotARegularFileError(f"not a regular file or a folder: {path}")
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
