"""`wowlab_core.process` against an injected fake process table (M10-09).

No test here needs a game install or a running client. Flavor folder names
appear in tests only (L6). Paths are built under `tmp_path` so they are
absolute on whatever platform runs the suite.
"""

from __future__ import annotations

import ast
import os
import sys
import threading
import time
import unicodedata
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
        self.written: list[str] = []

    def __setattr__(self, attr: str, value: Any) -> None:
        self._record_write("set", attr)
        object.__setattr__(self, attr, value)

    def __delattr__(self, attr: str) -> None:
        self._record_write("del", attr)
        object.__delattr__(self, attr)

    def _record_write(self, kind: str, attr: str) -> None:
        """The module may set and delete exactly one attribute: the temporary
        `cmdline` shadow around exe(). Anything else fails on the spot."""
        if attr.startswith("_") or attr in {"touched", "written"}:
            return
        if attr != "cmdline":
            pytest.fail(f"process module tried to {kind} psutil.Process.{attr}")
        self.written.append(f"{kind}:{attr}")

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("_") or attr in {"touched", "written"}:
            raise AttributeError(attr)
        self.touched.append(attr)
        if attr not in ALLOWED_SURFACE:
            pytest.fail(f"process module touched psutil.Process.{attr}")
        if attr == "pid":
            return self._pid
        return lambda: self._answer(attr)

    def _answer(self, attr: str) -> Any:
        value = self._values[attr]
        if isinstance(value, BaseException):
            raise value
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


@pytest.fixture
def cmdline_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The argv[0] fallback is switched off on Windows hosts; tests of the
    fallback's logic run the same everywhere."""
    monkeypatch.setattr(process, "_cmdline_is_a_listing_call", lambda: True)


@pytest.fixture
def on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process.sys, "platform", "win32")


@pytest.mark.usefixtures("on_windows")
@pytest.mark.parametrize("exe", [DENIED, ""], ids=["exe-denied", "exe-empty"])
@pytest.mark.parametrize("name", [DENIED, "", "svchost.exe", "Wow.exe"])
def test_cmdline_is_never_touched_on_windows(root: Path, name: Any, exe: Any) -> None:
    """psutil's Windows cmdline() reads the target's PEB with
    ReadProcessMemory (L7, ADR-0023). The argv[0] here would match by path,
    so a fallback that still ran would also show up in the result."""
    argv0 = str(root / "_retail_" / "Wow.exe")
    proc = FakeProcess(4, name=name, exe=exe, cmdline=[argv0])
    found = running_clients([root], flavor_folders=FLAVORS, process_iter=table(proc))
    assert "cmdline" not in proc.touched
    assert all(c.exe is None and c.matched_by != "path" for c in found)
    expected = {"Wow.exe": ClientState.RUNNING, "svchost.exe": None}.get(name, ClientState.UNKNOWN)
    assert [c.state for c in found] == ([expected] if expected else [])


def test_platform_switch_is_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process.sys, "platform", "win32")
    assert process._cmdline_is_a_listing_call() is False
    monkeypatch.setattr(process.sys, "platform", "darwin")
    assert process._cmdline_is_a_listing_call() is True
    monkeypatch.setattr(process.sys, "platform", "linux")
    assert process._cmdline_is_a_listing_call() is True


# argv[0] is chosen by the process: it may add a match, never clear one.


@pytest.mark.usefixtures("cmdline_allowed")
def test_unrelated_argv0_does_not_clear_a_process_with_name_and_exe_denied(
    root: Path, tmp_path: Path
) -> None:
    argv0 = str(tmp_path / "usr" / "bin" / "innocent")
    procs = table(FakeProcess(1, name=DENIED, exe=DENIED, cmdline=[argv0]))
    [found] = running_clients([root], process_iter=procs)
    assert found == ClientProcess(pid=1, state=ClientState.UNKNOWN)
    assert client_state([root], process_iter=procs) is ClientState.UNKNOWN


