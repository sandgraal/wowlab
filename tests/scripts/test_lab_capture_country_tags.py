"""`scripts/lab_capture.py`: the geoip and account countries in `.build.info` (M13-34).

The `Tags` cell of `.build.info` carries a region token (which matches the
`Branch` column), a `geoip-<CC>?` token (the country the client was in when
it last updated) and an `acct-<CCC>?` token (the account's country). The
scrub replaces the letters of those two tokens with fixed placeholders of the
same shape (`ZZ`, `ZZZ`), byte-level, and refuses the file when a `geoip` or
`acct` occurrence cannot be placed as a whole token of a Tags cell.

CONSTRUCTED INPUT (L8: hostile-input and boundary tests). Every `.build.info`
below is written for the test, in the shape of docs/LAB_FORMATS.md section 1
and its 2026-09-22 amendment: invented keys, hosts, region, countries and
versions. The region (`KR`), the geoip country (`FR`) and the account country
(`BRA`) are chosen to differ from each other, and none is taken from a
capture. No test needs or touches a real install (ADR-0012).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest
from test_lab_capture import build_install, capture, lab_capture, outputs

Identity = lab_capture.Identity

HEADER = (
    "Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|CDN Key!HEX:16|Install Key!HEX:16"
    "|IM Size!DEC:4|CDN Path!STRING:0|CDN Hosts!STRING:0|CDN Servers!STRING:0|Tags!STRING:0"
    "|Armadillo!STRING:0|Last Activated!STRING:0|Version!STRING:0|KeyRing!HEX:16"
    "|Product!STRING:0"
)
# Row 1 (`wow`) and row 2 (`wow_classic_beta`) carry different countries.
TAGS = (
    "OSX x86_64 KR? acct-BRA? geoip-FR? enUS speech?:OSX x86_64 KR? acct-BRA? geoip-FR? enUS text?"
)
TAGS_2 = (
    "OSX x86_64 KR? acct-MEX? geoip-DE? enUS speech?:OSX x86_64 KR? acct-MEX? geoip-DE? enUS text?"
)
HOSTS = "cdn.example.invalid level3.example.invalid"
SERVERS = "https://cdn.example.invalid/?maxhosts=4"


@dataclass(frozen=True)
class Tag:
    key: str
    real: str  # the invented country in row 1 of the constructed file
    real_2: str  # the invented country in row 2
    placeholder: bytes
    label: str

    @property
    def token(self) -> str:
        return f"{self.key}-{self.real}?"

    @property
    def token_2(self) -> str:
        return f"{self.key}-{self.real_2}?"

    @property
    def placed(self) -> bytes:
        return f"{self.key}-".encode() + self.placeholder + b"?"


GEOIP = Tag("geoip", "FR", "DE", lab_capture.GEOIP_PLACEHOLDER, lab_capture.GEOIP_UNPLACED_LABEL)
ACCT = Tag("acct", "BRA", "MEX", lab_capture.ACCT_PLACEHOLDER, lab_capture.ACCT_UNPLACED_LABEL)
REAL_COUNTRIES = [c for t in (GEOIP, ACCT) for c in (t.real, t.real_2)]
TAG_LIST = [GEOIP, ACCT]
TAG_IDS = [t.key for t in TAG_LIST]


def _row(product: str, version: str, tags: str) -> str:
    return f"kr|1|{'0a' * 16}|{'0b' * 16}|||tpr/wow|{HOSTS}|{SERVERS}|{tags}|||{version}||{product}"


def build_info(
    tags: str = TAGS, tags_2: str = TAGS_2, *, header: str = HEADER, eol: str = "\n"
) -> bytes:
    """A constructed two-product `.build.info` matching build_install's flavors."""
    lines = [
        header,
        _row("wow", "12.1.5.65432", tags),
        _row("wow_classic_beta", "1.60.1.60001", tags_2),
    ]
    return (eol.join(lines) + eol).encode("utf-8")


def _expected(original: bytes) -> bytes:
    """`original` with every invented country, and nothing else, made its placeholder."""
    for tag in TAG_LIST:
        for token in (tag.token, tag.token_2):
            original = original.replace(token.encode(), tag.placed)
    return original


