"""Per-vehicle port scheme (vehicle index i, 0-based).

    SYSID                 i + 1
    UDP  9003 + 10i       ArduPilot receives sensor data from the simulator   (--sim-port-in)
    UDP  9002 + 10i       simulator receives motor control from ArduPilot     (--sim-port-out)
    TCP  5762 + 10i       SERIAL1: DroneKit / student scripts
    TCP  5763 + 10i       SERIAL2: launcher health monitor
    UDP  14550            SERIAL0 udpclient target for a GCS, shared by all vehicles

ArduPilot applies the +10*i offset itself when started with `-I i`; this module mirrors that
so the launcher can connect and check for conflicts.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Iterable

STEP = 10
BASE_SIM_OUT = 9002
BASE_SIM_IN = 9003
BASE_DRONEKIT = 5762
BASE_MONITOR = 5763
# The most vehicles the launcher will start. Checked: the ports of vehicles 0..99 never overlap each other or
# Unreal's (highest is 9993), and MAVLink system ids go to 255. What a computer can really run is far lower:
# every vehicle is a flight-controller container plus a robot (with cameras) in the simulator.
MAX_VEHICLES = 100


@dataclass(frozen=True)
class VehiclePorts:
    index: int
    sysid: int
    sim_in: int
    sim_out: int
    dronekit: int
    monitor: int

    def connect_string(self, host: str = "127.0.0.1") -> str:
        return f"tcp:{host}:{self.dronekit}"

    def listening(self) -> list[tuple[int, str]]:
        """(port, protocol) pairs this vehicle's SITL will listen on."""
        return [(self.sim_in, "udp"), (self.dronekit, "tcp"), (self.monitor, "tcp")]


def vehicle_ports(index: int) -> VehiclePorts:
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
    return [vehicle_ports(i) for i in range(count)]


def port_is_free(port: int, proto: str = "tcp", host: str = "127.0.0.1") -> bool:
    """Best-effort bind test. In WSL mirrored mode WSL and Windows share one port space."""
    kind = socket.SOCK_STREAM if proto == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def parse_netstat(text: str) -> dict[tuple[str, int], int]:
    """(protocol, port) -> process id, from `netstat -ano` output (listening TCP ports and bound UDP ports)."""
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


def who_uses(ports_in_use: Iterable[tuple[int, str]]) -> dict[tuple[int, str], str]:
    """(port, protocol) -> "Discord.exe (pid 1234)" for the ports somebody is using. Windows only; best effort."""
    import subprocess

    wanted = list(ports_in_use)
    names: dict[tuple[int, str], str] = {}
    try:
        net = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=30).stdout
        tasks = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=30).stdout
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
            names[(port, proto)] = f"{by_pid.get(pid, 'an unknown program')} (pid {pid})"
    return names


def busy_ports(ports: Iterable[tuple[int, str]]) -> list[tuple[int, str]]:
    return [(port, proto) for port, proto in ports if not port_is_free(port, proto)]