@pytest.mark.usefixtures("cmdline_allowed")
def test_unrelated_argv0_does_not_clear_a_truncated_client_name(root: Path, tmp_path: Path) -> None:
    argv0 = str(tmp_path / "opt" / "wrapper" / "run")
    procs = table(FakeProcess(1, name="World of Warcraf", exe=DENIED, cmdline=[argv0]))
    [found] = running_clients([root], process_iter=procs)
    assert (found.state, found.name, found.exe) == (ClientState.UNKNOWN, "World of Warcraf", None)
    assert client_state([root], process_iter=procs) is ClientState.UNKNOWN


@pytest.mark.usefixtures("cmdline_allowed")
def test_unrelated_argv0_with_an_ordinary_readable_name_is_still_not_a_client(
    root: Path, tmp_path: Path
) -> None:
    argv0 = str(tmp_path / "usr" / "sbin" / "daemon")
    procs = table(FakeProcess(1, name="daemon", exe=DENIED, cmdline=[argv0]))
    assert running_clients([root], process_iter=procs) == []


@pytest.mark.usefixtures("cmdline_allowed")
def test_argv0_file_name_can_still_add_a_name_match(tmp_path: Path) -> None:
    argv0 = str(tmp_path / "x" / "WowClassic.exe")
    procs = table(FakeProcess(1, name=DENIED, exe=DENIED, cmdline=[argv0]))
    [found] = running_clients(process_iter=procs)
    assert (found.state, found.matched_by, found.exe_source) == (
        ClientState.RUNNING,
        "name",
        "cmdline",
    )


@pytest.mark.usefixtures("cmdline_allowed")
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


# ─── errors psutil did not classify ──────────────────────────────────────────


@pytest.mark.usefixtures("cmdline_allowed")
@pytest.mark.parametrize("field", ["name", "exe", "cmdline", "status"])
@pytest.mark.parametrize(
    "error",
    [OSError(5, "Input/output error"), PermissionError(13, "raw EACCES"), psutil.Error("odd")],
    ids=["oserror", "permissionerror", "psutil-error"],
)
def test_unclassified_error_makes_that_one_process_unknown(field: str, error: Exception) -> None:
    values: dict[str, Any] = {"name": "Wow.exe", "exe": "", "cmdline": [], "status": "running"}
    values[field] = error
    procs = table(FakeProcess(3, **values), FakeProcess(4, name="WowT.exe"))
    found = running_clients(process_iter=procs)
    assert found[0] == ClientProcess(pid=3, state=ClientState.UNKNOWN)
    assert [(c.pid, c.state) for c in found[1:]] == [(4, ClientState.RUNNING)]


def test_unclassified_error_alone_is_unknown_not_not_running() -> None:
    procs = table(FakeProcess(3, name=OSError("boom")))
    assert client_state(process_iter=procs) is ClientState.UNKNOWN


def test_programming_errors_are_not_swallowed() -> None:
    procs = table(FakeProcess(3, name=RuntimeError("bug")))
    with pytest.raises(RuntimeError, match="bug"):
        running_clients(process_iter=procs)


# ─── Unicode normalisation (APFS / HFS+ report NFD) ──────────────────────────


@pytest.mark.parametrize(("root_form", "exe_form"), [("NFC", "NFD"), ("NFD", "NFC")])
def test_path_match_survives_a_different_normalisation_form(
    tmp_path: Path, root_form: str, exe_form: str
) -> None:
    folder = "Jeux Vidéo"
    assert unicodedata.normalize("NFC", folder) != unicodedata.normalize("NFD", folder)
    root = tmp_path / unicodedata.normalize(root_form, folder) / "World of Warcraft"
    exe = (
        tmp_path / unicodedata.normalize(exe_form, folder) / "World of Warcraft" / "_retail_" / "c"
    )
    assert root.parts != exe.parts[: len(root.parts)]
    procs = table(FakeProcess(2, name="c", exe=exe))
    [found] = running_clients([root], flavor_folders=FLAVORS, process_iter=procs)
    assert (found.matched_by, found.flavor_folder) == ("path", "_retail_")