def _process(data: bytes, tmp_path: Path) -> tuple[list[str], bytes, object]:
    source = tmp_path / ".build.info"
    source.write_bytes(data)
    item = lab_capture.Item(
        source, PurePosixPath(".build.info"), "", "", lab_capture.kind_of(".build.info")
    )
    outcome = lab_capture.process(item, Identity(characters=["Orlavin"], realms=["Kestrel"]))
    return list(outcome.problems), outcome.result.data, outcome.result


def _quotes_a_country(text: str) -> bool:
    return any(country in text for country in REAL_COUNTRIES)


# ─── placed: each country becomes its placeholder, every other byte stays ────


def test_constructed_countries_become_placeholders_and_nothing_else_changes(
    tmp_path: Path,
) -> None:
    original = build_info()
    problems, data, result = _process(original, tmp_path)
    assert problems == []
    assert data == _expected(original)
    assert len(data) == len(original)
    # No row keeps a real country: each Tags cell holds only the placeholders.
    rows = data.splitlines()[1:]
    assert len(rows) == 2
    for row in rows:
        for country in REAL_COUNTRIES:
            assert country.encode() not in row
        for tag in TAG_LIST:
            assert row.count(tag.placed) == 2
    # The region token and the Branch column are untouched.
    assert data.count(b" KR? ") == original.count(b" KR? ") == 4
    assert all(line.startswith(b"kr|") for line in data.splitlines()[1:])
    # Each edit is the country letters, by offset, in the original.
    assert result.count("geoip") == 4  # type: ignore[attr-defined]
    assert result.count("acct") == 4  # type: ignore[attr-defined]
    by_reason = {t.key: t for t in TAG_LIST}
    for edit in result.edits:  # type: ignore[attr-defined]
        tag = by_reason[edit.reason]
        assert edit.old in (tag.real.encode(), tag.real_2.encode())
        assert edit.new == tag.placeholder
        prefix = f"{tag.key}-".encode()
        assert original[edit.offset - len(prefix) : edit.offset] == prefix
        assert original[edit.end : edit.end + 1] == b"?"


@pytest.mark.parametrize("tag", TAG_LIST, ids=TAG_IDS)
def test_placeholder_has_the_shape_of_the_country(tag: Tag) -> None:
    assert len(tag.placeholder) == len(tag.real)
    assert tag.placeholder.isalpha() and tag.placeholder.isupper()


@pytest.mark.parametrize("tag", TAG_LIST, ids=TAG_IDS)
@pytest.mark.parametrize(
    ("template", "eol"),
    [
        ("{t} OSX x86_64 KR?", "\n"),  # first token of the cell
        ("OSX x86_64 KR? {t}", "\n"),  # last token of the cell
        ("OSX KR?:{t} enUS text?", "\n"),  # first token of a later group
        ("OSX KR? {t} enUS speech?:OSX KR? {t} enUS text?", "\r\n"),  # CRLF lines
    ],
    ids=["constructed-first", "constructed-last", "constructed-after-colon", "constructed-crlf"],
)
def test_constructed_country_placed_wherever_it_is_a_whole_tags_token(
    tmp_path: Path, tag: Tag, template: str, eol: str
) -> None:
    original = build_info(template.format(t=tag.token), template.format(t=tag.token_2), eol=eol)
    problems, data, _ = _process(original, tmp_path)
    assert problems == []
    assert data == _expected(original)
    assert tag.token.encode() not in data and tag.token_2.encode() not in data


def test_constructed_bom_header_still_locates_tags(tmp_path: Path) -> None:
    original = b"\xef\xbb\xbf" + build_info()
    problems, data, _ = _process(original, tmp_path)
    assert problems == []
    assert data == _expected(original)


def test_constructed_placeholders_already_there_are_kept_without_an_edit(
    tmp_path: Path,
) -> None:
    original = _expected(build_info())
    problems, data, result = _process(original, tmp_path)
    assert problems == []
    assert data == original
    assert result.count("geoip") == 0 and result.count("acct") == 0  # type: ignore[attr-defined]


