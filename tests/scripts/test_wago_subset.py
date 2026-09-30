"""`scripts/wago_subset.py`: an ID-filtered subset is whole, verbatim lines (M12-01).

The rule is `docs/LAB_PLAN.md` §14.6: the header line and the whole lines
whose value in one column is in a set, byte for byte, in the source's order.
Most tests here run the tool on constructed CSVs (labelled `constructed`: the
hostile and boundary shapes a real recording may not show). One runs it on a
committed whole table, and one checks that the record splitter gives back
every committed wago CSV byte for byte.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "wago_subset.py"
WAGO = REPO / "lab" / "core" / "tests" / "fixtures" / "wago"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wago_subset", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wago_subset = _load()

E_ACUTE = chr(0xE9)  # the Write tool would decode an escape; chr() keeps the source ASCII

# A constructed table with the shapes a CSV record can take: a quoted comma,
# doubled quotes, a line break inside a quoted field (one record, two
# physical lines), a CRLF-terminated record, empty cells, non-ASCII text,
# numbers with and without leading zeros, and a last line with no newline.
CONSTRUCTED = (
    "ID,Name_lang,Kind\n"
    "1,plain,0\n"
    '2,"with, comma",1\n'
    '7,"say ""hi""",0\n'
    "007,leading zero,0\n"
    '3,"two\nlines",1\n'
    f"4,caf{E_ACUTE},\r\n"
    "5,,1\n"
    "6,last,0"
).encode()


def _physical_lines(data: bytes) -> list[bytes]:
    """Every physical line with its terminator; the last may have none."""
    return re.findall(rb"[^\n]*\n|[^\n]+\Z", data)


def _whole_lines_in_order(subset: bytes, source: bytes) -> bool:
    """True when each line of ``subset`` is a whole line of ``source``, the
    lines appear in the source's order, and each source line is used once."""
    src = _physical_lines(source)
    at = 0
    for line in _physical_lines(subset):
        while at < len(src) and src[at] != line:
            at += 1
        if at == len(src):
            return False
        at += 1
    return True


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run(
    tmp_path: Path,
    source: bytes,
    *args: str,
    sha: str | None = None,
    out_name: str = "out.csv",
) -> tuple[int, Path]:
    src = tmp_path / "source.csv"
    src.write_bytes(source)
    out = tmp_path / out_name
    argv = [str(src), "--sha256", sha or _sha(source), "--out", str(out), *args]
    return wago_subset.main(argv), out


# ─── the rule ────────────────────────────────────────────────────────────────


def test_constructed_subset_is_the_header_and_whole_lines_in_file_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(
        tmp_path,
        CONSTRUCTED,
        "--column",
        "ID",
        # given out of file order, and one value that matches nothing
        *("--value", "6", "--value", "3", "--value", "2", "--value", "4", "--value", "99"),
    )
    assert code == 0
    got = out.read_bytes()
    expected = (
        b"ID,Name_lang,Kind\n"
        b'2,"with, comma",1\n'
        b'3,"two\nlines",1\n' + f"4,caf{E_ACUTE},\r\n".encode() + b"6,last,0"
    )
    assert got == expected
    assert _whole_lines_in_order(got, CONSTRUCTED)
    assert got.startswith(_physical_lines(CONSTRUCTED)[0]), "the header is kept, first"
    printed = capsys.readouterr().out
    assert "kept: the header and 4 of 8 data lines" in printed
    assert "values with no line: 1 (99)" in printed


def test_constructed_line_break_inside_quotes_keeps_both_physical_lines_together(
    tmp_path: Path,
) -> None:
    code, out = _run(tmp_path, CONSTRUCTED, "--column", "Kind", "--value", "1")
    assert code == 0
    got = out.read_bytes()
    assert got == (b'ID,Name_lang,Kind\n2,"with, comma",1\n3,"two\nlines",1\n5,,1\n')
    assert _whole_lines_in_order(got, CONSTRUCTED)


def test_constructed_matching_is_exact_text_not_number(tmp_path: Path) -> None:
    code, out = _run(tmp_path, CONSTRUCTED, "--column", "ID", "--value", "7")
    assert code == 0
    assert out.read_bytes() == b'ID,Name_lang,Kind\n7,"say ""hi""",0\n'


