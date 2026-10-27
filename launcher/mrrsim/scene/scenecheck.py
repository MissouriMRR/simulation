"""Check that a Project AirSim scene matches the number of vehicles the launcher will start.

Each SITL vehicle runs in lock-step with the sim: it waits for sensor data on its own UDP port pair
and never opens its serial ports until it gets some. A vehicle with no robot to talk to therefore
just hangs, and the only symptom is a heartbeat timeout two minutes later. This catches it up front.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core import ports


class SceneParseError(ValueError):
    pass


def _drop_trailing_commas(text: str) -> str:
    """Project AirSim's JSON reader accepts a comma before a closing ] or }; Python's does not."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif ch == ",":
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j] in "]}":
                i += 1  # skip the comma
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas, leaving string contents alone."""
    return _drop_trailing_commas(_strip_comments(text))


def _strip_comments(text: str) -> str:
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def load_jsonc(path: Path) -> dict:
    try:
        return json.loads(strip_jsonc(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise SceneParseError(f"{path.name}: {exc}") from exc


def ardupilot_ports(robot_config: dict) -> tuple[int, int] | None:
    """(ports SITL receives sensors on, ports the sim receives motor output on), or None."""
    controller = robot_config.get("controller") or {}
    if controller.get("type") != "ardupilot-api":
        return None
    settings = controller.get("ardupilot-settings") or {}
    return settings.get("ardupilot-udp-port"), settings.get("local-host-udp-port")


def check_scene(config_dir: Path, scene_file: str, vehicles: int) -> list[str]:
    """Problems that would make `vehicles` SITL instances hang. Empty list = consistent."""
    scene = load_jsonc(config_dir / scene_file)
    robots = [a for a in scene.get("actors", []) if a.get("type") == "robot"]
    problems: list[str] = []
    found: dict[tuple[int, int], str] = {}
    for actor in robots:
        name = actor.get("name", "?")
        config_name = actor.get("robot-config")
        if not config_name:
            problems.append(f"robot {name} has no robot-config")
            continue
        path = config_dir / config_name
        if not path.is_file():
            problems.append(f"robot {name}: robot-config {config_name} not found in {config_dir.name}/")
            continue
        pair = ardupilot_ports(load_jsonc(path))
        if pair is not None:
            found[pair] = name

    if len(found) < vehicles:
        problems.append(
            f"scene has {len(found)} ArduPilot robot(s) but [sitl] vehicles = {vehicles}; "
            f"each SITL waits forever for its own robot"
        )
    elif len(found) > vehicles:
        problems.append(
            f"scene has {len(found)} ArduPilot robots but only {vehicles} vehicle(s) will be started; "
            f"the extra robots wait for an autopilot that never arrives"
        )
    for index in range(vehicles):
        vp = ports.vehicle_ports(index)
        if (vp.sim_in, vp.sim_out) not in found:
            problems.append(
                f"vehicle {index} (sysid {vp.sysid}): no robot uses ardupilot-udp-port {vp.sim_in} and "
                f"local-host-udp-port {vp.sim_out}"
            )
    return problems