def test_flavor_and_name_match_survive_a_different_normalisation_form(root: Path) -> None:
    nfc, nfd = (unicodedata.normalize(form, "_béta_") for form in ("NFC", "NFD"))
    procs = table(FakeProcess(2, name=nfd + ".exe", exe=root / nfd / "x"))
    [found] = running_clients([root], flavor_folders=[nfc], process_iter=procs)
    assert found.flavor_folder == nfc
    [found] = running_clients(extra_names=[nfc + ".exe"], process_iter=procs)
    assert found.matched_by == "name"


# ─── a bare string is not a list of names ────────────────────────────────────


@pytest.mark.parametrize("parameter", ["flavor_folders", "extra_names"])
@pytest.mark.parametrize("value", ["Wow-ARM64.exe", b"Wow-ARM64.exe"], ids=["str", "bytes"])
def test_bare_string_is_rejected(parameter: str, value: Any) -> None:
    single_letter = table(FakeProcess(1, name="w"))
    with pytest.raises(TypeError, match=parameter):
        running_clients(process_iter=single_letter, **{parameter: value})
    with pytest.raises(TypeError, match=parameter):
        client_state(process_iter=single_letter, **{parameter: value})


def test_bare_string_install_root_is_rejected(root: Path) -> None:
    with pytest.raises(TypeError, match="install_roots"):
        running_clients(str(root), process_iter=table())  # type: ignore[arg-type]


def test_single_letter_process_is_not_a_client() -> None:
    procs = table(FakeProcess(1, name="w"))
    assert running_clients(extra_names=["Wow-ARM64.exe"], process_iter=procs) == []


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


@pytest.mark.usefixtures("cmdline_allowed")
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
    # The `cmdline` shadow is for real psutil.Process objects only. An injected
    # fake is never written to: no set, no delete, of any attribute.
    for proc in procs:
        assert proc.written == [], proc.written
        assert "cmdline" not in vars(proc)


_SPY_LOG: dict[str, set[str]] = {"read": set(), "set": set(), "del": set()}


class _SpyProcess(psutil.Process):
    """A real `psutil.Process` (so psutil's own wrappers run, including the
    `exe()` that calls `self.cmdline()`) that records every public attribute
    read, set or deleted on it once `armed`, by the module or by psutil."""

    armed = False

    def __getattribute__(self, attr: str) -> Any:
        if not attr.startswith("_") and attr != "armed" and type(self).armed:
            _SPY_LOG["read"].add(attr)
        return super().__getattribute__(attr)

    def __setattr__(self, attr: str, value: Any) -> None:
        if not attr.startswith("_"):
            _SPY_LOG["set"].add(attr)
        super().__setattr__(attr, value)

    def __delattr__(self, attr: str) -> None:
        if not attr.startswith("_"):
            _SPY_LOG["del"].add(attr)
        super().__delattr__(attr)


