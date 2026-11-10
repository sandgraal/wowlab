# Probe from review of m16/44-capture-free-text; reproduces a login in a path's
# last component, after a space, left unredacted by `doctor --redact`.
"""M16-44: "`doctor --redact` replaces the login only inside path-shaped
tokens." `redact._PATH_TOKEN_RE` lets a folder name hold spaces only
*between* two separators; the last component stops at the first blank. So a
login that sits in the last component after a space is outside the token and
is printed as it is. On `main` the login was replaced everywhere, so this is
a regression: `D:\\Games\\WoW root` and `D:\\Games\\John Smith` (a Windows
login with a space, which `getpass.getuser()` returns as it is) leak the
login, while the same folder one level up is redacted.

Constructed (boundary case). The positive controls pin what the branch gets
right: the login in a middle component, with or without spaces, is replaced,
and a login in prose stays as it is.
"""

from __future__ import annotations

import pytest

from wowlab_core import redact

USER = redact.USER


@pytest.mark.parametrize(
    ("login", "text", "expected"),
    [
        ("root", "D:\\Games\\WoW root", f"D:\\Games\\WoW {USER}"),
        ("root", "/srv/games/my root", f"/srv/games/my {USER}"),
        ("John Smith", "D:\\Games\\John Smith", f"D:\\Games\\{USER}"),
    ],
)
def test_constructed_a_login_after_a_space_in_the_last_component_is_replaced(
    login: str, text: str, expected: str
) -> None:
    assert redact.Redactor(users=[login])(text) == expected


@pytest.mark.parametrize(
    ("login", "text", "expected"),
    [
        ("root", "D:\\Games\\WoW root\\x", f"D:\\Games\\WoW {USER}\\x"),
        ("John Smith", "D:\\John Smith\\WoW", f"D:\\{USER}\\WoW"),
        ("root", "root user, admin root", "root user, admin root"),
    ],
)
def test_constructed_positive_controls(login: str, text: str, expected: str) -> None:
    assert redact.Redactor(users=[login])(text) == expected
