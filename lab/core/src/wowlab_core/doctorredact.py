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
- the journal label whole, as `<LABEL>`: it is free text a player or a
  command wrote (a profile name, a snapshot message), so it can hold any
  name, a guild's included.

Each field of the report is treated as `DOCTOR_FIELDS` says (left as it is,
redacted, or replaced whole), in the model, before it is printed as text or
as JSON; a field missing from that table is replaced whole, and a test walks
the models so none is. Library log lines (tracebacks included) are redacted
while the report is built, Python warnings are not shown, and the message of
an error that stops it is redacted.

Read-only, like `doctor` itself (L1).
"""

from __future__ import annotations

import getpass
import logging
import os
import socket
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from enum import Enum
from pathlib import Path, PurePath
from typing import Any

from pydantic import BaseModel

from wowlab_core import install, layout, redact, wtfconfig

# Config values that name the account or a realm (redact.IDENTITY_CVARS has
# the full deny-list; the others are a GUID, caught by pattern, a club id, and
# audio device names, which `doctor` never prints).
_REALM_CVARS = ("realmName",)
_ACCOUNT_CVARS = ("accountName", "accountList")
_CONFIG_NAMES = frozenset({"config.wtf", "config-cache.wtf"})
# Host names that name no machine in particular.
_NOT_A_HOST_NAME = frozenset({"localhost", "localhost.localdomain"})
_LIBRARY_LOGGER = "wowlab_core"


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
    (or the one discovery finds). No install: no names, and the report holds
    none either."""
    found = Identities()
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
    "store.damaged.reason": REDACT,
    "store.journal_error": REDACT,
    "store.last_transaction.id": REDACT,
    "store.last_transaction.created_at": REDACT,
    # Free text a player or a command wrote (a profile name, a snapshot
    # message, restored paths): it can hold any name, a guild's included.
    "store.last_transaction.label": redact.LABEL,
    "store.last_transaction.state": REDACT,
    "problems": REDACT,
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
        return {
            name: _walk(getattr(value, name), f"{path}.{name}" if path else name, replace, fields)
            for name in type(value).model_fields
        }
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
    return treatment if treatment in redact.PLACEHOLDERS else redact.TEXT


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