def test_default_probe_against_the_real_process_table_stays_inside_the_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The un-injected path: real `psutil.process_iter`, every process as a
    recording subclass of `psutil.Process`. Lists processes on the test
    machine; reads nothing else. The only attribute ever set or deleted is the
    temporary `cmdline` shadow, and none is left behind."""
    for entries in _SPY_LOG.values():
        entries.clear()
    real_iter = psutil.process_iter
    spies: list[_SpyProcess] = []

    def spying_iter(*args: Any, **kwargs: Any) -> Iterable[Any]:
        assert not args and not kwargs, "process_iter must not prefetch attributes"
        for proc in real_iter():
            try:
                spy = _SpyProcess(proc.pid)
            except psutil.Error:
                continue
            spies.append(spy)
            yield spy

    monkeypatch.setattr(process.psutil, "process_iter", spying_iter)
    monkeypatch.setattr(_SpyProcess, "armed", True)
    found = running_clients([tmp_path / "no-install-here"])
    monkeypatch.setattr(_SpyProcess, "armed", False)

    assert spies
    assert _SPY_LOG["read"] <= ALLOWED_SURFACE, _SPY_LOG["read"] - ALLOWED_SURFACE
    assert _SPY_LOG["set"] == {"cmdline"}
    assert _SPY_LOG["del"] == {"cmdline"}
    assert not any("cmdline" in vars(spy) for spy in spies), "a cmdline shadow was left behind"
    unknown = [c for c in found if c.state is ClientState.UNKNOWN]
    print(f"real table: {len(spies)} processes, {len(found)} reported, {len(unknown)} unknown")


# ─── a real psutil.Process with only the platform layer stubbed ──────────────
# psutil's public exe() calls self.cmdline() by itself when the platform layer
# denies access or returns "". Fakes cannot show that; these drive the real
# wrapper. `_proc` is private psutil API and is touched by the tests only.


def _raise_denied(self: object) -> str:
    raise psutil.AccessDenied(os.getpid())


@pytest.fixture
def me() -> psutil.Process:
    return psutil.Process(os.getpid())


@pytest.fixture
def public_cmdline_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replaces the class-level public `psutil.Process.cmdline` with a recorder
    whose argv[0] is an absolute, existing, executable file: exactly what
    psutil's `exe()` fallback accepts as a guess."""
    calls: list[int] = []

    def recording(self: psutil.Process) -> list[str]:
        calls.append(self.pid)
        return [sys.executable, "-m", "probe"]

    monkeypatch.setattr(psutil.Process, "cmdline", recording)
    return calls


def _stub_platform_layer(
    monkeypatch: pytest.MonkeyPatch, me: psutil.Process, *, name: Any, exe: Any
) -> None:
    layer = type(me._proc)
    for attr, value in (("name", name), ("exe", exe)):
        stub = _raise_denied if value is DENIED else (lambda self, v=value: v)
        monkeypatch.setattr(layer, attr, stub)


def test_psutil_process_accepts_the_shadow_and_loses_it_afterwards(me: psutil.Process) -> None:
    """The obstacle check: if psutil ever adds __slots__ this fails loudly."""
    assert process._os_reported_exe(me) == psutil.Process(os.getpid()).exe()
    assert "cmdline" not in vars(me)
    assert me.cmdline.__func__ is psutil.Process.cmdline, "the class method is back in view"


@pytest.mark.usefixtures("on_windows")
@pytest.mark.parametrize("exe", [DENIED, ""], ids=["exe-denied", "exe-empty"])
@pytest.mark.parametrize("name", [DENIED, "probe", "Wow.exe"])
def test_real_psutil_exe_never_reaches_cmdline_on_windows(
    me: psutil.Process,
    public_cmdline_calls: list[int],
    monkeypatch: pytest.MonkeyPatch,
    name: Any,
    exe: Any,
) -> None:
    _stub_platform_layer(monkeypatch, me, name=name, exe=exe)
    install_root = Path(sys.executable).parent.parent
    found = running_clients([install_root], process_iter=lambda: [me])
    assert public_cmdline_calls == []
    assert "cmdline" not in vars(me)
    assert all(c.exe is None for c in found), "a guessed argv[0] came back as a path"
    expected = {"probe": [], "Wow.exe": [ClientState.RUNNING]}.get(name, [ClientState.UNKNOWN])
    assert [c.state for c in found] == expected


@pytest.mark.usefixtures("cmdline_allowed")
@pytest.mark.parametrize("exe", [DENIED, ""], ids=["exe-denied", "exe-empty"])
def test_real_psutil_argv0_guess_never_clears_a_process(
    me: psutil.Process,
    public_cmdline_calls: list[int],
    monkeypatch: pytest.MonkeyPatch,
    exe: Any,
) -> None:
    _stub_platform_layer(monkeypatch, me, name=DENIED, exe=exe)
    [found] = running_clients(process_iter=lambda: [me])
    assert (found.state, found.exe, found.exe_source) == (ClientState.UNKNOWN, None, None)
    assert client_state(process_iter=lambda: [me]) is ClientState.UNKNOWN
    assert "cmdline" not in vars(me)


