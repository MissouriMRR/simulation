"""The free camera's flying: where the keys and mouse take it (`step`), and the link to the scene loader that moves it.

The scene loader (containers/script/freecam_server.py) holds the simulator connection and moves the FreeCam robot; this
side sends it poses over UDP. Poses are (x, y, z, roll, pitch, yaw) in meters and degrees, world NED.
"""
from __future__ import annotations

import json
import math
import socket
from dataclasses import dataclass, replace

from ...core import ports

SPEED_M_S = 5.0  # to start with; the scroll wheel changes it while flying
MIN_SPEED_M_S, MAX_SPEED_M_S, WHEEL_STEP = 0.5, 100.0, 1.25  # one wheel notch: 25% faster or slower
FAST_FACTOR = 4.0  # with Shift
LOOK_DEG_PER_PX = 0.15
PITCH_LIMIT_DEG = 89.0


@dataclass(frozen=True)
class CameraPose:
    x: float
    y: float
    z: float
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0

    def as_list(self) -> list[float]:
        return [self.x, self.y, self.z, self.roll, self.pitch, self.yaw]


def wheel_speed(speed: float, notches: int) -> float:
    """The flying speed after turning the scroll wheel `notches` (up: positive), within MIN..MAX_SPEED_M_S."""
    return max(MIN_SPEED_M_S, min(MAX_SPEED_M_S, speed * WHEEL_STEP ** notches))


def step(pose: CameraPose, dt: float, forward: int, right: int, up: int, fast: bool, look_dx: float,
         look_dy: float, speed: float = SPEED_M_S) -> CameraPose:
    """Where the camera is after `dt` seconds: it turns with the mouse, then flies where it looks at `speed` m/s
    (forward/right/up are -1, 0 or 1)."""
    yaw = (pose.yaw + look_dx * LOOK_DEG_PER_PX + 180) % 360 - 180
    pitch = max(-PITCH_LIMIT_DEG, min(PITCH_LIMIT_DEG, pose.pitch - look_dy * LOOK_DEG_PER_PX))
    dist = speed * (FAST_FACTOR if fast else 1) * dt
    p, y = math.radians(pitch), math.radians(yaw)
    ahead = (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p))  # NED: up is -z
    side = (-math.sin(y), math.cos(y), 0.0)
    move = [dist * (forward * a + right * s) for a, s in zip(ahead, side)]
    move[2] -= dist * up
    return replace(pose, x=pose.x + move[0], y=pose.y + move[1], z=pose.z + move[2], pitch=pitch, yaw=yaw)


class Link:
    """UDP to the scene loader's free camera controller."""

    def __init__(self, host: str = "127.0.0.1", port: int = ports.FREECAM_PORT) -> None:
        self.address = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)

    def send(self, message: dict) -> None:
        try:
            self.sock.sendto(json.dumps(message).encode(), self.address)
        except OSError:
            pass  # nothing listens (yet): the next tick tries again

    def reply(self) -> dict | None:
        """The next answer that has arrived, or None."""
        try:
            return json.loads(self.sock.recv(4096))
        except (OSError, ValueError):  # nothing waiting, or nobody listening
            return None

    def close(self) -> None:
        self.sock.close()
