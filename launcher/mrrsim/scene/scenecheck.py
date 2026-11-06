"""Read Project AirSim's scene and robot-config files, and check that a scene matches the number of vehicles the
launcher will start.

The files are JSONC: JSON with // and /* */ comments and commas before a closing ] or }. `load_jsonc` reads one.

Each SITL vehicle runs in lock-step with the sim: it waits for sensor data on its own UDP port pair
and never opens its serial ports until it gets some. A vehicle with no robot to talk to therefore
just hangs, and the only symptom is a heartbeat timeout two minutes later. `check_scene` catches it up front.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core import ports

# === READING JSONC ============================================================


class SceneParseError(ValueError):
    """A scene or robot config cannot be read: it is not text, not valid JSONC, or not a JSON object."""


def _skip_string(text: str, start: int) -> int:
    """The index just after the string whose opening quote is at `start`."""
    i, n = start + 1, len(text)
    while i < n and text[i] != '"':
        i += 2 if text[i] == "\\" else 1
    return i + 1


def _strip_comments(text: str) -> str:
    """`text` without // and /* */ comments; the inside of strings is left alone."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == '"':
            end = _skip_string(text, i)
            out.append(text[i:end])
            i = end
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _drop_trailing_commas(text: str) -> str:
    """Project AirSim's JSON reader accepts a comma before a closing ] or }; Python's does not."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == '"':
            end = _skip_string(text, i)
            out.append(text[i:end])
            i = end
        elif text[i] == ",":
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if not (j < n and text[j] in "]}"):
                out.append(",")
            i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas, leaving string contents alone."""
    return _drop_trailing_commas(_strip_comments(text))


def load_jsonc(path: Path) -> dict:
    """The JSON object in a .jsonc file. Raises SceneParseError when the file cannot be read, is not UTF-8 text or
    valid JSONC, or holds something other than an object."""
    try:
        doc = json.loads(strip_jsonc(path.read_text(encoding="utf-8-sig")))  # a byte order mark is not an error
    except UnicodeDecodeError:
        raise SceneParseError(f"{path.name}: it is not UTF-8 text (save it as UTF-8)") from None
    except (OSError, ValueError) as exc:
        raise SceneParseError(f"{path.name}: {exc}") from exc
    if not isinstance(doc, dict):
        raise SceneParseError(f"{path.name}: the top level must be a JSON object, {{ ... }}")
    return doc


# === ROBOTS AND SCENES ========================================================


def robot_actors(scene: dict) -> list[dict]:
    """The robots of a scene: what is listed under `actors` and has the type "robot"."""
    actors = scene.get("actors")
    if not isinstance(actors, list):
        return []
    return [a for a in actors if isinstance(a, dict) and a.get("type") == "robot"]


def _ardupilot_controller(robot_config: dict) -> dict | None:
    """The controller of the robot config when it is an ArduPilot one, else None."""
    controller = robot_config.get("controller")
    if isinstance(controller, dict) and controller.get("type") == "ardupilot-api":
        return controller
    return None


def ardupilot_ports(robot_config: dict) -> tuple[int, int] | None:
    """(ports SITL receives sensors on, ports the sim receives motor output on); None when the robot has no ArduPilot
    controller or the controller does not give both as whole numbers."""
    controller = _ardupilot_controller(robot_config)
    settings = controller.get("ardupilot-settings") if controller else None
    if not isinstance(settings, dict):
        return None
    pair = settings.get("ardupilot-udp-port"), settings.get("local-host-udp-port")
    if all(type(port) is int for port in pair):  # (not a bool, which is also an int)
        return pair
    return None


# === CHECKING =================================================================


HOME_TOLERANCE_DEG = 1e-6  # latitude and longitude of the home point may differ this much (about 0.1 m)
HOME_TOLERANCE_M = 0.01    # and its altitude this much


def check_scene(config_dir: Path, scene_file: str, vehicles: int) -> list[str]:
    """Problems that would make `vehicles` SITL instances hang. Empty list = consistent.

    Raises SceneParseError when the scene or one of its robot configs cannot be read."""
    scene = load_jsonc(config_dir / scene_file)
    problems: list[str] = []
    owners: dict[tuple[int, int], list[str]] = {}  # a pair of ports -> the robots that use it
    for actor in robot_actors(scene):
        name = actor.get("name", "?")
        config_name = actor.get("robot-config")
        if not config_name:
            problems.append(f"robot {name} has no robot-config")
            continue
        path = config_dir / config_name
        if not path.is_file():
            problems.append(f"robot {name}: robot-config {config_name} not found in {config_dir.name}/")
            continue
        robot = load_jsonc(path)
        pair = ardupilot_ports(robot)
        if pair is not None:
            owners.setdefault(pair, []).append(name)
        elif _ardupilot_controller(robot) is not None:
            problems.append(
                f"robot {name}: the ArduPilot controller in {config_name} needs both an ardupilot-udp-port "
                f"and a local-host-udp-port, as whole numbers"
            )

    found = sum(len(names) for names in owners.values())
    if found < vehicles:
        problems.append(
            f"scene has {found} ArduPilot robot(s) but [sitl] vehicles = {vehicles}; "
            f"each SITL waits forever for its own robot"
        )
    elif found > vehicles:
        problems.append(
            f"scene has {found} ArduPilot robots but only {vehicles} vehicle(s) will be started; "
            f"the extra robots wait for an autopilot that never arrives"
        )
    for (sim_in, sim_out), names in owners.items():
        if len(names) > 1:
            problems.append(
                f"robots {', '.join(map(str, names))} use the same ports (ardupilot-udp-port {sim_in}, "
                f"local-host-udp-port {sim_out}); every vehicle needs its own pair"
            )
    for index in range(vehicles):
        vp = ports.vehicle_ports(index)
        if (vp.sim_in, vp.sim_out) not in owners:
            problems.append(
                f"vehicle {index} (sysid {vp.sysid}): no robot uses ardupilot-udp-port {vp.sim_in} and "
                f"local-host-udp-port {vp.sim_out}"
            )
    return problems


def home_problem(config_dir: Path, scene_file: str, home: str) -> str | None:
    """What is wrong when the scene's home-geo-point is not `home` (the "lat,lon,alt,heading" of [sitl] home), else None.
    ArduPilot then puts the vehicle where the simulator does not, by up to tens of meters. A scene with no home point
    (or one `home` cannot be compared with) is not a problem.

    Raises SceneParseError when the scene cannot be read."""
    point = load_jsonc(config_dir / scene_file).get("home-geo-point")
    try:
        lat, lon, alt, heading = (float(v) for v in home.split(","))
        scene_lat, scene_lon, scene_alt = (float(point[key]) for key in ("latitude", "longitude", "altitude"))
    except (KeyError, TypeError, ValueError):
        return None
    if (abs(lat - scene_lat) <= HOME_TOLERANCE_DEG and abs(lon - scene_lon) <= HOME_TOLERANCE_DEG
            and abs(alt - scene_alt) <= HOME_TOLERANCE_M):
        return None
    scene_home = f"{scene_lat:.8f},{scene_lon:.8f},{scene_alt:g}"
    return (
        f"the scene's home-geo-point is {scene_home} but [sitl] home is {home}, so ArduPilot's position does not match "
        f"the simulator's; set [sitl] home = \"{scene_home},{heading:g}\" in mrrsim.toml"
    )
