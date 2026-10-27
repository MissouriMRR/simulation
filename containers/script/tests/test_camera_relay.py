"""The camera relay against a fake simulator connection. Runs inside the airsim image:

    podman run --rm -v <containers/script>:/src:ro localhost/mrrsim-script:airsim \
        sh -c "cp -r /src /tmp/src && cd /tmp/src && python -m unittest tests.test_camera_relay"

launcher/tests/test_camera_relay_container.py does that when MRRSIM_CONTAINER_TESTS=1.
"""
import io
import json
import os
import socket
import sys
import time
import types
import unittest
from contextlib import redirect_stdout

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))   # in the image the receiver is /opt/mrrsim/lib; camera_relay finds it itself
import camera_relay  # noqa: E402
import mrrsim_camera  # noqa: E402


class FakeClient:
    def __init__(self):
        self.subscriptions = {}

    def subscribe(self, topic, callback, reliability=1.0):
        self.subscriptions[topic] = callback


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def rgb_message(width=64, height=48, colour=(10, 200, 30), encoding="RGB", stamp=5):
    picture = np.zeros((height, width, 3), np.uint8)
    picture[:] = colour
    return {"time_stamp": stamp, "width": width, "height": height, "encoding": encoding, "big_endian": False, "step": 1,
            "data": picture.tobytes(), "annotations": []}


def depth_message(width, height, metres, encoding="16FC1"):
    return {"time_stamp": 1, "width": width, "height": height, "encoding": encoding, "big_endian": False, "step": 2,
            "data": np.full((height, width), metres, np.float16).tobytes(), "annotations": []}


