"""Diagnose why a vehicle will not arm: prints the relevant parameters and GPS/EKF state, then asks
the autopilot to re-run its pre-arm checks every few seconds and shows what fails. It never arms
and never moves the vehicle.

    ./mrrsim.cmd up --script diag_arming.py
"""
import os
import sys
import time

from dronekit import connect

PARAMS = [
    "SCHED_LOOP_RATE", "GPS1_TYPE", "GPS1_DELAY_MS", "GPS1_RATE_MS", "SIM_GPS1_ENABLE",
    "SIM_GPS1_HZ", "SIM_GPS1_LAG_MS", "ARMING_SKIPCHK", "AHRS_EKF_TYPE", "SIM_SPEEDUP",
]
MAV_CMD_RUN_PREARM_CHECKS = 401
WATCH_S = 45
EVERY_S = 5


def main() -> int:
    conn = os.environ.get("SIM_CONNECT", "tcp:127.0.0.1:5762")
    print(f"connecting to {conn}", flush=True)
    vehicle = connect(conn, wait_ready=True, timeout=180, heartbeat_timeout=180)
    texts = []
    acks = []

    @vehicle.on_message("STATUSTEXT")
    def on_text(_vehicle, _name, message):
        texts.append(message.text)

    @vehicle.on_message("COMMAND_ACK")
    def on_ack(_vehicle, _name, message):
        if message.command == MAV_CMD_RUN_PREARM_CHECKS:
            acks.append(message.result)

    def run_prearm_checks():
        """Returns (passed, [new autopilot messages])."""
        before = len(texts)
        acks.clear()
        master = vehicle._master
        vehicle.send_mavlink(vehicle.message_factory.command_long_encode(
            master.target_system, master.target_component, MAV_CMD_RUN_PREARM_CHECKS, 0, 0, 0, 0, 0, 0, 0, 0))
        time.sleep(2.0)
        problems = sorted({t for t in texts[before:] if t.startswith("PreArm")})
        # the ACK only says the command ran; the PreArm messages say what failed
        return (bool(acks) and acks[-1] == 0 and not problems), problems

    try:
        print("\n== parameters (n/a = this ArduPilot version has no such parameter)", flush=True)
        for name in PARAMS:
            try:
                print(f"  {name:18} {vehicle.parameters[name]}", flush=True)
            except KeyError:
                print(f"  {name:18} n/a", flush=True)

        print(f"\n== pre-arm checks every {EVERY_S} s for {WATCH_S} s", flush=True)
        started = time.monotonic()
        passed = False
        while time.monotonic() - started < WATCH_S and not passed:
            time.sleep(EVERY_S - 2.0)
            passed, problems = run_prearm_checks()
            gps = vehicle.gps_0
            print(
                f"  t+{time.monotonic() - started:3.0f}s  {'PASS' if passed else 'FAIL'}  gps fix={gps.fix_type} "
                f"sats={gps.satellites_visible}  ekf_ok={vehicle.ekf_ok}",
                flush=True,
            )
            for problem in problems:
                print(f"           {problem}", flush=True)
        print("\nRESULT: pre-arm checks " + ("PASS" if passed else "still failing"), flush=True)
        return 0 if passed else 1
    finally:
        vehicle.close()


if __name__ == "__main__":
    sys.exit(main())
