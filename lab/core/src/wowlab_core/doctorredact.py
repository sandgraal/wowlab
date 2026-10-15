"""`wowlab doctor --redact`: a report safe to paste into an issue (M13-09).

docs/LAB_PLAN.md §15.2 "Redaction", ADR-0031: character, realm and account
folder names, BattleTags, guild names and absolute home paths never leave the
machine unredacted through anything the Lab asks a player to share.

What is replaced, and where the names come from:

- account folders, realm folders and character folders under every flavor's
  `WTF/Account/` (a `<digits>/<First>-<Second>` folder gives a character and
  a realm-style second name; the digits-only folder is left alone), and the
  `realmName`, `accountName` and `accountList` values of every `Config.wtf`
  and `config-cache.wtf`: as `<ACCOUNT>`, `<REALM>`, `<CHARACTER>`;
- this machine's home folder, and any path shaped like a home folder on
  macOS, Windows or Linux, as `<HOME>`;
- email addresses, BattleTags and player, account, guild and community GUIDs,
  by pattern (`wowlab_core.redact`);
- each name given with `--redact-name`, as `<NAME>`: a guild name, or a
  player's own name in a folder outside their home, which nothing in the
  install names as such;
- the login names the process runs as and this machine's network name, as
  `<USER>` and `<HOST>`;
- the same account, realm and character folders named by the entries of
  every snapshot in the store that loads, so names from another install, a
  deleted character or an earlier state are known too;
- the journal label whole, as `<LABEL>`: it is free text a player or a
  command wrote (a profile name, a snapshot message), so it can hold any
  name, a guild's included;
- in why a manifest or a journal record does not load, every piece of the
  record's own text a validation error quotes (`input_value=…`, a value
  error's message, a key that is not a field), as `<withheld>`; a reason
  of a shape not recognised is replaced whole.

Each field of the report is treated as `DOCTOR_FIELDS` says (left as it is,
redacted, replaced whole, or dropped), in the model, before it is printed as
text or as JSON; a field missing from that table is replaced whole, and a
test walks the models so none is. The first run (M13-04) is skipped under
`--redact` and its field dropped: `first_run` is always null. Library log
lines (tracebacks included) are redacted while the report is built, Python
warnings are not shown, and the message of an error that stops it is
redacted.

Read-only, like `doctor` itself (L1).
"""

from __future__ import annotations

import getpass
import logging
import os
import re
import socket
import typing
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from enum import Enum
from pathlib import Path, PurePath
from typing import Any

from pydantic import BaseModel
from pydantic_core.core_schema import ErrorType

from wowlab_core import guard, install, layout, redact, snapshot, wtfconfig

# Config values that name the account or a realm (redact.IDENTITY_CVARS has
# the full deny-list; the others are a GUID, caught by pattern, a club id, and
# audio device names, which `doctor` never prints).
_REALM_CVARS = ("realmName",)
_ACCOUNT_CVARS = ("accountName", "accountList")
_CONFIG_NAMES = frozenset({"config.wtf", "config-cache.wtf"})
# Host names that name no machine in particular.
_NOT_A_HOST_NAME = frozenset({"localhost", "localhost.localdomain"})
_LIBRARY_LOGGER = "wowlab_core"
_SAVED_VARIABLES = "savedvariables"  # casefolded: an account's or a character's, not a realm


class RedactedError(Exception):
    """What stopped the report, with its message redacted."""


class Identities(BaseModel):
    accounts: set[str] = set()
    realms: set[str] = set()
    characters: set[str] = set()


def home_folders() -> list[str]:
    """This machine's home folder, every way the process can name it."""
    found: list[str] = []
    for value in (os.environ.get("HOME"), os.environ.get("USERPROFILE")):
        if value:
            found.append(value)
    try:
        home = Path.home()
    except (RuntimeError, KeyError):  # no home can be determined
        return found
    found.append(str(home))
    with suppress(OSError, RuntimeError):
        found.append(str(home.resolve()))
    return found


