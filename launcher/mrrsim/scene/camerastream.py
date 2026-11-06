"""Camera -> UDP streams: the "Stream pictures to the script" setting of a camera in the Vehicles tab.

The setting lives in the camera block of the design under STREAM_KEY. It is the launcher's own, not Project AirSim's, so
it is taken out of the robot configs that are written for the simulator; what is left is a list of streams, one per
camera per vehicle, that the scene loader (which holds the simulator's one topic connection) relays as UDP packets.
The script receives them with `mrrsim_camera.CameraReceiver` (containers/script/mrrsim_camera.py).
"""
from __future__ import annotations

import copy
from typing import Any

from ..core import ports

STREAM_KEY = "mrrsim-camera-stream"
DEFAULTS: dict[str, Any] = {
    "enabled": False, "host": "127.0.0.1", "port": 5600, "image-type": 0, "fps": 10.0,
    "format": "jpeg", "quality": 80, "max-width": 0, "max-depth": 50.0,
}
_SPAN = ports.STEP * ports.MAX_VEHICLES  # the ports of every vehicle the launcher can start lie within this far of vehicle 0's
_ARDUPILOT_BASE = ports.BASE_DRONEKIT - 2  # SERIAL0: where the ports of ArduPilot's first instance start
RESERVED_PORTS = (  # (lowest, highest, owner): taken by the simulator and its tools; a camera cannot send to them
    (8989, 8990, "Project AirSim"), (14550, 14550, "the ground station output"),
    (_ARDUPILOT_BASE, _ARDUPILOT_BASE + _SPAN - 1, "ArduPilot"),
    (ports.BASE_SIM_OUT, ports.BASE_SIM_OUT + _SPAN - 1, "the vehicle's simulator link"),
)


def setting(block: dict, key: str) -> Any:
    """The camera block's value for one of the streaming settings: its own, else the default."""
    group = block.get(STREAM_KEY)
    if isinstance(group, dict) and key in group:
        return group[key]
    return DEFAULTS[key]


def set_to_stream(block: Any) -> bool:
    """Is `block` a camera whose "Stream pictures to the script" setting is on? (Whether or not the camera itself is.)"""
    return isinstance(block, dict) and block.get("type") == "camera" and setting(block, "enabled") is True


def is_streaming(block: Any) -> bool:
    """Does `block` send pictures: it is set to stream, and the camera is not switched off."""
    return set_to_stream(block) and block.get("enabled", True) is not False


def capture_ok(block: dict) -> bool:
    """Does the camera produce the chosen image type to its clients?"""
    image_type = setting(block, "image-type")
    return any(isinstance(c, dict) and c.get("image-type") == image_type and c.get("capture-enabled", False)
               for c in block.get("capture-settings") or [])


def reserved_port(port: Any) -> str:
    """What the simulator side uses `port` for ("ArduPilot", ...), or "" when a camera may send to it (also for anything
    that is not a whole number)."""
    if isinstance(port, int) and not isinstance(port, bool):
        for low, high, owner in RESERVED_PORTS:
            if low <= port <= high:
                return owner
    return ""


def streams(robot: dict, vehicles: int = 1) -> list[dict]:
    """The streams a vehicle design asks for, for `vehicles` copies of it: camera by camera, each for vehicle 0, 1, ..."""
    found = []
    for block in robot.get("sensors") or []:
        if not is_streaming(block):
            continue
        for vehicle in range(vehicles):
            found.append({
                "vehicle": vehicle, "camera": block.get("id", "Camera"), "image_type": int(setting(block, "image-type")),
                "host": setting(block, "host"), "port": int(setting(block, "port")), "fps": float(setting(block, "fps")),
                "format": setting(block, "format"), "quality": int(setting(block, "quality")),
                "max_width": int(setting(block, "max-width")), "max_depth": float(setting(block, "max-depth")),
            })
    return found


def strip(robot: dict) -> dict:
    """A copy of the robot config without the launcher's own camera setting, which the simulator does not know."""
    clean = copy.deepcopy(robot)
    for block in clean.get("sensors") or []:
        if isinstance(block, dict):
            block.pop(STREAM_KEY, None)
    return clean
