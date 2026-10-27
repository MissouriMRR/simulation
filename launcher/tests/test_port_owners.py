import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config, ports
from mrrsim.runtime import doctor

NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1180
  TCP    127.0.0.1:6463         0.0.0.0:0              LISTENING       12472
  TCP    127.0.0.1:50000        127.0.0.1:6463         ESTABLISHED     999
  TCP    [::]:6443              [::]:0                 LISTENING       4040
  UDP    0.0.0.0:5353           *:*                                    4512
  UDP    [::]:9013              *:*                                    777
"""


def cfg(vehicles: int) -> config.Config:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mrrsim.toml"
        path.write_text(f"[sitl]\nvehicles = {vehicles}\n", encoding="utf-8")
        return config.load(path)


class Netstat(unittest.TestCase):
    def test_listening_tcp_and_bound_udp_ports_are_read(self):
        owners = ports.parse_netstat(NETSTAT)
        self.assertEqual(owners[("tcp", 6463)], 12472)
        self.assertEqual(owners[("tcp", 6443)], 4040)  # IPv6 form
        self.assertEqual(owners[("udp", 5353)], 4512)
        self.assertEqual(owners[("udp", 9013)], 777)

    def test_connections_that_are_not_listening_are_ignored(self):
        self.assertNotIn(("tcp", 50000), ports.parse_netstat(NETSTAT))

    def test_junk_lines_are_skipped(self):
        self.assertEqual(ports.parse_netstat("nothing useful\n\n  TCP  bad\n"), {})


class PortsCheck(unittest.TestCase):
    def test_it_names_the_program_and_the_vehicle(self):
        with mock.patch.object(ports, "busy_ports", return_value=[(6463, "tcp")]), \
                mock.patch.object(ports, "who_uses", return_value={(6463, "tcp"): "Discord.exe (pid 12472)"}):
            check = doctor._ports(cfg(100), unreal=True)
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn("tcp/6463", check.detail)
        self.assertIn("vehicle 70's health check", check.detail)
        self.assertIn("Discord.exe (pid 12472)", check.detail)
        self.assertIn("fewer vehicles", check.hint)

    def test_a_leftover_simulator_gets_the_other_hint(self):
        with mock.patch.object(ports, "busy_ports", return_value=[(8989, "tcp")]), \
                mock.patch.object(ports, "who_uses", return_value={(8989, "tcp"): "Simulation-Win64-Shipping.exe (pid 5)"}):
            check = doctor._ports(cfg(1), unreal=True)
        self.assertIn("mrrsim down", check.hint)

    def test_all_free_is_ok(self):
        with mock.patch.object(ports, "busy_ports", return_value=[]):
            check = doctor._ports(cfg(2), unreal=True)
        self.assertEqual(check.status, doctor.OK)


if __name__ == "__main__":
    unittest.main()
