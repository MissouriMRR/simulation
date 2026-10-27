"""A camera that sends its pictures to the running script over UDP: the setting, the checks, the launcher's wiring and
the receiver module the script imports. (The relay that runs inside the scene loader's container is tested by
test_camera_relay_container.py.)"""
import copy
import io
import json
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
from mrrsim.designs import vehicle_design as vd, vehicle_schema as vs
from mrrsim.runtime import session
from mrrsim.scene import camerastream, scenecheck, scenegen

CONTAINER_SCRIPTS = paths.repo_root() / "containers" / "script"
sys.path.insert(0, str(CONTAINER_SCRIPTS))
import mrrsim_camera as mc  # noqa: E402  (the module the script imports; it is plain standard library)

SIM = paths.repo_root() / "sim_config"


def design_with_stream(**stream) -> dict:
    doc = scenecheck.load_jsonc(SIM / "robot_ardu_quadrotor.jsonc")
    camera = next(s for s in doc["sensors"] if s["id"] == "DownCamera")
    camera[camerastream.STREAM_KEY] = {"enabled": True, "port": 5601, **stream}
    return doc


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Setting(unittest.TestCase):
    def test_it_is_a_group_of_the_camera_block_with_defaults(self):
        group = vs.CAMERA.setting(camerastream.STREAM_KEY)
        self.assertEqual(group.kind, vs.GROUP)
        self.assertEqual({c.key for c in group.children},
                         {"enabled", "host", "port", "image-type", "fps", "format", "quality", "max-width", "max-depth"})
        for child in group.children:
            self.assertEqual(child.default, camerastream.DEFAULTS[child.key], child.key)
            self.assertEqual(child.source, "mrrsim")
        self.assertFalse(camerastream.DEFAULTS["enabled"])

    def test_no_other_block_has_it(self):
        for bt in vs.BLOCK_TYPES:
            if bt is not vs.CAMERA:
                self.assertIsNone(bt.setting(camerastream.STREAM_KEY), bt.title)

    def test_the_launcher_default_vehicle_is_untouched(self):
        doc = scenecheck.load_jsonc(SIM / "robot_ardu_quadrotor.jsonc")
        self.assertEqual(camerastream.streams(doc, 1), [])


class Streams(unittest.TestCase):
    def test_one_stream_per_camera_per_vehicle_with_defaults_filled_in(self):
        found = camerastream.streams(design_with_stream(), 2)
        self.assertEqual([(s["vehicle"], s["camera"], s["port"]) for s in found],
                         [(0, "DownCamera", 5601), (1, "DownCamera", 5601)])
        first = found[0]
        self.assertEqual((first["host"], first["image_type"], first["fps"], first["format"], first["quality"],
                          first["max_width"]), ("127.0.0.1", 0, 10.0, "jpeg", 80, 0))

    def test_settings_are_used(self):
        found = camerastream.streams(design_with_stream(host="10.1.2.3", fps=2.5, format="png", **{"max-width": 320, "image-type": 2}), 1)
        self.assertEqual((found[0]["host"], found[0]["fps"], found[0]["format"], found[0]["max_width"], found[0]["image_type"]),
                         ("10.1.2.3", 2.5, "png", 320, 2))

    def test_switched_off_or_disabled_cameras_send_nothing(self):
        doc = design_with_stream()
        doc["sensors"][next(i for i, s in enumerate(doc["sensors"]) if s["id"] == "DownCamera")][camerastream.STREAM_KEY]["enabled"] = False
        self.assertEqual(camerastream.streams(doc, 1), [])
        doc = design_with_stream()
        next(s for s in doc["sensors"] if s["id"] == "DownCamera")["enabled"] = False
        self.assertEqual(camerastream.streams(doc, 1), [])

    def test_stripping_removes_only_the_launchers_key_and_copies(self):
        doc = design_with_stream()
        before = copy.deepcopy(doc)
        clean = camerastream.strip(doc)
        self.assertEqual(doc, before)
        self.assertNotIn(camerastream.STREAM_KEY, json.dumps(clean))
        clean_camera = next(s for s in clean["sensors"] if s["id"] == "DownCamera")
        original = next(s for s in before["sensors"] if s["id"] == "DownCamera")
        original = {k: v for k, v in original.items() if k != camerastream.STREAM_KEY}
        self.assertEqual(clean_camera, original)