def identities(root: Path | None) -> Identities:
    """The names under `WTF/Account/` of every flavor of the install `root`
    (or the one discovery finds), and of every snapshot in the store that
    loads."""
    found = Identities()
    _store_names(found)
    try:
        inst = install.discover(root)
    except install.InstallError:
        return found
    for flavor in inst.flavors:
        lay = layout.Layout.for_flavor(flavor, inst.root)
        try:
            walk = lay.wtf_walk()
        except OSError:
            continue  # doctor reports what it cannot read; it cannot print it either
        for account in walk.accounts:
            found.accounts.add(account.folder)
            for realm in account.realms:
                if realm.kind == "name":
                    found.realms.add(realm.folder)
                for character in realm.characters:
                    found.characters.add(character.first_name)
                    if character.second_name:
                        found.realms.add(character.second_name)
        for f in walk.wtf_files:
            if f.name.casefold() in _CONFIG_NAMES:
                _harvest(lay.flavor_path / f.path, found)
    return found


def _store_names(found: Identities) -> None:
    """The account, realm and character folders the entries of every
    loadable snapshot name, by the rules `layout.Layout.wtf_walk` uses: a
    snapshot may hold another install, a deleted or transferred character, or
    an earlier state of this one. A store that cannot be read gives none."""
    try:
        manifests = snapshot.SnapshotStore().list_lenient().manifests
    except (OSError, snapshot.SnapshotError):
        return
    seen: set[tuple[str, ...]] = set()
    named = Identities()
    for manifest in manifests:
        for entry in manifest.entries:
            # The folders above the entry; the entry itself may be a file.
            folders = tuple(entry.path.split("/")[:-1])
            if folders in seen:
                continue
            seen.add(folders)
            _account_names(folders, named)
    found.accounts.update(_names(named.accounts))
    found.realms.update(_names(named.realms))
    found.characters.update(_names(named.characters))


def _account_names(folders: tuple[str, ...], found: Identities) -> None:
    folded = [part.casefold() for part in folders]
    for i in range(len(folders) - 2):
        if folded[i] != "wtf" or folded[i + 1] != "account":
            continue
        below = folders[i + 2 :]
        found.accounts.add(below[0])
        if len(below) < 2 or below[1].casefold() == _SAVED_VARIABLES:
            continue
        realm = below[1]
        numeric = realm.isascii() and realm.isdigit()
        if not numeric:
            found.realms.add(realm)
        if len(below) < 3 or below[2].casefold() == _SAVED_VARIABLES:
            continue
        first, sep, second = below[2].partition("-")
        if numeric and sep:
            found.characters.add(first)
            found.realms.add(second)
        else:
            found.characters.add(below[2])


def _harvest(path: Path, found: Identities) -> None:
    try:
        doc = wtfconfig.read_config(path)
    except (OSError, ValueError):
        return
    for name in _REALM_CVARS:
        cvar = doc.get(name)
        if cvar is not None:
            found.realms.update(_names(cvar.value.split("|")))
    for name in _ACCOUNT_CVARS:
        cvar = doc.get(name)
        if cvar is not None:
            found.accounts.update(_names(cvar.value.split("|")))


def _names(values: Iterable[str]) -> Iterator[str]:
    for value in values:
        text = value.strip().lstrip("!").strip()
        if len(text) >= 2 and any(c.isalnum() for c in text):
            yield text


def machine_names() -> tuple[list[str], list[str]]:
    """The login names the process runs as, and this machine's network name
    (whole and up to its first dot). Each is matched by the same rule as any
    other name: whole words only when shorter than `EMBEDDED_MIN_CHARS`."""
    users: list[str] = []
    with suppress(Exception):  # getuser raises when no name can be found
        users.append(getpass.getuser())
    for variable in ("USER", "USERNAME", "LOGNAME"):
        value = os.environ.get(variable)
        if value:
            users.append(value)
    hosts: list[str] = []
    with suppress(OSError):
        host = socket.gethostname()
        hosts.extend((host, host.split(".", 1)[0]))
    return (
        list(_names(users)),
        [h for h in _names(hosts) if h.casefold() not in _NOT_A_HOST_NAME],
    )