def test_constructed_build_info_without_country_tags_is_untouched(tmp_path: Path) -> None:
    original = build_info("OSX x86_64 KR? enUS speech?", "OSX x86_64 KR? enUS text?")
    problems, data, _ = _process(original, tmp_path)
    assert problems == []
    assert data == original


# ─── unplaceable: refused, and the refusal never quotes a country ────────────


def _token_cases(k: str, c: str) -> list[tuple[str, str]]:
    """(case id, replacement for the token `<k>-<c>?`)."""
    return [
        ("wider-country", f"{k}-{c}A?"),
        ("narrower-country", f"{k}-{c[:-1]}?"),
        ("lower-case-country", f"{k}-{c.lower()}?"),
        ("no-question-mark", f"{k}-{c}"),
        ("upper-case-key", f"{k.upper()}-{c}?"),
        ("glued-before", f"x{k}-{c}?"),
        ("glued-after", f"{k}-{c}?x"),
    ]


def _refusal_cases() -> list[tuple[str, Tag, bytes]]:
    no_tags_header = HEADER.replace("Tags!STRING:0", "Labels!STRING:0")
    two_tags_header = HEADER.replace("Armadillo!STRING:0", "Tags!STRING:0")
    cases: list[tuple[str, Tag, bytes]] = []
    for tag in TAG_LIST:
        # A bad token in either row refuses the whole file, the other row being fine.
        for name, token in _token_cases(tag.key, tag.real):
            cases.append((f"row1-{name}", tag, build_info(TAGS.replace(tag.token, token))))
        for name, token in _token_cases(tag.key, tag.real_2):
            bad = TAGS_2.replace(tag.token_2, token)
            cases.append((f"row2-{name}", tag, build_info(TAGS, bad)))
        cases += [
            ("other-separator", tag, build_info(TAGS.replace(" " + tag.token, "," + tag.token))),
            ("other-column", tag, build_info().replace(b"tpr/wow", tag.token.encode(), 1)),
            ("no-tags-header", tag, build_info(header=no_tags_header)),
            ("two-tags-headers", tag, build_info(header=two_tags_header)),
            (
                "row-shorter-than-header",
                tag,
                build_info().replace(b"||wow_classic_beta", b"|wow_classic_beta"),
            ),
            ("cr-only-lines", tag, build_info(eol="\r")),
        ]
    return cases


CASES = _refusal_cases()


@pytest.mark.parametrize(
    ("tag", "data"),
    [(c[1], c[2]) for c in CASES],
    ids=[f"constructed-{c[1].key}-{c[0]}" for c in CASES],
)
def test_constructed_unplaceable_country_tag_refuses_the_file(
    tmp_path: Path, tag: Tag, data: bytes
) -> None:
    problems, _, _ = _process(data, tmp_path)
    assert [p for p in problems if p.startswith(tag.label)], problems
    assert not _quotes_a_country(" ".join(problems))


@pytest.mark.parametrize("tag", TAG_LIST, ids=TAG_IDS)
def test_constructed_refused_build_info_is_not_written(tmp_path: Path, tag: Tag) -> None:
    root = tmp_path / "World of Warcraft"
    build_install(root)
    # Row 1 is placeable; only row 2's token is not. The whole file is refused.
    wider = f"{tag.key}-{tag.real_2}A?"
    (root / ".build.info").write_bytes(build_info(TAGS, TAGS_2.replace(tag.token_2, wider)))
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_", "--log-lines", "10") != 0
    assert "macos/.build.info" not in outputs(out)


# ─── end to end: a capture run ───────────────────────────────────────────────


def test_constructed_capture_writes_placeholders_and_counts_them(
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
    assert "acct-country-replaced: 4" in row
    for tag in TAG_LIST:
        assert tag.token not in printed and tag.token_2 not in printed


def test_constructed_test_helper_install_still_captures(tmp_path: Path) -> None:
    """build_install's own `.build.info` has no Tags column: nothing to place, nothing refused."""
    root = tmp_path / "World of Warcraft"
    build_install(root)
    out = tmp_path / "incoming"
    assert capture(root, out, "--flavor", "_retail_", "--log-lines", "10") == 0
    assert outputs(out)["macos/.build.info"] == (root / ".build.info").read_bytes()
