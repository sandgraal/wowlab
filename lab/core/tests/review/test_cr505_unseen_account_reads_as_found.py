# Probe from review of m16/116-char-list-account-flavors; reproduces a flavor whose --account folder wowlab could not look inside reported as `account_found: true`.
"""M16-116 wants `flavors[]` to let a script tell "no account X there" from
"flavor not looked at". When a flavor's account folder X is a link that
wowlab does not follow (a `not_looked_at` place), `_char_list_flavor` takes
its hidden branch and returns no rows, and `char_list` marks that flavor
`account_found: true` with `characters: 0`: the account folder was not
found there, wowlab could not look inside it.

Positive control: the same setup with X a real folder (copied, not linked)
is `account_found: true` with characters, as the ticket wants.

Constructed boundary input (L8): the two-flavor tree of
`test_cli_flavors.py` in `tmp_path`; nothing reads or writes a real install.
"""

# ruff: noqa: F811  (the autouse fixture imported from test_cli_flavors)
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli_flavors import (
    ACCOUNT,
    EMPTY,
    FLAVOR,
    OTHER_ACCOUNT,
    run,
    two_flavors,  # noqa: F401
)


def _second_flavor_account(root: Path, tmp_path: Path, *, link: bool) -> None:
    """The second flavor gets WTF/Account/<OTHER_ACCOUNT>, a link to a copy
    outside the install (link=True) or the copy itself (link=False)."""
    account = root / EMPTY / "WTF" / "Account"
    account.mkdir(parents=True)
    outside = tmp_path / "outside-account"
    shutil.copytree(root / FLAVOR / "WTF" / "Account" / ACCOUNT, outside)
    if not link:
        shutil.copytree(outside, account / OTHER_ACCOUNT)
        return
    try:
        (account / OTHER_ACCOUNT).symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")


def _report() -> dict[str, object]:
    result = run("char", "list", "--account", OTHER_ACCOUNT, "--json", "--brief")
    data: dict[str, object] = json.loads(result.stdout)
    return data


def test_positive_control_real_account_folder_is_found(two_flavors: Path, tmp_path: Path) -> None:
    _second_flavor_account(two_flavors, tmp_path, link=False)
    data = _report()
    flavors = {f["folder"]: f for f in data["flavors"]}  # type: ignore[attr-defined]
    assert flavors[FLAVOR]["account_found"] is False
    assert flavors[EMPTY]["account_found"] is True
    assert flavors[EMPTY]["characters"]


def test_unseen_account_folder_is_not_reported_found(two_flavors: Path, tmp_path: Path) -> None:
    _second_flavor_account(two_flavors, tmp_path, link=True)
    data = _report()
    # The place is reported as not looked inside...
    unseen = [(n["flavor"], n["path"]) for n in data["not_looked_at"]]  # type: ignore[attr-defined]
    assert unseen == [(EMPTY, f"WTF/Account/{OTHER_ACCOUNT}")]
    flavors = {f["folder"]: f for f in data["flavors"]}  # type: ignore[attr-defined]
    # ...so the flavor entry must not claim the account folder was found there.
    assert flavors[EMPTY]["account_found"] is not True, flavors[EMPTY]
