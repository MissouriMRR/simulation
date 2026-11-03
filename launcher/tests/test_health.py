import socket
import threading
import time
import unittest
from unittest import mock

from mrrsim.runtime import health


def v2_heartbeat(sysid: int = 1, compid: int = 1) -> bytes:
    header = bytes([0xFD, 9, 0, 0, 7, sysid, compid, 0, 0, 0])
    payload = bytes([0, 0, 0, 0, 2, 3, 81, 3, 3])  # custom_mode, type, autopilot, base_mode, status, version
    return header + payload + b"\x12\x34"


def v1_heartbeat(sysid: int = 1, compid: int = 1) -> bytes:
    header = bytes([0xFE, 9, 7, sysid, compid, 0])
    payload = bytes([0, 0, 0, 0, 2, 3, 81, 3, 3])
    return header + payload + b"\x12\x34"


class FindHeartbeats(unittest.TestCase):
    def test_v2_frame(self):
        self.assertEqual(health.find_heartbeats(v2_heartbeat(5, 2)), [(5, 2)])

    def test_v1_frame(self):
        self.assertEqual(health.find_heartbeats(v1_heartbeat(3, 1)), [(3, 1)])

    def test_frame_after_garbage_and_between_other_bytes(self):
        data = b"\x00\x01\xfd\xff" + v2_heartbeat(2) + b"\x55" * 5 + v2_heartbeat(4)
        self.assertEqual(health.find_heartbeats(data), [(2, 1), (4, 1)])

    def test_truncated_frame_is_ignored(self):
        self.assertEqual(health.find_heartbeats(v2_heartbeat()[:-4]), [])

    def test_other_message_ids_are_not_heartbeats(self):
        frame = bytearray(v2_heartbeat())
        frame[7] = 24  # GPS_RAW_INT
        self.assertEqual(health.find_heartbeats(bytes(frame)), [])

    def test_noise(self):
        self.assertEqual(health.find_heartbeats(bytes(range(256)) * 4), [])

    def test_nothing_in_nothing_out(self):
        self.assertEqual(health.find_heartbeats(b""), [])

    def test_a_frame_with_another_payload_length_is_not_a_heartbeat(self):
        frame = bytearray(v2_heartbeat())
        frame[1] = 8
        self.assertEqual(health.find_heartbeats(bytes(frame)), [])

    def test_a_truncated_v1_frame_is_ignored(self):
        self.assertEqual(health.find_heartbeats(v1_heartbeat()[:-3]), [])

    def test_a_signed_frame_does_not_hide_the_next_one(self):
        signed = bytearray(v2_heartbeat(2))
        signed[2] = 1  # incompat flag: a 13-byte signature follows the CRC
        data = bytes(signed) + b"\xaa" * 13 + v2_heartbeat(4)
        self.assertEqual(health.find_heartbeats(data), [(2, 1), (4, 1)])

    def test_every_sender_on_the_port_is_reported(self):
        data = v2_heartbeat(1, 1) + v2_heartbeat(255, 190) + v1_heartbeat(1, 1)
        self.assertEqual(health.find_heartbeats(data), [(1, 1), (255, 190), (1, 1)])


class FakeSitl:
    """TCP server that streams heartbeats for `sysid`, like a SITL serial port."""

    def __init__(self, sysid: int | None):
        self.sysid = sysid
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.port = self.server.getsockname()[1]
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        self.server.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.server.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                for _ in range(20):
                    if self._stop.is_set():
                        break
                    if self.sysid is not None:
                        try:
                            conn.sendall(v2_heartbeat(self.sysid))
                        except OSError:
                            break
                    self._stop.wait(0.1)

    def close(self):
        self._stop.set()
        self.thread.join(timeout=2)
        self.server.close()


class ScriptedServer:
    """TCP server that treats its connections one by one as a script: a list of (seconds to wait, bytes to send) steps;
    after the steps the connection is closed. A connection beyond the scripts gets nothing and is closed."""

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.port = self.server.getsockname()[1]
        self.connections = 0
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self.connections += 1
            steps = self.scripts.pop(0) if self.scripts else []
            with conn:
                try:
                    for pause, data in steps:
                        time.sleep(pause)
                        conn.sendall(data)
                except OSError:
                    pass

    def close(self):
        self.server.close()
        self.thread.join(timeout=2)


