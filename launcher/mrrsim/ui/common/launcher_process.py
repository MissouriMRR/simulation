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

UNREAL_EXE = "Simulation-Win64-Shipping.exe"
CREATE_NO_WINDOW = 0x08000000


def cli_command() -> list[str]:
    """The command that runs the command-line launcher: its sibling exe when installed, else this source tree."""
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).with_name("MRRSimulator.exe"))]
    return [sys.executable, "-B", "-m", "mrrsim"]


def stop_file() -> Path:
    return paths.data_dir() / "stop.request"


def kill_unreal() -> int:
    """Force-close Unreal (and anything it started) at once. Returns how many processes were ended."""
    if os.name != "nt":
        return 0
    result = subprocess.run(
        ["taskkill", "/F", "/T", "/IM", UNREAL_EXE], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW
    )
    return result.stdout.upper().count("SUCCESS")


def launcher_env() -> dict[str, str]:
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
        return self.proc is not None and self.proc.poll() is None

    def start(self, command: list[str], cwd: Path) -> None:
        cwd.mkdir(parents=True, exist_ok=True)
        stop_file().unlink(missing_ok=True)
        self.proc = subprocess.Popen(
            command, cwd=cwd, env=launcher_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        with proc.stdout:
            for line in proc.stdout:
                self.events.put(("line", line.rstrip("\r\n")))
        self.events.put(("exit", proc.wait()))

    def request_stop(self) -> None:
        """Same as Ctrl+C in the launcher; asking again stops the script at once."""
        stop_file().parent.mkdir(parents=True, exist_ok=True)
        stop_file().write_text(str(threading.get_ident()), encoding="utf-8")
        os.utime(stop_file(), None)

    def kill(self) -> None:
        if self.running:
            self.proc.kill()