def redactor(root: Path | None, names: Iterable[str] = ()) -> redact.Redactor:
    found = identities(root)
    users, hosts = machine_names()
    return redact.Redactor(
        accounts=found.accounts,
        characters=found.characters,
        realms=found.realms,
        names=names,
        users=users,
        hosts=hosts,
        homes=home_folders(),
    )


def check_names(names: Iterable[str]) -> list[str]:
    """`--redact-name` values; one shorter than two characters would rewrite
    every word it stands in, so it is refused (ValueError)."""
    out: list[str] = []
    for name in names:
        text = name.strip()
        if len(text) < 2:
            raise ValueError(f"--redact-name needs two or more characters, got {name!r}")
        out.append(text)
    return out


# ─── what each field of the report gets ──────────────────────────────────────

AS_IS = "as-is"  # a Literal or enum: it can hold only the values the code names
REDACT = "redact"  # text: every identifying string in it becomes a placeholder
DROP = "drop"  # an optional field, of any type: always None in the redacted report
# Why a manifest or a journal record does not load: the record text a
# validation error quotes is withheld (`withhold_record_text`), then redacted.
RECORD_ERROR = "record-error"
# Any other value is a placeholder from `redact.PLACEHOLDERS`: the field is
# replaced whole. A field not listed at all is replaced whole with
# `redact.TEXT` (fail closed); a test walks the models so none is missed.
type Treatment = str

DOCTOR_FIELDS: dict[str, Treatment] = {
    "install_root": REDACT,
    "found_by": REDACT,
    "install_error": REDACT,
    "locations.searched": REDACT,
    "locations.could_not_check.location": REDACT,
    "locations.could_not_check.reason": REDACT,
    "flavors.folder": REDACT,
    "flavors.product": REDACT,
    "flavors.version": REDACT,
    "flavors.game_data.status": AS_IS,
    "flavors.game_data.version": REDACT,
    "flavors.game_data.detail": REDACT,
    "flavors.sync_cvars.file": REDACT,
    "flavors.sync_cvars.account": REDACT,
    "flavors.sync_cvars.name": REDACT,
    "flavors.sync_cvars.value": REDACT,
    "other_dirs": REDACT,
    "client.state": AS_IS,
    "client.processes.state": AS_IS,
    "client.processes.matched_by": AS_IS,
    "client.processes.exe_source": AS_IS,
    "client.processes.name": REDACT,
    "client.processes.exe": REDACT,
    "client.processes.install_root": REDACT,
    "client.processes.flavor_folder": REDACT,
    "client.processes.status": REDACT,
    "client.error": REDACT,
    "store.path": REDACT,
    "store.damaged.name": REDACT,
    "store.damaged.reason": RECORD_ERROR,
    "store.journal_error": RECORD_ERROR,
    "store.last_transaction.id": REDACT,
    "store.last_transaction.created_at": REDACT,
    # Free text a player or a command wrote (a profile name, a snapshot
    # message, restored paths): it can hold any name, a guild's included.
    "store.last_transaction.label": redact.LABEL,
    "store.last_transaction.state": REDACT,
    "problems": REDACT,
    # The first run (M13-04) is skipped under --redact (conductor decision,
    # M13-09): no scan, no question, no snapshot. Dropped here as well, so
    # a first-run report can never reach a redacted one.
    "first_run": DROP,
}


def redact_model[M: BaseModel](
    model: M, replace: redact.Redactor, fields: Mapping[str, Treatment]
) -> M:
    """`model` with every string in it, dict keys included, treated as
    `fields` says, validated back into its type."""
    data = _walk(model, "", replace, fields)
    return type(model).model_validate(data)