class WaitHeartbeatOverTheWire(unittest.TestCase):
    def serve(self, *scripts):
        server = ScriptedServer(*scripts)
        self.addCleanup(server.close)
        return server

    def test_a_heartbeat_that_arrives_in_two_pieces_is_found(self):
        frame = v2_heartbeat(3)
        server = self.serve([(0, frame[:7]), (0.3, frame[7:])])
        ok, detail = health.wait_heartbeat("127.0.0.1", server.port, 3, timeout=5)
        self.assertTrue(ok, detail)

    def test_a_connection_that_is_dropped_is_made_again(self):
        server = self.serve([], [(0, v2_heartbeat(1))])  # the first one closes at once
        ok, detail = health.wait_heartbeat("127.0.0.1", server.port, 1, timeout=10)
        self.assertTrue(ok, detail)
        self.assertEqual(server.connections, 2)

    def test_noise_before_the_heartbeat_does_not_matter(self):
        server = self.serve([(0, b"\x00\xfd\xfe\x09" * 50 + v1_heartbeat(2))])
        ok, detail = health.wait_heartbeat("127.0.0.1", server.port, 2, timeout=5)
        self.assertTrue(ok, detail)

    def test_the_gcs_heartbeat_is_not_the_vehicles(self):
        server = self.serve([(0, v2_heartbeat(255, 190))])
        ok, detail = health.wait_heartbeat("127.0.0.1", server.port, 1, timeout=2)
        self.assertFalse(ok)
        self.assertEqual(detail, "heartbeat from sysid [255] but expected 1")

    def test_a_container_that_dies_while_the_port_is_open_and_silent_is_noticed_within_seconds(self):
        server = self.serve([(0, b"")] + [(0.2, b"")] * 20)  # stays connected and says nothing
        started = time.monotonic()
        ok, detail = health.wait_heartbeat("127.0.0.1", server.port, 1, timeout=30, alive=lambda: time.monotonic() - started < 0.3)
        self.assertEqual((ok, detail), (False, "container exited"))
        self.assertLess(time.monotonic() - started, 2.5)  # it used to take 4.6 s

    def test_alive_is_not_asked_at_every_packet(self):
        server = self.serve([(0.01, b"\x00" * 16)] * 300)  # a busy port, 100 packets a second
        asked = []
        ok, detail = health.wait_heartbeat("127.0.0.1", server.port, 1, timeout=2.2, alive=lambda: asked.append(1) or True)
        self.assertFalse(ok)
        self.assertTrue(2 <= len(asked) <= 5, len(asked))  # about once a second, not hundreds of times

    def test_a_timeout_that_has_run_out_still_tries_once(self):
        server = self.serve([(0, v2_heartbeat(1))])
        ok, detail = health.wait_heartbeat("127.0.0.1", server.port, 1, timeout=0)
        self.assertFalse(ok)
        self.assertEqual(server.connections, 1)
        self.assertNotIn("nothing is listening", detail)

    def test_the_ids_that_were_heard_are_listed_in_order(self):
        server = self.serve([(0, v2_heartbeat(7) + v2_heartbeat(3))])
        ok, detail = health.wait_heartbeat("127.0.0.1", server.port, 1, timeout=2)
        self.assertFalse(ok)
        self.assertIn("sysid [3, 7] but expected 1", detail)


