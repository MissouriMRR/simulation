"""Readiness checks: TCP ports and MAVLink heartbeats (stdlib only)."""

from __future__ import annotations

import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable


# === CONSTANTS ================================================================


# How often `alive()` is asked while a heartbeat is awaited. Asking can be as
# slow as running a command in WSL, so it is not asked at every packet.
ALIVE_INTERVAL_S = 1.0
# The most ports that are tried at the same time.
MAX_PROBES = 64


# === TYPES ====================================================================


AliveFn = Callable[[], bool]


# === TCP PORTS ================================================================


def tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Can a TCP connection to host:port be opened within `timeout` seconds?"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def open_ports(host: str, ports: list[int], timeout: float = 1.0) -> list[int]:
    """The ports among `ports` that accept a connection, in the order given.
    They are tried at the same time: on Windows a port nobody listens on takes
    the whole `timeout` to refuse, so trying them one after another would cost
    that much for each."""
    if not ports:
        return []
    with ThreadPoolExecutor(max_workers=min(len(ports), MAX_PROBES)) as pool:
        answers = list(pool.map(lambda port: tcp_open(host, port, timeout), ports))
    return [port for port, answer in zip(ports, answers) if answer]


def wait_tcp(
    host: str,
    ports: list[int],
    timeout: float,
    alive: AliveFn | None = None,
    interval: float = 0.5,
) -> list[int]:
    """Wait until every port accepts a connection. Returns the ports still
    closed (empty = ready).

    Looks at least once, even with a timeout of 0, and may run over `timeout`
    by up to a second (one look). Stops early (returning what is still closed)
    when `alive()` turns False."""
    deadline = time.monotonic() + timeout
    pending = list(ports)
    while pending:
        now_open = set(open_ports(host, pending))
        pending = [port for port in pending if port not in now_open]
        left = deadline - time.monotonic()
        if not pending or left <= 0 or (alive is not None and not alive()):
            break
        time.sleep(min(interval, left))
    return pending


# === MAVLINK HEARTBEATS =======================================================


def find_heartbeats(data: bytes) -> list[tuple[int, int]]:
    """(sysid, compid) of every MAVLink HEARTBEAT frame found in `data`.

    Scans for MAVLink 1 (0xFE) and 2 (0xFD) frames with msgid 0 and length 9.
    The CRC is not checked: this only answers "is a MAVLink autopilot talking on
    this port?"."""
    found: list[tuple[int, int]] = []
    i = 0
    n = len(data)
    while i < n:
        magic = data[i]
        # v2: magic len incompat compat seq sys comp msgid[3]
        if magic == 0xFD and i + 10 + 9 + 2 <= n:
            if data[i + 1] == 9 and data[i + 7 : i + 10] == b"\x00\x00\x00":
                found.append((data[i + 5], data[i + 6]))
                i += 10 + 9 + 2
                continue
        # v1: magic len seq sys comp msgid
        elif magic == 0xFE and i + 6 + 9 + 2 <= n:
            if data[i + 1] == 9 and data[i + 5] == 0:
                found.append((data[i + 3], data[i + 4]))
                i += 6 + 9 + 2
                continue
        i += 1
    return found


def wait_heartbeat(
    host: str,
    port: int,
    sysid: int,
    timeout: float,
    alive: AliveFn | None = None,
) -> tuple[bool, str]:
    """Connect to a SITL serial port and wait for a heartbeat from `sysid`.

    Returns (ok, detail). SITL accepts one client per serial port, so keep this
    short-lived. Tries at least once, even with a timeout of 0. `alive()` is
    asked about once a second, also while reading, so a container that dies is
    noticed within about a second."""
    deadline = time.monotonic() + timeout
    connected = False
    seen: set[int] = set()
    next_check = 0.0

    def container_gone() -> bool:
        nonlocal next_check
        if alive is None or time.monotonic() < next_check:
            return False
        next_check = time.monotonic() + ALIVE_INTERVAL_S
        return not alive()

    while True:
        if container_gone():
            return False, "container exited"
        try:
            with socket.create_connection((host, port), timeout=2.0) as sock:
                connected = True
                sock.settimeout(1.0)
                buffer = b""
                read_until = min(deadline, time.monotonic() + 4.0)
                while time.monotonic() < read_until:
                    if container_gone():
                        return False, "container exited"
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buffer = (buffer + chunk)[-8192:]
                    seen.update(sysid_ for sysid_, _ in find_heartbeats(buffer))
                    if sysid in seen:
                        return True, f"heartbeat from sysid {sysid}"
        except OSError:
            pass
        left = deadline - time.monotonic()
        if left <= 0:
            break
        time.sleep(min(0.5, left))
    if seen:
        return (
            False,
            f"heartbeat from sysid {sorted(seen)} but expected {sysid}",
        )
    if connected:
        return False, (
            "port is open but no heartbeat arrived. SITL is lock-step: check "
            "that the scene loaded and the robot's ArduPilot controller points "
            "at 127.0.0.1 with the right ports"
        )
    return False, f"nothing is listening on {host}:{port}"