def _walk(value: Any, path: str, replace: redact.Redactor, fields: Mapping[str, Treatment]) -> Any:
    if isinstance(value, BaseModel):
        out: dict[str, Any] = {}
        for name in type(value).model_fields:
            child = f"{path}.{name}" if path else name
            out[name] = (
                None
                if fields.get(child) == DROP
                else _walk(getattr(value, name), child, replace, fields)
            )
        return out
    if isinstance(value, Enum):
        treatment = fields.get(path, redact.TEXT)
        return value if treatment == AS_IS else _text(str(value.value), treatment, replace)
    if isinstance(value, str | PurePath):
        return _text(str(value), fields.get(path, redact.TEXT), replace)
    if isinstance(value, list | tuple | set | frozenset):
        return [_walk(v, path, replace, fields) for v in value]
    if isinstance(value, dict):
        key = fields.get(path, redact.TEXT)
        return {
            (_text(k, key, replace) if isinstance(k, str) else k): _walk(v, path, replace, fields)
            for k, v in value.items()
        }
    return value


def _text(value: str, treatment: Treatment, replace: redact.Redactor) -> str:
    if treatment == AS_IS:
        return value
    if treatment == REDACT:
        return replace(value)
    if treatment == RECORD_ERROR:
        return replace(withhold_record_text(value))
    return treatment if treatment in redact.PLACEHOLDERS else redact.TEXT


# ─── what a validation error quotes from the record ──────────────────────────

WITHHELD = "<withheld>"

# `str(pydantic.ValidationError)`: a header, then per error an optional
# location line, a message line ending in `[type=…, input_value=…,
# input_type=…]`, and an optional link line. The quoted input is a `repr`,
# so it holds no line break, but it may hold quotes, brackets, escapes and
# the text `, input_type=…]` itself: the value runs from the first
# `input_value=` to the last `, input_type=` of its line.
_HEADER_RE = re.compile(r"(?P<lead>.*?)(?P<count>\d+) validation errors? for (?P<title>\w+)")
_ERROR_RE = re.compile(
    r"  (?P<message>.*?) \[type=(?P<type>[a-z_]+), input_value=.*"
    r", input_type=(?P<input_type>[A-Za-z_][A-Za-z0-9_]*)\]"
)
_LINK_RE = re.compile(
    r"    For further information visit https://errors\.pydantic\.dev/[0-9.]+/v/[a-z_]+"
)
# Messages kept as they are: pydantic's own fixed text for these error types,
# which never quotes the input. A value error's or an assertion's message is
# the validator's own text and may quote it (`entry path …: 'WTF/Account/…'`).
_FIXED_MESSAGE_RE = re.compile(
    r"Field required|Extra inputs are not permitted|Input should be (?:a|an) [a-z ,]+"
    r"|Invalid JSON: [a-zA-Z ]+(?: at line \d+ column \d+)?"
    r"|Instance is frozen|Field is frozen"
)
_ERROR_TYPES = frozenset(typing.get_args(ErrorType))
# `carries id '…'` (a manifest), `carries the id '…'` (a journal record):
# the id read from the record, which did not match its file's name.
_CARRIES_ID_RE = re.compile(r"(?P<lead>\bcarries (?:the )?id ).*")


def _field_names() -> frozenset[str]:
    """Every field name of the models a manifest or a journal record is
    validated as: the only words a validation error's location may keep."""
    names: set[str] = set()
    todo: list[type[BaseModel]] = [snapshot.Manifest, guard._JournalRecord]
    while todo:
        model = todo.pop()
        for name, info in model.model_fields.items():
            names.add(name)
            if info.alias:
                names.add(info.alias)
            todo.extend(_models_in(info.annotation))
    return frozenset(names)