class WaitHeartbeat(unittest.TestCase):
    def test_success(self):
        sitl = FakeSitl(sysid=2)
        self.addCleanup(sitl.close)
        ok, detail = health.wait_heartbeat("127.0.0.1", sitl.port, 2, timeout=5)
        self.assertTrue(ok, detail)

    def test_wrong_sysid_is_reported(self):
        sitl = FakeSitl(sysid=9)
        self.addCleanup(sitl.close)
        ok, detail = health.wait_heartbeat("127.0.0.1", sitl.port, 2, timeout=3)
        self.assertFalse(ok)
        self.assertIn("expected 2", detail)

    def test_open_port_without_heartbeat_explains_lockstep(self):
        sitl = FakeSitl(sysid=None)
        self.addCleanup(sitl.close)
        ok, detail = health.wait_heartbeat("127.0.0.1", sitl.port, 1, timeout=2)
        self.assertFalse(ok)
        self.assertIn("lock-step", detail)

    def test_nothing_listening(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        ok, detail = health.wait_heartbeat("127.0.0.1", port, 1, timeout=1)
        self.assertFalse(ok)
        self.assertIn("nothing is listening", detail)

    def test_dead_container_aborts_early(self):
        ok, detail = health.wait_heartbeat("127.0.0.1", 9, 1, timeout=30, alive=lambda: False)
        self.assertFalse(ok)
        self.assertEqual(detail, "container exited")


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class TcpOpen(unittest.TestCase):
    def test_a_listening_port_is_open(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            self.assertTrue(health.tcp_open("127.0.0.1", server.getsockname()[1]))

    def test_a_port_nobody_listens_on_is_not(self):
        self.assertFalse(health.tcp_open("127.0.0.1", closed_port()))

    def test_a_name_that_does_not_exist_is_not_open_and_not_an_error(self):
        self.assertFalse(health.tcp_open("no-such-host.invalid", 80, timeout=2))


class WaitTcp(unittest.TestCase):
    def test_no_ports_is_ready_at_once(self):
        self.assertEqual(health.wait_tcp("127.0.0.1", [], timeout=5), [])

    def test_only_the_ports_that_stay_closed_are_returned_in_order(self):
        shut_a, shut_b = closed_port(), closed_port()
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            ports = [shut_a, server.getsockname()[1], shut_b]
            self.assertEqual(health.wait_tcp("127.0.0.1", ports, timeout=1, interval=0.1), [shut_a, shut_b])

    def test_a_port_that_opens_later_is_waited_for(self):
        server = socket.socket()
        self.addCleanup(server.close)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        threading.Timer(0.5, server.listen).start()
        self.assertEqual(health.wait_tcp("127.0.0.1", [port], timeout=5, interval=0.1), [])

    def test_it_stops_at_once_when_the_thing_it_waits_for_has_died(self):
        port = closed_port()
        started = time.monotonic()
        pending = health.wait_tcp("127.0.0.1", [port], timeout=30, alive=lambda: False)
        self.assertEqual(pending, [port])
        self.assertLess(time.monotonic() - started, 5)

    def test_it_keeps_waiting_while_the_thing_is_alive(self):
        asked = []
        with mock.patch.object(health, "tcp_open", return_value=False):  # (refusing a connection takes a second on Windows)
            pending = health.wait_tcp("h", [5], timeout=0.5, alive=lambda: asked.append(1) or True, interval=0.1)
        self.assertEqual(pending, [5])
        self.assertGreater(len(asked), 2)

    def test_a_timeout_of_zero_still_looks_once(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            self.assertEqual(health.wait_tcp("127.0.0.1", [server.getsockname()[1]], timeout=0), [])

    def test_a_timeout_that_has_run_out_asks_whether_it_is_alive_no_more(self):
        asked = []
        port = closed_port()
        pending = health.wait_tcp("127.0.0.1", [port], timeout=0, alive=lambda: asked.append(1) or True)
        self.assertEqual(pending, [port])
        self.assertEqual(asked, [])

    def test_closed_ports_are_looked_at_together_not_one_after_another(self):
        ports = [closed_port() for _ in range(4)]
        started = time.monotonic()
        self.assertEqual(health.wait_tcp("127.0.0.1", ports, timeout=0.5), ports)
        self.assertLess(time.monotonic() - started, 2.5)  # one at a time it takes 4 s on Windows

    def test_the_wait_does_not_sleep_past_the_deadline(self):
        with mock.patch.object(health, "open_ports", return_value=[]):
            started = time.monotonic()
            health.wait_tcp("h", [5], timeout=0.3, interval=10)
        self.assertLess(time.monotonic() - started, 2)


class OpenPorts(unittest.TestCase):
    def test_the_open_ones_come_back_in_the_order_given(self):
        servers = [socket.socket() for _ in range(3)]
        for server in servers:
            self.addCleanup(server.close)
            server.bind(("127.0.0.1", 0))
            server.listen()
        shut = closed_port()
        ports = [s.getsockname()[1] for s in servers]
        asked = [ports[2], shut, ports[0], ports[1]]
        self.assertEqual(health.open_ports("127.0.0.1", asked), [ports[2], ports[0], ports[1]])

    def test_nothing_to_try_is_nothing_open(self):
        self.assertEqual(health.open_ports("127.0.0.1", []), [])

    def test_many_closed_ports_cost_about_one_look(self):
        ports = [closed_port() for _ in range(10)]
        started = time.monotonic()
        self.assertEqual(health.open_ports("127.0.0.1", ports), [])
        self.assertLess(time.monotonic() - started, 3)  # 10 s one at a time on Windows

    def test_more_ports_than_probes_are_all_answered(self):
        with mock.patch.object(health, "MAX_PROBES", 2), mock.patch.object(health, "tcp_open", side_effect=lambda h, p, t: p % 2):
            self.assertEqual(health.open_ports("h", [1, 2, 3, 4, 5]), [1, 3, 5])
    def test_returns_ports_that_never_opened(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.assertEqual(health.wait_tcp("127.0.0.1", [port], timeout=1), [port])

    def test_ready_when_listening(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            self.assertEqual(health.wait_tcp("127.0.0.1", [port], timeout=2), [])


if __name__ == "__main__":
    unittest.main()
