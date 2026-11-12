"""The command-line launcher as the window uses it: the command that starts it, its output, and how to stop it.

The window does not start the simulator itself. It runs the command-line launcher (`mrrsim run <script> ...`) as a
background process and shows what it prints, so the window and the command line always behave the same way. Stop asks
that process to stop the way Ctrl+C would: the script gets time to land the vehicle first.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from ...core import paths

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: no console window flashes up for a child process


def cli_command() -> list[str]:
    """The command that runs the command-line launcher: its sibling exe when installed, else this source tree."""
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).with_name("MRRSimulator.exe"))]
    return [sys.executable, "-B", "-m", "mrrsim"]


def stop_file() -> Path:
    """The file the window touches to ask a run to stop (`mrrsim run --stop-file` watches it)."""
    return paths.data_dir() / "stop.request"


def kill_unreal(exe_name: str) -> int:
    """Force-close Unreal (the program `exe_name`, and anything it started) at once. Returns how many processes were
    ended."""
    if os.name != "nt":
        return 0
    result = subprocess.run(["taskkill", "/F", "/T", "/IM", exe_name], capture_output=True, text=True,
                            creationflags=NO_WINDOW)
    return result.stdout.upper().count("SUCCESS")


def launcher_env() -> dict[str, str]:
    """The environment of the launcher process: UTF-8, unbuffered output, and (from the source tree) the package on
    its import path."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    if not getattr(sys, "frozen", False):  # running from the source tree: the child must find the package
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(paths.repo_root() / "launcher"), env.get("PYTHONPATH")]))
    return env


class Runner:
    """One launcher process at a time; its output arrives on `events` as ("line", text) and ("exit", code)."""

    def __init__(self) -> None:
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.proc: subprocess.Popen | None = None

    @property
    def running(self) -> bool:
        """Is the launcher process still going?"""
        return self.proc is not None and self.proc.poll() is None

    def start(self, command: list[str], cwd: Path) -> None:
        """Start the launcher in `cwd`. A stop request left over from an earlier run is removed first."""
        cwd.mkdir(parents=True, exist_ok=True)
        stop_file().unlink(missing_ok=True)
        self.proc = subprocess.Popen(
            command, cwd=cwd, env=launcher_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW,
        )
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        """Pass on every line the process prints, then its exit code (runs in its own thread)."""
        with proc.stdout:
            for line in proc.stdout:
                self.events.put(("line", line.rstrip("\r\n")))
        self.events.put(("exit", proc.wait()))

    def request_stop(self) -> None:
        """Same as Ctrl+C in the launcher; asking again stops the script at once. The launcher watches the stop file's
        modification time, so touching it is the request."""
        stop_file().parent.mkdir(parents=True, exist_ok=True)
        stop_file().touch()

    def kill(self) -> None:
        """End the launcher process at once (the window is closing)."""
        if self.running:
            self.proc.kill()
