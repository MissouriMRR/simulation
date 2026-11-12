"""Run a program that flies ONE vehicle as a swarm: one copy per vehicle.

    python -m mrrsim_fleet SCRIPT [ARGS...]

starts `python -u SCRIPT ARGS -i N` for N = 1 ... SIM_VEHICLE_COUNT, all at once (drone N is vehicle N-1 of the
launcher, the one SIM_CONNECT_<N-1> reaches). Every line a copy prints is shown with `[drone N]` in front. Ctrl+C or
a stop request reaches every copy as the same signal, so each one can land by itself. The exit code is the first
copy's failure, or 0 when every copy ended well.

The pictures of a camera that streams to the script (SIM_CAMERA_PORT, see mrrsim_camera.py) come to one UDP port,
and only one program can listen on it. This program listens there instead and passes each picture on to the copy
whose vehicle took it, on a port of its own: the copy's SIM_CAMERA_PORT, so its CameraReceiver() just works.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading

import mrrsim_camera

RELAY_HOST = "127.0.0.1"
LOCK = threading.Lock()  # one line of output at a time, so copies do not break each other's lines


def say(text: str) -> None:
    """Print one line; lines of different copies never mix."""
    with LOCK:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()


def relay_port(port: int, index: int) -> int:
    """The port the copy for vehicle `index` (counted from 0) gets its pictures on: the camera's port plus one for
    each vehicle before it, and one more."""
    return port + 1 + index


def child_command(script: str, args: list[str], number: int) -> list[str]:
    """The command of the copy for drone `number`: the script, its arguments, and -i number."""
    return [sys.executable, "-u", script, *args, "-i", str(number)]


def child_env(env: dict[str, str], index: int, camera_port: int | None) -> dict[str, str]:
    """The environment of the copy for vehicle `index`: the same as ours, with its own picture port."""
    own = dict(env)
    if camera_port is not None:
        own["SIM_CAMERA_PORT"] = str(relay_port(camera_port, index))
    return own


def relay_pictures(port: int, count: int, stop: threading.Event) -> None:
    """Pass the picture packets that arrive on `port` on to the copy of the vehicle they come from, until `stop`
    is set. Packets of an unknown vehicle, and anything that is not a picture packet, are dropped."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as exc:
        say(f"[fleet] cannot pass the pictures on: UDP port {port} is not free ({exc})")
        sock.close()
        return
    sock.settimeout(0.2)
    with sock:
        while not stop.is_set():
            try:
                packet = sock.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            if len(packet) < mrrsim_camera.HEADER.size:
                continue
            fields = mrrsim_camera.HEADER.unpack_from(packet)
            if fields[0] != mrrsim_camera.MAGIC or fields[3] >= count:
                continue
            try:
                sock.sendto(packet, (RELAY_HOST, relay_port(port, fields[3])))
            except OSError:
                pass


def show_output(proc: subprocess.Popen, number: int) -> None:
    """Show what a copy prints, each line marked with its drone number (runs in its own thread)."""
    for line in proc.stdout:
        say(f"[drone {number}] {line.rstrip()}")


def exit_code(code: int) -> int:
    """A copy that a signal ended has a negative code; shells call that 128 plus the signal."""
    return code if code >= 0 else 128 - code


def main(argv: list[str] | None = None) -> int:
    """Start one copy per vehicle, pass the pictures on and wait for every copy. The exit code is the first copy's
    failure, else 0."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m mrrsim_fleet SCRIPT [ARGS...]", file=sys.stderr)
        return 2
    script, args = argv[0], argv[1:]
    count = int(os.environ.get("SIM_VEHICLE_COUNT", "1"))
    camera_port = int(os.environ["SIM_CAMERA_PORT"]) if os.environ.get("SIM_CAMERA_PORT") else None

    stop = threading.Event()
    relay = None
    if camera_port is not None:
        relay = threading.Thread(target=relay_pictures, args=(camera_port, count, stop), daemon=True)
        relay.start()

    copies = []
    for index in range(count):
        copies.append(subprocess.Popen(
            child_command(script, args, index + 1), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=child_env(dict(os.environ), index, camera_port), text=True, encoding="utf-8", errors="replace",
            bufsize=1,
        ))
    say(f"[fleet] started {count} cop{'y' if count == 1 else 'ies'} of {script} {' '.join(args)}")
    readers = [threading.Thread(target=show_output, args=(proc, i + 1), daemon=True) for i, proc in enumerate(copies)]
    for reader in readers:
        reader.start()

    def forward(signum, _frame) -> None:
        for proc in copies:
            if proc.poll() is None:
                proc.send_signal(signum)

    names = ("SIGINT", "SIGTERM")
    before = {name: signal.signal(getattr(signal, name), forward) for name in names}
    try:
        codes = [exit_code(proc.wait()) for proc in copies]
    finally:
        for name, handler in before.items():
            signal.signal(getattr(signal, name), handler)
    for reader, proc in zip(readers, copies):
        reader.join(timeout=5)
        proc.stdout.close()
    stop.set()
    if relay is not None:
        relay.join(timeout=2)
    for number, code in enumerate(codes, 1):
        if code:
            say(f"[fleet] drone {number} ended with code {code}")
    return next((code for code in codes if code), 0)


if __name__ == "__main__":
    raise SystemExit(main())
