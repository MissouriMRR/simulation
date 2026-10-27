"""Readiness checks: TCP ports and MAVLink heartbeats (stdlib only)."""
from __future__ import annotations

import socket
import time
from typing import Callable

AliveFn = Callable[[], bool]


def tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_tcp(
    host: str,
    ports: list[int],
    timeout: float,
    alive: AliveFn | None = None,
    interval: float = 0.5,
) -> list[int]:
    """Wait until every port accepts a connection. Returns the ports still closed (empty = ready).

    Stops early (returning what is still closed) when `alive()` turns False.
    """
    deadline = time.monotonic() + timeout
    pending = list(ports)
    while pending and time.monotonic() < deadline:
        pending = [p for p in pending if not tcp_open(host, p)]
        if not pending:
            break
        if alive is not None and not alive():
            break
        time.sleep(interval)
    return pending


def find_heartbeats(data: bytes) -> list[tuple[int, int]]:
    """(sysid, compid) of every MAVLink HEARTBEAT frame found in `data`.

    Scans for MAVLink 1 (0xFE) and 2 (0xFD) frames with msgid 0 and length 9. The CRC is not
    checked: this only answers "is a MAVLink autopilot talking on this port?".
    """
    found: list[tuple[int, int]] = []
    i = 0
    n = len(data)
    while i < n:
        magic = data[i]
        if magic == 0xFD and i + 10 + 9 + 2 <= n:  # v2: magic len incompat compat seq sys comp msgid[3]
            if data[i + 1] == 9 and data[i + 7:i + 10] == b"\x00\x00\x00":
                found.append((data[i + 5], data[i + 6]))
                i += 10 + 9 + 2
                continue
        elif magic == 0xFE and i + 6 + 9 + 2 <= n:  # v1: magic len seq sys comp msgid
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

    Returns (ok, detail). SITL accepts one client per serial port, so keep this short-lived.
    """
    deadline = time.monotonic() + timeout
    connected = False
    seen: set[int] = set()
    while time.monotonic() < deadline:
        if alive is not None and not alive():
            return False, "container exited"
        try:
            with socket.create_connection((host, port), timeout=2.0) as sock:
                connected = True
                sock.settimeout(1.0)
                buffer = b""
                read_until = min(deadline, time.monotonic() + 4.0)
                while time.monotonic() < read_until:
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
        time.sleep(0.5)
    if seen:
        return False, f"heartbeat from sysid {sorted(seen)} but expected {sysid}"
    if connected:
        return False, (
            "port is open but no heartbeat arrived. SITL is lock-step: check that the scene loaded "
            "and the robot's ArduPilot controller points at 127.0.0.1 with the right ports"
        )
    return False, f"nothing is listening on {host}:{port}"