@pytest.mark.usefixtures("cmdline_allowed")
def test_real_psutil_argv0_is_labelled_cmdline_when_it_adds_a_match(
    me: psutil.Process, public_cmdline_calls: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off Windows the module's own fallback still runs, and says what it is."""
    _stub_platform_layer(monkeypatch, me, name="probe", exe=DENIED)
    install_root = Path(sys.executable).parent.parent
    [found] = running_clients([install_root], process_iter=lambda: [me])
    assert (found.state, found.matched_by) == (ClientState.RUNNING, "path")
    assert (found.exe, found.exe_source) == (Path(sys.executable), "cmdline")
    assert public_cmdline_calls == [me.pid], "one call, the module's own, none from exe()"


def test_real_psutil_os_reported_exe_is_labelled_exe_and_may_clear(
    me: psutil.Process, public_cmdline_calls: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_platform_layer(monkeypatch, me, name=DENIED, exe=sys.executable)
    assert running_clients(process_iter=lambda: [me]) == []
    install_root = Path(sys.executable).parent.parent
    [found] = running_clients([install_root], process_iter=lambda: [me])
    assert (found.exe, found.exe_source) == (Path(sys.executable), "exe")
    assert public_cmdline_calls == []


# The routing tests above record the PUBLIC cmdline. That alone would miss a
# psutil whose exe() fallback went straight to the platform layer
# (self._proc.cmdline()), so these record the platform layer itself and where
# each call came from. This is the safety argument for an unbounded psutil>=7.0.


class _LayerRecorder:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.exe_depth = 0
        self.exe_impl: Callable[[psutil.Process], str] = psutil.Process.exe


@pytest.fixture
def layer(me: psutil.Process, monkeypatch: pytest.MonkeyPatch) -> _LayerRecorder:
    """Records every platform-layer cmdline call and whether it happened while
    the public exe() was on the stack. The public cmdline stays real."""
    recorder = _LayerRecorder()

    def tracking_exe(self: psutil.Process) -> str:
        recorder.exe_depth += 1
        try:
            return recorder.exe_impl(self)
        finally:
            recorder.exe_depth -= 1

    def layer_cmdline(self: object) -> list[str]:
        recorder.calls.append("inside-exe" if recorder.exe_depth else "outside-exe")
        return [sys.executable, "-m", "probe"]

    monkeypatch.setattr(psutil.Process, "exe", tracking_exe)
    monkeypatch.setattr(type(me._proc), "cmdline", layer_cmdline)
    return recorder


@pytest.mark.usefixtures("on_windows")
@pytest.mark.parametrize("exe", [DENIED, "", sys.executable], ids=["denied", "empty", "readable"])
@pytest.mark.parametrize("name", [DENIED, "probe", "Wow.exe"])
def test_platform_layer_cmdline_is_never_reached_on_windows(
    me: psutil.Process, layer: _LayerRecorder, monkeypatch: pytest.MonkeyPatch, name: Any, exe: Any
) -> None:
    _stub_platform_layer(monkeypatch, me, name=name, exe=exe)
    running_clients([Path(sys.executable).parent.parent], process_iter=lambda: [me])
    assert layer.calls == []


@pytest.mark.usefixtures("cmdline_allowed")
@pytest.mark.parametrize("exe", [DENIED, "", sys.executable], ids=["denied", "empty", "readable"])
@pytest.mark.parametrize("name", [DENIED, "probe", "Wow.exe"])
def test_platform_layer_cmdline_is_never_reached_from_inside_exe_on_any_platform(
    me: psutil.Process, layer: _LayerRecorder, monkeypatch: pytest.MonkeyPatch, name: Any, exe: Any
) -> None:
    _stub_platform_layer(monkeypatch, me, name=name, exe=exe)
    found = running_clients([Path(sys.executable).parent.parent], process_iter=lambda: [me])
    assert "inside-exe" not in layer.calls
    # The module's own, labelled fallback: exactly when exe() gave nothing.
    assert layer.calls == ([] if exe == sys.executable else ["outside-exe"])
    assert all(c.exe_source == ("exe" if exe == sys.executable else "cmdline") for c in found)
    assert "cmdline" not in vars(me)


def test_the_layer_recorder_catches_a_psutil_that_bypasses_the_public_cmdline(
    me: psutil.Process, layer: _LayerRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proof the two tests above are not passing by luck. A simulated future
    psutil whose exe() fallback calls the platform layer directly defeats the
    shadow; the recorder sees it, and the mislabelled path it produces."""

    def future_exe(self: psutil.Process) -> str:
        try:
            return str(self._proc.exe())
        except psutil.AccessDenied:
            return str(self._proc.cmdline()[0])

    layer.exe_impl = future_exe
    _stub_platform_layer(monkeypatch, me, name="probe", exe=DENIED)
    [found] = running_clients([Path(sys.executable).parent.parent], process_iter=lambda: [me])
    assert layer.calls == ["inside-exe"]
    assert found.exe_source == "exe", "the mislabel the tests above exist to catch"


# ─── threads ─────────────────────────────────────────────────────────────────


def _shared_observed_table(size: int) -> tuple[list[psutil.Process], list[str]]:
    """Real psutil.Process subclasses, shared between threads the way
    process_iter's cache shares them. exe() lingers and checks that its shadow
    is still in place when it finishes."""
    problems: list[str] = []

    class Lingering(psutil.Process):
        def exe(self) -> str:
            time.sleep(0.002)
            if "cmdline" not in vars(self):
                problems.append("exe() ran without its shadow")
            return ""

        def name(self) -> str:
            return "zsh"

        def cmdline(self) -> list[str]:
            return []

    return [Lingering(os.getpid()) for _ in range(size)], problems


def test_concurrent_calls_over_the_same_table_complete_and_leave_no_shadow() -> None:
    table_, problems = _shared_observed_table(8)
    start = threading.Barrier(4)
    errors: list[BaseException] = []
    results: list[list[ClientProcess]] = []

    def worker() -> None:
        try:
            start.wait(timeout=10)
            for _ in range(5):
                results.append(running_clients(process_iter=lambda: table_))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "deadlock"
    assert errors == []
    assert problems == []
    assert results == [[]] * 20
    assert not any("cmdline" in vars(proc) for proc in table_)


def test_the_lock_is_released_when_inspection_raises() -> None:
    procs = table(FakeProcess(3, name=RuntimeError("bug")))
    with pytest.raises(RuntimeError):
        running_clients(process_iter=procs)
    assert process._SHADOW_LOCK.acquire(timeout=1)
    process._SHADOW_LOCK.release()


@pytest.mark.parametrize("error", [psutil.AccessDenied(1), OSError("boom"), RuntimeError("bug")])
def test_shadow_is_removed_when_exe_raises(error: Exception) -> None:
    class Raising(psutil.Process):
        def exe(self) -> str:
            assert vars(self)["cmdline"]() == []
            raise error

    proc = Raising(os.getpid())
    with pytest.raises(type(error)):
        process._os_reported_exe(proc)
    assert "cmdline" not in vars(proc)


@pytest.mark.parametrize("raises", [False, True], ids=["returns", "raises"])
def test_an_instance_attribute_that_was_already_there_is_put_back(raises: bool) -> None:
    """Restore exactly: delete only what was absent before."""

    class Observed(psutil.Process):
        def exe(self) -> str:
            assert vars(self)["cmdline"]() == [], "shadow in place during exe()"
            if raises:
                raise psutil.AccessDenied(self.pid)
            return "/somewhere/else"

    def own() -> list[str]:
        return ["mine"]

    proc = Observed(os.getpid())
    proc.cmdline = own  # type: ignore[method-assign]
    if raises:
        with pytest.raises(psutil.AccessDenied):
            process._os_reported_exe(proc)
    else:
        assert process._os_reported_exe(proc) == "/somewhere/else"
    assert vars(proc)["cmdline"] is own


def test_injected_fakes_are_never_modified(root: Path) -> None:
    """Instance-attribute fakes, a spec'd mock that passes isinstance(), and a
    fake with a `cmdline` of its own: all come back exactly as they went in."""
    from types import SimpleNamespace
    from unittest import mock

    def make_namespace() -> SimpleNamespace:
        return SimpleNamespace(
            pid=1, name=lambda: "zsh", exe=lambda: "", cmdline=lambda: [], status=lambda: "running"
        )

    namespace = make_namespace()
    before = dict(vars(namespace))

    specced = mock.MagicMock(spec=psutil.Process)
    assert isinstance(specced, psutil.Process), "the case a bare isinstance() would get wrong"
    specced.pid = 2
    specced.name.return_value = "zsh"
    specced.exe.return_value = ""
    specced.cmdline.return_value = []
    specced.status.return_value = "running"
    specced_cmdline = specced.cmdline

    assert running_clients([root], process_iter=lambda: [namespace, specced]) == []
    assert vars(namespace) == before
    assert specced.cmdline is specced_cmdline
    assert specced.cmdline() == []


def test_injected_object_that_refuses_assignment_is_still_asked_for_its_exe(root: Path) -> None:
    class Slotted:
        __slots__ = ("_exe",)
        pid = 5

        def __init__(self, exe: str) -> None:
            self._exe = exe

        def name(self) -> str:
            return "client"

        def exe(self) -> str:
            return self._exe

        def cmdline(self) -> list[str]:
            raise AssertionError("not needed when exe() answers")

        def status(self) -> str:
            return "running"

    procs = [Slotted(str(root / "_retail_" / "client"))]
    [found] = running_clients([root], flavor_folders=FLAVORS, process_iter=lambda: procs)
    assert (found.matched_by, found.exe_source, found.flavor_folder) == ("path", "exe", "_retail_")


def test_psutil_process_that_refuses_assignment_is_not_asked_for_its_exe() -> None:
    class Sealed(psutil.Process):
        def __setattr__(self, attr: str, value: Any) -> None:
            if attr == "cmdline":
                raise AttributeError(attr)
            super().__setattr__(attr, value)

        def exe(self) -> str:
            raise AssertionError("exe() must not run unshadowed on a psutil.Process")

    assert process._os_reported_exe(Sealed(os.getpid())) == ""


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
        "Error",  # caught per process, to fail closed
        "Process",  # type check only; a second assertion below holds it to that
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
        "sys",  # platform check that keeps cmdline() off Windows
        "threading",  # the lock around the shadow
        "typing",
        "unicodedata",
        "psutil",
        "pydantic",
    }
    # `from psutil import X` would slip past the attribute check above.
    assert not any(isinstance(n, ast.ImportFrom) and n.module == "psutil" for n in ast.walk(tree))

    # `psutil.Process` appears only as the second argument of isinstance():
    # never constructed, never subscripted, no attribute taken from it.
    process_refs = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and n.attr == "Process"
        and isinstance(n.value, ast.Name)
        and n.value.id == "psutil"
    ]
    isinstance_args = [
        n.args[1]
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id in {"isinstance", "issubclass"}
        and len(n.args) == 2
    ]
    assert process_refs
    assert all(any(ref is arg for arg in isinstance_args) for ref in process_refs)

    # No private psutil API: no `_proc`, no other underscore attribute on anything
    # that is not this module's own helper.
    assert "_proc" not in {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

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
