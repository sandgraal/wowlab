"""install: root resolution, flavor join, partial installs, reads only (M10-05).

Real `.build.info` / `.flavor.info` bytes come from the fixture corpus and
are copied into synthetic installs under `tmp_path`. Inputs the corpus does
not have yet (several products, a short row, CRLF and a BOM, an unknown
column name, broken flavor folders) are constructed, and every test that
uses one says `constructed` in its name. No test reads the real environment
variable or a real default location: `environ` and `defaults` are injected.
"""

from __future__ import annotations

import io
import os
import stat
import sys
import tokenize
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

import wowlab_core
from wowlab_core import install as install_mod
from wowlab_core.install import (
    ENV_ROOT,
    InstallError,
    InstallNotFoundError,
    NotAnInstallError,
    default_roots,
    discover,
    parse_build_info,
    parse_flavor_info,
    read_install,
    resolve_root,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "macos"
REAL_BUILD_INFO = (FIXTURES / ".build.info").read_bytes()
REAL_FLAVOR_INFO = (FIXTURES / "forever" / ".flavor.info").read_bytes()
REAL_FOLDER = "_classic_beta_"  # the flavor column of the fixture's provenance row

# The real file's 15-cell header; constructed rows are laid out against it.
HEADER = REAL_BUILD_INFO.split(b"\n", 1)[0].decode("utf-8")


def _row(product: str, version: str, key: str, active: str = "1") -> str:
    cells = {"Branch": "us", "Active": active, "Build Key": key, "CDN Key": "cdn",
             "Tags": "tags?", "Version": version, "Product": product}  # fmt: skip
    return "|".join(cells.get(c.split("!")[0], "") for c in HEADER.split("|"))


def _flavor_info(product: str) -> bytes:
    return f"Product Flavor!STRING:0\n{product}\n".encode()


def _make_install(
    base: Path, build_info: bytes | None, flavors: dict[str, bytes | None] | None = None
) -> Path:
    root = base / "World of Warcraft"
    root.mkdir(parents=True)
    if build_info is not None:
        (root / ".build.info").write_bytes(build_info)
    for folder, info in (flavors or {}).items():
        (root / folder).mkdir()
        if info is not None:
            (root / folder / ".flavor.info").write_bytes(info)
    return root


def _real_install(base: Path) -> Path:
    return _make_install(base, REAL_BUILD_INFO, {REAL_FOLDER: REAL_FLAVOR_INFO})


# ─── root resolution ─────────────────────────────────────────────────────────


def test_explicit_root_overrides_environment_and_defaults(tmp_path: Path) -> None:
    explicit = _real_install(tmp_path / "explicit")
    from_env = _real_install(tmp_path / "env")
    default = _real_install(tmp_path / "default")

    got = discover(explicit, environ={ENV_ROOT: str(from_env)}, defaults=[default])

    assert got.root == explicit
    assert resolve_root(str(explicit), environ={ENV_ROOT: str(from_env)}) == explicit


def test_environment_overrides_defaults(tmp_path: Path) -> None:
    from_env = _real_install(tmp_path / "env")
    default = _real_install(tmp_path / "default")

    assert discover(environ={ENV_ROOT: str(from_env)}, defaults=[default]).root == from_env


def test_environment_is_read_from_os_environ_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from_env = _real_install(tmp_path / "env")
    default = _real_install(tmp_path / "default")
    monkeypatch.setenv(ENV_ROOT, str(from_env))

    assert discover(defaults=[default]).root == from_env


def test_empty_environment_variable_counts_as_unset(tmp_path: Path) -> None:
    default = _real_install(tmp_path / "default")

    assert discover(environ={ENV_ROOT: ""}, defaults=[default]).root == default


def test_defaults_are_searched_in_order_for_the_first_install(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    not_install = tmp_path / "empty"
    not_install.mkdir()
    first = _real_install(tmp_path / "first")
    second = _real_install(tmp_path / "second")

    got = discover(environ={}, defaults=[missing, not_install, first, second])

    assert got.root == first


def test_defaults_come_from_default_roots_when_not_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = _real_install(tmp_path / "default")
    monkeypatch.setattr(install_mod, "default_roots", lambda: (default,))

    assert discover(environ={}).root == default


def test_no_install_anywhere_raises_not_found_with_the_search_list(tmp_path: Path) -> None:
    candidates = [tmp_path / "a", tmp_path / "b"]

    with pytest.raises(InstallNotFoundError) as caught:
        discover(environ={}, defaults=candidates)

    assert caught.value.searched == tuple(candidates)
    assert isinstance(caught.value, InstallError)


def test_explicit_root_without_build_info_raises_typed_error(tmp_path: Path) -> None:
    root = _make_install(tmp_path, None, {REAL_FOLDER: REAL_FLAVOR_INFO})
    default = _real_install(tmp_path / "default")

    with pytest.raises(NotAnInstallError) as caught:
        discover(root, environ={}, defaults=[default])

    assert caught.value.root == root
    assert caught.value.source == "argument"
    assert isinstance(caught.value, InstallError)


def test_environment_root_without_build_info_raises_and_does_not_fall_through(
    tmp_path: Path,
) -> None:
    root = _make_install(tmp_path, None)
    default = _real_install(tmp_path / "default")

    with pytest.raises(NotAnInstallError) as caught:
        discover(environ={ENV_ROOT: str(root)}, defaults=[default])

    assert caught.value.source == "environment"


def test_read_install_without_build_info_raises_typed_error(tmp_path: Path) -> None:
    with pytest.raises(NotAnInstallError):
        read_install(_make_install(tmp_path, None))


def test_build_info_that_is_a_directory_is_not_an_install_constructed(tmp_path: Path) -> None:
    root = _make_install(tmp_path, None)
    (root / ".build.info").mkdir()

    with pytest.raises(NotAnInstallError):
        discover(root, environ={}, defaults=[])
    with pytest.raises(NotAnInstallError):
        read_install(root)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs")
def test_fifo_files_are_never_read_constructed(tmp_path: Path) -> None:
    """A FIFO named `.build.info` or `.flavor.info` would block a read."""
    root = _make_install(tmp_path, None, {"_pipe_": None})
    os.mkfifo(root / ".build.info")
    with pytest.raises(NotAnInstallError):
        read_install(root)

    (root / ".build.info").unlink()
    (root / ".build.info").write_bytes(REAL_BUILD_INFO)
    os.mkfifo(root / "_pipe_" / ".flavor.info")
    assert read_install(root).flavors == ()


def test_relative_and_tilde_roots_are_made_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _real_install(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert discover("World of Warcraft", environ={}, defaults=[]).root == root

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # what expanduser reads on Windows
    assert discover("~/World of Warcraft", environ={}, defaults=[]).root == root


# ─── platform defaults ───────────────────────────────────────────────────────


def test_default_roots_macos() -> None:
    assert default_roots("darwin") == (PurePosixPath("/Applications/World of Warcraft"),)


def test_default_roots_windows_system_drive_first_then_every_drive() -> None:
    got = default_roots("win32", drives=["D:\\", "c:\\", "E:\\"])

    assert got == (
        PureWindowsPath("C:\\Program Files (x86)\\World of Warcraft"),
        PureWindowsPath("C:\\Program Files\\World of Warcraft"),
        PureWindowsPath("D:\\Program Files (x86)\\World of Warcraft"),
        PureWindowsPath("D:\\Program Files\\World of Warcraft"),
        PureWindowsPath("E:\\Program Files (x86)\\World of Warcraft"),
        PureWindowsPath("E:\\Program Files\\World of Warcraft"),
    )


@pytest.mark.parametrize("platform", ["linux", "freebsd14", "cygwin"])
def test_default_roots_elsewhere_is_empty(platform: str) -> None:
    assert default_roots(platform) == ()


def test_default_roots_for_this_machine_is_a_tuple_of_paths() -> None:
    got = default_roots()
    assert isinstance(got, tuple)
    if sys.platform in {"darwin", "win32"}:
        assert got, "macOS and Windows have defaults"


# ─── flavors and the join ────────────────────────────────────────────────────


def test_several_products_join_by_product_code_sorted_by_folder_constructed(
    tmp_path: Path,
) -> None:
    text = "\n".join(
        [
            HEADER,
            _row("wow", "12.1.5.65432", "aa" * 16),
            _row("wow_classic_beta", "1.60.1.69913", "bb" * 16),
            _row("wowt", "12.2.0.66000", "cc" * 16),
        ]
    )
    root = _make_install(
        tmp_path,
        (text + "\n").encode(),
        {
            "_retail_": _flavor_info("wow"),
            "_classic_beta_": _flavor_info("wow_classic_beta"),
            "_ptr_": _flavor_info("wowt"),
        },
    )

    got = read_install(root)

    assert [f.folder for f in got.flavors] == ["_classic_beta_", "_ptr_", "_retail_"]
    by_folder = {f.folder: f for f in got.flavors}
    assert by_folder["_retail_"].version == "12.1.5.65432"
    assert by_folder["_retail_"].build == 65432
    assert by_folder["_retail_"].build_key == "aa" * 16
    assert by_folder["_ptr_"].product == "wowt"
    assert by_folder["_ptr_"].path == root / "_ptr_"
    assert [r.product for r in got.products] == ["wow", "wow_classic_beta", "wowt"]


def test_flavor_with_no_matching_row_is_kept_with_version_none_constructed(
    tmp_path: Path,
) -> None:
    """Real `.build.info` (one product); a constructed second flavor folder
    whose product has no row."""
    root = _make_install(
        tmp_path,
        REAL_BUILD_INFO,
        {REAL_FOLDER: REAL_FLAVOR_INFO, "_retail_": _flavor_info("wow")},
    )

    got = read_install(root)

    assert [f.folder for f in got.flavors] == [REAL_FOLDER, "_retail_"]
    orphan = got.flavors[1]
    assert orphan.product == "wow"
    assert (orphan.version, orphan.build, orphan.build_key) == (None, None, None)
    assert got.flavors[0].version == "1.60.1.69913"


def test_two_rows_for_one_product_prefer_the_active_one_constructed(tmp_path: Path) -> None:
    text = "\n".join(
        [
            HEADER,
            _row("wow", "12.1.0.60000", "aa" * 16, active="0"),
            _row("wow", "12.1.5.65432", "bb" * 16, active="1"),
        ]
    )
    root = _make_install(tmp_path, text.encode(), {"_retail_": _flavor_info("wow")})

    assert read_install(root).flavors[0].version == "12.1.5.65432"


def test_folders_that_are_not_flavors_are_ignored_constructed(tmp_path: Path) -> None:
    root = _make_install(
        tmp_path,
        REAL_BUILD_INFO,
        {
            REAL_FOLDER: REAL_FLAVOR_INFO,
            "_no_flavor_info_": None,  # matches _*_ but has no .flavor.info
            "Data": _flavor_info("wow"),  # has the file but not the name shape
            "_": _flavor_info("wow"),  # a lone underscore is not _*_
            "_half": _flavor_info("wow"),
        },
    )
    (root / "_flavor_info_is_a_dir_").mkdir()
    (root / "_flavor_info_is_a_dir_" / ".flavor.info").mkdir()
    (root / "_file_").write_bytes(b"")

    assert [f.folder for f in read_install(root).flavors] == [REAL_FOLDER]


def test_empty_flavor_info_gives_an_empty_product_and_no_version_constructed(
    tmp_path: Path,
) -> None:
    root = _make_install(tmp_path, REAL_BUILD_INFO, {"_odd_": b""})

    (flavor,) = read_install(root).flavors
    assert (flavor.folder, flavor.product, flavor.version) == ("_odd_", "", None)


def test_empty_build_info_is_an_install_with_no_products_constructed(tmp_path: Path) -> None:
    root = _make_install(tmp_path, b"", {REAL_FOLDER: REAL_FLAVOR_INFO})

    got = read_install(root)

    assert got.products == ()
    assert got.raw_build_info == ""
    assert got.flavors[0].product == "wow_classic_beta"
    assert got.flavors[0].version is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads anything")
def test_unreadable_flavor_info_keeps_the_flavor_constructed(tmp_path: Path) -> None:
    root = _real_install(tmp_path)
    info = root / REAL_FOLDER / ".flavor.info"
    info.chmod(0)
    try:
        (flavor,) = read_install(root).flavors
    finally:
        info.chmod(0o644)
    assert (flavor.folder, flavor.product, flavor.version) == (REAL_FOLDER, "", None)


# ─── .build.info grammar ─────────────────────────────────────────────────────


def test_unknown_column_is_kept_in_extra_in_header_order_constructed() -> None:
    text = "Zeta!STRING:0|Product!STRING:0|Alpha!DEC:4|Version!STRING:0\nz|wow|7|12.1.5.65432\n"

    (row,) = parse_build_info(text).rows

    assert row.extra == {"Zeta": "z", "Alpha": "7"}
    assert list(row.extra) == ["Zeta", "Alpha"]
    assert (row.product, row.version) == ("wow", "12.1.5.65432")
    assert row.build_key is None, "a column the header lacks is None, not empty"


def test_short_row_is_padded_with_empty_cells_constructed() -> None:
    text = "Product!STRING:0|Version!STRING:0|Build Key!HEX:16|Tags!STRING:0\nwow|12.1.5.65432\n"

    (row,) = parse_build_info(text).rows

    assert (row.product, row.version, row.build_key, row.tags) == ("wow", "12.1.5.65432", "", "")


def test_long_row_keeps_cells_past_the_header_constructed() -> None:
    (row,) = parse_build_info("Product!STRING:0\nwow|x|y\n").rows

    assert row.product == "wow"
    assert row.overflow == ("x", "y")


def test_duplicate_header_name_keeps_both_cells_constructed() -> None:
    (row,) = parse_build_info("Product!STRING:0|Product!STRING:0\nwow|wowt\n").rows

    assert row.product == "wow"
    assert row.extra == {"Product#1": "wowt"}


def test_crlf_and_bom_parse_and_raw_keeps_them_constructed(tmp_path: Path) -> None:
    data = b"\xef\xbb\xbf" + REAL_BUILD_INFO.replace(b"\n", b"\r\n")
    root = _make_install(tmp_path, data, {REAL_FOLDER: REAL_FLAVOR_INFO.replace(b"\n", b"\r\n")})

    got = read_install(root)

    assert got.raw_build_info.encode("utf-8") == data
    (flavor,) = got.flavors
    assert (flavor.product, flavor.version, flavor.build) == (
        "wow_classic_beta",
        "1.60.1.69913",
        69913,
    )
    assert got.products[0].product == "wow_classic_beta", "no stray CR in the last cell"


def test_undecodable_bytes_survive_in_raw_build_info_constructed(tmp_path: Path) -> None:
    data = REAL_BUILD_INFO.replace(b"tpr/wow", b"tpr/\xff\xfe")
    root = _make_install(tmp_path, data, {REAL_FOLDER: REAL_FLAVOR_INFO})

    got = read_install(root)

    assert got.raw_build_info.encode("utf-8", "surrogateescape") == data
    assert got.flavors[0].version == "1.60.1.69913"


def test_non_numeric_last_version_component_gives_no_build_constructed(tmp_path: Path) -> None:
    text = f"{HEADER}\n{_row('wow', '12.1.5.x', 'aa' * 16)}\n"
    root = _make_install(tmp_path, text.encode(), {"_r_": _flavor_info("wow")})

    (flavor,) = read_install(root).flavors
    assert (flavor.version, flavor.build) == ("12.1.5.x", None)


def test_empty_version_cell_gives_no_version_constructed(tmp_path: Path) -> None:
    text = f"{HEADER}\n{_row('wow', '', '')}\n"
    root = _make_install(tmp_path, text.encode(), {"_r_": _flavor_info("wow")})

    (flavor,) = read_install(root).flavors
    assert (flavor.version, flavor.build, flavor.build_key) == (None, None, None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (REAL_FLAVOR_INFO.decode(), "wow_classic_beta"),
        ("Product Flavor!STRING:0\r\nwow\r\n", "wow"),
        ("Other!STRING:0|Product Flavor!STRING:0\nx|wow\n", "wow"),
        ("Something!STRING:0\nwow\n", "wow"),
        ("Product Flavor!STRING:0\n", ""),
        ("", ""),
    ],
    ids=["real", "crlf-constructed", "second-column-constructed", "no-header-name-constructed",
         "header-only-constructed", "empty-constructed"],
)  # fmt: skip
def test_parse_flavor_info(text: str, expected: str) -> None:
    assert parse_flavor_info(text) == expected


def test_active_flag() -> None:
    rows = parse_build_info("Active!DEC:1\n1\n0\n\n2\nx\n").rows
    assert [r.is_active for r in rows] == [True, False, True, False]


# ─── reads only (L1) ─────────────────────────────────────────────────────────

_RECORDING: list[tuple[str, tuple[Any, ...]]] | None = None
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_MUTATING_EVENTS = frozenset(
    {
        "os.mkdir",
        "os.remove",
        "os.rename",
        "os.rmdir",
        "os.symlink",
        "os.link",
        "os.chmod",
        "os.chown",
        "os.utime",
        "os.truncate",
        "os.chflags",
        "os.lchflags",
        "os.setxattr",
        "os.removexattr",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.move",
        "shutil.rmtree",
        "tempfile.mkstemp",
        "tempfile.mkdtemp",
    }
)


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if _RECORDING is None:
        return
    if event == "open":
        _path, mode, flags = args
        if (isinstance(mode, str) and set(mode) & set("wax+")) or (
            isinstance(flags, int) and flags & _WRITE_FLAGS
        ):
            _RECORDING.append((event, args))
    elif event in _MUTATING_EVENTS:
        _RECORDING.append((event, args))


sys.addaudithook(_audit)  # process-wide and permanent; inert unless recording


def _tree_state(root: Path) -> dict[str, tuple[int, int, bytes | None]]:
    state: dict[str, tuple[int, int, bytes | None]] = {}
    for path in [root, *sorted(root.rglob("*"))]:
        st = path.lstat()
        body = path.read_bytes() if stat.S_ISREG(st.st_mode) else None
        state[path.relative_to(root).as_posix()] = (st.st_mode, st.st_mtime_ns, body)
    return state


def _set_read_only(root: Path, read_only: bool) -> None:
    for path in [*sorted(root.rglob("*"), reverse=True), root]:
        if path.is_dir():
            path.chmod(0o555 if read_only else 0o755)
        else:
            path.chmod(0o444 if read_only else 0o644)


@pytest.fixture
def read_only_install(tmp_path: Path) -> Iterator[Path]:
    """The real fixture install plus a constructed orphan flavor, made
    read-only for the length of the test."""
    root = _make_install(
        tmp_path,
        REAL_BUILD_INFO,
        {REAL_FOLDER: REAL_FLAVOR_INFO, "_orphan_": _flavor_info("wow"), "_bare_": None},
    )
    _set_read_only(root, True)
    try:
        yield root
    finally:
        _set_read_only(root, False)


def test_discovery_writes_nothing_anywhere_on_a_read_only_tree(read_only_install: Path) -> None:
    global _RECORDING
    root = read_only_install
    before = _tree_state(root)

    _RECORDING = []
    try:
        got = discover(root, environ={}, defaults=[])
        again = discover(environ={ENV_ROOT: str(root)}, defaults=[])
        also = discover(environ={}, defaults=[root.parent / "nope", root])
        read_install(root)
        resolve_root(root, environ={}, defaults=[])
        writes, _RECORDING = _RECORDING, None
    finally:
        _RECORDING = None

    assert writes == [], f"discovery attempted writes: {writes}"
    assert _tree_state(root) == before, "the install tree changed"
    assert [f.folder for f in got.flavors] == [REAL_FOLDER, "_orphan_"]
    assert got == again == also


def test_the_write_probe_sees_a_write() -> None:
    """The audit probe above is not vacuous."""
    global _RECORDING
    _RECORDING = []
    try:
        with io.open(os.devnull, "w"):  # noqa: UP020 - the builtin open is what is audited
            pass
        seen, _RECORDING = _RECORDING, None
    finally:
        _RECORDING = None
    assert seen and seen[0][0] == "open"


# ─── L6: no flavor constants in the package ──────────────────────────────────

_FORBIDDEN = ("_retail_", "_classic", "wow_classic")


def test_package_code_names_no_flavor_outside_comments() -> None:
    """Docstrings count as code here; only `#` comments are exempt."""
    package = Path(wowlab_core.__file__).resolve().parent
    hits: list[str] = []
    for source in sorted(package.rglob("*.py")):
        with source.open("rb") as handle:
            for token in tokenize.tokenize(handle.readline):
                if token.type == tokenize.COMMENT:
                    continue
                for needle in _FORBIDDEN:
                    if needle in token.string:
                        hits.append(f"{source.name}:{token.start[0]}: {needle!r}")
    assert hits == []


def test_flavor_grep_would_catch_a_hit() -> None:
    """The scan above is not vacuous: a string literal is a hit, a comment is not."""
    sample = b'x = "_retail_"  # _classic\n'
    found = [
        t.string
        for t in tokenize.tokenize(io.BytesIO(sample).readline)
        if t.type != tokenize.COMMENT and any(n in t.string for n in _FORBIDDEN)
    ]
    assert found == ['"_retail_"']
