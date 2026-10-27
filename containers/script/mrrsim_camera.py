"""Receive a simulator camera's pictures in your own script.

In the Vehicles tab, switch on "Stream pictures to the script (UDP)" for a camera and pick a port. While the
simulator runs, that camera's pictures are sent to that port as UDP packets; this module collects the
packets back into pictures:

    from mrrsim_camera import CameraReceiver

    with CameraReceiver(5600) as camera:            # the port you chose on the camera (CameraReceiver() for the first one)
        while True:
            frame = camera.get(timeout=5)           # the newest picture you have not seen yet
            if frame is None:
                print("no picture for 5 s")
                continue
            image = frame.image()                   # a NumPy array (BGR, like OpenCV), needs the full image
            print(frame.vehicle, frame.camera, frame.width, frame.height)

`frame.data` holds the JPEG (or PNG) bytes as sent, so a script that does not need the pixels can save or
forward them without decoding: `frame.save("picture.jpg")`.

Pictures arrive at most as often as you chose on the camera ("Frames per second"). A picture whose packets did
not all arrive is dropped, and a script that is slower than the camera skips to the newest picture; `dropped`
counts both. Several cameras (or several vehicles) can send to one port: `frame.vehicle` is the vehicle's
number counted from 0 (the same number as SIM_CONNECT_<n>) and `frame.camera` the camera's name.

Run `python -m mrrsim_camera --port 5600` to see what a camera sends without writing a script.

The packet format (version 1, big-endian), should you want to receive the pictures in another language:
    4s magic "MRCF" | B version=1 | B format (1 JPEG, 2 PNG) | H vehicle | I picture number | H packet index |
    H packet count | I picture size in bytes | H width | H height | q simulator time (ns) | B camera-name length
    then the camera name (UTF-8) and this packet's share of the picture. The picture is the shares in order.
"""
from __future__ import annotations

import argparse
import collections
import os
import socket
import struct
import threading
import time
from dataclasses import dataclass, field

MAGIC = b"MRCF"
VERSION = 1
FORMAT_JPEG, FORMAT_PNG = 1, 2
FORMAT_NAMES = {FORMAT_JPEG: "jpeg", FORMAT_PNG: "png"}
FORMAT_CODES = {name: code for code, name in FORMAT_NAMES.items()}
HEADER = struct.Struct(">4sBBHIHHIHHqB")
# One packet is at most this many bytes, header included. It is small on purpose: WSL's mirrored networking silently
# drops UDP packets over about 1472 bytes between programs on this computer (measured: 1400 arrives, 1500 never does).
MAX_DATAGRAM = 1400
DEFAULT_PORT = 5600