def test_constructed_quoted_cell_matches_on_its_unquoted_text(tmp_path: Path) -> None:
    code, out = _run(tmp_path, CONSTRUCTED, "--column", "Name_lang", "--value", 'say "hi"')
    assert code == 0
    assert out.read_bytes() == b'ID,Name_lang,Kind\n7,"say ""hi""",0\n'


def test_constructed_empty_set_needs_no_values_and_gives_the_header_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(tmp_path, CONSTRUCTED, "--column", "ID", "--no-values")
    assert code == 0
    assert out.read_bytes() == b"ID,Name_lang,Kind\n"
    assert "0 of 8 data lines" in capsys.readouterr().out


def test_constructed_no_value_option_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as info:
        _run(tmp_path, CONSTRUCTED, "--column", "ID")
    assert info.value.code == 2
    assert not (tmp_path / "out.csv").exists()


def test_constructed_values_from_another_csv_with_except(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = tmp_path / "ids.csv"
    ids.write_bytes(b"Ref,SpellID,Other\n10,5,0\n11,0,1\n12,5,\n13,6,77\n")
    code, out = _run(
        tmp_path,
        CONSTRUCTED,
        "--column",
        "ID",
        "--values-from",
        str(ids),
        "--values-column",
        "SpellID",
        "--values-column",
        "Other",
        "--except",
        "0",
    )
    assert code == 0
    assert out.read_bytes() == b"ID,Name_lang,Kind\n1,plain,0\n5,,1\n6,last,0"
    printed = capsys.readouterr().out
    assert f"sha256 {_sha(ids.read_bytes())}" in printed
    assert "except 0 (1 dropped)" in printed
    assert "column ID in 4 distinct values" in printed  # 5, 1, 6, 77
    assert "values with no line: 1 (77)" in printed


def test_constructed_records_give_back_the_source_byte_for_byte() -> None:
    parts = list(wago_subset.records(CONSTRUCTED))
    assert b"".join(r.raw for r in parts) == CONSTRUCTED
    assert [r.line for r in parts] == [1, 2, 3, 4, 5, 6, 8, 9, 10]


# ─── refusals: exit 1, nothing written ───────────────────────────────────────


def _refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    source: bytes,
    *args: str,
    sha: str | None = None,
    out_name: str = "out.csv",
) -> str:
    code, out = _run(tmp_path, source, *args, sha=sha, out_name=out_name)
    assert code == 1
    assert not out.exists(), "a refusal writes nothing"
    err = capsys.readouterr().err
    assert err.startswith("wago_subset: refused: ")
    return err


def test_constructed_wrong_sha256_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    err = _refused(tmp_path, capsys, CONSTRUCTED, "--column", "ID", "--value", "1", sha="0" * 64)
    assert f"sha256 is {_sha(CONSTRUCTED)}" in err


def test_constructed_malformed_sha256_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as info:
        _run(tmp_path, CONSTRUCTED, "--column", "ID", "--value", "1", sha=_sha(CONSTRUCTED).upper())
    assert info.value.code == 2


def test_constructed_missing_column_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert "not in the header" in _refused(
        tmp_path, capsys, CONSTRUCTED, "--column", "Id", "--value", "1"
    )


def test_constructed_repeated_column_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = b"ID,Name,ID\n1,a,2\n"
    assert "named 2 times" in _refused(tmp_path, capsys, source, "--column", "ID", "--value", "1")


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        pytest.param(b"ID,Name\n1,a\n2,b,extra\n", "3 fields", id="constructed-extra-field"),
        pytest.param(b"ID,Name\n1,a\n2\n", "1 fields", id="constructed-short-row"),
        pytest.param(b"ID,Name\n1,a\n\n2,b\n", "an empty line", id="constructed-blank-line"),
        pytest.param(b'ID,Name\n1,"open\n2,b\n', "unterminated quote", id="constructed-open-quote"),
        pytest.param(b'ID,Name\n1,a"b\n2,c\n', "unterminated quote", id="constructed-bare-quote"),
        pytest.param(
            b'ID,Name\n1,"a"b\n', "not readable as CSV", id="constructed-text-after-quote"
        ),
        pytest.param(b'ID,Name\n1,a"b,"c\n', "not readable as CSV", id="constructed-quote-to-eof"),
        pytest.param(b"ID,Name\n1,\xff\n", "not valid UTF-8", id="constructed-bad-utf8"),
        pytest.param(b"\xef\xbb\xbfID,Name\n1,a\n", "byte-order mark", id="constructed-bom"),
        pytest.param(b"", "empty", id="constructed-empty-file"),
    ],
)
def test_constructed_malformed_source_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source: bytes, reason: str
) -> None:
    assert reason in _refused(tmp_path, capsys, source, "--column", "ID", "--value", "1")


