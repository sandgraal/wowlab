"""`wowlab_core.process` against an injected fake process table (M10-09).

No test here needs a game install or a running client. Flavor folder names
appear in tests only (L6). Paths are built under `tmp_path` so they are
absolute on whatever platform runs the suite.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import psutil
import pytest

from wowlab_core import process
from wowlab_core.process import (
    KNOWN_CLIENT_NAMES,
    ClientProcess,
    ClientState,
    client_state,
    running_clients,
)

ALLOWED_SURFACE = {"pid", "name", "exe", "cmdline", "status"}

DENIED = object()
GONE = object()
ZOMBIE = object()


class FakeProcess:
    """Stands in for `psutil.Process`. Records every attribute the module
    touches and fails the test on the spot for anything outside the allowed
    surface, so a regression cannot hide behind a swallowed exception."""

    def __init__(
        self,
        pid: int,
        *,
        name: Any = "",
        exe: Any = "",
        cmdline: Any = (),
        status: Any = "running",
    ) -> None:
        self._values = {"name": name, "exe": exe, "cmdline": cmdline, "status": status}
        self._pid = pid
        self.touched: list[str] = []

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("_") or attr == "touched":
            raise AttributeError(attr)
        self.touched.append(attr)
        if attr not in ALLOWED_SURFACE:
            pytest.fail(f"process module touched psutil.Process.{attr}")
        if attr == "pid":
            return self._pid
        return lambda: self._answer(attr)

    def _answer(self, attr: str) -> Any:
        value = self._values[attr]
        if value is DENIED:
            raise psutil.AccessDenied(self._pid)
        if value is GONE:
            raise psutil.NoSuchProcess(self._pid)
        if value is ZOMBIE:
            raise psutil.ZombieProcess(self._pid)
        return list(value) if attr == "cmdline" else str(value)


def table(*procs: FakeProcess) -> Callable[[], Iterable[Any]]:
    return lambda: iter(procs)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "Games" / "World of Warcraft"


FLAVORS = ("_retail_", "_classic_beta_")


# ─── matching by executable path ─────────────────────────────────────────────


def test_matches_executable_under_install_root_and_derives_flavor(root: Path) -> None:
    exe = root / "_classic_beta_" / "SomeNewClient.exe"
    procs = table(
        FakeProcess(10, name="zsh", exe="/bin/zsh"),
        FakeProcess(42, name="SomeNewClient.exe", exe=exe),
    )
    [found] = running_clients([root], flavor_folders=FLAVORS, process_iter=procs)
    assert found == ClientProcess(
        pid=42,
        state=ClientState.RUNNING,
        matched_by="path",
        name="SomeNewClient.exe",
        exe=exe,
        exe_source="exe",
        install_root=root,
        flavor_folder="_classic_beta_",
        status="running",
    )


def test_macos_bundle_path_derives_flavor(root: Path) -> None:
    exe = root / "_retail_" / "World of Warcraft.app" / "Contents" / "MacOS" / "World of Warcraft"
    procs = table(FakeProcess(7, name="World of Warcraft", exe=exe))
    [found] = running_clients([root], flavor_folders=FLAVORS, process_iter=procs)
    assert (found.pid, found.exe, found.flavor_folder) == (7, exe, "_retail_")
    assert found.matched_by == "path"


def test_path_match_is_case_insensitive_and_reports_the_callers_spelling(root: Path) -> None:
    exe = Path(str(root).upper()) / "_RETAIL_" / "Wow.exe"
    procs = table(FakeProcess(5, name="Wow.exe", exe=exe))
    [found] = running_clients([root], flavor_folders=FLAVORS, process_iter=procs)
    assert found.matched_by == "path"
    assert found.install_root == root
    assert found.flavor_folder == "_retail_"


def test_executable_at_the_install_root_matches_without_a_flavor(root: Path) -> None:
    procs = table(FakeProcess(8, name="Launcher", exe=root / "Launcher.exe"))
    [found] = running_clients([root], flavor_folders=FLAVORS, process_iter=procs)
    assert found.state is ClientState.RUNNING
    assert found.flavor_folder is None


def test_folder_the_caller_did_not_name_is_not_reported_as_a_flavor(root: Path) -> None:
    procs = table(FakeProcess(8, name="helper", exe=root / "Tools" / "helper"))
    [found] = running_clients([root], flavor_folders=FLAVORS, process_iter=procs)
    assert found.matched_by == "path"
    assert found.flavor_folder is None
    [found] = running_clients([root], process_iter=procs)
    assert found.flavor_folder is None


def test_sibling_directory_with_a_shared_prefix_is_not_under_the_root(root: Path) -> None:
    sibling = root.with_name(root.name + " Backup") / "_retail_" / "notes"
    procs = table(FakeProcess(9, name="notes", exe=sibling))
    assert running_clients([root], process_iter=procs) == []


def test_symlinked_install_root_matches_the_resolved_executable(tmp_path: Path) -> None:
    real = tmp_path / "volume" / "World of Warcraft"
    real.mkdir(parents=True)
    link = tmp_path / "wow-link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    procs = table(FakeProcess(3, name="client", exe=real.resolve() / "_retail_" / "client"))
    [found] = running_clients([link], flavor_folders=FLAVORS, process_iter=procs)
    assert found.install_root == link
    assert found.flavor_folder == "_retail_"


def test_filesystem_root_is_refused_as_an_install_root() -> None:
    with pytest.raises(ValueError, match="not an install root"):
        running_clients([Path(Path.cwd().anchor)], process_iter=table())


# ─── matching by known name ──────────────────────────────────────────────────


@pytest.mark.parametrize("name", KNOWN_CLIENT_NAMES)
def test_matches_every_known_client_name_without_any_install_root(name: str) -> None:
    procs = table(FakeProcess(1, name="explorer.exe"), FakeProcess(2, name=name))
    [found] = running_clients(process_iter=procs)
    assert (found.pid, found.state, found.matched_by) == (2, ClientState.RUNNING, "name")
    assert found.exe is None
    assert found.flavor_folder is None


def test_known_names_are_the_ones_the_spec_lists() -> None:
    assert set(KNOWN_CLIENT_NAMES) == {
        "Wow.exe",
        "WowClassic.exe",
        "WowT.exe",
        "WowB.exe",
        "World of Warcraft",
        "World of Warcraft Classic",
    }


def test_name_match_ignores_case() -> None:
    [found] = running_clients(process_iter=table(FakeProcess(2, name="wow.EXE")))
    assert found.matched_by == "name"


def test_name_match_keeps_an_executable_outside_every_known_root(
    root: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "other" / "_retail_" / "Wow.exe"
    procs = table(FakeProcess(2, name="Wow.exe", exe=elsewhere))
    [found] = running_clients([root], flavor_folders=FLAVORS, process_iter=procs)
    assert (found.matched_by, found.exe) == ("name", elsewhere)
    assert found.install_root is None
    assert found.flavor_folder is None


def test_executable_file_name_matches_when_the_process_name_is_denied(tmp_path: Path) -> None:
    exe = tmp_path / "x" / "WowClassic.exe"
    [found] = running_clients(process_iter=table(FakeProcess(2, name=DENIED, exe=exe)))
    assert (found.state, found.matched_by, found.name) == (ClientState.RUNNING, "name", None)


def test_extra_names_from_discovery_are_matched() -> None:
    procs = table(FakeProcess(2, name="WowForeverMaybe.exe"))
    assert running_clients(process_iter=procs) == []
    [found] = running_clients(extra_names=["WowForeverMaybe.exe"], process_iter=procs)
    assert found.matched_by == "name"


def test_lookalike_names_do_not_match() -> None:
    procs = table(
        FakeProcess(1, name="Wow.exe.bak"),
        FakeProcess(2, name="NotWow.exe"),
        FakeProcess(3, name="World of Warcraft Launcher", exe="/Applications/L/Launcher"),
    )
    assert running_clients(process_iter=procs) == []


# ─── access denied and unknown ───────────────────────────────────────────────


def test_access_denied_on_everything_yields_unknown() -> None:
    procs = table(FakeProcess(66, name=DENIED, exe=DENIED, cmdline=DENIED, status=DENIED))
    [found] = running_clients(process_iter=procs)
    assert found == ClientProcess(pid=66, state=ClientState.UNKNOWN)
    assert client_state(process_iter=procs) is ClientState.UNKNOWN


def test_denied_exe_with_known_name_is_running_with_no_path() -> None:
    procs = table(FakeProcess(5, name="Wow.exe", exe=DENIED, cmdline=DENIED, status=DENIED))
    [found] = running_clients(process_iter=procs)
    assert (found.state, found.matched_by) == (ClientState.RUNNING, "name")
    assert found.exe is None
    assert found.status is None


def test_denied_exe_with_an_ordinary_readable_name_is_not_a_client() -> None:
    procs = table(FakeProcess(1, name="launchd", exe=DENIED, cmdline=DENIED))
    assert running_clients(process_iter=procs) == []
    assert client_state(process_iter=procs) is ClientState.NOT_RUNNING


def test_truncated_name_that_prefixes_a_client_name_is_unknown_when_nothing_else_reads() -> None:
    procs = table(FakeProcess(9, name="World of Warcraf", exe=DENIED, cmdline=DENIED))
    [found] = running_clients(process_iter=procs)
    assert (found.state, found.name) == (ClientState.UNKNOWN, "World of Warcraf")


def test_truncated_name_is_settled_by_a_readable_executable() -> None:
    launcher = FakeProcess(
        9, name="World of Warcraf", exe="/Applications/L/World of Warcraft Launcher"
    )
    assert running_clients(process_iter=table(launcher)) == []


def test_cmdline_stands_in_for_a_denied_exe(root: Path) -> None:
    exe = root / "_retail_" / "client"
    procs = table(FakeProcess(4, name="client", exe=DENIED, cmdline=[str(exe), "-flag"]))
    [found] = running_clients([root], flavor_folders=FLAVORS, process_iter=procs)
    assert (found.matched_by, found.exe, found.exe_source) == ("path", exe, "cmdline")
    assert found.flavor_folder == "_retail_"


def test_relative_argv0_is_not_treated_as_a_path(root: Path) -> None:
    procs = table(FakeProcess(4, name="client", exe="", cmdline=["./client"]))
    assert running_clients([root], process_iter=procs) == []


def test_cmdline_is_not_read_when_exe_is_available(root: Path) -> None:
    proc = FakeProcess(4, name="Wow.exe", exe=root / "_retail_" / "Wow.exe")
    running_clients([root], process_iter=table(proc))
    assert "cmdline" not in proc.touched


# ─── processes that are gone ─────────────────────────────────────────────────


@pytest.mark.parametrize("field", ["name", "exe", "status"])
@pytest.mark.parametrize("how", [GONE, ZOMBIE], ids=["exited", "zombie"])
def test_process_that_vanishes_mid_inspection_is_skipped(field: str, how: object) -> None:
    values: dict[str, Any] = {"name": "Wow.exe", "exe": "", "status": "running"}
    values[field] = how
    procs = table(FakeProcess(3, **values), FakeProcess(4, name="WowT.exe"))
    assert [c.pid for c in running_clients(process_iter=procs)] == [4]


@pytest.mark.parametrize("status", [psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD])
def test_zombie_client_is_not_running(status: str) -> None:
    procs = table(FakeProcess(3, name="Wow.exe", status=status))
    assert running_clients(process_iter=procs) == []


# ─── the summary guard will use ──────────────────────────────────────────────


def test_client_state_prefers_running_over_unknown(root: Path) -> None:
    unknown = FakeProcess(1, name=DENIED, exe=DENIED, cmdline=DENIED)
    client = FakeProcess(2, name="Wow.exe", exe=root / "_retail_" / "Wow.exe")
    assert client_state([root], process_iter=table()) is ClientState.NOT_RUNNING
    assert client_state([root], process_iter=table(unknown)) is ClientState.UNKNOWN
    assert client_state([root], process_iter=table(unknown, client)) is ClientState.RUNNING


def test_results_are_sorted_by_pid_and_immutable() -> None:
    procs = table(FakeProcess(9, name="Wow.exe"), FakeProcess(2, name="WowB.exe"))
    found = running_clients(process_iter=procs)
    assert [c.pid for c in found] == [2, 9]
    with pytest.raises(Exception, match="frozen"):
        found[0].pid = 1  # type: ignore[misc]


# ─── ADR-0023: process listing only ──────────────────────────────────────────


def test_module_touches_nothing_on_a_process_beyond_the_allowed_surface(root: Path) -> None:
    """Every branch of the module, driven through fakes that fail on any
    attribute outside pid/name/exe/cmdline/status."""
    procs = [
        FakeProcess(1, name="Wow.exe", exe=root / "_retail_" / "Wow.exe"),
        FakeProcess(2, name="World of Warcraft"),
        FakeProcess(3, name=DENIED, exe=DENIED, cmdline=DENIED, status=DENIED),
        FakeProcess(4, name="x", exe=DENIED, cmdline=[str(root / "_retail_" / "x")]),
        FakeProcess(5, name="zsh", exe="/bin/zsh"),
        FakeProcess(6, name="Wow.exe", exe=GONE),
        FakeProcess(7, name="World of Warcraf", exe="", cmdline=DENIED),
        FakeProcess(8, name="Wow.exe", status=psutil.STATUS_ZOMBIE),
    ]
    found = running_clients([root], flavor_folders=FLAVORS, process_iter=lambda: procs)
    assert [c.pid for c in found] == [1, 2, 3, 4, 7]
    touched = {attr for proc in procs for attr in proc.touched}
    assert touched <= ALLOWED_SURFACE
    assert touched == ALLOWED_SURFACE, "the fakes should exercise the whole allowed surface"


class _Spy:
    """Wraps a real `psutil.Process` and records what is asked of it."""

    def __init__(self, proc: psutil.Process, log: set[str]) -> None:
        self._proc = proc
        self._log = log

    def __getattr__(self, attr: str) -> Any:
        self._log.add(attr)
        return getattr(self._proc, attr)


def test_default_probe_against_the_real_process_table_stays_inside_the_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The un-injected path: real `psutil.process_iter`, every Process wrapped
    in a spy. Lists processes on the test machine; reads nothing else."""
    log: set[str] = set()
    real_iter = psutil.process_iter
    seen = 0

    def spying_iter(*args: Any, **kwargs: Any) -> Iterable[Any]:
        nonlocal seen
        assert not args and not kwargs, "process_iter must not prefetch attributes"
        for proc in real_iter():
            seen += 1
            yield _Spy(proc, log)

    monkeypatch.setattr(process.psutil, "process_iter", spying_iter)
    found = running_clients([tmp_path / "no-install-here"])
    assert seen > 0
    assert log <= ALLOWED_SURFACE, log - ALLOWED_SURFACE
    unknown = [c for c in found if c.state is ClientState.UNKNOWN]
    print(f"real table: {seen} processes, {len(found)} reported, {len(unknown)} unknown")


