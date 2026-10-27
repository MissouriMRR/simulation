"""Load a Project AirSim scene, then (optionally) hold the client connection open.

Runs inside the `airsim` image target. The launcher starts it after Unreal is up and
waits for the line "Scene loaded" in its log before starting SITL.

--hold keeps the connection open until SIGTERM/SIGINT. The launcher defaults to holding
because it is not known whether the scene survives the client disconnecting; if it does,
set `hold = false` in mrrsim.toml.

Camera streams (MRRSIM_CAMERA_STREAMS, a JSON list written by the launcher) are started here too, because the
simulator's topics can only be received by one program at a time and this is the one that holds the connection.
"""
import argparse
import json
import os
import signal
import sys
import time

from projectairsim import ProjectAirSimClient, World


def start_camera_streams(client, world) -> None:
    text = os.environ.get("MRRSIM_CAMERA_STREAMS", "")
    if not text:
        return
    try:
        import camera_relay

        camera_relay.start(client, world.parent_topic, json.loads(text), log=lambda line: print(line, flush=True))
    except Exception as exc:  # the scene is loaded and flying still works; the launcher shows this line
        print(f"camera stream FAILED: {type(exc).__name__}: {exc}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1", help="machine running Unreal")
    ap.add_argument("--config-dir", default="/sim_config", help="folder with the scene + robot .jsonc files")
    ap.add_argument("--scene", required=True, help="scene .jsonc file name inside --config-dir")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds to wait after the scene loads")
    ap.add_argument("--hold", action="store_true", help="stay connected until signalled")
    args = ap.parse_args()

    stop = False

    def _on_signal(signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    print(f"scene_loader: connecting to {args.host}", flush=True)
    client = ProjectAirSimClient(address=args.host)
    client.connect()
    try:
        # sim_config_path wants a trailing slash.
        world = World(
            client,
            args.scene,
            delay_after_load_sec=args.delay,
            sim_config_path=os.path.join(args.config_dir, ""),
        )
        start_camera_streams(client, world)
        print("Scene loaded", flush=True)  # the launcher waits for exactly this line
        while args.hold and not stop:
            time.sleep(0.5)
    finally:
        try:
            client.disconnect()
        except Exception as exc:  # shutting down; nothing useful to do about it
            print(f"scene_loader: disconnect failed: {exc}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
