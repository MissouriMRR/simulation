"""Fly every vehicle at once: arm, take off to 10 m (a different altitude per vehicle, so they never
share airspace), hover 5 s, land. One thread per vehicle.

    ./mrrsim.cmd up --vehicles 2 --script takeoff_land_all.py

The launcher generates the matching N-vehicle scene (see sim_config/generate_scene.py).

The launcher passes SIM_VEHICLE_COUNT and SIM_CONNECT_<i> (tcp:127.0.0.1:5762 + 10i).
"""
# mrrsim: vehicles=2
import os
import sys
import threading
import time

from dronekit import VehicleMode, connect

BASE_ALT_M = 10
ALT_STEP_M = 3
HOVER_S = 5


def wait_for(predicate, what, timeout_s, poll_s=1.0):
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out waiting for {what}")
        time.sleep(poll_s)


def fly(index, conn, vehicles, results):
    tag = f"[vehicle {index}]"
    altitude = BASE_ALT_M + ALT_STEP_M * index
    vehicle = connect(conn, wait_ready=True, timeout=180, heartbeat_timeout=180)
    vehicles[index] = vehicle
    try:
        wait_for(lambda: vehicle.is_armable, "vehicle to become armable", 120)
        vehicle.mode = VehicleMode("GUIDED")
        wait_for(lambda: vehicle.mode.name == "GUIDED", "GUIDED mode", 15)
        vehicle.armed = True
        wait_for(lambda: vehicle.armed, "arming", 30)
        print(f"{tag} taking off to {altitude} m", flush=True)
        vehicle.simple_takeoff(altitude)
        wait_for(lambda: vehicle.location.global_relative_frame.alt >= altitude * 0.95, "target altitude", 90)
        print(f"{tag} hovering {HOVER_S} s", flush=True)
        time.sleep(HOVER_S)
        print(f"{tag} landing", flush=True)
        vehicle.mode = VehicleMode("LAND")
        wait_for(lambda: not vehicle.armed, "disarm after landing", 120)
        print(f"{tag} landed and disarmed", flush=True)
        results[index] = True
    except Exception as exc:  # report per vehicle; the others keep flying
        print(f"{tag} FAILED: {exc!r}", flush=True)
        results[index] = False
    finally:
        try:
            if vehicle.armed:
                vehicle.mode = VehicleMode("LAND")
        finally:
            vehicle.close()


def main() -> int:
    count = int(os.environ.get("SIM_VEHICLE_COUNT", "1"))
    vehicles = {}
    results = {}
    threads = [
        threading.Thread(target=fly, args=(i, os.environ[f"SIM_CONNECT_{i}"], vehicles, results), daemon=True)
        for i in range(count)
    ]
    print(f"flying {count} vehicle(s)", flush=True)
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("interrupted; landing every vehicle", flush=True)
        for vehicle in list(vehicles.values()):
            try:
                vehicle.mode = VehicleMode("LAND")
            except Exception:
                pass
        time.sleep(15)
        return 130
    ok = all(results.get(i) for i in range(count))
    print("all vehicles landed" if ok else f"results: {results}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