class CopyForAirSim(unittest.TestCase):
    def test_the_copied_settings_are_plain_json_without_the_launchers_key(self):
        design = vd.Design(design_with_stream())
        text = design.airsim_json()
        self.assertNotIn(camerastream.STREAM_KEY, text)
        data = json.loads(text)                                         # valid JSON, no header comment
        self.assertEqual(data["sensors"][0]["id"], design.doc["sensors"][0]["id"])
        self.assertIn(camerastream.STREAM_KEY, json.dumps(design.doc))  # the design itself keeps it
        self.assertEqual(scenecheck.ardupilot_ports(data), scenecheck.ardupilot_ports(design.doc))


class Checks(unittest.TestCase):
    def issues(self, doc):
        return [i for i in vd.Design(doc).issues() if i.level in ("error", "warning")]

    def test_a_good_setting_has_no_problems(self):
        self.assertEqual([i for i in self.issues(design_with_stream()) if "camera" in i.message.lower() and "stream" in i.message.lower()], [])
        self.assertEqual([i.message for i in self.issues(design_with_stream()) if i.level == "error"], [])

    def test_the_chosen_capture_must_send_images_to_scripts(self):
        doc = design_with_stream(**{"image-type": 3})            # the DownCamera has no segmentation capture
        errors = [i for i in self.issues(doc) if i.level == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("Send images to scripts", errors[0].message)
        doc = design_with_stream()
        camera = next(s for s in doc["sensors"] if s["id"] == "DownCamera")
        camera["capture-settings"][0]["capture-enabled"] = False
        self.assertEqual(len([i for i in self.issues(doc) if i.level == "error"]), 1)

    def test_a_port_the_simulator_uses_is_refused(self):
        for port in (8989, 5762, 9003, 14550):
            errors = [i.message for i in self.issues(design_with_stream(port=port)) if i.level == "error"]
            self.assertEqual(len(errors), 1, port)
            self.assertIn(str(port), errors[0])

    def test_a_port_outside_the_range_or_a_wrong_type_is_reported(self):
        self.assertTrue([i for i in self.issues(design_with_stream(port=80)) if "usual minimum" in i.message])
        self.assertTrue([i for i in self.issues(design_with_stream(port="five")) if i.level == "error"])
        self.assertTrue([i for i in self.issues(design_with_stream(format="gif")) if i.level == "error"])

    def test_an_off_setting_is_not_checked_for_captures(self):
        doc = design_with_stream(**{"image-type": 3, "enabled": False})
        self.assertEqual([i for i in self.issues(doc) if i.level == "error"], [])

    def test_a_switched_off_camera_is_only_mentioned(self):
        doc = design_with_stream()
        next(s for s in doc["sensors"] if s["id"] == "DownCamera")["enabled"] = False
        levels = {i.level for i in vd.Design(doc).issues() if "sends no pictures" in i.message}
        self.assertEqual(levels, {"info"})

    def test_the_design_with_the_setting_can_fly(self):
        self.assertEqual(vd.runnable_problem(design_with_stream()), "")


class Wiring(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.dir = self.tmp / "sim_config"
        self.dir.mkdir()
        for name in ("scene_ardupilot_quadrotor.jsonc", "robot_ardu_quadrotor.jsonc"):
            shutil.copy(SIM / name, self.dir / name)
        patch = mock.patch.dict("os.environ", {"MRRSIM_VEHICLES_DIR": str(self.tmp / "vehicles"),
                                               "MRRSIM_DATA_DIR": str(self.tmp / "data")})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(session.SHUTTING_DOWN.clear)
        self.cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        self.cfg.scene.config_dir = self.dir
        self.cfg.scene.scene = "scene_ardupilot_quadrotor.jsonc"
        self.library = vd.Library(self.tmp / "vehicles", self.dir)

    def args(self, **kw):
        base = dict(scene=None, no_scene=False, no_cameras=False, design="Spy")
        base.update(kw)
        return mock.Mock(**base)

    def prepare(self, **kw):
        out = io.StringIO()
        with redirect_stdout(out):
            cli._prepare_scene(self.cfg, self.args(**kw))
        return out.getvalue()

    def test_the_streams_come_from_the_design_for_every_vehicle(self):
        self.library.create("Spy", design_with_stream(port=5620))
        self.cfg.sitl.vehicles = 3
        said = self.prepare()
        self.assertEqual([(s["vehicle"], s["port"]) for s in self.cfg.scene.camera_streams], [(0, 5620), (1, 5620), (2, 5620)])
        self.assertIn("DownCamera sends its pictures to UDP port 5620", said)
        self.assertEqual(said.count("sends its pictures"), 1)         # told once per camera, not per vehicle

    def test_the_robot_configs_the_simulator_reads_do_not_carry_the_setting(self):
        self.library.create("Spy", design_with_stream())
        self.cfg.sitl.vehicles = 2
        self.prepare()
        for path in self.dir.glob("robot_design_*.jsonc"):
            self.assertNotIn(camerastream.STREAM_KEY, path.read_text(encoding="utf-8"), path.name)

    def test_without_cameras_nothing_is_streamed(self):
        self.library.create("Spy", design_with_stream())
        self.prepare(no_cameras=True)
        self.assertEqual(self.cfg.scene.camera_streams, [])

    def test_a_design_that_streams_a_missing_capture_is_refused(self):
        self.library.create("Spy", design_with_stream(**{"image-type": 3}))
        with self.assertRaises(config.ConfigError) as caught:
            self.prepare()
        self.assertIn("Send images to scripts", str(caught.exception))

    def test_a_design_that_does_not_stream_changes_nothing(self):
        doc = scenecheck.load_jsonc(SIM / "robot_ardu_quadrotor.jsonc")
        self.library.create("Spy", doc)
        self.prepare()
        self.assertEqual(self.cfg.scene.camera_streams, [])

    def test_the_loader_gets_the_streams_and_always_holds_the_connection(self):
        self.library.create("Spy", design_with_stream(port=5633))
        self.cfg.scene.hold = False
        self.prepare()
        sess = session.Session(self.cfg, dry_run=True)
        started = {}
        real = sess.podman.run_detached

        def spy(image, name, **kw):
            started[name] = kw
            return real(image, name, **kw)

        with mock.patch.object(sess.podman, "run_detached", side_effect=spy), self.assertLogs(level="INFO") as logs:
            sess._load_scene()
        loader = started[session.SCENE_NAME]
        self.assertIn("--hold", loader["command"])
        self.assertTrue(loader["rm"])
        sent = json.loads(loader["env"]["MRRSIM_CAMERA_STREAMS"])
        self.assertEqual((sent[0]["camera"], sent[0]["port"], sent[0]["image_type"]), ("DownCamera", 5633, 0))
        self.assertIn("holding", "\n".join(logs.output))

    def test_no_streams_means_the_loader_is_started_as_before(self):
        sess = session.Session(self.cfg, dry_run=True)
        started = {}
        real = sess.podman.run_detached
        with mock.patch.object(sess.podman, "run_detached", side_effect=lambda i, n, **kw: started.update({n: kw}) or real(i, n, **kw)), \
                self.assertLogs(level="INFO"):
            sess._load_scene()
        self.assertIsNone(started[session.SCENE_NAME]["env"])

    def test_the_script_finds_the_receiver_and_the_ports(self):
        self.library.create("Spy", design_with_stream(port=5644))
        self.cfg.sitl.vehicles = 2
        self.prepare()
        self.cfg.scripts.main = "x.py"
        self.cfg.scripts.dir = self.tmp
        sess = session.Session(self.cfg, dry_run=True)
        seen = {}
        with mock.patch.object(sess.podman, "run_attached", side_effect=lambda image, name, **kw: seen.update(kw)), \
                self.assertLogs(level="INFO"):
            sess._run_script()
        env = seen["env"]
        self.assertEqual(env["SIM_CAMERA_PORT"], "5644")
        self.assertEqual(json.loads(env["SIM_CAMERA_STREAMS"]),
                         [{"vehicle": 0, "camera": "DownCamera", "port": 5644}, {"vehicle": 1, "camera": "DownCamera", "port": 5644}])
        self.assertTrue(env["PYTHONPATH"].endswith(session.SCRIPT_LIB))
        self.assertTrue(env["PYTHONPATH"].startswith("/work"))

    def test_a_failed_relay_is_a_warning_not_a_stop(self):
        log = self.tmp / "scene.log"
        log.write_text("Scene loaded\ncamera stream FAILED: ImportError: no module\n", encoding="utf-8")
        sess = session.Session(self.cfg, dry_run=True)
        with self.assertLogs(level="INFO") as logs:
            sess._report_camera_streams(log, [{"camera": "C", "port": 5600}])
        self.assertIn("camera stream FAILED", "\n".join(logs.output))
        self.assertIn("WARNING", "\n".join(logs.output))


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
