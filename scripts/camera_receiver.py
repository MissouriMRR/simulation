"""Receive the pictures of a simulator camera: counts them and saves one a second into SIM_OUT_DIR.

Switch the camera on first. In the Vehicles tab: duplicate the provided vehicle, select its camera ("DownCamera"),
open "Stream pictures to the script (UDP)", set "Stream the pictures" to true (the default port 5600 is fine), Save,
press "Use when running" and run this script. The same from the command line:  mrrsim run camera_receiver.py --design NAME

The pictures arrive as UDP packets; mrrsim_camera.CameraReceiver puts them back together. It needs no flying: the
vehicle just sits on the ground, which is enough to see the camera work. To use the pictures in a flight script,
create the receiver at the top and call get() or latest() whenever the script wants the newest picture.

    SIM_CAMERA_PORT      the port of the first camera that sends (CameraReceiver() uses it by default)
    SIM_CAMERA_STREAMS   JSON list of {"vehicle", "camera", "port"} for every camera that sends
    SIM_OUT_DIR          /out  (writable; kept with the session logs)
"""
import os
import time

from mrrsim_camera import CameraReceiver

RUN_S = 30
out_dir = os.environ.get("SIM_OUT_DIR", ".")
print(f"listening for camera pictures (SIM_CAMERA_PORT={os.environ.get('SIM_CAMERA_PORT', 'not set: using 5600')})", flush=True)

started = last_saved = time.monotonic()
count = 0
with CameraReceiver() as camera:
    while time.monotonic() - started < RUN_S:
        frame = camera.get(timeout=5)
        if frame is None:
            print("no picture for 5 s: is the camera's streaming switched on in the Vehicles tab?", flush=True)
            continue
        count += 1
        if count == 1:
            print(f"first picture: vehicle {frame.vehicle}, camera {frame.camera}, {frame.width}x{frame.height} {frame.format}", flush=True)
        if time.monotonic() - last_saved >= 1.0:
            last_saved = time.monotonic()
            name = f"camera_v{frame.vehicle}_{frame.camera}_{frame.number:06d}.{'jpg' if frame.format == 'jpeg' else 'png'}"
            frame.save(os.path.join(out_dir, name))
            print(f"{count} pictures so far, {camera.dropped} dropped; saved {name}", flush=True)
print(f"done: {count} pictures in {RUN_S} s (they are in {out_dir})")
