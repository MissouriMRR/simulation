"""List the robot-config settings that Project AirSim's own C++ code reads.

    python tools/projectairsim_keys.py SOURCE_DIR [OUT.json]

SOURCE_DIR holds the repository's files at their repository paths (core_sim/src/..., vehicle_apis/...);
only those two folders are needed. The result is what the simulator really accepts, which is not always what its
documentation or its JSON schema say, so the vehicle designer's settings catalog (launcher/mrrsim/designs/vehicle_schema.py)
is checked against it (launcher/tests/test_vehicle_schema.py reads the committed copy in tests/data).

How: core_sim names every config key once in src/constant.hpp (`Config::number_of_channels = "number-of-channels"`)
and the loaders read keys through those names (`JsonUtils::GetNumber<float>(json, Constant::Config::range, ...)`).
Each component's loader file is scanned for those getter calls. The controller APIs (vehicle_apis) read their
settings with nlohmann's `.value("key", ...)` instead, so those are scanned for that pattern.
Commented-out code is removed first, so settings the code no longer reads are not listed.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# component -> source files (substrings of the path below core_sim/src)
COMPONENTS = {
    "sensor-common": ["sensors/sensor_impl.hpp"],
    "camera": ["sensors/camera.cpp"],
    "lidar": ["sensors/lidar.cpp"],
    "radar": ["sensors/radar.cpp"],
    "imu": ["sensors/imu.cpp"],
    "gps": ["sensors/gps.cpp"],
    "barometer": ["sensors/barometer.cpp"],
    "magnetometer": ["sensors/magnetometer.cpp"],
    "airspeed": ["sensors/airspeed.cpp"],
    "distance-sensor": ["sensors/distance_sensor.cpp"],
    "battery": ["sensors/battery.cpp"],
    "actuator-common": ["actuators/actuator_impl.hpp"],
    "input-map": ["actuators/control_mapper.cpp"],
    "rotor": ["actuators/rotor.cpp"],
    "tilt": ["actuators/tilt.cpp"],
    "lift-drag-control-surface": ["actuators/lift_drag_control_surface.cpp"],
    "wheel": ["actuators/wheel.cpp"],
    "gimbal": ["actuators/gimbal.cpp"],
    "unreal-vehicle": ["actuators/unreal_vehicle.cpp"],
    "link": ["/link.cpp", "link/collision.cpp", "link/visual.cpp", "link/material.cpp",
             "link/geometry/file_mesh.cpp", "link/geometry/unreal_mesh.cpp"],
    "inertial": ["link/inertial.cpp"],
    "joint": ["/joint.cpp"],
    "robot": ["actor/robot.cpp"],
}
# controllers: file (below vehicle_apis/) -> component
CONTROLLERS = {
    "multirotor_api/src/simple_flight_api.cpp": "controller-simple-flight",
    "rover_api/src/simple_drive/simple_drive_api.cpp": "controller-simple-drive",
    "multirotor_api/src/mavlink_api.cpp": "controller-px4",
    "multirotor_api/src/arducopter_api.cpp": "controller-ardupilot",
    "multirotor_api/src/manual_controller_api.cpp": "controller-manual",
    "multirotor_api/src/matlab_controller_api.cpp": "controller-matlab",
}
CONSTANT = re.compile(r'static constexpr const char\*\s+(\w+)\s*=\s*"([^"]*)"')
GETTER = re.compile(r'JsonUtils::(\w+)(?:<(\w+)>)?\(\s*[^,()]+?\s*,\s*(?:Constant::)?Config::(\w+)', re.S)
VALUE = re.compile(r'\.value\(\s*"([^"]+)"')
CONTAINS = re.compile(r'\.contains\(\s*"([^"]+)"')


def uncomment(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def main(source: Path) -> dict:
    constants: dict[str, str] = {}
    for name, value in CONSTANT.findall((source / "core_sim/src/constant.hpp").read_text(encoding="utf-8", errors="replace")):
        constants.setdefault(name, value)
    result: dict[str, dict[str, str]] = {}
    for path in sorted((source / "core_sim/src").rglob("*")):
        if path.suffix not in (".cpp", ".hpp"):
            continue
        rel = path.relative_to(source / "core_sim/src").as_posix()
        for component, needles in COMPONENTS.items():
            if any(n in "/" + rel for n in needles):
                text = uncomment(path.read_text(encoding="utf-8", errors="replace"))
                for kind, template, ident in GETTER.findall(text):
                    key = constants.get(ident)
                    if key and "::" not in key:  # a few names there are C++ namespaces, not config keys
                        result.setdefault(component, {}).setdefault(key, kind + (f"<{template}>" if template else ""))
    for rel, component in CONTROLLERS.items():
        text = uncomment((source / "vehicle_apis" / rel).read_text(encoding="utf-8", errors="replace"))
        for key in VALUE.findall(text) + CONTAINS.findall(text):
            result.setdefault(component, {}).setdefault(key, "value")
    # Simple Flight tuning parameters (MC_*, FW_*, MPC_*): read from a map by name
    params: set[str] = set()
    for path in (source / "vehicle_apis/multirotor_api").rglob("*"):
        if path.suffix in (".cpp", ".hpp"):
            params.update(re.findall(r'params_map(?:\.find|\.at)\("([A-Z][A-Z0-9_]+)"', uncomment(path.read_text(encoding="utf-8", errors="replace"))))
    return {"components": {c: dict(sorted(v.items())) for c, v in sorted(result.items())},
            "simple-flight-parameters": sorted(params)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    data = main(Path(sys.argv[1]))
    text = json.dumps(data, indent=1, sort_keys=False) + "\n"
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {sys.argv[2]}: {sum(len(v) for v in data['components'].values())} settings in {len(data['components'])} components")
    else:
        print(text)
