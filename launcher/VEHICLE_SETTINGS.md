# Vehicle settings: where they come from and where the sources disagree

The Vehicles tab shows every setting of a Project AirSim robot config. This page records how the list was
made, and what was found when the documentation, the JSON schema and the simulator's code were compared.
The data lives in `launcher/mrrsim/designs/vehicle_schema.py`; `launcher/tests/test_vehicle_schema.py` keeps it
complete.

Everything here was checked against iamaisim/ProjectAirSim at commit `62229cd` (the same version the script
image is built from).

## Sources, in order of trust

1. **The C++ code** (`core_sim/src`, `vehicle_apis`). This is what the simulator reads. Every config key is named
   once in `core_sim/src/constant.hpp`, and the loaders read them through those names.
   `tools/projectairsim_keys.py` extracts the list; the committed copy is
   `launcher/tests/data/projectairsim_code_keys.json` (326 settings in 29 components, plus the 94 Simple Flight
   tuning parameters).
2. **The documentation**: `docs/sensors/*.md` (the pointer you gave), `docs/config_robot.md`, the controller,
   physics and vehicle pages.
3. **The JSON schema** in the Python client (`robot_config_schema.jsonc`) and the example robot configs (53 of
   them: all checked, see below).

The catalog uses the code's spelling and ranges, the docs' wording for help text, and the schema's `required`
lists. Where a setting in the docs or schema is not read by the code, it is kept and marked, so the editor can
warn instead of letting it silently do nothing.

## Settings the docs list under a different name than the code reads

The simulator ignores a key it does not know, without a message. A config that follows the docs for these has the
setting silently left at its default. The Vehicles tab lists each as a warning, and **Fix spellings** renames them.

| Part | Docs / JSON schema / examples say | The code reads |
|---|---|---|
| Barometer, airspeed | `pressure-factor-sigma`, `uncorrelated-noise-sigma` | `pressure_factor_sigma`, `uncorrelated_noise_sigma` |
| GPS | `eph-time-constant` | `eph-time-const` |
| GPS | `eph-min_2d`, `eph-min_3d` (examples) | `eph-min-2d`, `eph-min-3d` |
| Magnetometer | `noise_sigma`, `noise_bias` (examples) | `noise-sigma`, `noise-bias` |
| ArduPilot controller | `use-distance-sensor` (docs, schema, **and this repository's provided vehicle**) | `use-sensor-distance` |
| LiDAR | `vertical-fov-start-deg`, `vertical-fov-end-deg` (docs) | `vertical-fov-upper-deg`, `vertical-fov-lower-deg` |
| LiDAR type | `generic-cylindrical` ... (docs, hyphens) | `generic_cylindrical` ... (underscores) |
| Radar mask | `ange-max`, `evelocity-min` (JSON schema typos) | `range-max`, `velocity-min` |
| Camera | `auto-exposoure-high-percent` (docs typo) | `auto-exposure-high-percent` |
| Camera auto exposure | method values 0, 1, 3 (docs) | 0 histogram, 1 basic, 2 manual (code) |
| Tilt input map | `clamp-input`, `clamp-output` (docs) | `clamp_input`, `clamp_output` (the hyphenated docs spelling is ignored) |
| Controller `type` | the schema lists `ackermann` under `airframe-setup` | rovers read it as `vehicle-setup` of the Simple Drive controller |

The provided vehicle (`sim_config/robot_ardu_quadrotor.jsonc`) has `use-distance-sensor`, so that setting has
never had an effect there. It is `false`, which is also the default, so nothing flies differently; the design
copy has it renamed by **Fix spellings**.

## Documented, but the code never reads it

| Setting | Where documented | Note |
|---|---|---|
| LiDAR `radial-scaling` | `docs/sensors/lidar.md` | the simulator uses its built-in value per lidar type |
| Lift-drag control surface `target` | `docs/config_robot.md` example | only the tilt actuator reads `target` |

## Read by the code, missing from the JSON schema (96 settings)

The schema is the shortest of the three sources. It lacks (per part): camera exposure and bounding-box settings
(`annotation-settings`, `auto-exposure-*`, `motion-blur-amount`, `max-depth-meters`, depth of field, chromatic
aberration, the post-processing model, the gimbal id); LiDAR scan extras (`scan-rpy`,
`report-point-cloud`, `report-azimuth-elevation-range`, the laser spacing and spread settings); the whole lift-drag
aerodynamics model (15 settings), `cross-section-areas-xyz`, matrix inertia (`ixx`, `iyy`, `izz`), the Unreal
solver settings; joint `limit`, `damping-constant`, `spring-constant`, `max-force`, `parent-dominates` and the
`continuous` and `revolute` types; link `material` (`color`, `texture`) and `file_mesh` (`file_name`); IMU `gravity`;
the PX4 serial/UDP/MAVLink-id settings; the MATLAB `control-connection`; and the vehicle-level `jsbsim-model` and
`jsbsim-script`.

## Read by the code, mentioned in no document (119 settings)

Mostly the same families as above: the camera extras, the aerodynamics model, the joint settings, the PX4
connection and id settings. The catalog gives each a label and, where the C++ header states one, its default.

## Where the example configs are wrong

All 53 robot configs in Project AirSim's client examples open in the Vehicles tab with no errors. The warnings
found, grouped:

- `pressure-factor-sigma` / `uncorrelated-noise-sigma` in 18 of them
- `eph-time-constant` / `eph-min_2d` / `eph-min_3d` in 8
- `use-distance-sensor` in 2
- `noise_sigma` / `noise_bias` in 1
- two examples (`robot_x8_jsbsim.jsonc`, `robot_drone_landing.jsonc`) have trailing commas, which
  Project AirSim's reader accepts and Python's does not; the launcher's JSONC reader now accepts them too.

## The launcher's own camera setting

A camera block also has **Stream pictures to the script (UDP)**, stored in the design as `mrrsim-camera-stream`. It is
not a Project AirSim setting: the launcher removes it from the robot configs it writes for the simulator and relays the
camera's pictures to the script instead (README, "Receiving a camera's pictures in your script"). It is marked
`source="mrrsim"` in `vehicle_schema.py` and is not part of the comparison with the simulator's code.

## What the editor does not cover

- **Scene settings** (clock, weather, home point, other actors) are not vehicle settings; they stay in the scene
  file the launcher generates from `sim_config/scene_ardupilot_quadrotor.jsonc`.
- **Controller types other than ArduPilot** can be edited and saved, but the launcher's SITL containers are
  ArduPilot, so such a design is marked "cannot be flown here" and cannot be chosen in the Run tab.
- **Comments** in a `.jsonc` file are not kept when the design is saved (the file is written again from its data,
  keeping the key order). Settings the catalog does not know are kept as they are.
- **`script`** (environment-actor trajectories) is not a robot setting and is kept untouched if present.
- The `jsbsim-api` controller type exists in the code but has no settings of its own that the code reads.

## Keeping it complete

`test_vehicle_schema.py` fails if the catalog lacks a setting that `projectairsim_code_keys.json` lists. To update
for a newer Project AirSim: put its `core_sim` and `vehicle_apis` folders somewhere, run

    python tools/projectairsim_keys.py PATH_TO_SOURCE launcher/tests/data/projectairsim_code_keys.json

and add what the test then reports to `launcher/mrrsim/designs/vehicle_schema.py`.
