# Probe from review of m13/09-redaction; reproduces part of another user's home folder name surviving redaction when it holds an apostrophe.
"""`redact._HOME_SHAPE_RE` takes the home folder's name up to the next
separator, quote or line end, and `'` is one of its stop characters. A
Windows profile folder may hold an apostrophe (`C:\\Users\\Sean O'Brien`), so
a home path of a user other than this machine's (an install in another
account's folder on a shared PC, named by `--root` or `WOWLAB_WOW_ROOT`)
comes out as `<HOME>'Brien\\...`: the surname is printed. ADR-0031: absolute
home paths never leave the machine unredacted.

This machine's own home is matched whole by the known-home pass, so the
probe uses a home that is not this machine's. Positive control: the same
path without the apostrophe loses the whole name.

Constructed (hostile input, L8); pure string redaction, no install.
"""

from __future__ import annotations

from wowlab_core import redact


def test_constructed_windows_home_with_an_apostrophe_is_redacted_whole() -> None:
    replace = redact.Redactor(homes=[r"C:\Users\Jaina Proudmoore"])
    out = replace(r"Install: C:\Users\Sean O'Brien\Games\World of Warcraft")
    assert "Brien" not in out, out


def test_positive_control_the_same_home_without_an_apostrophe_is_redacted() -> None:
    replace = redact.Redactor(homes=[r"C:\Users\Jaina Proudmoore"])
    out = replace(r"Install: C:\Users\Sean OBrien\Games\World of Warcraft")
    assert out == r"Install: <HOME>\Games\World of Warcraft", out
