"""Smoke test: arm, take off to 10 m, hover 5 s, land, wait for disarm.

Runs inside the `mrrsim-script` container (Python 3.12 + DroneKit). The launcher passes the
connection details through environment variables:

    SIM_CONNECT          tcp:127.0.0.1:5762   (vehicle 0, SERIAL1)
    SIM_CONNECT_<i>      one per vehicle
    SIM_VEHICLE_COUNT    number of vehicles
    SIM_OUT_DIR          /out  (writable; the launcher keeps it with the session logs)

On an error or Ctrl+C the vehicle is switched to LAND before the script exits.
"""
import os
import sys
import time

from dronekit import VehicleMode, connect

TARGET_ALT_M = 10
HOVER_S = 5


def wait_for(predicate, what, timeout_s, poll_s=1.0):
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError(f"timed out waiting for {what}")
        time.sleep(poll_s)


def main() -> int:
    conn = os.environ.get("SIM_CONNECT", "tcp:127.0.0.1:5762")
    print(f"connecting to {conn}", flush=True)
    vehicle = connect(conn, wait_ready=True, timeout=180, heartbeat_timeout=180)
    try:
        wait_for(lambda: vehicle.is_armable, "vehicle to become armable", 120)
        vehicle.mode = VehicleMode("GUIDED")
        wait_for(lambda: vehicle.mode.name == "GUIDED", "GUIDED mode", 15)

        vehicle.armed = True
        wait_for(lambda: vehicle.armed, "arming", 30)

        # Take off promptly: ArduCopter disarms if it is armed but not flying for ~15 s.
        print(f"taking off to {TARGET_ALT_M} m", flush=True)
        vehicle.simple_takeoff(TARGET_ALT_M)
        wait_for(
            lambda: vehicle.location.global_relative_frame.alt >= TARGET_ALT_M * 0.95,
            "target altitude",
            90,
        )

        print(f"hovering {HOVER_S} s", flush=True)
        time.sleep(HOVER_S)

        print("landing", flush=True)
        vehicle.mode = VehicleMode("LAND")
        wait_for(lambda: not vehicle.armed, "disarm after landing", 120)
        print("landed and disarmed", flush=True)
        return 0
    except KeyboardInterrupt:
        print("interrupted; switching to LAND", flush=True)
        return 130
    finally:
        if vehicle.armed:
            vehicle.mode = VehicleMode("LAND")
            # Give the autopilot time to finish landing before the connection closes.
            try:
                wait_for(lambda: not vehicle.armed, "disarm", 60)
            except (TimeoutError, KeyboardInterrupt):
                pass
        vehicle.close()


if __name__ == "__main__":
    sys.exit(main())
