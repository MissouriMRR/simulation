"""Show the simulator's (Unreal's) window inside a frame of the MRRSimulator window (Windows only).

Unreal opens its own top-level window. `Embedder` finds it by the name of its program, takes the title bar and borders
off it and makes it a child of a tkinter frame (SetParent), then keeps it the size of that frame. Giving the window back
(`release`) puts the borders on again and makes it a top-level window, so closing the frame does not close the sim.

Everything here is ctypes on user32; on other systems `available()` is False and nothing is attempted.
"""
from __future__ import annotations

import os
from typing import Callable

IS_WINDOWS = os.name == "nt"
UNREAL_EXE = "Simulation-Win64-Shipping.exe"

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD, WS_VISIBLE = 0x40000000, 0x10000000
WS_POPUP, WS_CAPTION, WS_THICKFRAME, WS_SYSMENU = 0x80000000, 0x00C00000, 0x00040000, 0x00080000
WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_BORDER, WS_DLGFRAME = 0x00020000, 0x00010000, 0x00800000, 0x00400000
WS_CLIPCHILDREN, WS_CLIPSIBLINGS = 0x02000000, 0x04000000
WS_EX_APPWINDOW, WS_EX_WINDOWEDGE, WS_EX_CLIENTEDGE, WS_EX_DLGMODALFRAME, WS_EX_STATICEDGE = 0x40000, 0x100, 0x200, 0x1, 0x20000
SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x0004, 0x0010, 0x0020, 0x0040
SW_SHOW, SW_RESTORE = 5, 9
FRAME_STYLES = WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_BORDER | WS_DLGFRAME | WS_POPUP
FRAME_EX_STYLES = WS_EX_APPWINDOW | WS_EX_WINDOWEDGE | WS_EX_CLIENTEDGE | WS_EX_DLGMODALFRAME | WS_EX_STATICEDGE

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    user32.SetParent.restype = wintypes.HWND
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.SetFocus.argtypes = [wintypes.HWND]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def available() -> bool:
    return IS_WINDOWS


def _image_name(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def find_window(exe_name: str = UNREAL_EXE) -> int | None:
    """The handle of the main window of the running program called `exe_name`: a visible top-level window (or one we
    already made a child) of that program with a real size. None if there is none."""
    found: list[int] = []
    names: dict[int, str] = {}

    def visit(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in names:
            names[pid.value] = _image_name(pid.value)
        if names[pid.value].lower() == exe_name.lower() and user32.IsWindowVisible(hwnd) and not user32.GetParent(hwnd):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if rect.right - rect.left >= 100 and rect.bottom - rect.top >= 100:
                found.append(int(hwnd))
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found[0] if found else None


def class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


class Embedder:
    """Keeps one foreign window inside a parent window. Create it, call `attach(child)` once the window exists, `resize`
    whenever the parent changes size, `release` to give the window back."""

    def __init__(self, parent_hwnd: int) -> None:
        self.parent = parent_hwnd
        self.child: int | None = None
        self._saved: tuple[int, int] | None = None
        self._saved_rect: tuple[int, int, int, int] | None = None

    @property
    def attached(self) -> bool:
        return self.child is not None and bool(user32.IsWindow(self.child))

    def attach(self, child_hwnd: int) -> None:
        self._saved = (user32.GetWindowLongPtrW(child_hwnd, GWL_STYLE), user32.GetWindowLongPtrW(child_hwnd, GWL_EXSTYLE))
        self._saved_rect = window_rect(child_hwnd)
        style = (self._saved[0] & ~FRAME_STYLES) | WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN | WS_CLIPSIBLINGS
        user32.SetWindowLongPtrW(child_hwnd, GWL_STYLE, style)
        user32.SetWindowLongPtrW(child_hwnd, GWL_EXSTYLE, self._saved[1] & ~FRAME_EX_STYLES)
        user32.SetParent(child_hwnd, self.parent)
        self.child = child_hwnd
        self.resize()

    def resize(self, width: int | None = None, height: int | None = None) -> None:
        """Make the child fill the parent's client area (or the given size)."""
        if not self.attached:
            return
        if width is None or height is None:
            rect = wintypes.RECT()
            user32.GetClientRect(self.parent, ctypes.byref(rect))
            width, height = rect.right - rect.left, rect.bottom - rect.top
        user32.SetWindowPos(self.child, None, 0, 0, max(width, 1), max(height, 1),
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW)

    def focus(self) -> None:
        if self.attached:
            user32.SetFocus(self.child)

    def release(self) -> None:
        """Give the window back to the desktop with its borders (if it still exists)."""
        if not self.attached or self._saved is None:
            self.child = None
            return
        user32.ShowWindow(self.child, SW_SHOW)
        user32.SetParent(self.child, None)
        user32.SetWindowLongPtrW(self.child, GWL_STYLE, self._saved[0])
        user32.SetWindowLongPtrW(self.child, GWL_EXSTYLE, self._saved[1])
        left, top, width, height = self._saved_rect or (100, 100, 1280, 720)
        user32.SetWindowPos(self.child, None, left, top, width, height, SWP_NOZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW)
        self.child = None


def wait_for_window(parent_hwnd: int, schedule: Callable[[int, Callable[[], None]], object], on_attached: Callable[[Embedder], None],
                    is_wanted: Callable[[], bool], poll_ms: int = 400, exe_name: str = UNREAL_EXE) -> None:
    """Poll (with `schedule(ms, fn)`, like tkinter's `after`) until the program's window appears while `is_wanted()`,
    then embed it and call `on_attached(embedder)`."""
    def poll() -> None:
        if not is_wanted():
            return
        hwnd = find_window(exe_name)
        if hwnd is None:
            schedule(poll_ms, poll)
            return
        embedder = Embedder(parent_hwnd)
        embedder.attach(hwnd)
        on_attached(embedder)

    schedule(poll_ms, poll)