@dataclass
class Frame:
    """One picture from one camera."""
    vehicle: int           # the vehicle's number, counted from 0 (as in SIM_CONNECT_<n>)
    camera: str            # the camera's name in the Vehicles tab
    number: int            # counts up by one per picture the camera sent (gaps mean pictures were lost)
    sim_time_ns: int       # simulator time the picture was taken at
    width: int
    height: int
    format: str            # "jpeg" or "png"
    data: bytes            # the encoded picture
    received_at: float = field(default_factory=time.time)

    def image(self):
        """The picture as a NumPy array (height, width, 3), blue-green-red like OpenCV. Needs numpy and cv2 (the full image)."""
        try:
            import cv2
            import numpy
        except ImportError as exc:
            raise RuntimeError(
                "decoding the picture needs numpy and cv2; the full script image has them (a script that imports "
                "cv2 gets it automatically). Use frame.data for the encoded bytes."
            ) from exc
        decoded = cv2.imdecode(numpy.frombuffer(self.data, dtype=numpy.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError("the picture could not be decoded")
        return decoded

    def save(self, path) -> None:
        with open(path, "wb") as handle:
            handle.write(self.data)


def packets_for(data: bytes, *, vehicle: int, camera: str, number: int, sim_time_ns: int, width: int, height: int,
                format: str = "jpeg", max_datagram: int = MAX_DATAGRAM) -> list[bytes]:
    """Split one encoded picture into the UDP packets that carry it."""
    name = camera.encode("utf-8")[:255]
    room = max(1, max_datagram - HEADER.size - len(name))
    pieces = [data[i:i + room] for i in range(0, len(data), room)] or [b""]
    if len(pieces) > 0xFFFF:
        raise ValueError(f"a picture of {len(data)} bytes does not fit in {0xFFFF} packets")
    return [
        HEADER.pack(MAGIC, VERSION, FORMAT_CODES[format], vehicle, number & 0xFFFFFFFF, index, len(pieces), len(data),
                    min(width, 0xFFFF), min(height, 0xFFFF), sim_time_ns, len(name)) + name + piece
        for index, piece in enumerate(pieces)
    ]


class _Assembly:
    __slots__ = ("number", "parts", "count", "size", "meta")

    def __init__(self, number: int, count: int, size: int, meta: tuple) -> None:
        self.number, self.count, self.size, self.meta = number, count, size, meta
        self.parts: dict[int, bytes] = {}


class CameraReceiver:
    """Listens on a UDP port and puts the packets of the simulator's camera pictures back together."""

    def __init__(self, port: int | None = None, host: str = "0.0.0.0", *, buffer_bytes: int = 8 << 20) -> None:
        """`port` is the one chosen on the camera. Left out, it is the port of the first camera that sends pictures
        (the launcher sets SIM_CAMERA_PORT), or 5600 when the script runs on its own."""
        if port is None:
            port = int(os.environ.get("SIM_CAMERA_PORT", DEFAULT_PORT))
        self.port, self.host = port, host
        self.received = 0      # pictures completed
        self.dropped = 0       # pictures lost: missing packets, or skipped because a newer one had arrived
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buffer_bytes)
        except OSError:
            pass
        self._sock.bind((host, port))
        self._sock.settimeout(0.2)
        self._cond = threading.Condition()
        self._unread: "collections.OrderedDict[tuple, Frame]" = collections.OrderedDict()
        self._latest: Frame | None = None
        self._building: dict[tuple, _Assembly] = {}
        self._closed = False
        self._thread = threading.Thread(target=self._listen, name="mrrsim-camera", daemon=True)
        self._thread.start()

    # ---------------------------------------------------------------- what to call
    def get(self, timeout: float | None = None) -> Frame | None:
        """The newest picture you have not seen yet (of the camera that has waited longest, if several send here).
        Waits up to `timeout` seconds (forever if None); returns None when nothing arrived in time or after close()."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while not self._unread:
                if self._closed:
                    return None
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._cond.wait(remaining)
            _, frame = self._unread.popitem(last=False)
            return frame

    def latest(self) -> Frame | None:
        """The most recent picture, without waiting and without marking it as seen; None before the first one."""
        return self._latest

    def frames(self, timeout: float | None = None):
        """Yield pictures as they arrive, until `timeout` seconds pass without one (never, if None) or close()."""
        while True:
            frame = self.get(timeout)
            if frame is None:
                return
            yield frame

    __iter__ = frames

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        self._thread.join(timeout=2)
        self._sock.close()

    def __enter__(self) -> "CameraReceiver":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- inside
    def _listen(self) -> None:
        while not self._closed:
            try:
                packet = self._sock.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            self._take(packet)

    def _take(self, packet: bytes) -> None:
        if len(packet) < HEADER.size:
            return
        (magic, version, fmt, vehicle, number, index, count, size, width, height, sim_time,
         name_length) = HEADER.unpack_from(packet)
        if magic != MAGIC or version != VERSION or fmt not in FORMAT_NAMES or not count or index >= count:
            return
        start = HEADER.size + name_length
        if len(packet) < start:
            return
        camera = packet[HEADER.size:start].decode("utf-8", "replace")
        key = (vehicle, camera)
        building = self._building.get(key)
        if building is not None and building.number != number:
            if _newer(number, building.number):
                self.dropped += 1                 # the older picture never completed
                building = None
            else:
                return                            # a straggler from a picture already given up on
        if building is None:
            building = self._building[key] = _Assembly(number, count, size, (fmt, width, height, sim_time))
        building.parts[index] = packet[start:]
        if len(building.parts) < building.count:
            return
        del self._building[key]
        data = b"".join(building.parts[i] for i in range(building.count))
        if len(data) != building.size:
            self.dropped += 1
            return
        frame = Frame(vehicle, camera, number, sim_time, width, height, FORMAT_NAMES[fmt], data)
        with self._cond:
            if key in self._unread:
                self.dropped += 1                 # replaced before the script asked for it
                del self._unread[key]
            self._unread[key] = frame
            self._latest = frame
            self.received += 1
            self._cond.notify_all()


def _newer(number: int, than: int) -> bool:
    """Picture numbers wrap at 2**32; compare them the way sequence numbers are compared."""
    return 0 < ((number - than) & 0xFFFFFFFF) < 0x80000000


def main() -> int:
    ap = argparse.ArgumentParser(description="Show the pictures a simulator camera sends to a UDP port.")
    ap.add_argument("--port", type=int, help=f"UDP port (default: SIM_CAMERA_PORT, else {DEFAULT_PORT})")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long (default: until Ctrl+C)")
    ap.add_argument("--save", metavar="FOLDER", help="save every picture into this folder")
    args = ap.parse_args()
    started = time.monotonic()
    counts: dict[tuple, int] = collections.Counter()
    with CameraReceiver(args.port) as camera:
        print(f"listening on UDP port {camera.port}", flush=True)
        try:
            while not args.seconds or time.monotonic() - started < args.seconds:
                frame = camera.get(timeout=1)
                if frame is None:
                    continue
                key = (frame.vehicle, frame.camera)
                counts[key] += 1
                if counts[key] == 1 or counts[key] % 30 == 0:
                    elapsed = max(time.monotonic() - started, 1e-6)
                    print(f"vehicle {frame.vehicle} {frame.camera}: {frame.width}x{frame.height} {frame.format}, "
                          f"{len(frame.data)} bytes, {counts[key]} pictures ({counts[key] / elapsed:.1f}/s), "
                          f"{camera.dropped} dropped", flush=True)
                if args.save:
                    import os
                    os.makedirs(args.save, exist_ok=True)
                    frame.save(os.path.join(args.save, f"v{frame.vehicle}_{frame.camera}_{frame.number:06d}.{frame.format[:3]}"))
        except KeyboardInterrupt:
            pass
    print(f"{camera.received} pictures received, {camera.dropped} dropped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
