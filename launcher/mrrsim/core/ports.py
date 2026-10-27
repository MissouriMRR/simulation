"""
Per-vehicle port scheme (vehicle index i, 0-based).

    SYSID                 i + 1
    UDP  9003 + 10i       ArduPilot receives sensor data (--sim-port-in)
    UDP  9002 + 10i       simulator receives motor control (--sim-port-out)
    TCP  5762 + 10i       SERIAL1: DroneKit / student scripts
    TCP  5763 + 10i       SERIAL2: launcher health monitor
    UDP  14550            SERIAL0 target for a GCS, shared by all vehicles

ArduPilot applies the +10*i offset itself when started with `-I i`; this
module mirrors that so the launcher can connect and check for conflicts.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Iterable

# === CONSTANTS ================================================================


# Each vehicle's ports are the first vehicle's plus STEP times its number.
STEP = 10
# Vehicle 0's ports (see the table at the top of this file)
BASE_SIM_OUT = 9002
BASE_SIM_IN = 9003
BASE_DRONEKIT = 5762
BASE_MONITOR = 5763

MAX_VEHICLES = 100


# === A VEHICLE'S PORTS ========================================================


@dataclass(frozen=True)
class VehiclePorts:
    """All the ports and the MAVLink id of one vehicle."""

    index: int  # the vehicle's number, from 0
    sysid: int  # its MAVLink system id
    sim_in: int  # UDP: ArduPilot receives sensor data here
    sim_out: int  # UDP: the simulator receives motor control here
    dronekit: int  # TCP: where scripts connect (SERIAL1)
    monitor: int  # TCP: the launcher's health check (SERIAL2)

    def connect_string(self, host: str = "127.0.0.1") -> str:
        """What a script gives DroneKit to connect to this vehicle."""
        return f"tcp:{host}:{self.dronekit}"

    def listening(self) -> list[tuple[int, str]]:
        """(port, protocol) pairs this vehicle's SITL will listen on."""
        return [
            (self.sim_in, "udp"),
            (self.dronekit, "tcp"),
            (self.monitor, "tcp"),
        ]


def vehicle_ports(index: int) -> VehiclePorts:
    """The ports of vehicle `index` (counted from 0); ValueError when it is
    outside 0..MAX_VEHICLES-1."""
    if not 0 <= index < MAX_VEHICLES:
        raise ValueError(f"vehicle index {index} out of range 0..{MAX_VEHICLES - 1}")
    offset = STEP * index
    return VehiclePorts(
        index=index,
        sysid=index + 1,
        sim_in=BASE_SIM_IN + offset,
        sim_out=BASE_SIM_OUT + offset,
        dronekit=BASE_DRONEKIT + offset,
        monitor=BASE_MONITOR + offset,
    )


def all_vehicle_ports(count: int) -> list[VehiclePorts]:
    """The ports of vehicles 0..count-1."""
    return [vehicle_ports(i) for i in range(count)]


# === WHO IS USING A PORT ======================================================


def port_is_free(port: int, proto: str = "tcp", host: str = "127.0.0.1") -> bool:
    """Best-effort bind test."""
    kind = socket.SOCK_STREAM if proto == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def busy_ports(ports: Iterable[tuple[int, str]]) -> list[tuple[int, str]]:
    """Of these (port, protocol) pairs, the ones that cannot be used right now
    because something already has them."""
    return [(port, proto) for port, proto in ports if not port_is_free(port, proto)]


def parse_netstat(text: str) -> dict[tuple[str, int], int]:
    """(protocol, port) -> process id, from `netstat -ano` output (listening TCP
    ports and bound UDP ports)."""
    owners: dict[tuple[str, int], int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() not in ("TCP", "UDP"):
            continue
        proto = parts[0].lower()
        if proto == "tcp" and parts[3].upper() != "LISTENING":
            continue
        local, pid = parts[1], parts[-1]
        port = local.rsplit(":", 1)[-1]
        if port.isdigit() and pid.isdigit():
            owners[(proto, int(port))] = int(pid)
    return owners


def who_uses(
    ports_in_use: Iterable[tuple[int, str]],
) -> dict[tuple[int, str], str]:
    """(port, protocol) -> "Discord.exe (pid 1234)" for the ports somebody is
    using. Windows only; best effort."""
    import subprocess

    wanted = list(ports_in_use)
    names: dict[tuple[int, str], str] = {}
    try:
        net = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=30
        ).stdout
        tasks = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return names
    by_pid: dict[int, str] = {}
    for line in tasks.splitlines():
        cells = [c.strip('"') for c in line.split('","')]
        if len(cells) > 1 and cells[1].isdigit():
            by_pid[int(cells[1])] = cells[0].strip('"')
    owners = parse_netstat(net)
    for port, proto in wanted:
        pid = owners.get((proto, port))
        if pid is not None:
            names[(port, proto)] = (
                f"{by_pid.get(pid, 'an unknown program')} (pid {pid})"
            )
    return names
