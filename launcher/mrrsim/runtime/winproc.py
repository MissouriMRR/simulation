"""Windows process plumbing: kill-on-close Job Object, the installer's app mutexes, pid checks.

Everything degrades to a no-op off Windows so the launcher can be developed and unit-tested
anywhere. NOTE: a Job Object only covers Windows processes. Containers live inside WSL, so the
launcher also labels them and reaps leftovers on the next start (see podman.Podman.reap).
"""
from __future__ import annotations

import os
import subprocess
import sys

from ..core.log import LOG

IS_WINDOWS = sys.platform == "win32"

# The Inno Setup installer sets AppMutex=MRRSimulatorMutex,Global\MRRSimulatorMutex and blocks
# installs/uninstalls while either exists, so the launcher must hold both while it runs.
APP_MUTEXES = ("MRRSimulatorMutex", "Global\\MRRSimulatorMutex")

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _ERROR_ALREADY_EXISTS = 183

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wt.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wt.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wt.DWORD),
            ("SchedulingClass", wt.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _k32.CreateJobObjectW.argtypes = [wt.LPVOID, wt.LPCWSTR]
    _k32.CreateJobObjectW.restype = wt.HANDLE
    _k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD]
    _k32.SetInformationJobObject.restype = wt.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    _k32.AssignProcessToJobObject.restype = wt.BOOL
    _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k32.OpenProcess.restype = wt.HANDLE
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.CloseHandle.restype = wt.BOOL
    _k32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
    _k32.CreateMutexW.restype = wt.HANDLE
    _k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    _k32.GetExitCodeProcess.restype = wt.BOOL
    _k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    _k32.QueryFullProcessImageNameW.restype = wt.BOOL
    _k32.GlobalMemoryStatusEx.restype = wt.BOOL


class JobObject:
    """Windows Job Object with KILL_ON_JOB_CLOSE: assigned processes die with the launcher."""

    def __init__(self) -> None:
        self._handle = None
        if not IS_WINDOWS:
            return
        handle = _k32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = _k32.SetInformationJobObject(
            handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(limits), ctypes.sizeof(limits)
        )
        if not ok:
            error = ctypes.get_last_error()
            _k32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        self._handle = handle

    def assign(self, proc: subprocess.Popen) -> bool:
        if not self._handle:
            return False
        process = _k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, proc.pid)
        if not process:
            LOG.warning("job object: OpenProcess(%s) failed (error %s)", proc.pid, ctypes.get_last_error())
            return False
        try:
            if not _k32.AssignProcessToJobObject(self._handle, process):
                LOG.warning("job object: assign pid %s failed (error %s)", proc.pid, ctypes.get_last_error())
                return False
            return True
        finally:
            _k32.CloseHandle(process)

    def close(self) -> None:
        """Closing the last handle kills every process still in the job."""
        if self._handle:
            _k32.CloseHandle(self._handle)
            self._handle = None


class AppMutexes:
    """Holds the named mutexes the installer checks. acquire() is False if another launcher runs."""

    def __init__(self) -> None:
        self._handles: list = []

    def acquire(self) -> bool:
        if not IS_WINDOWS:
            return True
        for name in APP_MUTEXES:
            handle = _k32.CreateMutexW(None, False, name)
            if not handle:
                # e.g. Global\ may be refused in some sessions; the local mutex still counts.
                LOG.warning("could not create mutex %s (error %s)", name, ctypes.get_last_error())
                continue
            if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
                _k32.CloseHandle(handle)
                self.release()
                return False
            self._handles.append(handle)
        return True

    def release(self) -> None:
        for handle in self._handles:
            _k32.CloseHandle(handle)
        self._handles.clear()


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if not IS_WINDOWS:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wt.DWORD()
        return bool(_k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == _STILL_ACTIVE
    finally:
        _k32.CloseHandle(handle)


def image_name(pid: int) -> str | None:
    """Executable file name of a running pid (used to avoid killing a recycled pid)."""
    if not IS_WINDOWS:
        return None
    handle = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wt.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return os.path.basename(buffer.value)
    finally:
        _k32.CloseHandle(handle)


def total_ram_gb() -> float | None:
    if not IS_WINDOWS:
        return None

    class _MemStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
            ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]

    _k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemStatus)]
    status = _MemStatus()
    status.dwLength = ctypes.sizeof(_MemStatus)
    if not _k32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status.ullTotalPhys / 1024**3
