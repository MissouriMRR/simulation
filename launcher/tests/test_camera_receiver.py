"""The receiver module a script imports to get the camera pictures (containers/script/mrrsim_camera.py) and the packets
it reads. It is plain standard library, so these tests run here; the relay that sends the packets runs inside the scene
loader's container and is tested by test_camera_relay_container.py."""
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import paths

sys.path.insert(0, str(paths.repo_root() / "containers" / "script"))
import mrrsim_camera as mc  # noqa: E402  (the module the script imports)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def send_all(packets, port):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for packet in packets:
            sock.sendto(packet, ("127.0.0.1", port))
            time.sleep(0.0005)


def make_packets(data=b"x" * 10, number=0, vehicle=0, camera="Cam", **kw):
    return mc.packets_for(data, vehicle=vehicle, camera=camera, number=number, sim_time_ns=123456789, width=64, height=48, **kw)


class Receiver(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.rx = mc.CameraReceiver(self.port, "127.0.0.1")
        self.addCleanup(self.rx.close)

    def test_a_small_picture_arrives_whole(self):
        send_all(make_packets(b"\xff\xd8jpeg\xff\xd9", number=7, vehicle=2, camera="Down"), self.port)
        frame = self.rx.get(timeout=2)
        self.assertEqual((frame.vehicle, frame.camera, frame.number, frame.sim_time_ns, frame.width, frame.height, frame.format),
                         (2, "Down", 7, 123456789, 64, 48, "jpeg"))
        self.assertEqual(frame.data, b"\xff\xd8jpeg\xff\xd9")

    def test_a_big_picture_is_split_and_put_back_together(self):
        data = bytes(range(256)) * 1000                                  # 256 kB
        packets = make_packets(data, format="png")
        self.assertGreater(len(packets), 3)
        self.assertTrue(all(len(p) <= mc.MAX_DATAGRAM for p in packets))
        send_all(packets, self.port)
        frame = self.rx.get(timeout=2)
        self.assertEqual(frame.data, data)
        self.assertEqual(frame.format, "png")

    def test_packets_may_arrive_in_any_order(self):
        data = bytes(range(256)) * 1000
        packets = make_packets(data)
        send_all(list(reversed(packets)), self.port)
        self.assertEqual(self.rx.get(timeout=2).data, data)

    def test_a_picture_missing_a_packet_is_dropped_when_the_next_one_starts(self):
        data = bytes(range(256)) * 1000
        packets = make_packets(data, number=1)
        send_all(packets[:1] + packets[2:], self.port)                   # packet 1 is lost
        self.assertIsNone(self.rx.get(timeout=0.4))
        send_all(make_packets(b"next", number=2), self.port)
        self.assertEqual(self.rx.get(timeout=2).number, 2)
        self.assertEqual(self.rx.dropped, 1)

    def test_a_slow_script_skips_to_the_newest_picture(self):
        for number in range(5):
            send_all(make_packets(bytes([number]) * 10, number=number), self.port)
        time.sleep(0.3)
        frame = self.rx.get(timeout=2)
        self.assertEqual(frame.number, 4)
        self.assertEqual(self.rx.dropped, 4)
        self.assertIsNone(self.rx.get(timeout=0.2))

    def test_latest_does_not_use_the_picture_up(self):
        self.assertIsNone(self.rx.latest())
        send_all(make_packets(b"abc", number=3), self.port)
        self.assertEqual(self.rx.get(timeout=2).number, 3)
        self.assertEqual(self.rx.latest().number, 3)
        self.assertEqual(self.rx.latest().number, 3)

    def test_cameras_and_vehicles_sharing_a_port_stay_apart(self):
        send_all(make_packets(b"a" * 100_000, vehicle=0, camera="A") + make_packets(b"b" * 100_000, vehicle=1, camera="A")
                 + make_packets(b"c" * 10, vehicle=0, camera="B"), self.port)
        got = {(f.vehicle, f.camera): f.data[:1] for f in (self.rx.get(timeout=2) for _ in range(3))}
        self.assertEqual(got, {(0, "A"): b"a", (1, "A"): b"b", (0, "B"): b"c"})

    def test_junk_is_ignored(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for junk in (b"", b"hello", b"MRCF" + b"\x00" * 3, b"\x00" * 100):
                sock.sendto(junk, ("127.0.0.1", self.port))
        send_all(make_packets(b"ok"), self.port)
        self.assertEqual(self.rx.get(timeout=2).data, b"ok")

    def test_get_times_out_and_close_wakes_a_waiting_reader(self):
        started = time.monotonic()
        self.assertIsNone(self.rx.get(timeout=0.3))
        self.assertGreaterEqual(time.monotonic() - started, 0.25)
        result = []
        waiter = threading.Thread(target=lambda: result.append(self.rx.get()))
        waiter.start()
        time.sleep(0.2)
        self.rx.close()
        waiter.join(timeout=3)
        self.assertFalse(waiter.is_alive())
        self.assertEqual(result, [None])

    def test_frames_iterates_until_the_timeout(self):
        send_all(make_packets(b"1", number=1) + make_packets(b"2", number=2), self.port)
        time.sleep(0.2)
        self.assertEqual([f.number for f in self.rx.frames(timeout=0.3)], [2])    # the older one was skipped

    def test_the_port_comes_from_the_environment_when_not_given(self):
        port = free_port()
        with mock.patch.dict("os.environ", {"SIM_CAMERA_PORT": str(port)}):
            with mc.CameraReceiver(host="127.0.0.1") as rx:
                self.assertEqual(rx.port, port)

    def test_the_picture_number_wraps_around(self):
        self.assertTrue(mc._newer(1, 0xFFFFFFFF))
        self.assertFalse(mc._newer(0xFFFFFFFF, 1))
        self.assertFalse(mc._newer(5, 5))

    def test_image_without_opencv_explains_itself(self):
        frame = mc.Frame(0, "C", 0, 0, 1, 1, "jpeg", b"x")
        with mock.patch.dict(sys.modules, {"cv2": None}):
            with self.assertRaises(RuntimeError) as caught:
                frame.image()
        self.assertIn("frame.data", str(caught.exception))

    def test_save_writes_the_bytes(self):
        target = Path(tempfile.mkdtemp()) / "p.jpg"
        self.addCleanup(shutil.rmtree, target.parent, True)
        mc.Frame(0, "C", 0, 0, 1, 1, "jpeg", b"\x01\x02").save(target)
        self.assertEqual(target.read_bytes(), b"\x01\x02")


class Packets(unittest.TestCase):
    def test_the_header_is_what_the_docstring_says(self):
        packet = make_packets(b"data", number=9, vehicle=3, camera="Cam")[0]
        self.assertEqual(packet[:4], b"MRCF")
        self.assertEqual(packet[4], 1)                              # version
        self.assertEqual(mc.HEADER.size, 4 + 1 + 1 + 2 + 4 + 2 + 2 + 4 + 2 + 2 + 8 + 1)
        self.assertEqual(packet[mc.HEADER.size:], b"Camdata")

    def test_an_empty_picture_is_one_packet(self):
        self.assertEqual(len(make_packets(b"")), 1)

    def test_every_packet_but_the_last_is_full(self):
        packets = make_packets(b"z" * 150_000, camera="C")
        self.assertEqual([len(p) for p in packets[:-1]], [mc.MAX_DATAGRAM] * (len(packets) - 1))


if __name__ == "__main__":
    unittest.main()
