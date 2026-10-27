"""The packaged Unreal / Project AirSim build, running natively on Windows."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from ..core import inifile
from ..core.config import UnrealCfg
from ..core.log import LOG
from . import health
from .winproc import IS_WINDOWS, JobObject, image_name, pid_alive


class UnrealError(RuntimeError):
    pass


# Windows process creation flags. Unreal gets the CPU ahead of the WSL VM (vmmem) under contention.
# "low" is Windows' IDLE_PRIORITY_CLASS. There is deliberately no "realtime": it needs administrator rights and can
# starve the rest of Windows (mouse, keyboard) while the simulator runs.
PRIORITY_CLASSES = {
    "low": 0x00000040,
    "below_normal": 0x00004000,
    "normal": 0,
    "above_normal": 0x00008000,
    "high": 0x00000080,
}


GAME_USER_SETTINGS = "/Script/Engine.GameUserSettings"
FULLSCREEN_MODES = {"fullscreen": 0, "windowed_fullscreen": 1, "windowed": 2}  # UE's EWindowMode


def display_settings_file(exe: Path) -> Path:
    """Where this packaged game keeps the display settings it remembers between runs."""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / exe.parents[2].name / "Saved" / "Config" / "Windows" / "GameUserSettings.ini"


def display_settings(cfg: UnrealCfg) -> dict[str, int]:
    mode = FULLSCREEN_MODES["windowed" if cfg.windowed else "fullscreen"]
    return {
        "ResolutionSizeX": cfg.width, "ResolutionSizeY": cfg.height,
        "LastUserConfirmedResolutionSizeX": cfg.width, "LastUserConfirmedResolutionSizeY": cfg.height,
        "DesiredScreenWidth": cfg.width, "DesiredScreenHeight": cfg.height,
        "LastUserConfirmedDesiredScreenWidth": cfg.width, "LastUserConfirmedDesiredScreenHeight": cfg.height,
        "FullscreenMode": mode, "LastConfirmedFullscreenMode": mode, "PreferredFullscreenMode": mode,
    }


def apply_display_settings(cfg: UnrealCfg) -> None:
    """Make the display settings Unreal remembers match [unreal] width / height / windowed.

    The command-line flags only decide how the window starts. When the scene loads, Unreal re-applies
    what it saved last time, so a saved "borderless fullscreen" turns a small window into a full-screen
    one at the moment the vehicle spawns. Writing the settings before every start keeps the window
    exactly what the config (or the student) asked for."""
    path = display_settings_file(cfg.exe)
    try:
        old = path.read_bytes().decode("utf-8-sig") if path.is_file() else ""
        new = inifile.set_keys(old, GAME_USER_SETTINGS, display_settings(cfg))
        if new != old:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(new)
    except OSError as exc:
        LOG.warning("could not update Unreal's display settings (%s): %s. The window may change size when "
                    "the vehicle spawns.", path, exc)


def _taskkill(pid: int, force: bool) -> None:
    cmd = ["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else [])
    subprocess.run(cmd, capture_output=True)


def terminate_pid(pid: int, grace: float = 4.0) -> None:
    """Ask a process tree to close (WM_CLOSE), then force-kill it after `grace` seconds."""
    if not pid_alive(pid):
        return
    if not IS_WINDOWS:
        import os
        import signal

        os.kill(pid, signal.SIGTERM)
        return
    _taskkill(pid, force=False)
    deadline = time.monotonic() + grace
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.25)
    if pid_alive(pid):
        LOG.warning("pid %s did not close within %.0f s (Project AirSim sometimes hangs while unloading a scene); "
                    "forcing", pid, grace)
        _taskkill(pid, force=True)


def pid_is(pid: int, exe: Path) -> bool:
    """True if `pid` is a live process running `exe` (guards against recycled pids)."""
    if not pid_alive(pid):
        return False
    name = image_name(pid)
    return name is not None and name.lower() == exe.name.lower()


class UnrealProcess:
    def __init__(self, cfg: UnrealCfg, log_path: Path, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.log_path = log_path
        self.dry_run = dry_run
        self.proc: subprocess.Popen | None = None
        self._log_handle = None

    @property
    def command(self) -> list[str]:
        return [str(self.cfg.exe), *self.cfg.launch_args]

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, job: JobObject | None = None) -> None:
        if self.dry_run:
            LOG.info("[dry-run] start %s", subprocess.list2cmdline(self.command))
            return
        if not self.cfg.exe.is_file():
            raise UnrealError(f"Unreal executable not found: {self.cfg.exe}")
        apply_display_settings(self.cfg)
        self._log_handle = open(self.log_path, "wb")
        # New process group: Ctrl+C in the launcher's console must not be delivered to the sim.
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP | PRIORITY_CLASSES[self.cfg.priority]) if IS_WINDOWS else 0
        self.proc = subprocess.Popen(
            self.command,
            cwd=self.cfg.exe.parent,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        if job is not None:
            job.assign(self.proc)
        LOG.info("started Unreal (pid %s)", self.proc.pid)

    def wait_ready(self, timeout: float | None = None) -> None:
        if self.dry_run:
            return
        timeout = self.cfg.ready_timeout_s if timeout is None else timeout
        ports = [self.cfg.topic_port, self.cfg.service_port]
        LOG.info("waiting for Project AirSim ports %s on %s (up to %.0f s)", ports, self.cfg.host, timeout)
        pending = health.wait_tcp(self.cfg.host, ports, timeout, alive=self.alive)
        if not pending:
            LOG.info("Unreal is accepting connections")
            return
        if not self.alive():
            code = self.proc.returncode if self.proc else "?"
            raise UnrealError(f"Unreal exited during startup (exit code {code}). See {self.log_path} and projectairsim_server.log")
        raise UnrealError(f"Unreal is running but ports {pending} did not open within {timeout:.0f} s")

    def stop(self, grace: float | None = None) -> None:
        if self.proc is not None and self.proc.poll() is None:
            LOG.info("stopping Unreal (pid %s)", self.proc.pid)
            terminate_pid(self.proc.pid, self.cfg.close_grace_s if grace is None else grace)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                LOG.warning("Unreal did not exit")
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
