"""What the Run tab says to the launcher and how it reads the launcher's answers (no widgets, so it is tested directly)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ...core import config, ports
from ..common.launcher_process import cli_command, stop_file

RESOLUTIONS = ["1280x720", "960x540", "640x360"]
PROVIDED_CHOICE = "Provided vehicle"  # the Run tab's vehicle choice that means 'no design'
MAX_VEHICLES = ports.MAX_VEHICLES
STOPPED = 130  # the launcher's exit code after Ctrl+C or the Stop button

# IARC mode (the Run IARC button): the selected file run as IARC-10's flight code (its run.py). That code flies ONE
# drone per copy, so the launcher starts one copy per vehicle, copy N with -i N (--per-vehicle, see
# containers/script/mrrsim_fleet.py). The values come from IARC-10:
# - each drone has its own IP and TCP port in the project's mission_config.json (drone_info): the drones talk to each
#   other on them, and each copy reaches its own SITL at its IP (IARC reads that host from SITL_MAVLINK_HOST);
# - its field is in Huntsville (mission_config.json, mission_field_corners): IARC_HOME is the middle of the start edge,
#   between corners 1 and 2;
# - its drones stand on a north-south line (state_machine/states/impl/poif_impl.py, ENTRY_BEARING_DEG): drone 1 at the
#   home point, the others IARC_SPACING_M apart to the north (+x), facing north.
IARC_VEHICLES = 4
IARC_CONFIG = "mission_config.json"  # in the project folder: the file run.py reads when it is not given --config
IARC_DESIGN = "IARC Drone"  # the vehicle (sim_config/designs; its camera stream is off until turned on in Vehicles)
IARC_HOME = "34.6251269,-86.8091462"
IARC_SPACING_M = 5.0
IARC_SPAWN_Z = -15.0  # the provided scene's spawn height (sim_config/scene_ardupilot_quadrotor.jsonc, NED: up is -z)
IARC_ARGS = "--airsim --auto-start mission " + " ".join(
    f"--vehicle-pose {i}={IARC_SPACING_M * i:g},0,{IARC_SPAWN_Z:g},0,0,0" for i in range(IARC_VEHICLES))

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
    project: Path | None = None, script_args: str = "", design: str | None = None, per_vehicle: bool = False,
    home: str | None = None, copy_env: dict[str, list[str]] | None = None,
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
    if per_vehicle:
        command.append("--per-vehicle")
    if home:
        command += ["--home", home]
    for name, values in (copy_env or {}).items():
        command += ["--copy-env", f"{name}={','.join(values)}"]
    return command


Drone = tuple[int, str, int]  # a drone of mission_config.json: its id, IP and TCP port


def iarc_drones(project: Path | None) -> list[Drone]:
    """Drones 1 .. IARC_VEHICLES of the project's mission_config.json (drone_info). ValueError says what is wrong."""
    if project is None:
        raise ValueError("Run IARC runs a file of a project folder: add the IARC-10 folder (Add project folder...) and "
                         "pick its run.py.")
    path = project / IARC_CONFIG
    try:
        listed = {int(d["id"]): (str(d["IP"]), int(d["port"]))
                  for d in json.loads(path.read_text(encoding="utf-8-sig"))["drone_info"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Could not read the drones (drone_info) of {path}:\n{exc}") from None
    missing = [n for n in range(1, IARC_VEHICLES + 1) if n not in listed]
    if missing:
        raise ValueError(f"{path} has no drone {', '.join(map(str, missing))} in drone_info; IARC mode flies drones "
                         f"1 to {IARC_VEHICLES}.")
    return [(n, *listed[n]) for n in range(1, IARC_VEHICLES + 1)]


def iarc_command(config_file: Path, script: Path, drones: list[Drone], cameras_off: bool, resolution: str | None,
                 project: Path | None = None) -> list[str]:
    """The command of the Run IARC button: `script` as IARC-10's flight code, one copy per drone, flying the IARC Drone
    with IARC_ARGS from IARC_HOME; copy N reaches its SITL at drone N's IP."""
    return build_run_command(config_file, script, IARC_VEHICLES, cameras_off, resolution, project=project,
                             script_args=IARC_ARGS, design=IARC_DESIGN, per_vehicle=True, home=IARC_HOME,
                             copy_env={"SITL_MAVLINK_HOST": [ip for _number, ip, _port in drones]})


def iarc_ports_problem(drones: list[Drone]) -> str | None:
    """Which drone's TCP port another program holds (and which program), or None when they are all free. A port is
    checked on the drone's IP and on 0.0.0.0, where IARC's server listens. A drone whose port is taken would wait
    forever, saying nothing."""
    busy = [(n, ip, port) for n, ip, port in drones
            if not (ports.port_is_free(port, "tcp", host=ip) and ports.port_is_free(port, "tcp", host="0.0.0.0"))]
    if not busy:
        return None
    who = ports.who_uses([(port, "tcp") for _n, _ip, port in busy])
    taken = "; ".join(f"drone {n} ({ip}:{port})" + (f" by {who[(port, 'tcp')]}" if (port, "tcp") in who else "")
                      for n, ip, port in busy)
    return (f"IARC mode needs each drone's TCP port from {IARC_CONFIG}: the drones talk to each other on them. "
            f"In use: {taken}.\nClose that program (or end the earlier run) and try again.")


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
