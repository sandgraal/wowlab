"""A Windows Job Object holding one `wowlab ui` run and everything it starts.

The run is created suspended, assigned to a new job whose limit is
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, and only then resumed, so every
process it starts is in the job from its first instruction. Ending the run
terminates the job: the run and all its descendants, with no process
enumeration and no pid matching. If `wowlab ui` itself dies, the last handle
to the job closes and Windows kills them anyway.

This is the only FFI in the Lab. It calls kernel32 (`CreateJobObjectW`,
`SetInformationJobObject`, `AssignProcessToJobObject`, `TerminateJobObject`,
`CloseHandle`) and ntdll's `NtResumeProcess`, all on handles this module
created; it never opens another process (ADR-0023, L7).
"""

from __future__ import annotations

import sys
from typing import Any

CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class JobError(OSError):
    """The run could not be put in a job; it was killed and not resumed."""


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.restype = ctypes.c_long

    def _failed(what: str) -> JobError:
        code = ctypes.get_last_error()
        return JobError(code, f"{what} failed: {ctypes.FormatError(code)}")

    class Job:
        """One job object; `adopt` a suspended run, `terminate` it whole."""

        def __init__(self) -> None:
            handle = _kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise _failed("CreateJobObjectW")
            self._handle: int | None = handle
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _kernel32.SetInformationJobObject(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                error = _failed("SetInformationJobObject")
                self.close()
                raise error

        def adopt(self, process: Any) -> None:  # a subprocess.Popen
            """Put a run started with CREATE_SUSPENDED in the job, then resume
            it. On failure the run is killed, never resumed."""
            # Popen's process handle (private, Windows only): full access,
            # from CreateProcess, open until the Popen is reaped.
            process_handle = int(process._handle)
            if not _kernel32.AssignProcessToJobObject(self._handle, process_handle):
                error = _failed("AssignProcessToJobObject")
                process.kill()
                raise error
            if _ntdll.NtResumeProcess(process_handle) < 0:
                process.kill()
                raise JobError(0, "NtResumeProcess failed")

        def terminate(self) -> None:
            if self._handle is not None:
                _kernel32.TerminateJobObject(self._handle, 1)

        def close(self) -> None:
            """Terminate whatever is left in the job and release it."""
            if self._handle is not None:
                handle, self._handle = self._handle, None
                _kernel32.TerminateJobObject(handle, 1)
                _kernel32.CloseHandle(handle)

else:

    class Job:
        """Windows only."""

        def __init__(self) -> None:
            raise OSError("job objects exist only on Windows")

        def adopt(self, process: Any) -> None:
            raise NotImplementedError

        def terminate(self) -> None:
            raise NotImplementedError

        def close(self) -> None:
            raise NotImplementedError
