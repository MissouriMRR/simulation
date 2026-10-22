"""
ProjectAirSim entry point for the SUAS flight code.

Must be run with the env container's system Python, which is the only interpreter there
that has projectairsim installed:
    python simulation/interfaces/suas.py
The flight code is launched as a subprocess via `uv run`, so it still runs in the project's
own uv environment (which has dronekit but not projectairsim).
"""

import collections
import collections.abc

# commentjson (a projectairsim dependency) still uses the pre-3.10 collections aliases
collections.MutableMapping = collections.abc.MutableMapping

import argparse
import os
import subprocess
import sys
from pathlib import Path

try:
    from projectairsim import ProjectAirSimClient, World
    from projectairsim.utils import projectairsim_log
except ModuleNotFoundError as err:
    raise SystemExit(
        f"projectairsim is not installed for this interpreter ({sys.executable}).\n"
        "It only exists in the env container's system Python (/usr/local/bin/python).\n"
        "Run this from the repo root inside the env container:\n"
        "    python simulation/interfaces/suas.py\n"
        "Do not use `uv run` here: that selects the project's .venv, which has the\n"
        "flight dependencies but not projectairsim."
    ) from err

# this file lives at <repo>/simulation/interfaces/suas.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SIM_CONFIG_PATH = str(PROJECT_ROOT / "simulation" / "sim_config")

# multidrone_world.py sits at the top level of the simulation submodule
sys.path.insert(0, str(PROJECT_ROOT / "simulation"))
from multidrone_world import MultidroneWorld

EMPTY_SCENE = "scene_ardu_empty.jsonc"
DRONE_SCENE = "scene_ardu_quadrotor_template.jsonc"

# How many drones to spawn. Must match NUM_DRONES on the sim container: every drone needs a
# SITL behind it, and one that spawns without one softlocks. The SUAS flight code only
# connects to a single vehicle (udp:127.0.0.1:14550, the first SITL's MAVProxy stream), so
# anything past the first drone sits in the scene uncontrolled.
NUM_DRONES = int(os.environ.get("NUM_DRONES", "1"))


def run_suas_code():
    command = ["uv", "run", "run.py", "--airsim"]
    try:
        subprocess.run(command, cwd=PROJECT_ROOT, check=True)
        print("Flight script executed successfully!")
    except subprocess.CalledProcessError as err:
        print(f"The flight script failed with exit code {err.returncode}")
    except FileNotFoundError:
        print("Error: 'uv' is not installed")


def load_empty_scene(client):
    """
    The SITL pulls scene data on startup, so a scene has to exist before it starts, but a
    drone that spawns before its SITL is running softlocks. Loading an empty scene first
    breaks that circular wait.
    """
    World(client, EMPTY_SCENE, delay_after_load_sec=2, sim_config_path=SIM_CONFIG_PATH)


def spawn_drones(client):
    """
    Clones the scene's single drone into a 1 x NUM_DRONES row, offsetting each copy's
    ArduPilot UDP ports by 10 to match sim_vehicle.py --instance.
    """
    MultidroneWorld(
        client,
        DRONE_SCENE,
        delay_after_load_sec=2,
        sim_config_path=SIM_CONFIG_PATH,
        drone_grid=(1, NUM_DRONES),
    )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    # The run splits in two because a fresh SITL has to come up between the empty scene and
    # the drones: each spawn restarts the sim clock at 0, and a SITL already fed by an earlier
    # drone waits for sim time to climb back to where that drone left off before its flight
    # code runs again. 'all' asks for the restart at a prompt; launch.ps1 drives the stages
    # itself and does the restart in between.
    parser.add_argument(
        "--stage",
        choices=("all", "empty", "spawn"),
        default="all",
        help="all: empty scene, prompt for a sim restart, then spawn and fly (default). "
        "empty: load the empty scene and exit. "
        "spawn: spawn the drone(s) into the already-loaded scene and fly.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    client = ProjectAirSimClient()

    try:
        print("Connecting to projectAirSim...")
        client.connect()

        if args.stage in ("all", "empty"):
            load_empty_scene(client)

        if args.stage == "empty":
            print(
                f"Empty scene loaded. Start the sim container (NUM_DRONES={NUM_DRONES}), then "
                f"rerun this with --stage spawn."
            )
            return

        if args.stage == "all":
            input(
                f"Empty scene loaded. Restart the sim container now so the SITL is fresh "
                f"(NUM_DRONES={NUM_DRONES}), wait for it to finish booting, then press Enter to "
                f"spawn the drone(s): "
            )

        spawn_drones(client)

        run_suas_code()

    except KeyboardInterrupt:
        print("Interrupted.")
    except Exception as err:
        projectairsim_log().error(f"Exception occurred: {err}", exc_info=True)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
