import errno
import socket
import unittest
from unittest import mock

from mrrsim.core import ports


class PortScheme(unittest.TestCase):
    def test_vehicle_zero_matches_the_documented_scheme(self):
        vp = ports.vehicle_ports(0)
        self.assertEqual(vp.sysid, 1)
        self.assertEqual((vp.sim_in, vp.sim_out), (9003, 9002))
        self.assertEqual((vp.dronekit, vp.monitor), (5762, 5763))
        self.assertEqual(vp.connect_string(), "tcp:127.0.0.1:5762")

    def test_each_vehicle_shifts_by_ten(self):
        vp = ports.vehicle_ports(3)
        self.assertEqual(vp.sysid, 4)
        self.assertEqual((vp.sim_in, vp.sim_out), (9033, 9032))
        self.assertEqual((vp.dronekit, vp.monitor), (5792, 5793))

    def test_ports_never_collide_across_vehicles(self):
        seen = set()
        for vp in ports.all_vehicle_ports(ports.MAX_VEHICLES):
            for entry in vp.listening() + [(vp.sim_out, "udp")]:
                self.assertNotIn(entry, seen)
                seen.add(entry)

    def test_index_out_of_range(self):
        with self.assertRaises(ValueError):
            ports.vehicle_ports(-1)
        with self.assertRaises(ValueError):
            ports.vehicle_ports(ports.MAX_VEHICLES)


class BusyPorts(unittest.TestCase):
    def test_detects_a_listening_tcp_port(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            self.assertFalse(ports.port_is_free(port, "tcp"))
            self.assertEqual(ports.busy_ports([(port, "tcp")]), [(port, "tcp")])

    def test_free_port_is_reported_free(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertTrue(ports.port_is_free(port, "tcp"))


def has_ipv6() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::", 0))
        return True
    except OSError:
        return False


class EveryAddress(unittest.TestCase):
    """A port held on one address must not look free because it can still be bound on another."""

    def hold(self, host: str, kind: int):
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(family, kind)
        self.addCleanup(sock.close)
        sock.bind((host, 0))
        if kind == socket.SOCK_STREAM:
            sock.listen()
        return sock.getsockname()[1]

    def test_a_port_held_on_any_address_is_busy_for_tcp_and_udp(self):
        hosts = ["127.0.0.1", "0.0.0.0"] + (["::"] if has_ipv6() else [])
        for proto, kind in (("tcp", socket.SOCK_STREAM), ("udp", socket.SOCK_DGRAM)):
            for host in hosts:
                with self.subTest(proto=proto, held_on=host):
                    port = self.hold(host, kind)
                    self.assertFalse(ports.port_is_free(port, proto))
                    self.assertEqual(ports.busy_ports([(port, proto)]), [(port, proto)])

    def test_a_port_nobody_holds_is_free_for_tcp_and_udp(self):
        for proto, kind in (("tcp", socket.SOCK_STREAM), ("udp", socket.SOCK_DGRAM)):
            with socket.socket(socket.AF_INET, kind) as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            self.assertTrue(ports.port_is_free(port, proto), proto)

    def test_one_address_can_still_be_asked_about_on_its_own(self):
        port = self.hold("127.0.0.1", socket.SOCK_STREAM)
        self.assertFalse(ports.port_is_free(port, "tcp", host="127.0.0.1"))

    def test_an_ipv6_error_that_is_not_about_the_port_does_not_make_it_busy(self):
        """No IPv6 on this computer (or the address missing) says nothing about the port."""
        real = socket.socket

        def no_ipv6(family=socket.AF_INET, *args, **kwargs):
            if family == socket.AF_INET6:
                raise OSError(97, "address family not supported")
            return real(family, *args, **kwargs)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with mock.patch.object(ports.socket, "socket", side_effect=no_ipv6):
            self.assertTrue(ports.port_is_free(port, "tcp"))
        self.assertIsNone(
            _bind_with(ports, socket.AF_INET6, OSError(10049, "the address is not valid here"))
        )

    def test_in_use_and_access_denied_errors_mean_busy(self):
        for number in (errno.EADDRINUSE, errno.EACCES, 10048, 10013):
            self.assertIs(_bind_with(ports, socket.AF_INET6, OSError(number, "x")), False, number)

    def test_any_ipv4_bind_failure_means_busy(self):
        self.assertIs(_bind_with(ports, socket.AF_INET, OSError(10049, "x")), False)


def _bind_with(module, family: int, error: OSError):
    """What `_can_bind` answers when binding the port raises `error`."""
    fake = mock.MagicMock()
    fake.__enter__.return_value = fake
    fake.bind.side_effect = error
    with mock.patch.object(module.socket, "socket", return_value=fake):
        return module._can_bind(family, "::" if family == socket.AF_INET6 else "127.0.0.1", 1234, socket.SOCK_STREAM)


if __name__ == "__main__":
    unittest.main()