def _psutil_attributes_used(tree: ast.AST) -> set[str]:
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "psutil"
    }


def test_source_uses_only_listing_parts_of_psutil_and_no_ffi() -> None:
    source = Path(process.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert _psutil_attributes_used(tree) <= {
        "process_iter",
        "AccessDenied",
        "NoSuchProcess",
        "STATUS_ZOMBIE",
        "STATUS_DEAD",
    }

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {
        "__future__",
        "collections",
        "enum",
        "pathlib",
        "typing",
        "psutil",
        "pydantic",
    }
    # `from psutil import X` would slip past the attribute check above.
    assert not any(isinstance(n, ast.ImportFrom) and n.module == "psutil" for n in ast.walk(tree))

    # Method calls on anything: none of psutil.Process's intrusive API by name.
    forbidden = {
        "memory_maps", "memory_info", "memory_full_info", "open_files", "connections",
        "net_connections", "threads", "environ", "cwd", "kill", "terminate", "suspend",
        "resume", "send_signal", "nice", "ionice", "rlimit", "cpu_affinity", "num_handles",
        "as_dict", "oneshot", "children", "parent", "parents", "username", "wait",
    }  # fmt: skip
    called = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not (called & forbidden), called & forbidden


def test_library_source_names_no_flavor_folder() -> None:
    source = Path(process.__file__).read_text(encoding="utf-8")
    for token in ("_retail_", "_classic", "_beta_", "_ptr_"):
        assert token not in source