def test_constructed_existing_output_is_never_replaced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out.csv"
    out.write_bytes(b"committed fixture\n")
    code, _ = _run(tmp_path, CONSTRUCTED, "--column", "ID", "--value", "1")
    assert code == 1
    assert out.read_bytes() == b"committed fixture\n"
    assert "already exists" in capsys.readouterr().err


def _fake_install(tmp_path: Path) -> Path:
    root = tmp_path / "install"
    (root / "_flavor_" / "Data").mkdir(parents=True)
    (root / ".build.info").write_bytes(b"Branch!STRING:0|Version!STRING:0\nus|1.0.0.1\n")
    return root


@pytest.mark.parametrize(
    "where",
    ["install/out.csv", "install/_flavor_/out.csv", "install/_flavor_/Data/out.csv"],
    ids=["constructed-install-root", "constructed-flavor", "constructed-data"],
)
def test_constructed_out_inside_an_install_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    _fake_install(tmp_path)
    err = _refused(tmp_path, capsys, CONSTRUCTED, "--column", "ID", "--value", "1", out_name=where)
    assert "inside a game install" in err


def test_constructed_out_through_a_symlink_into_an_install_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _fake_install(tmp_path)
    try:
        (tmp_path / "elsewhere").symlink_to(root / "_flavor_", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")
    err = _refused(
        tmp_path, capsys, CONSTRUCTED, "--column", "ID", "--value", "1", out_name="elsewhere/o.csv"
    )
    assert "inside a game install" in err
    assert not (root / "_flavor_" / "o.csv").exists()


def test_constructed_empty_value_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as info:
        _run(tmp_path, CONSTRUCTED, "--column", "Kind", "--value", "")
    assert info.value.code == 2
    assert not (tmp_path / "out.csv").exists()


def test_constructed_source_over_max_bytes_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    err = _refused(
        tmp_path,
        capsys,
        CONSTRUCTED,
        "--column",
        "ID",
        "--value",
        "1",
        "--max-bytes",
        str(len(CONSTRUCTED) - 1),
    )
    assert f"over {len(CONSTRUCTED) - 1} bytes" in err
    code, out = _run(
        tmp_path,
        CONSTRUCTED,
        "--column",
        "ID",
        "--value",
        "1",
        "--max-bytes",
        str(len(CONSTRUCTED)),
    )
    assert code == 0, "a file exactly at the cap is read"
    assert out.exists()


def test_constructed_values_from_over_max_bytes_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = tmp_path / "ids.csv"
    ids.write_bytes(b"SpellID\n1\n" + b"2\n" * len(CONSTRUCTED))
    err = _refused(
        tmp_path,
        capsys,
        CONSTRUCTED,
        "--column",
        "ID",
        "--values-from",
        str(ids),
        "--values-column",
        "SpellID",
        "--max-bytes",
        str(len(CONSTRUCTED)),
    )
    assert "ids.csv is over" in err


def test_constructed_long_record_is_refused_before_it_is_decoded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cap = 2 * (csv.field_size_limit() + 3)
    # Invalid UTF-8 throughout: decoding it first would give a UTF-8 error.
    source = b"ID,Name\n1,a\n2," + b"\xff" * cap + b"\n"
    err = _refused(tmp_path, capsys, source, "--column", "ID", "--value", "1")
    assert f"more than 2 fields can hold ({cap})" in err
    assert "UTF-8" not in err


def test_constructed_long_header_is_refused_before_it_is_decoded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = b"ID," + b"\xff" * csv.field_size_limit() + b"\n1,a\n"
    err = _refused(tmp_path, capsys, source, "--column", "ID", "--value", "1")
    assert "the header is" in err
    assert "UTF-8" not in err


def test_constructed_a_failed_write_leaves_no_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = Path.open

    class _DiskFull:
        """Wraps the created file; the first write puts a few bytes down, then fails."""

        def __init__(self, handle: io.BufferedWriter) -> None:
            self._handle = handle

        def __enter__(self) -> _DiskFull:
            return self

        def __exit__(self, *_exc: object) -> None:
            self._handle.close()

        def write(self, data: bytes) -> int:
            self._handle.write(data[:3])
            self._handle.flush()
            raise OSError(28, "No space left on device", str(tmp_path / "out.csv"))

    def fake_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        handle = real_open(self, mode, *args, **kwargs)  # type: ignore[call-overload]
        return _DiskFull(handle) if mode == "xb" else handle

    monkeypatch.setattr(Path, "open", fake_open)
    code, out = _run(tmp_path, CONSTRUCTED, "--column", "ID", "--value", "1")
    assert code == 1
    assert not out.exists(), "the partly written file this run created is removed"
    assert "No space left on device" in capsys.readouterr().err


def test_constructed_printed_filter_escapes_control_and_format_characters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    esc, bel, rlo = chr(0x1B), chr(0x07), chr(0x202E)
    column = "Spell" + rlo + "DI"
    hostile = [
        esc + "[31mred" + bel,
        "x" + rlo + "gnp.exe",
        "9\nkept: the header and 999 of 999 data lines",
    ]
    ids = tmp_path / ("ids" + rlo + ".csv")
    body = [f'"{column}"'] + ['"' + v.replace('"', '""') + '"' for v in hostile]
    ids.write_bytes(("\n".join(body) + "\n").encode("utf-8"))
    code, _ = _run(
        tmp_path,
        CONSTRUCTED,
        "--column",
        "ID",
        "--values-from",
        str(ids),
        "--values-column",
        column,
        "--except",
        bel,
    )
    assert code == 0
    printed = capsys.readouterr().out
    for raw in (esc, bel, rlo):
        assert raw not in printed, f"{raw!r} reached the terminal"
    lines = printed.splitlines()
    assert len(lines) == 4, "a newline in a value cannot add a line"
    assert sum(line.startswith("kept:") for line in lines) == 1
    assert ascii(hostile[0]) in printed and ascii(column) in printed


# ─── real recordings ─────────────────────────────────────────────────────────


def test_subset_of_a_committed_whole_table_is_whole_lines_in_file_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = WAGO / "TraitNodeXTraitNodeEntry.1.60.1.70058.csv"
    data = source.read_bytes()
    with source.open(newline="", encoding="utf-8") as handle:
        table = list(csv.DictReader(handle))
    # every node of one talent tree, by way of the committed TraitNode table
    with (WAGO / "TraitNode.1.60.1.70058.csv").open(newline="", encoding="utf-8") as handle:
        nodes = sorted(r["ID"] for r in csv.DictReader(handle) if r["TraitTreeID"] == "1116")
    assert nodes, "tree 1116 has nodes in the recording"
    out = tmp_path / "subset.csv"
    argv = [str(source), "--sha256", _sha(data), "--column", "TraitNodeID", "--out", str(out)]
    for node in nodes:
        argv += ["--value", node]
    assert wago_subset.main(argv) == 0
    got = out.read_bytes()

    assert _whole_lines_in_order(got, data)
    assert _physical_lines(got)[0] == _physical_lines(data)[0], "the header is kept"
    kept = list(csv.DictReader(io.StringIO(got.decode("utf-8"), newline="")))
    expected = [r for r in table if r["TraitNodeID"] in set(nodes)]
    assert kept == expected, "exactly the source's matching rows, in the source's order"
    assert f"kept: the header and {len(expected)} of {len(table)} data lines" in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize(
    "path", sorted(WAGO.glob("*.csv")), ids=lambda p: p.name if isinstance(p, Path) else str(p)
)
def test_records_give_back_every_committed_wago_csv_byte_for_byte(path: Path) -> None:
    data = path.read_bytes()
    parts = list(wago_subset.records(data))
    assert b"".join(r.raw for r in parts) == data
    with path.open(newline="", encoding="utf-8") as handle:
        assert len(parts) == len(list(csv.reader(handle))), "one record per CSV row"
