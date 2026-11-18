"""`wowlab casc stat` and `casc cat` (docs/LAB_PLAN.md §17.1, M15-06).

`stat` says where one file data id stands in the flavor's build, in the
words of §17.1's four states (and "unknown id" for a number that is no file
data id), with its size and content key when it is local; it reads the
install's archives and writes nothing anywhere. `cat` decodes a local file
and writes it under the user data directory (`cascfiles`): by default to
`casc/<product>-<version>/files/<fdid>-<ckey8>.bin`, never over an existing
file; with `--out`, to another path inside that build folder, or with
`--out -` to stdout (never to a terminal, and never with `--json`).
`wowlab ui` refuses `cat --out` in both forms, so from the UI a file goes
only to its default place.
"""

import hashlib
import sys
from pathlib import Path
from typing import Annotated, Literal

import typer

from wowlab_core import casc, cascfiles, install
from wowlab_core.cli._common import (
    EXIT_USAGE,
    CliError,
    FlavorOpt,
    JsonOpt,
    RootOpt,
    _bytes,
    _discover,
    _emit,
    _handled,
    _Out,
    _say,
    _say_note,
    _select_flavor,
    casc_app,
)

# ─── casc ────────────────────────────────────────────────────────────────────


class CascStatReport(_Out):
    """`wowlab casc stat --json`: where one file data id stands in the
    flavor's build. `state` is `local`, `not_local` (in this game version's
    files, not in the client's local files), `not_in_build` (not part of
    this game version's files), `encrypted`, or `unknown_id` (a number that
    is no file data id); `words` says it as the text output does. When
    local, `size` (decoded bytes), `content_key` (the MD5 of the decoded
    file) and `encoding_key` (the copy that would be read); `content_keys`
    and `encoding_keys` are every key tried, in order, and `key_names` the
    encryption key names met."""

    flavor: str
    product: str
    version: str
    fdid: int
    state: casc.FileState
    words: str
    size: int | None
    content_key: str | None
    encoding_key: str | None
    content_keys: list[str]
    encoding_keys: list[str]
    key_names: list[str]
    note: str | None


class CascCatReport(_Out):
    """`wowlab casc cat --json`: the file written (`outcome` `written`), or
    found already there with the same bytes (`already_extracted`: nothing
    was written). `path` is where it is; `content_key` is the MD5 of its
    bytes and `size` their count."""

    flavor: str
    product: str
    version: str
    fdid: int
    outcome: Literal["written", "already_extracted"]
    path: str
    size: int
    content_key: str


CascFdidOpt = Annotated[
    int,
    typer.Option("--fdid", help="The file data id: the number the game's tables name a file by."),
]

_CASC_STDOUT = "-"


def _casc_flavor(root: Path | None, flavor: str | None) -> tuple[install.Install, install.Flavor]:
    inst, _ = _discover(root)
    return inst, _select_flavor(inst, flavor)


def _casc_open(inst: install.Install, chosen: install.Flavor) -> casc.CascSource:
    """The chosen flavor's build, opened for reads."""
    try:
        return casc.open_source(inst, chosen)
    except casc.CascError as exc:
        raise CliError(f"cannot read the game-data archives of {chosen.folder}: {exc}") from exc


