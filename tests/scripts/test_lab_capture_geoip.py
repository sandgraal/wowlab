"""`scripts/lab_capture.py`: the geoip country in `.build.info` (M13-34).

The `Tags` cell of `.build.info` carries a region token (which matches the
`Branch` column) and a `geoip-<CC>?` token: the country the client was in when
it last updated. The scrub replaces the two country letters of that token with
a fixed placeholder of the same shape, byte-level, and refuses the file when a
`geoip` occurrence cannot be placed as a whole token of a Tags cell.

CONSTRUCTED INPUT (L8: hostile-input and boundary tests). Every `.build.info`
below is written for the test, in the shape of docs/LAB_FORMATS.md section 1
and its 2026-09-22 amendment: invented keys, hosts, region, geoip country and
versions. The region (`KR`) and the geoip country (`FR`) are chosen to differ,
and neither is taken from a capture. No test needs or touches a real install
(ADR-0012).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from test_lab_capture import build_install, capture, lab_capture, outputs

Identity = lab_capture.Identity
PLACEHOLDER = lab_capture.GEOIP_PLACEHOLDER
UNPLACED = lab_capture.GEOIP_UNPLACED_LABEL

HEADER = (
    "Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|CDN Key!HEX:16|Install Key!HEX:16"
    "|IM Size!DEC:4|CDN Path!STRING:0|CDN Hosts!STRING:0|CDN Servers!STRING:0|Tags!STRING:0"
    "|Armadillo!STRING:0|Last Activated!STRING:0|Version!STRING:0|KeyRing!HEX:16"
    "|Product!STRING:0"
)
TAGS = (
    "OSX x86_64 KR? acct-KOR? geoip-FR? enUS speech?:OSX x86_64 KR? acct-KOR? geoip-FR? enUS text?"
)
HOSTS = "cdn.example.invalid level3.example.invalid"
SERVERS = "https://cdn.example.invalid/?maxhosts=4"


def _row(product: str, version: str, tags: str = TAGS) -> str:
    return f"kr|1|{'0a' * 16}|{'0b' * 16}|||tpr/wow|{HOSTS}|{SERVERS}|{tags}|||{version}||{product}"


def build_info(tags: str = TAGS, *, header: str = HEADER, eol: str = "\n") -> bytes:
    """A constructed two-product `.build.info` matching build_install's flavors."""
    lines = [
        header,
        _row("wow", "12.1.5.65432", tags),
        _row("wow_classic_beta", "1.60.1.60001", tags),
    ]
    return (eol.join(lines) + eol).encode("utf-8")


def _expected(original: bytes) -> bytes:
    """`original` with every `geoip-FR?` country, and nothing else, made the placeholder."""
    return original.replace(b"geoip-FR?", b"geoip-" + PLACEHOLDER + b"?")


def _process(data: bytes, tmp_path: Path) -> tuple[list[str], bytes, object]:
    source = tmp_path / ".build.info"
    source.write_bytes(data)
    item = lab_capture.Item(
        source, PurePosixPath(".build.info"), "", "", lab_capture.kind_of(".build.info")
    )
    outcome = lab_capture.process(item, Identity(characters=["Orlavin"], realms=["Kestrel"]))
    return list(outcome.problems), outcome.result.data, outcome.result


# ─── placed: the country becomes the placeholder, every other byte stays ─────


def test_constructed_geoip_country_becomes_placeholder_and_nothing_else_changes(
    tmp_path: Path,
) -> None:
    original = build_info()
    problems, data, result = _process(original, tmp_path)
    assert problems == []
    assert data == _expected(original)
    assert len(data) == len(original)
    assert b"geoip-FR?" not in data
    # The region token, the account token and the Branch column are untouched.
    assert data.count(b" KR? ") == original.count(b" KR? ") == 4
    assert data.count(b"acct-KOR?") == 4
    assert all(line.startswith(b"kr|") for line in data.splitlines()[1:])
    # Each edit is the two country letters, by offset, in the original.
    assert result.count("geoip") == 4  # type: ignore[attr-defined]
    for edit in result.edits:  # type: ignore[attr-defined]
        assert edit.reason == "geoip"
        assert edit.old == b"FR" and edit.new == PLACEHOLDER
        assert original[edit.offset - 6 : edit.offset] == b"geoip-"
        assert original[edit.end : edit.end + 1] == b"?"


def test_placeholder_has_the_shape_of_a_country() -> None:
    assert len(PLACEHOLDER) == 2 and PLACEHOLDER.isalpha() and PLACEHOLDER.isupper()


