"""The free camera's controller: takes the Simulator tab's moves over UDP and moves the FreeCam robot.

Runs inside scene_loader.py, which holds the simulator connection. One JSON message per datagram (meters, degrees,
world NED):
    {"cmd": "get"}                               reply {"pose": [x, y, z, roll, pitch, yaw]}
    {"cmd": "pose", "pose": [x, y, z, roll, pitch, yaw]}   FreeCam moves there (no reply)
    {"cmd": "view", "to": "freecam" | "vehicle"} Unreal's main view switches to that camera; reply {"ok": true}
    {"cmd": "chase"}                             FreeCam follows the first drone (until a pose, get or vehicle view);
                                                 reply {"ok": true}
"""
from __future__ import annotations

import json
import math
import socket
import threading
import time

FREECAM = "FreeCam"  # the robot's name in launcher-generated scenes (launcher/mrrsim/scene/scenegen.py)
CHASE_BEHIND_M, CHASE_ABOVE_M, CHASE_PITCH_DEG = 8.0, 3.0, -15.0  # where the chase camera sits, from the drone
TICK_S = 1 / 30  # how often the chase follows the drone


def quaternion(roll: float, pitch: float, yaw: float) -> dict:
    """The rotation of these angles (degrees) as a {w, x, y, z} quaternion."""
    cr, sr = math.cos(math.radians(roll) / 2), math.sin(math.radians(roll) / 2)
    cp, sp = math.cos(math.radians(pitch) / 2), math.sin(math.radians(pitch) / 2)
    cy, sy = math.cos(math.radians(yaw) / 2), math.sin(math.radians(yaw) / 2)
    return {"w": cr * cp * cy + sr * sp * sy, "x": sr * cp * cy - cr * sp * sy,
            "y": cr * sp * cy + sr * cp * sy, "z": cr * cp * sy - sr * sp * cy}


def euler(q: dict) -> tuple[float, float, float]:
    """(roll, pitch, yaw) in degrees of a {w, x, y, z} quaternion."""
    w, x, y, z = q["w"], q["x"], q["y"], q["z"]
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def streaming_cameras(scene_config: dict) -> list[tuple[str, str]]:
    """(robot, camera) of every streamed camera, in the order Unreal's main view cycles through them."""
    found = []
    for actor in scene_config.get("actors", []):
        robot = actor.get("robot-config") if actor.get("type") == "robot" else None
        for sensor in (robot or {}).get("sensors", []) if isinstance(robot, dict) else []:
            if sensor.get("type") == "camera" and sensor.get("enabled", True) and any(
                    c.get("streaming-enabled") for c in sensor.get("capture-settings", [])):
                found.append((actor["name"], sensor["id"]))
    return found


class Controller:
    """Answers the messages; `robot` is FreeCam's client, `vehicle` the first drone's, `world` the scene's,
    `make_pose` builds a client Pose."""

    def __init__(self, robot, vehicle, world, make_pose) -> None:
        self.robot, self.vehicle, self.world, self.make_pose = robot, vehicle, world, make_pose
        self.cameras = streaming_cameras(world.get_configuration())
        self.view = 0  # the main view starts on the first streamed camera
        self.chasing = False

    def _move(self, x, y, z, roll, pitch, yaw) -> None:
        self.robot.set_pose(self.make_pose(x, y, z, quaternion(roll, pitch, yaw)), True)

    def tick(self) -> None:
        """While chasing: FreeCam behind and above the first drone, looking where it heads."""
        if not self.chasing:
            return
        pose = self.vehicle.get_ground_truth_pose()
        t, yaw = pose["translation"], euler(pose["rotation"])[2]
        back_x, back_y = math.cos(math.radians(yaw)) * CHASE_BEHIND_M, math.sin(math.radians(yaw)) * CHASE_BEHIND_M
        self._move(t["x"] - back_x, t["y"] - back_y, t["z"] - CHASE_ABOVE_M, 0.0, CHASE_PITCH_DEG, yaw)

    def _switch_to(self, target: int) -> None:
        for _ in range((target - self.view) % len(self.cameras)):
            self.world.switch_streaming_view()
        self.view = target

    def handle(self, message: dict) -> dict | None:
        cmd = message.get("cmd")
        if cmd in ("pose", "get") or (cmd == "view" and message.get("to") != "freecam"):
            self.chasing = False  # flown, or another view chosen
        if cmd == "pose":
            self._move(*(float(v) for v in message["pose"]))
            return None
        if cmd == "chase":
            self.chasing = True
            return self.handle({"cmd": "view", "to": "freecam"})
        if cmd == "get":
            pose = self.robot.get_ground_truth_pose()
            t = pose["translation"]
            return {"pose": [t["x"], t["y"], t["z"], *euler(pose["rotation"])]}
        if cmd == "view":
            want = FREECAM if message.get("to") == "freecam" else None
            for index, (robot, _camera) in enumerate(self.cameras):
                if (robot == FREECAM) == (want == FREECAM):
                    self._switch_to(index)
                    return {"ok": True}
            return {"ok": False}
        return {"error": f"unknown command {cmd!r}"}


def _client_pose(x, y, z, rotation):
    from projectairsim.types import Pose, Quaternion, Vector3

    return Pose({"translation": Vector3({"x": x, "y": y, "z": z}), "rotation": Quaternion(rotation),
                 "frame_id": "DEFAULT_ID"})


def serve(controller: Controller, sock: socket.socket, stop: threading.Event, log) -> None:
    """Answer messages on `sock` and keep a chase going, until `stop` is set; a bad message is logged, not fatal.
    One thread does both, so only one request to the simulator is ever under way."""
    sock.settimeout(TICK_S)
    next_tick = time.monotonic()
    while not stop.is_set():
        try:
            data, sender = sock.recvfrom(4096)
        except socket.timeout:
            data = None
        if data is not None:
            try:
                reply = controller.handle(json.loads(data))
            except Exception as exc:  # one bad message must not end the free camera
                reply = {"error": f"{type(exc).__name__}: {exc}"}
                log(f"free camera: {reply['error']}")
            if reply is not None:
                sock.sendto(json.dumps(reply).encode(), sender)
        if time.monotonic() >= next_tick:
            next_tick = time.monotonic() + TICK_S
            try:
                controller.tick()
            except Exception as exc:  # the chase stops; the free camera goes on
                controller.chasing = False
                log(f"free camera: chase stopped ({type(exc).__name__}: {exc})")


def start(client, world, port: int, log) -> threading.Event | None:
    """Serve the free camera on UDP `port` in a thread; returns the event that stops it (None without FreeCam)."""
    if FREECAM not in [a["name"] for a in world.get_configuration().get("actors", []) if a.get("type") == "robot"]:
        log("free camera: the scene has none")
        return None
    from projectairsim.robot import Robot

    vehicles = [a["name"] for a in world.get_configuration()["actors"]
                if a.get("type") == "robot" and a["name"] != FREECAM]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    stop = threading.Event()
    controller = Controller(Robot(client, world, FREECAM), Robot(client, world, vehicles[0]), world, _client_pose)
    threading.Thread(target=serve, args=(controller, sock, stop, log), daemon=True).start()
    log(f"free camera: listening on UDP {port}")
    return stop
