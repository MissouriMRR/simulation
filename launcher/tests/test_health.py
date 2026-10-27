import socket
import threading
import unittest

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


class WaitTcp(unittest.TestCase):
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
