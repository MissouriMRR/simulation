"""What the Run tab says to the launcher and how it reads the launcher's answers (no widgets, so it is tested directly)."""
from __future__ import annotations

import re
from pathlib import Path

from ...core import config, ports
from ..common.launcher_process import cli_command, stop_file

RESOLUTIONS = ["1280x720", "960x540", "640x360"]
PROVIDED_CHOICE = "Provided vehicle"  # the Run tab's vehicle choice that means 'no design'
MAX_VEHICLES = ports.MAX_VEHICLES
STOPPED = 130  # the launcher's exit code after Ctrl+C or the Stop button

# a fragment of a launcher log line -> what to tell the student (test_commands.py checks the launcher still logs each)
PROGRESS = [
    ("Checking that WSL is responsive", "Waking up WSL (the first start can take a minute)..."),
    ("preflight: checking", "Checking this computer..."),
    ("started Unreal", "Starting the simulator..."),
    ("loading scene", "Loading the scene..."),
    ("starting SITL", "Starting the flight controller..."),
    ("running script", "Running your script"),
    ("shutting down", "Shutting down..."),
]

DOCTOR_SUMMARY = re.compile(r"(\d+) checks: (\d+) failed, (\d+) warnings")


def _launcher(config_file: Path, *args: str) -> list[str]:
    """The launcher with this window's settings file (-c: the same one, whatever folder it starts in), the given
    command, and the stop file the Stop button touches."""
    return cli_command() + ["-c", str(config_file), *args, "--stop-file", str(stop_file())]


def build_run_command(
    config_file: Path, script: Path, vehicles: int, cameras_off: bool, resolution: str | None,
    project: Path | None = None, script_args: str = "", design: str | None = None,
) -> list[str]:
    """The command of a simulation run: `mrrsim run <script>` with the options picked in the Run tab."""
    command = _launcher(config_file, "run", str(script), "--vehicles", str(vehicles))
    if project:
        command += ["--project", str(project)]
    if design:
        command += ["--design", design]
    if script_args.strip():
        command.append(f"--script-args={script_args.strip()}")  # one argument with an equals sign: "-s" stays the script's
    if cameras_off:
        command.append("--no-cameras")
    if resolution:
        command += ["--resolution", resolution]
    return command


def doctor_command(config_file: Path) -> list[str]:
    """The command of the Run doctor button."""
    return _launcher(config_file, "doctor")


def pip_command(config_file: Path, removing: bool, names: list[str]) -> list[str]:
    """The command that installs (or removes) Python packages for the scripts."""
    return _launcher(config_file, "pip", "remove" if removing else "install", *names)


def valid_resolution(text: str) -> bool:
    """Is `text` a window size like 960x540, within the sizes the launcher accepts?"""
    parts = text.lower().split("x")
    return len(parts) == 2 and all(p.isdigit() and config.MIN_PIXELS <= int(p) <= config.MAX_PIXELS for p in parts)


def line_tag(text: str) -> str | None:
    """How an output line is colored: "script" (the script printed it), "error", "warning", "ok", or None."""
    if text.startswith("[script]"):
        return "script"
    if " ERROR " in text or text.startswith("[FAIL]"):
        return "error"
    if " WARNING " in text or text.startswith("[WARN]"):
        return "warning"
    if text.startswith("[ OK ]"):
        return "ok"
    return None


def doctor_status(code: int, summary: tuple[int, int, int] | None) -> str:
    """One line for the status bar from doctor's last line ("16 checks: 0 failed, 1 warnings")."""
    if code == STOPPED:
        return "Doctor stopped"
    if summary is None:
        return "Doctor did not finish: see the output" if code else "Doctor finished"
    checks, failed, warnings = summary
    if failed:
        return f"Doctor: {failed} of {checks} checks failed. See the red lines in the output"
    if warnings:
        return f"Doctor: all {checks} checks passed, {warnings} warning(s). See the output"
    return f"Doctor: all {checks} checks passed"


def pip_status(code: int, output: str, removing: bool = False) -> str:
    """One line for the status bar after `mrrsim pip install` or `remove`."""
    if code == STOPPED:
        return "Stopped: see the output for what was done"
    if removing:
        return "Package removal failed: see the output" if code != 0 else "Packages removed"
    if code != 0:
        return "Package install failed: see the output"
    if "[WARN] import" in output:
        return "Installed, but it does not import: see the warnings in the output"
    return "Packages installed: they are available to every script"
