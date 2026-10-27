# sim_config/

Project AirSim scene and robot configs (`.jsonc`). This folder is mounted read-only at
`/sim_config` in the scene-loader container, and `mrrsim.toml` -> `[scene] scene` names the
scene file inside it.

## What to put here

1. The scene file used by `tests/ardupilot_quadrotor.py` in the Project AirSim repo.
2. Every `.jsonc` it references (usually one robot config).

Nothing is bundled yet because the exact files come from your Project AirSim checkout.

## What the robot config must say

- The **ArduPilot** controller (not SimpleFlight).
- SITL address **`127.0.0.1`** (Windows and WSL share loopback in mirrored mode).
- Ports per vehicle `i` (0-based), matching what ArduPilot derives from `-I i`:

| | Vehicle 0 | Vehicle 1 | Vehicle i |
|---|---|---|---|
| UDP: SITL receives sensor data | 9003 | 9013 | 9003 + 10i |
| UDP: sim receives motor control | 9002 | 9012 | 9002 + 10i |

- The scene's home geo-point must match `[sitl] home` in `config/mrrsim.toml`.
- One robot per vehicle, each at a different position.

`mrrsim doctor` (and the preflight of `mrrsim up`) checks that the scene file exists and that it has
exactly one ArduPilot robot per vehicle, with the right ports for each. Without that match a SITL
vehicle waits for sensor data forever and its serial ports never open.

## Files here

| File | Use |
|---|---|
| `scene_ardupilot_quadrotor.jsonc` | one vehicle (the default in `config/mrrsim.toml`); also the template for more |
| `robot_ardu_quadrotor.jsonc` | vehicle 0, ports 9003 / 9002; also the template for more |
| `generate_scene.py` | writes the files below for N vehicles |
| `scene_ardupilot_<N>quadrotors.jsonc` | **generated**: Drone1..DroneN, 3 m apart along y |
| `robot_ardu_quadrotor_<k>.jsonc` | **generated**: robot for Drone`k` (k = 2..N), same as the template but with its own UDP ports |
| `*_nocam*.jsonc` | **generated** with `--no-cameras`: the same scene and robots with every camera sensor disabled |
| `*_<W>x<H>*.jsonc` | **generated** when the simulator window is not the size of the scene's streamed camera (1280x720): the same scene and robots with that camera set to the window's size, so Unreal does not resize its window when the vehicle spawns |

## Changing the vehicle

The robot config here is the *provided* vehicle. To change what the vehicle is made of (sensors, rotors, physics,
controller settings, every setting Project AirSim reads) use the **Vehicles** tab of the window: duplicate the
provided vehicle, edit your copy, and choose it for runs (`mrrsim run script.py --design NAME`). The files here
are never edited by it. See the main README, "The Vehicles tab".

## More than one vehicle

`mrrsim up --vehicles N` generates the N-vehicle scene and uses it, unless you pass `--scene`.
With one vehicle and the default 1280x720 window the original scene is used unchanged. To generate by hand:

```powershell
python sim_config\generate_scene.py 3 [--spacing 5]
```

`--no-cameras` (on `up` or the script) writes the same set with every camera sensor disabled, as
`scene_ardupilot_<N>quadrotors_nocam.jsonc` and `robot_ardu_quadrotor_nocam[_<k>].jsonc`, also for one
vehicle. It is opt-in: without the flag the cameras are on. Scripts that read camera images need them on.

Up to 100 vehicles. They spawn 3 m apart (`--spacing`): the first ten in a line along y, then the next row
starts one spacing along x, so 100 drones make a 10 x 10 block. Every vehicle needs its own ports (10 apart),
so a program already using one of them (Discord uses tcp/6463, which is vehicle 70's) blocks the start;
`mrrsim doctor` names the program and the vehicle.

Clock, home point and the robot itself are copied from the two template files, so edit those and
the generated ones follow on the next run. Generated files carry a `GENERATED` comment and are
overwritten each time; a file without it is never touched. They are git-ignored.