@pytest.mark.parametrize(
    ("tags", "eol"),
    [
        ("geoip-FR? OSX x86_64 KR?", "\n"),  # first token of the cell
        ("OSX x86_64 KR? geoip-FR?", "\n"),  # last token of the cell
        ("OSX KR?:geoip-FR? enUS text?", "\n"),  # first token of a later group
        (TAGS, "\r\n"),  # CRLF lines
    ],
    ids=["constructed-first", "constructed-last", "constructed-after-colon", "constructed-crlf"],
)
def test_constructed_geoip_placed_wherever_it_is_a_whole_tags_token(
    tmp_path: Path, tags: str, eol: str
) -> None:
    original = build_info(tags, eol=eol)
    problems, data, _ = _process(original, tmp_path)
    assert problems == []
    assert data == _expected(original)


def test_constructed_bom_header_still_locates_tags(tmp_path: Path) -> None:
    original = b"\xef\xbb\xbf" + build_info()
    problems, data, _ = _process(original, tmp_path)
    assert problems == []
    assert data == _expected(original)


def test_constructed_placeholder_already_there_is_kept_without_an_edit(tmp_path: Path) -> None:
    original = _expected(build_info())
    problems, data, result = _process(original, tmp_path)
    assert problems == []
    assert data == original
    assert result.count("geoip") == 0  # type: ignore[attr-defined]


def test_constructed_build_info_without_geoip_is_untouched(tmp_path: Path) -> None:
    original = build_info("OSX x86_64 KR? enUS speech?")
    problems, data, _ = _process(original, tmp_path)
    assert problems == []
    assert data == original


# ─── unplaceable: refused, and the refusal never quotes the country ──────────


def _refusal_cases() -> list[tuple[str, bytes]]:
    good = build_info()
    no_tags_header = HEADER.replace("Tags!STRING:0", "Labels!STRING:0")
    two_tags_header = HEADER.replace("Armadillo!STRING:0", "Tags!STRING:0")
    short_row = build_info().replace(b"||wow_classic_beta", b"|wow_classic_beta")
    return [
        ("three-letter-country", build_info(TAGS.replace("geoip-FR?", "geoip-FRA?"))),
        ("lower-case-country", build_info(TAGS.replace("geoip-FR?", "geoip-fr?"))),
        ("no-question-mark", build_info(TAGS.replace("geoip-FR?", "geoip-FR"))),
        ("upper-case-key", build_info(TAGS.replace("geoip-FR?", "GEOIP-FR?"))),
        ("glued-before", build_info(TAGS.replace("geoip-FR?", "xgeoip-FR?"))),
        ("glued-after", build_info(TAGS.replace("geoip-FR?", "geoip-FR?x"))),
        ("other-separator", build_info(TAGS.replace(" geoip-FR?", ",geoip-FR?"))),
        ("other-column", good.replace(b"tpr/wow", b"geoip-FR?", 1)),
        ("no-tags-header", build_info(header=no_tags_header)),
        ("two-tags-headers", build_info(header=two_tags_header)),
        ("row-shorter-than-header", short_row),
        ("cr-only-lines", build_info(eol="\r")),
    ]


@pytest.mark.parametrize(
    "data", [c[1] for c in _refusal_cases()], ids=[f"constructed-{c[0]}" for c in _refusal_cases()]
)
def test_constructed_unplaceable_geoip_refuses_the_file(tmp_path: Path, data: bytes) -> None:
    problems, _, _ = _process(data, tmp_path)
    refusals = [p for p in problems if p.startswith(UNPLACED)]
    assert refusals, problems
    text = " ".join(problems)
    assert "FR" not in text


def test_constructed_refused_build_info_is_not_written(tmp_path: Path) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    (root / ".build.info").write_bytes(build_info(TAGS.replace("geoip-FR?", "geoip-FRA?")))
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_", "--log-lines", "10") != 0
    assert "macos/.build.info" not in outputs(out)


# ─── end to end: a capture run ───────────────────────────────────────────────


def test_constructed_capture_writes_placeholder_and_counts_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    original = build_info()
    (root / ".build.info").write_bytes(original)
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_", "--log-lines", "10") == 0
    assert outputs(out)["macos/.build.info"] == _expected(original)
    assert (root / ".build.info").read_bytes() == original, "the install is never written"
    printed = capsys.readouterr().out
    row = next(line for line in printed.splitlines() if "`macos/.build.info`" in line)
    assert "geoip-country-replaced: 4" in row
    assert "geoip-FR" not in printed


def test_constructed_test_helper_install_still_captures(tmp_path: Path) -> None:
    """build_install's own `.build.info` has no Tags column: nothing to place, nothing refused."""
    root = tmp_path / "World of Warcraft"
    build_install(root)
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_", "--log-lines", "10") == 0
    assert outputs(out)["macos/.build.info"] == (root / ".build.info").read_bytes()