class Relay(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.rx = mrrsim_camera.CameraReceiver(self.port, "127.0.0.1")
        self.addCleanup(self.rx.close)
        self.client = FakeClient()
        self.lines = []

    def start(self, **spec):
        spec = {"vehicle": 0, "camera": "DownCamera", "image_type": 0, "host": "127.0.0.1", "port": self.port, "fps": 100000,
                "format": "jpeg", "quality": 90, "max_width": 0, "max_depth": 50, **spec}
        streams = camera_relay.start(self.client, "/Sim/Scene", [spec], log=self.lines.append)
        return streams[0], self.client.subscriptions[streams[0].topic]

    def test_the_topic_is_the_cameras_capture_of_the_vehicle(self):
        stream, _ = self.start(vehicle=2, camera="Front", image_type=3)
        self.assertEqual(stream.topic, "/Sim/Scene/robots/Drone3/sensors/Front/segmentation_camera")
        self.assertTrue(any("Drone3/Front" in line and "udp 127.0.0.1" in line for line in self.lines))

    def test_every_image_type_has_a_topic(self):
        for image_type in range(7):
            topic = camera_relay.topic_for("/Sim/S", {"vehicle": 0, "camera": "C", "image_type": image_type})
            self.assertTrue(topic.startswith("/Sim/S/robots/Drone1/sensors/C/"), topic)

    def test_a_picture_arrives_with_its_colours_and_labels(self):
        _, callback = self.start()
        callback("t", rgb_message(colour=(10, 200, 30), stamp=77))
        frame = self.rx.get(timeout=2)
        self.assertEqual((frame.vehicle, frame.camera, frame.width, frame.height, frame.format, frame.sim_time_ns),
                         (0, "DownCamera", 64, 48, "jpeg", 77))
        image = frame.image()
        self.assertEqual(image.shape, (48, 64, 3))
        self.assertTrue(np.abs(image.astype(int) - np.array((10, 200, 30))).max() <= 6)   # BGR in, BGR out (JPEG is lossy)
        self.assertTrue(any("first picture sent" in line for line in self.lines))

    def test_png_is_lossless(self):
        _, callback = self.start(format="png")
        callback("t", rgb_message(colour=(1, 2, 3)))
        frame = self.rx.get(timeout=2)
        self.assertEqual(frame.format, "png")
        self.assertEqual(frame.image()[5, 5].tolist(), [1, 2, 3])

    def test_wide_pictures_are_scaled_down_and_say_so(self):
        _, callback = self.start(max_width=32)
        callback("t", rgb_message(width=128, height=64))
        frame = self.rx.get(timeout=2)
        self.assertEqual((frame.width, frame.height, frame.image().shape[:2]), (32, 16, (16, 32)))

    def test_a_big_picture_needs_several_packets_and_still_arrives(self):
        _, callback = self.start(format="png")
        noise = np.random.default_rng(1).integers(0, 255, (480, 640, 3), dtype=np.uint8)
        message = {"time_stamp": 1, "width": 640, "height": 480, "encoding": "RGB", "big_endian": False, "step": 1,
                   "data": noise.tobytes(), "annotations": []}
        callback("t", message)
        frame = self.rx.get(timeout=3)
        self.assertGreater(len(frame.data), mrrsim_camera.MAX_DATAGRAM)
        self.assertTrue((frame.image() == noise).all())

    def test_the_rate_is_limited(self):
        _, callback = self.start(fps=5)
        for _ in range(30):
            callback("t", rgb_message())
        time.sleep(0.3)
        self.assertEqual(self.rx.received, 1)               # all 30 arrived within a few ms: one is sent
        time.sleep(0.25)
        for _ in range(5):
            callback("t", rgb_message())
        time.sleep(0.3)
        self.assertEqual(self.rx.received, 2)

    def test_picture_numbers_count_up(self):
        _, callback = self.start()
        numbers = []
        for _ in range(3):
            callback("t", rgb_message())
            numbers.append(self.rx.get(timeout=2).number)
        self.assertEqual(numbers, [0, 1, 2])

    def test_depth_is_sent_as_grey_scaled_to_the_maximum(self):
        _, callback = self.start(image_type=2, max_depth=20)
        callback("t", depth_message(32, 24, 10.0))
        grey = self.rx.get(timeout=2).image()
        self.assertTrue(abs(int(grey[3, 3, 0]) - 127) <= 4)
        callback("t", depth_message(32, 24, float("inf")))
        self.assertGreaterEqual(int(self.rx.get(timeout=2).image()[3, 3, 0]), 250)       # sky: white

    def test_a_bad_picture_does_not_stop_the_stream(self):
        stream, callback = self.start()
        callback("t", {"width": 4, "height": 4, "encoding": "RGB", "data": b"short"})
        callback("t", rgb_message())
        self.assertEqual(self.rx.get(timeout=2).width, 64)
        self.assertEqual(stream.failures, 1)
        self.assertTrue(any("could not send" in line for line in self.lines))

    def test_an_unknown_image_type_is_refused_before_anything_is_subscribed(self):
        with self.assertRaises(ValueError):
            camera_relay.start(self.client, "/Sim/S", [{"vehicle": 0, "camera": "C", "image_type": 9, "port": 1}])
        self.assertEqual(self.client.subscriptions, {})

    def test_several_streams_are_started_together(self):
        specs = [{"vehicle": v, "camera": "C", "image_type": 0, "port": self.port, "fps": 100} for v in range(3)]
        camera_relay.start(self.client, "/Sim/S", specs, log=self.lines.append)
        self.assertEqual(len(self.client.subscriptions), 3)

    def test_to_8bit_handles_the_other_pixel_types(self):
        self.assertEqual(camera_relay.to_8bit(np.array([[0.0, 1.0, 2.0]], np.float32), False, 50).tolist(), [[0, 255, 255]])
        self.assertEqual(camera_relay.to_8bit(np.array([[256, 65535]], np.uint16), False, 50).tolist(), [[1, 255]])


class SceneLoader(unittest.TestCase):
    def setUp(self):
        import scene_loader
        self.loader = scene_loader
        self.world = types.SimpleNamespace(parent_topic="/Sim/Scene")
        self.addCleanup(os.environ.pop, "MRRSIM_CAMERA_STREAMS", None)

    def test_streams_are_started_through_the_environment(self):
        client = FakeClient()
        os.environ["MRRSIM_CAMERA_STREAMS"] = json.dumps([{"vehicle": 0, "camera": "C", "image_type": 0, "port": 5600}])
        with redirect_stdout(io.StringIO()):
            self.loader.start_camera_streams(client, self.world)
        self.assertEqual(list(client.subscriptions), ["/Sim/Scene/robots/Drone1/sensors/C/scene_camera"])

    def test_a_broken_setup_prints_the_line_the_launcher_looks_for(self):
        os.environ["MRRSIM_CAMERA_STREAMS"] = "{not json"
        out = io.StringIO()
        with redirect_stdout(out):
            self.loader.start_camera_streams(FakeClient(), self.world)
        self.assertIn("camera stream FAILED", out.getvalue())

    def test_nothing_happens_without_streams(self):
        client = FakeClient()
        os.environ.pop("MRRSIM_CAMERA_STREAMS", None)
        self.loader.start_camera_streams(client, self.world)
        self.assertEqual(client.subscriptions, {})


if __name__ == "__main__":
    unittest.main()
