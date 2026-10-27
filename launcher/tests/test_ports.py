import socket
import unittest

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


if __name__ == "__main__":
    unittest.main()
