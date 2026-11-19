"""freecam_server.py with a fake simulator: the angle math, which camera the main view shows, and the UDP messages."""
import json
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import freecam_server as fs  # noqa: E402


def camera(name, streaming=True):
    return {"id": name, "type": "camera", "capture-settings": [{"image-type": 0, "streaming-enabled": streaming}]}


SCENE = {"actors": [
    {"type": "robot", "name": "Drone1", "robot-config": {"sensors": [camera("Chase"), camera("DownCamera", False)]}},
    {"type": "robot", "name": "Drone2", "robot-config": {"sensors": [camera("Chase")]}},
    {"type": "robot", "name": "FreeCam", "robot-config": {"sensors": [camera("FreeCamCamera")]}},
    {"type": "env_actor", "name": "Tree"},
]}


class Angles(unittest.TestCase):
    def test_a_rotation_comes_back_as_the_same_angles(self):
        for angles in ((0, 0, 0), (0, -20, 0), (0, -45, -90), (5, 30, 170)):
            back = fs.euler(fs.quaternion(*angles))
            for got, want in zip(back, angles):
                self.assertAlmostEqual(got, want, places=6)


class Controller(unittest.TestCase):
    def setUp(self):
        self.world = mock.Mock(get_configuration=mock.Mock(return_value=SCENE))
        self.robot, self.drone = mock.Mock(), mock.Mock()
        self.c = fs.Controller(self.robot, self.drone, self.world, lambda x, y, z, q: (x, y, z, q))

    def test_the_streamed_cameras_in_scene_order(self):
        self.assertEqual(fs.streaming_cameras(SCENE), [("Drone1", "Chase"), ("Drone2", "Chase"), ("FreeCam", "FreeCamCamera")])

    def test_the_view_switches_just_enough_times_to_reach_a_camera(self):
        self.assertEqual(self.c.handle({"cmd": "view", "to": "freecam"}), {"ok": True})
        self.assertEqual(self.world.switch_streaming_view.call_count, 2)       # Drone1 -> Drone2 -> FreeCam
        self.assertEqual(self.c.handle({"cmd": "view", "to": "vehicle"}), {"ok": True})
        self.assertEqual(self.world.switch_streaming_view.call_count, 3)       # FreeCam -> Drone1
        self.c.handle({"cmd": "view", "to": "vehicle"})
        self.assertEqual(self.world.switch_streaming_view.call_count, 3)       # already there

    def test_pose_and_get(self):
        self.assertIsNone(self.c.handle({"cmd": "pose", "pose": [1, 2, -3, 0, -20, 90]}))
        (x, y, z, q), reset = self.robot.set_pose.call_args.args
        self.assertEqual((x, y, z, reset), (1, 2, -3, True))
        self.assertAlmostEqual(fs.euler(q)[2], 90)
        self.robot.get_ground_truth_pose.return_value = {"translation": {"x": 4, "y": 5, "z": -6},
                                                         "rotation": fs.quaternion(0, -10, 45)}
        pose = self.c.handle({"cmd": "get"})["pose"]
        self.assertEqual(pose[:3], [4, 5, -6])
        self.assertAlmostEqual(pose[4], -10)
        self.assertIn("unknown command", self.c.handle({"cmd": "dance"})["error"])

    def test_chase_follows_the_first_drone_from_behind_and_above_until_flown_or_another_view(self):
        self.drone.get_ground_truth_pose.return_value = {"translation": {"x": 10, "y": 0, "z": -15},
                                                         "rotation": fs.quaternion(0, 0, 90)}       # heading east
        self.assertEqual(self.c.handle({"cmd": "chase"}), {"ok": True})
        self.assertEqual(self.world.switch_streaming_view.call_count, 2)                 # shown: FreeCam
        self.c.tick()
        (x, y, z, q), _reset = self.robot.set_pose.call_args.args
        self.assertAlmostEqual(x, 10)
        self.assertAlmostEqual(y, -fs.CHASE_BEHIND_M)                                     # behind it: to the west
        self.assertAlmostEqual(z, -15 - fs.CHASE_ABOVE_M)
        self.assertAlmostEqual(fs.euler(q)[2], 90)
        self.robot.get_ground_truth_pose.return_value = {"translation": {"x": 0, "y": 0, "z": 0},
                                                         "rotation": fs.quaternion(0, 0, 0)}
        for stop in ({"cmd": "pose", "pose": [0, 0, 0, 0, 0, 0]}, {"cmd": "get"}, {"cmd": "view", "to": "vehicle"}):
            self.c.handle({"cmd": "chase"})
            self.c.handle(stop)
            self.assertFalse(self.c.chasing, stop)
        self.robot.set_pose.reset_mock()
        self.c.tick()
        self.robot.set_pose.assert_not_called()


class OverUdp(unittest.TestCase):
    def test_a_get_is_answered_and_a_bad_message_does_not_stop_it(self):
        controller = mock.Mock()
        controller.handle.side_effect = [ValueError("bad"), {"pose": [0, 0, 0, 0, 0, 0]}]
        controller.tick.side_effect = RuntimeError("drone gone")                    # a failed chase is logged too
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        stop, logged = threading.Event(), []
        thread = threading.Thread(target=fs.serve, args=(controller, server, stop, logged.append), daemon=True)
        thread.start()
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(3)
        try:
            client.sendto(b'{"cmd": "x"}', server.getsockname())
            self.assertIn("ValueError", json.loads(client.recv(4096))["error"])
            client.sendto(b'{"cmd": "get"}', server.getsockname())
            self.assertEqual(json.loads(client.recv(4096)), {"pose": [0, 0, 0, 0, 0, 0]})
        finally:
            stop.set()
            thread.join(3)
            client.close()
            server.close()
        self.assertTrue(logged)

    def test_without_a_free_camera_in_the_scene_nothing_is_started(self):
        world = mock.Mock(get_configuration=mock.Mock(return_value={"actors": SCENE["actors"][:2]}))
        logged = []
        self.assertIsNone(fs.start(mock.Mock(), world, 0, logged.append))
        self.assertIn("has none", logged[0])


if __name__ == "__main__":
    unittest.main()
