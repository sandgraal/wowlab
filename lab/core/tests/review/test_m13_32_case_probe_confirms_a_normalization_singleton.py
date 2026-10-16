# Probe from review of m13/32-explain-followups-impl; reproduces a name pair that differs in normalization as well as case (Kelvin sign vs "k") being confirmed by the case probe alone, so an NTFS-like volume (folds case, keeps normalization apart) calls two names one.
"""Constructed (L8 hostile/boundary): `_Volume.same` picks the case-only
branch whenever `a.lower() == b.lower()`. U+212A KELVIN SIGN lower-cases to
"k" but is not "K" under NFC; U+0130 lower-cases to two code points. NTFS
compares names letter by letter through its upcase table and keeps both
pairs apart, so only "unknown" (None) or False is sound when the volume
folds case but not normalization. The volume's answers are stood in, so this
runs on any system. Code points are built with `chr` so the source stays
ASCII."""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import pathfacts

NTFS_LIKE = {"case": True, "normalization": False}

KELVIN, ANGSTROM, OHM, DOTTED_I = chr(0x212A), chr(0x212B), chr(0x2126), chr(0x0130)


def _ntfs_like(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    def folds(self: pathfacts._Volume, parts: tuple[str, ...], difference: str) -> bool | None:
        asked.append(difference)
        return NTFS_LIKE[difference]

    monkeypatch.setattr(pathfacts._Volume, "_folds", folds)
    return asked


@pytest.mark.parametrize(
    ("a", "b"),
    [
        pytest.param(KELVIN, "k", id="constructed-kelvin-sign"),
        pytest.param(ANGSTROM, chr(0xE5), id="constructed-angstrom-sign"),
        pytest.param(OHM, chr(0x3C9), id="constructed-ohm-sign"),
        pytest.param(DOTTED_I, "i" + chr(0x307), id="constructed-dotted-capital-i"),
    ],
)
def test_case_probe_alone_never_confirms_a_normalization_difference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, a: str, b: str
) -> None:
    (tmp_path / "Ab").mkdir()
    _ntfs_like(monkeypatch)
    volume = pathfacts._Volume(tmp_path)
    # NTFS holds these as two names; a True here confirms the wrong file.
    assert volume.same(f"Ab/{a}.lua", f"Ab/{b}.lua") is not True


def test_positive_control_a_plain_case_difference_is_still_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Ab").mkdir()
    asked = _ntfs_like(monkeypatch)
    volume = pathfacts._Volume(tmp_path)
    assert volume.same("Ab/abc.lua", "Ab/ABC.lua") is True
    assert asked == ["case"]