def _models_in(kind: Any) -> Iterator[type[BaseModel]]:
    if isinstance(kind, type) and issubclass(kind, BaseModel):
        yield kind
    for arg in typing.get_args(kind):
        yield from _models_in(arg)


def withhold_record_text(reason: str) -> str:
    """`reason` with every piece of the record's own text a validation error
    quotes replaced by `<withheld>`: the input value, the message of an error
    type whose message is not pydantic's fixed text, a location part that is
    not a field name or an index, and the id of a `carries id` message.

    Fail closed: a reason that holds a validation error, or the text
    `input_value=`, of a shape not recognised is replaced whole (`<TEXT>`)."""
    lines = reason.split("\n")
    header = _HEADER_RE.fullmatch(lines[0])
    if header is None:
        if len(lines) > 1 or "input_value=" in reason or "validation error" in reason:
            return redact.TEXT
        return _CARRIES_ID_RE.sub(lambda m: m["lead"] + WITHHELD, reason)
    out = [lines[0]]
    fields = _field_names()
    errors = 0
    for line in lines[1:]:
        error = _ERROR_RE.fullmatch(line)
        if error is not None:
            errors += 1
            kind = error["type"] if error["type"] in _ERROR_TYPES else WITHHELD
            message = error["message"]
            if kind == WITHHELD or not _FIXED_MESSAGE_RE.fullmatch(message):
                message = WITHHELD
            out.append(
                f"  {message} [type={kind}, input_value={WITHHELD}, "
                f"input_type={error['input_type']}]"
            )
        elif _LINK_RE.fullmatch(line):
            continue  # pydantic's documentation link adds nothing to a report
        elif line and not line.startswith(" "):
            out.append(
                ".".join(
                    part if part.isdecimal() or part in fields else WITHHELD
                    for part in line.split(".")
                )
            )
        else:
            return redact.TEXT
    if errors != int(header["count"]):
        return redact.TEXT
    return "\n".join(out)


# ─── while the report is built ───────────────────────────────────────────────


class _RedactingFilter(logging.Filter):
    """Folds a record's traceback and stack into its message, redacts it, and
    drops them, so no handler formats an unredacted one."""

    def __init__(self, replace: redact.Redactor) -> None:
        super().__init__()
        self._replace = replace

    def filter(self, record: logging.LogRecord) -> bool:
        parts = [record.getMessage()]
        if record.exc_info:
            parts.append(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            parts.append(record.exc_text)
        if record.stack_info:
            parts.append(record.stack_info)
        record.msg = self._replace("\n".join(parts))
        record.args = None
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


@contextmanager
def redacting(replace: redact.Redactor) -> Iterator[None]:
    """While the report is built: library log lines are redacted, Python
    warnings (which print a file path and a source line) are not shown, and
    an error that stops it becomes a `RedactedError` whose message is."""
    logger = logging.getLogger(_LIBRARY_LOGGER)
    log_filter = _RedactingFilter(replace)
    # A logger's own filters see only records logged on it, not those of its
    # children (`wowlab_core.gamedata`, ...); its handlers see both.
    filtered: list[logging.Filterer] = [logger, *logger.handlers]
    for target in filtered:
        target.addFilter(log_filter)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        raise RedactedError(replace(f"{type(exc).__name__}: {message}")) from None
    finally:
        for target in filtered:
            target.removeFilter(log_filter)


def redacted[M: BaseModel](
    build: Callable[[], M],
    root: Path | None,
    names: Iterable[str],
    fields: Mapping[str, Treatment],
) -> M:
    """The report `build` makes, treated field by field as `fields` says.

    The names are found first. If finding them fails, the error is redacted
    with what is known without them (the home folder, the patterns, `names`),
    and nothing else is printed."""
    names = list(names)
    floor = redact.Redactor(names=names, homes=home_folders())
    with redacting(floor):
        replace = redactor(root, names)
    with redacting(replace):
        return redact_model(build(), replace, fields)
