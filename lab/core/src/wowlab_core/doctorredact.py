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
- a journal label (`label`) whole, as `<LABEL>`: it is free text a player or
  a command wrote (a profile name, a snapshot message), so it can hold any
  name, a guild's included.

Every string of the report is redacted, in the model, before it is printed
as text or as JSON; so are library log lines while the report is built, and
the message of an error that stops it.

Read-only, like `doctor` itself (L1).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from wowlab_core import install, layout, redact, wtfconfig

# Config values that name the account or a realm (redact.IDENTITY_CVARS has
# the full deny-list; the others are a GUID, caught by pattern, a club id, and
# audio device names, which `doctor` never prints).
_REALM_CVARS = ("realmName",)
_ACCOUNT_CVARS = ("accountName", "accountList")
_CONFIG_NAMES = frozenset({"config.wtf", "config-cache.wtf"})
# Fields replaced whole (see the module docstring).
_FREE_TEXT_FIELDS = frozenset({"label"})
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


def redactor(root: Path | None, names: Iterable[str] = ()) -> redact.Redactor:
    found = identities(root)
    return redact.Redactor(
        accounts=found.accounts,
        characters=found.characters,
        realms=found.realms,
        names=names,
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


def redact_model[M: BaseModel](model: M, replace: redact.Redactor) -> M:
    """`model` with every string in it redacted, validated back into its type."""
    data = _walk(model.model_dump(mode="json"), replace, None)
    return type(model).model_validate(data)


def _walk(value: Any, replace: redact.Redactor, key: str | None) -> Any:
    if isinstance(value, str):
        return redact.LABEL if key in _FREE_TEXT_FIELDS else replace(value)
    if isinstance(value, list):
        return [_walk(v, replace, None) for v in value]
    if isinstance(value, dict):
        return {k: _walk(v, replace, k) for k, v in value.items()}
    return value


class _RedactingFilter(logging.Filter):
    def __init__(self, replace: redact.Redactor) -> None:
        super().__init__()
        self._replace = replace

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._replace(record.getMessage())
        record.args = None
        return True


@contextmanager
def redacting(replace: redact.Redactor) -> Iterator[None]:
    """While the report is built: library log lines are redacted, and an
    error that stops it becomes a `RedactedError` whose message is."""
    logger = logging.getLogger(_LIBRARY_LOGGER)
    log_filter = _RedactingFilter(replace)
    # A logger's own filters see only records logged on it, not those of its
    # children (`wowlab_core.gamedata`, ...); its handlers see both.
    filtered: list[logging.Filterer] = [logger, *logger.handlers]
    for target in filtered:
        target.addFilter(log_filter)
    try:
        yield
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        raise RedactedError(replace(f"{type(exc).__name__}: {message}")) from None
    finally:
        for target in filtered:
            target.removeFilter(log_filter)


def redacted[M: BaseModel](build: Callable[[], M], root: Path | None, names: Iterable[str]) -> M:
    """The report `build` makes, with every string in it redacted.

    The names are found first. If finding them fails, the error is redacted
    with what is known without them (the home folder, the patterns, `names`),
    and nothing else is printed."""
    names = list(names)
    floor = redact.Redactor(names=names, homes=home_folders())
    with redacting(floor):
        replace = redactor(root, names)
    with redacting(replace):
        return redact_model(build(), replace)
