"""Security review probes for M14-04 (the /api/looks-page read route)."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import lookspage, ui

# All inputs constructed. A race (a link swapped in between the lstat check and
# the open) is modelled by making _is_link answer False; Windows by removing
# os.O_NOFOLLOW. What is then read is bounded by the PAGE_HEADER check and cap.

SECRET = b"m14-04-review-secret-not-a-page"
PAGEISH = lookspage.PAGE_HEADER + b"m14-04-review-planted</html>\n"


@pytest.fixture
def where(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    page = tmp_path / "data" / "pages" / "looks.html"
    page.parent.mkdir(parents=True)
    monkeypatch.setattr(lookspage, "default_page_path", lambda: page)
    return page


def _body(cap: int = ui.DEFAULT_OUTPUT_CAP_BYTES) -> tuple[int, str] | bytes:
    try:
        return ui._looks_page_body(cap)
    except ui._RefusedError as refused:
        return (refused.status, refused.code)


def _raced(monkeypatch: pytest.MonkeyPatch, windows: bool) -> None:
    monkeypatch.setattr(ui, "_is_link", lambda path: False)
    if windows:
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
@pytest.mark.parametrize("windows", [False, True], ids=["posix", "windows-model"])
def test_file_link_swapped_in_after_lstat_constructed(
    where: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: bool
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_bytes(SECRET)
    where.symlink_to(secret)
    _raced(monkeypatch, windows)
    result = _body()
    assert result == (403, "not_a_page"), result
    # A link to a file that begins as a page: posix still refuses (O_NOFOLLOW);
    # the windows model serves it (the named residual), bounded by the header.
    where.unlink()
    pageish = tmp_path / "pageish.html"
    pageish.write_bytes(PAGEISH)
    where.symlink_to(pageish)
    result = _body()
    if windows:
        assert isinstance(result, bytes) and b"m14-04-review-planted" in result
    else:
        assert result == (403, "not_a_page"), result


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_folder_link_swapped_in_after_lstat_constructed(
    where: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # O_NOFOLLOW covers the last component only: a raced pages/ link is
    # followed on every platform, and the header check is what bounds it.
    real = tmp_path / "elsewhere"
    real.mkdir()
    (real / "looks.html").write_bytes(SECRET)
    where.parent.rmdir()
    where.parent.symlink_to(real, target_is_directory=True)
    _raced(monkeypatch, False)
    assert _body() == (403, "not_a_page")
    (real / "looks.html").write_bytes(PAGEISH)
    result = _body()
    assert isinstance(result, bytes) and b"m14-04-review-planted" in result


def test_hard_link_to_a_non_page_is_refused_constructed(where: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_bytes(SECRET)
    try:
        os.link(secret, where)
    except OSError as exc:
        pytest.skip("no hard links here: " + str(exc))
    assert _body() == (403, "not_a_page")


def test_large_non_page_is_read_only_to_the_cap_constructed(
    where: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    where.write_bytes(b"\0" * (8 * 1024 * 1024))
    reads: list[int] = []
    real_read = os.read

    def counting(fd: int, n: int) -> bytes:
        chunk = real_read(fd, n)
        reads.append(len(chunk))
        return chunk

    monkeypatch.setattr(os, "read", counting)
    assert _body(cap=100_000) == (403, "too_large")
    assert sum(reads) <= 100_000 + ui._READ_CHUNK, sum(reads)


@pytest.fixture
def served(tmp_path: Path, where: Path) -> Iterator[Any]:
    where.write_bytes(PAGEISH)
    server = ui.make_server(wowlab_argv=[sys.executable, "-c", "pass"])
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


def _status(server: Any, query: str) -> tuple[int, bytes]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from test_ui_looks import _get as looks_get

    reply = looks_get(server, "/api/looks-page?" + query)
    return reply.status, reply.body


AMP = chr(38)
MANY = AMP.join("f" + str(i) + "=1" for i in range(40))
SHAPES = [
    "token=TOKEN" + AMP + "token=TOKEN",
    "token=TOKEN;path=other",
    "token=TOKEN" + AMP + "=x",
    "token=TOKEN" + AMP + "token",
    "token=TOKEN" + AMP + "%00=1",
    "token=TOKEN" + AMP + MANY,
]


@pytest.mark.parametrize("query", SHAPES)
def test_query_shapes_do_not_reach_the_read_constructed(served: Any, query: str) -> None:
    status, body = _status(served, query.replace("TOKEN", served.token))
    assert status in (400, 403), (query, status)
    assert b"planted" not in body


def test_percent_encoded_token_name_is_the_token_constructed(served: Any) -> None:
    # %74oken decodes to token in both the check and the field-name set.
    status, body = _status(served, "%74oken=" + served.token)
    assert status == 200 and b"planted" in body
