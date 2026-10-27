"""What the Run tab says to the launcher and how it reads the launcher's answers (no widgets, so it is tested directly)."""
from __future__ import annotations

import re
from pathlib import Path

from ...core import ports
from ..common.launcher_process import cli_command, stop_file

RESOLUTIONS = ["1280x720", "960x540", "640x360"]
PROVIDED_CHOICE = "Provided vehicle"  # the Run tab's vehicle choice that means 'no design'
MAX_VEHICLES = ports.MAX_VEHICLES

# a fragment of a launcher log line -> what to tell the student
PROGRESS = [
    ("checking that WSL answers", "Waking up WSL (the first start can take a minute)..."),
    ("preflight", "Checking this computer..."),
    ("started Unreal", "Starting the simulator..."),
    ("loading scene", "Loading the scene..."),
    ("starting SITL", "Starting the flight controller..."),
    ("running script", "Running your script"),
    ("shutting down", "Shutting down..."),
]

DOCTOR_SUMMARY = re.compile(r"(\d+) checks: (\d+) failed, (\d+) warnings")


def build_run_command(
    config_file: Path, script: Path, vehicles: int, cameras_off: bool, resolution: str | None,
    project: Path | None = None, script_args: str = "", design: str | None = None,
) -> list[str]:
    # -c: the launcher must use the same settings file as this window, whatever folder it starts in
    command = cli_command() + ["-c", str(config_file), "run", str(script), "--vehicles", str(vehicles),
                               "--stop-file", str(stop_file())]
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


def valid_resolution(text: str) -> bool:
    parts = text.lower().split("x")
    return len(parts) == 2 and all(p.isdigit() and 160 <= int(p) <= 16384 for p in parts)


def doctor_status(code: int, summary: tuple[int, int, int] | None) -> str:
    """One line for the status bar from doctor's last line ("16 checks: 0 failed, 1 warnings")."""
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
    if removing:
        return "Package removal failed: see the output" if code != 0 else "Packages removed"
    if code != 0:
        return "Package install failed: see the output"
    if "[WARN] import" in output:
        return "Installed, but it does not import: see the warnings in the output"
    return "Packages installed: they are available to every script"
