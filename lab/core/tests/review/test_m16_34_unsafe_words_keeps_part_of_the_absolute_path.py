# Probe from review of m16/34-addons-check-orphans; reproduces addoncheck._unsafe_words leaving part of the absolute path in the error when a folder above the file has " holds " or " is not a regular file" in its name
"""Constructed (not a real capture): an `AddOns.txt` over the 1 MiB bound
under a folder whose name holds the word " holds ". `_unsafe_words` cuts the
`UnsafeReadError` text at the *first* " holds ", which is inside the path, so
the error that `addons check` prints (text caveat and `--json` `error`) keeps
the tail of the absolute path. The positive control is the same file under
a plain folder name. Nothing reads or writes a real install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import addoncheck, snapshot, wtfconfig


def _words_for(folder: Path) -> str:
    target = folder / "WTF" / "AddOns.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x" * (wtfconfig.MAX_ADDONS_TXT_BYTES + 1))
    with pytest.raises(snapshot.UnsafeReadError) as caught:
        wtfconfig.read_addons(target)
    return addoncheck._unsafe_words(caught.value)


def test_control_plain_folder_name(tmp_path: Path) -> None:
    words = _words_for(tmp_path / "plain")
    assert str(tmp_path) not in words
    assert "WTF" not in words
    assert words.startswith("holds ")


@pytest.mark.parametrize("name", ["jane holds games", "x is not a regular file y"])
def test_a_folder_name_with_the_cut_word_keeps_no_path(tmp_path: Path, name: str) -> None:
    words = _words_for(tmp_path / name)
    assert "WTF" not in words, words
    assert "AddOns.txt" not in words, words
