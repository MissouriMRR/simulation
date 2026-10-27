"""Camera -> UDP streams: the "Stream pictures to the script" setting of a camera in the Vehicles tab.

The setting lives in the camera block of the design under STREAM_KEY. It is the launcher's own, not Project AirSim's, so
it is taken out of the robot configs that are written for the simulator; what is left is a list of streams, one per
camera per vehicle, that the scene loader (which holds the simulator's one topic connection) relays as UDP packets.
The script receives them with `mrrsim_camera.CameraReceiver` (containers/script/mrrsim_camera.py).
"""
from __future__ import annotations

import copy
from typing import Any

STREAM_KEY = "mrrsim-camera-stream"
DEFAULTS: dict[str, Any] = {
    "enabled": False, "host": "127.0.0.1", "port": 5600, "image-type": 0, "fps": 10.0,
    "format": "jpeg", "quality": 80, "max-width": 0, "max-depth": 50.0,
}
RESERVED_PORTS = (  # taken by the simulator and its tools; a camera cannot send to them
    (8989, 8990, "Project AirSim"), (14550, 14550, "the ground station output"),
    (5760, 5800, "ArduPilot"), (9002, 9100, "the vehicle's simulator link"),
)


def setting(block: dict, key: str) -> Any:
    group = block.get(STREAM_KEY)
    if isinstance(group, dict) and key in group:
        return group[key]
    return DEFAULTS[key]


def is_streaming(block: Any) -> bool:
    return (isinstance(block, dict) and block.get("type") == "camera" and block.get("enabled", True) is not False
            and isinstance(block.get(STREAM_KEY), dict) and setting(block, "enabled") is True)


def capture_ok(block: dict) -> bool:
    """Does the camera produce the chosen image type to its clients?"""
    image_type = setting(block, "image-type")
    return any(isinstance(c, dict) and c.get("image-type") == image_type and c.get("capture-enabled", False)
               for c in block.get("capture-settings") or [])


def reserved_port(port: Any) -> str:
    if isinstance(port, int) and not isinstance(port, bool):
        for low, high, owner in RESERVED_PORTS:
            if low <= port <= high:
                return owner
    return ""


def streams(robot: dict, vehicles: int = 1) -> list[dict]:
    """The streams a vehicle design asks for, for `vehicles` copies of it (vehicle 0, 1, ...)."""
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
