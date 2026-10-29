"""The packaged Unreal / Project AirSim build, running natively on Windows."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from ..core import inifile
from ..core.config import UnrealCfg
from ..core.log import LOG
from . import health
from .winproc import IS_WINDOWS, JobObject, image_name, pid_alive

# === CONSTANTS ================================================================


# Windows process creation flags. Unreal gets the CPU ahead of the WSL VM
# (vmmem) under contention. "low" is Windows' IDLE_PRIORITY_CLASS.
PRIORITY_CLASSES = {
    "low": 0x00000040,
    "below_normal": 0x00004000,
    "normal": 0,
    "above_normal": 0x00008000,
    "high": 0x00000080,
}

TASKKILL_TIMEOUT_S = 15  # taskkill itself must not be able to hang a shut-down

GAME_USER_SETTINGS = "/Script/Engine.GameUserSettings"

FULLSCREEN_MODES = {
    "fullscreen": 0,
    "windowed_fullscreen": 1,
    "windowed": 2,
}  # UE's EWindowMode


# === ERRORS ===================================================================


class UnrealError(RuntimeError):
    """Unreal could not be started or did not come up. `up` logs it and
    returns exit code 2."""


# === DISPLAY SETTINGS =========================================================


def display_settings_file(exe: Path) -> Path | None:
    """Where this packaged game keeps the display settings it remembers between
    runs. The project's folder name comes from the packaged layout
    (<project>/Binaries/Win64/<exe>); None if `exe` is not in a folder that
    deep."""
    if len(exe.parents) < 3 or not exe.parents[2].name:
        return None
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return (
        Path(local)
        / exe.parents[2].name
        / "Saved"
        / "Config"
        / "Windows"
        / "GameUserSettings.ini"
    )


def display_settings(cfg: UnrealCfg) -> dict[str, int]:
    """The GameUserSettings keys that make Unreal's remembered window match
    [unreal] width / height / windowed."""
    mode = FULLSCREEN_MODES["windowed" if cfg.windowed else "fullscreen"]
    return {
        "ResolutionSizeX": cfg.width,
        "ResolutionSizeY": cfg.height,
        "LastUserConfirmedResolutionSizeX": cfg.width,
        "LastUserConfirmedResolutionSizeY": cfg.height,
        "DesiredScreenWidth": cfg.width,
        "DesiredScreenHeight": cfg.height,
        "LastUserConfirmedDesiredScreenWidth": cfg.width,
        "LastUserConfirmedDesiredScreenHeight": cfg.height,
        "FullscreenMode": mode,
        "LastConfirmedFullscreenMode": mode,
        "PreferredFullscreenMode": mode,
    }


def apply_display_settings(cfg: UnrealCfg) -> None:
    """Make the display settings Unreal remembers match [unreal] width / height
    / windowed.

    The command-line flags only decide how the window starts. When the scene
    loads, Unreal re-applies what it saved last time, so a saved "borderless
    fullscreen" turns a small window into a full-screen one at the moment the
    vehicle spawns."""
    path = display_settings_file(cfg.exe)
    if path is None:
        LOG.warning(
            "cannot tell which project %s belongs to (expected "
            "<project>/Binaries/Win64/<exe>), so Unreal's display settings are "
            "not updated. The window may change size when the vehicle spawns.",
            cfg.exe,
        )
        return
    try:
        old = path.read_bytes().decode("utf-8-sig") if path.is_file() else ""
        new = inifile.set_keys(old, GAME_USER_SETTINGS, display_settings(cfg))
        if new != old:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(new)
    except OSError as exc:
        LOG.warning(
            "could not update Unreal's display settings (%s): %s. The window "
            "may change size when the vehicle spawns.",
            path,
            exc,
        )


# === PROCESSES ================================================================


def _end_process(pid: int, force: bool) -> None:
    """Ask a process to close, or with `force` end it outright. On Windows this
    is `taskkill` on the whole process tree, which is given
    `TASKKILL_TIMEOUT_S` to finish; elsewhere it is SIGTERM or SIGKILL."""
    if not IS_WINDOWS:
        try:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        except OSError:
            pass  # already gone
        return
    cmd = ["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else [])
    try:
        subprocess.run(cmd, capture_output=True, timeout=TASKKILL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        LOG.warning(
            "taskkill for pid %s did not finish in %s s", pid, TASKKILL_TIMEOUT_S
        )


def terminate_pid(pid: int, grace: float = 4.0) -> None:
    """Ask a process tree to close (WM_CLOSE on Windows), then force-kill it
    after `grace` seconds."""
    if not pid_alive(pid):
        return
    _end_process(pid, force=False)
    deadline = time.monotonic() + grace
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.25)
    if pid_alive(pid):
        LOG.warning(
            "pid %s did not close within %.0f s (Project AirSim sometimes "
            "hangs while unloading a scene); forcing",
            pid,
            grace,
        )
        _end_process(pid, force=True)


def pid_is(pid: int, exe: Path) -> bool:
    """True if `pid` is a live process running `exe` (guards against recycled
    pids)."""
    if not pid_alive(pid):
        return False
    name = image_name(pid)
    return name is not None and name.lower() == exe.name.lower()


# === THE UNREAL PROCESS =======================================================


class UnrealProcess:
    """The Unreal build as a child process: start it, wait until Project
    AirSim accepts connections, and stop it."""

    def __init__(self, cfg: UnrealCfg, log_path: Path, dry_run: bool = False) -> None:
        """`log_path` receives Unreal's console output. Nothing is started
        until `start`."""
        self.cfg = cfg
        self.log_path = log_path
        self.dry_run = dry_run
        self.proc: subprocess.Popen | None = None
        self._log_handle = None

    @property
    def command(self) -> list[str]:
        """The command line that starts Unreal."""
        return [str(self.cfg.exe), *self.cfg.launch_args]

    @property
    def pid(self) -> int | None:
        """The process id, or None if Unreal has not been started."""
        return self.proc.pid if self.proc else None

    def alive(self) -> bool:
        """True while the process is running."""
        return self.proc is not None and self.proc.poll() is None

    def start(self, job: JobObject | None = None) -> None:
        """Apply the display settings, then launch Unreal with its output in
        the log file. With a `job`, Windows ends Unreal when the launcher
        ends."""
        if self.dry_run:
            LOG.info("[dry-run] start %s", subprocess.list2cmdline(self.command))
            return
        if not self.cfg.exe.is_file():
            raise UnrealError(f"Unreal executable not found: {self.cfg.exe}")
        apply_display_settings(self.cfg)
        # New process group: Ctrl+C in the launcher's console must not be
        # delivered to the sim.
        flags = (
            (subprocess.CREATE_NEW_PROCESS_GROUP | PRIORITY_CLASSES[self.cfg.priority])
            if IS_WINDOWS
            else 0
        )
        try:
            self._log_handle = open(self.log_path, "wb")
            self.proc = subprocess.Popen(
                self.command,
                cwd=self.cfg.exe.parent,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        except OSError as exc:
            self._close_log()
            raise UnrealError(f"could not start {self.cfg.exe}: {exc}") from exc
        if job is not None:
            job.assign(self.proc)
        LOG.info("started Unreal (pid %s)", self.proc.pid)

    def wait_ready(self, timeout: float | None = None) -> None:
        """Wait until Project AirSim's topic and service ports accept
        connections. Raises UnrealError if Unreal exits first or the time
        (`ready_timeout_s`, unless `timeout` is given) runs out."""
        if self.dry_run:
            return
        timeout = self.cfg.ready_timeout_s if timeout is None else timeout
        ports = [self.cfg.topic_port, self.cfg.service_port]
        LOG.info(
            "waiting for Project AirSim ports %s on %s (up to %.0f s)",
            ports,
            self.cfg.host,
            timeout,
        )
        pending = health.wait_tcp(self.cfg.host, ports, timeout, alive=self.alive)
        if not pending:
            LOG.info("Unreal is accepting connections")
            return
        if not self.alive():
            code = self.proc.returncode if self.proc else "?"
            raise UnrealError(
                f"Unreal exited during startup (exit code {code}). See "
                f"{self.log_path} and projectairsim_server.log"
            )
        raise UnrealError(
            f"Unreal is running but ports {pending} did not open within {timeout:.0f} s"
        )

    def stop(self, grace: float | None = None) -> bool:
        """Close Unreal (forced after `grace` seconds, `close_grace_s` unless
        given) and close its log file. Fine to call when it is not running.
        Returns False if Unreal was still running afterwards."""
        gone = True
        if self.proc is not None and self.proc.poll() is None:
            terminate_pid(
                self.proc.pid,
                self.cfg.close_grace_s if grace is None else grace,
            )
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                LOG.warning("Unreal did not exit")
                gone = False
        self._close_log()
        return gone

    def _close_log(self) -> None:
        """Close Unreal's log file, if it is open."""
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
