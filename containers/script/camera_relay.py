"""Send camera pictures from the simulator to a UDP port, for a script to receive with mrrsim_camera.CameraReceiver.

Runs inside scene_loader.py, because only one program at a time can receive the simulator's topics (its topic
connection is a one-to-one NNG pair) and the scene loader is the program that holds that connection.

A stream is a dict, written by the launcher from the camera's "Stream pictures to the script" settings:
    vehicle    index of the vehicle, counted from 0 (it is the robot "Drone<index + 1>")
    camera     the camera sensor's name
    image_type 0..6, which of the camera's captures to send
    host, port where to send the packets
    fps        at most this many pictures per second
    format     "jpeg" or "png"
    quality    JPEG quality 1..100
    max_width  scale pictures wider than this down (0: leave them)
    max_depth  depth pictures: the distance (m) shown as white
"""
from __future__ import annotations

import os
import socket
import sys
import time

import cv2
import numpy as np
from projectairsim.utils import unpack_image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))   # where mrrsim_camera.py lives
from mrrsim_camera import packets_for  # noqa: E402

# The topic's last part for each image type (as in projectairsim.robot.Robot.set_sensor_topics).
TOPIC_NAMES = {
    0: "scene_camera", 1: "depth_planar_camera", 2: "depth_camera", 3: "segmentation_camera",
    4: "depth_vis_camera", 5: "disparity_normalized_camera", 6: "surface_normals_camera",
}
DEPTH_TYPES = (1, 2)
REPORT_EVERY_S = 30.0
BURST = 32          # packets sent back to back


def topic_for(world_topic: str, stream: dict) -> str:
    return f"{world_topic}/robots/Drone{int(stream['vehicle']) + 1}/sensors/{stream['camera']}/{TOPIC_NAMES[int(stream['image_type'])]}"


def to_8bit(image: np.ndarray, depth: bool, max_depth: float) -> np.ndarray:
    """Whatever the simulator sent, as something a JPEG can hold: 8 bits per channel."""
    if image.dtype == np.uint8:
        return image
    if depth:
        meters = image.astype(np.float32)
        meters[~np.isfinite(meters)] = max_depth          # sky and misses read as "far"
        return np.clip(meters / max(max_depth, 1e-6) * 255.0, 0, 255).astype(np.uint8)
    if image.dtype == np.uint16:
        return (image >> 8).astype(np.uint8)
    return np.clip(image.astype(np.float32) * 255.0, 0, 255).astype(np.uint8)   # float pictures run 0..1


class Stream:
    def __init__(self, spec: dict, topic: str, log) -> None:
        self.spec, self.topic, self.log = spec, topic, log
        self.label = f"Drone{int(spec['vehicle']) + 1}/{spec['camera']}"
        self.interval = 1.0 / max(float(spec.get("fps", 10)), 0.01)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
        except OSError:
            pass
        self.address = (spec.get("host", "127.0.0.1"), int(spec["port"]))
        self.number = 0
        self.next_at = 0.0
        self.sent = 0
        self.failures = 0
        self.window_start = time.monotonic()
        self.window_count = 0

    def on_image(self, _topic: str, message: dict) -> None:
        now = time.monotonic()
        if now < self.next_at:
            return
        target = self.next_at + self.interval      # a steady rate, but never a burst to catch up after a stall
        self.next_at = target if target > now else now + self.interval
        try:
            self._send(message)
        except Exception as exc:    # one bad picture must not stop the stream (or the loader's receive thread)
            self.failures += 1
            if self.failures <= 3 or self.failures % 100 == 0:
                self.log(f"camera stream {self.label}: could not send a picture ({type(exc).__name__}: {exc})")

    def _send(self, message: dict) -> None:
        spec = self.spec
        image_type = int(spec["image_type"])
        image = to_8bit(unpack_image(message), image_type in DEPTH_TYPES, float(spec.get("max_depth", 50)))
        max_width = int(spec.get("max_width", 0) or 0)
        height, width = image.shape[:2]
        if max_width and width > max_width:
            height = max(1, round(height * max_width / width))
            width = max_width
            image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        fmt = spec.get("format", "jpeg")
        if fmt == "png":
            ok, encoded = cv2.imencode(".png", image)
        else:
            ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, int(spec.get("quality", 80))])
        if not ok:
            raise RuntimeError("the picture could not be encoded")
        sim_time = int(message.get("time_stamp", 0))
        packets = packets_for(encoded.tobytes(), vehicle=int(spec["vehicle"]), camera=str(spec["camera"]),
                              number=self.number, sim_time_ns=sim_time, width=width, height=height, format=fmt)
        for index, packet in enumerate(packets):
            self.sock.sendto(packet, self.address)
            if index % BURST == BURST - 1:
                time.sleep(0.0003)   # a picture is many packets; without a pause they overflow the receiver's buffer
        self.number += 1
        self.sent += 1
        self.window_count += 1
        if self.sent == 1:
            self.log(f"camera stream {self.label}: first picture sent ({width}x{height} {fmt}, {len(encoded)} bytes) "
                     f"to udp {self.address[0]}:{self.address[1]}")
        elapsed = time.monotonic() - self.window_start
        if elapsed >= REPORT_EVERY_S:
            self.log(f"camera stream {self.label}: {self.window_count / elapsed:.1f} pictures/s to udp "
                     f"{self.address[0]}:{self.address[1]} ({self.sent} sent)")
            self.window_start, self.window_count = time.monotonic(), 0


def start(client, world_topic: str, specs: list[dict], log=print) -> list[Stream]:
    """Subscribe to each stream's topic. Raises ValueError for a spec that cannot work."""
    streams = []
    for spec in specs:
        if int(spec.get("image_type", 0)) not in TOPIC_NAMES:
            raise ValueError(f"camera {spec.get('camera')!r}: image type {spec.get('image_type')} is not 0..6")
        stream = Stream(spec, topic_for(world_topic, spec), log)
        streams.append(stream)
    for stream in streams:
        client.subscribe(stream.topic, stream.on_image)
        log(f"camera stream {stream.label}: {stream.topic} -> udp {stream.address[0]}:{stream.address[1]}, "
            f"at most {stream.spec.get('fps', 10):g} pictures/s")
    return streams