@casc_app.command("stat", short_help="Whether a game file is on this computer, by its id.")
@_handled
def casc_stat(
    fdid: CascFdidOpt,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Say where one of the game's files stands in this game version: local
    (on this computer, readable), not in your client's local files (part of
    this game version, but not on this computer), not among the files this
    client loads, not part of this game version's files, or encrypted. For a
    local file, its size and content key. wowlab never downloads a game
    file. Reads only; writes nothing anywhere.

    \f
    JSON: CascStatReport."""
    inst, chosen = _casc_flavor(root, flavor)
    folder, source = chosen.folder, _casc_open(inst, chosen)
    try:
        found = source.stat(fdid)
    except casc.CascError as exc:
        raise CliError(f"cannot say where file {fdid} stands: {exc}") from exc
    index = source.report.index
    report = CascStatReport(
        flavor=folder,
        product=index.product,
        version=index.version,
        fdid=fdid,
        state=found.state,
        words=found.words,
        size=found.size,
        content_key=found.content_key if found.state == "local" else None,
        encoding_key=found.encoding_key,
        content_keys=list(found.content_keys),
        encoding_keys=list(found.encoding_keys),
        key_names=list(found.key_names),
        note=found.note,
    )
    if json_out:
        _emit(report)
        return
    _say(f"file {fdid} in {folder} ({index.product} {index.version}): {found.words}")
    if found.state == "local":
        if found.size is not None:
            _say(f"  size: {_bytes(found.size)}")
        _say(f"  content key: {found.content_key}")
    elif found.state == "encrypted":
        names = ", ".join(found.key_names)
        label = "key name" if len(found.key_names) == 1 else "key names"
        _say_note(f"  wowlab does not decrypt the game's files ({label} {names})")
    elif found.note:
        _say_note(f"  {found.note}")


@casc_app.command("cat", short_help="Extract a game file, by its id, into wowlab's data folder.")
@_handled
def casc_cat(
    fdid: CascFdidOpt,
    out: Annotated[
        str | None,
        typer.Option(
            "--out",
            metavar="<path>",
            help="Where to write it instead, inside the same casc/<product>-<version>/ folder "
            "(a relative path is taken inside it) but outside its files/ and characters/ "
            "folders, or - for the output (never a terminal). Anywhere else is refused. "
            "Default: files/<id>-<first 8 digits of its content key>.bin in that folder, "
            "under wowlab's data folder.",
        ),
    ] = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Decode one of the game's files from the install's archives, check it
    against its content key, and write it under wowlab's data folder, in a
    folder of its own for this game version. A file already extracted is
    not written again, and nothing there is ever overwritten. Never writes
    into the install, and never downloads: a file that is not on this
    computer, or is encrypted, is reported and not extracted.

    \f
    JSON: CascCatReport."""
    to_stdout = out == _CASC_STDOUT
    if to_stdout and json_out:
        raise CliError(
            "--out - writes the file's bytes to the output, so --json cannot", EXIT_USAGE
        )
    if to_stdout and sys.stdout.isatty():
        raise CliError(
            "--out - would write the file's bytes to the terminal; send them to a program "
            "or a file instead, or leave --out off",
            EXIT_USAGE,
        )
    if out is not None and not out.strip():
        raise CliError("--out is empty; name a path, or - for the output", EXIT_USAGE)
    inst, chosen = _casc_flavor(root, flavor)
    if out is not None and not to_stdout and chosen.product and chosen.version:
        # Before the archives are opened, so a refused --out costs nothing;
        # checked again below against the build the archives name.
        cascfiles.check_out(out, cascfiles.build_dir(chosen.product, chosen.version))
    folder_name, source = chosen.folder, _casc_open(inst, chosen)
    index = source.report.index
    try:
        found = source.stat(fdid)
    except casc.CascError as exc:
        raise CliError(f"cannot extract file {fdid}: {exc}") from exc
    if found.state != "local" or found.content_key is None or found.size is None:
        if found.state == "unknown_id":
            what = "not a file data id (a whole number from 0 to 2,147,483,647)"
        else:
            what = found.words + (f": {found.note}" if found.note else "")
        raise CliError(f"cannot extract file {fdid}: it is {what}; nothing was written")
    build = cascfiles.build_dir(index.product, index.version)
    target: Path | None = None
    if not to_stdout:
        target = cascfiles.check_out(
            out
            if out is not None
            else cascfiles.default_path(index.product, index.version, fdid, found.content_key),
            build,
            default=out is None,
        )
        if cascfiles.already_there(target, found.size, found.content_key):
            _casc_done(folder_name, index, fdid, "already_extracted", target, found, json_out)
            return
    try:
        data = source.read(fdid)
    except casc.CascError as exc:
        raise CliError(f"cannot extract file {fdid}: {exc}; nothing was written") from exc
    ckey = hashlib.md5(data, usedforsecurity=False).hexdigest()
    if target is None:
        _casc_to_stdout(data)
        return
    if out is None and ckey != found.content_key:
        target = cascfiles.default_path(index.product, index.version, fdid, ckey)
    written = cascfiles.write_new(data, target, build, ckey, default=out is None)
    stat = found.model_copy(update={"content_key": ckey, "size": data.nbytes})
    outcome: Literal["written", "already_extracted"] = "written" if written else "already_extracted"
    _casc_done(folder_name, index, fdid, outcome, target, stat, json_out)


def _casc_to_stdout(data: memoryview) -> None:
    stream = sys.stdout.buffer
    stream.write(data)
    stream.flush()


def _casc_done(
    flavor: str,
    index: casc.CascIndexReport,
    fdid: int,
    outcome: Literal["written", "already_extracted"],
    path: Path,
    found: casc.CascFileStat,
    json_out: bool,
) -> None:
    assert found.content_key is not None and found.size is not None
    if json_out:
        _emit(
            CascCatReport(
                flavor=flavor,
                product=index.product,
                version=index.version,
                fdid=fdid,
                outcome=outcome,
                path=str(path),
                size=found.size,
                content_key=found.content_key,
            )
        )
        return
    if outcome == "already_extracted":
        _say(f"Already extracted: {path}")
        _say(f"  file {fdid}, the same content key; nothing was written")
        return
    _say(
        f"Extracted file {fdid} ({_bytes(found.size)}) from {index.product} {index.version} "
        f"to {path}"
    )
