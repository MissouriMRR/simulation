"""A camera that sends its pictures to the running script over UDP: the setting, the checks and the launcher's wiring.
(The receiver the script imports is tested by test_camera_receiver.py, and the relay that runs inside the scene
loader's container by test_camera_relay_container.py.)"""
import copy
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths, ports
from mrrsim.designs import vehicle_design as vd, vehicle_schema as vs
from mrrsim.runtime import session
from mrrsim.scene import camerastream, scenecheck

SIM = paths.repo_root() / "sim_config"


def down_camera(doc: dict) -> dict:
    """The camera block of the provided vehicle that the tests switch streaming on for."""
    return next(s for s in doc["sensors"] if s["id"] == "DownCamera")


def design_with_stream(**stream) -> dict:
    doc = scenecheck.load_jsonc(SIM / "robot_ardu_quadrotor.jsonc")
    down_camera(doc)[camerastream.STREAM_KEY] = {"enabled": True, "port": 5601, **stream}
    return doc


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
        down_camera(doc)[camerastream.STREAM_KEY]["enabled"] = False
        self.assertEqual(camerastream.streams(doc, 1), [])
        doc = design_with_stream()
        down_camera(doc)["enabled"] = False
        self.assertEqual(camerastream.streams(doc, 1), [])

    def test_stripping_removes_only_the_launchers_key_and_copies(self):
        doc = design_with_stream()
        before = copy.deepcopy(doc)
        clean = camerastream.strip(doc)
        self.assertEqual(doc, before)
        self.assertNotIn(camerastream.STREAM_KEY, json.dumps(clean))
        original = {k: v for k, v in down_camera(before).items() if k != camerastream.STREAM_KEY}
        self.assertEqual(down_camera(clean), original)


def with_second_camera(doc: dict) -> dict:
    """`doc` with a copy of its streaming camera, called Front, after it."""
    front = copy.deepcopy(down_camera(doc))
    front["id"] = "Front"
    doc["sensors"].append(front)
    return doc


class Predicates(unittest.TestCase):
    def test_a_camera_is_set_to_stream_and_streams_unless_it_is_switched_off(self):
        block = {"type": "camera", camerastream.STREAM_KEY: {"enabled": True}}
        self.assertTrue(camerastream.set_to_stream(block))
        self.assertTrue(camerastream.is_streaming(block))
        off = {**block, "enabled": False}
        self.assertTrue(camerastream.set_to_stream(off))
        self.assertFalse(camerastream.is_streaming(off))

    def test_anything_else_is_neither(self):
        group = {camerastream.STREAM_KEY: {"enabled": True}}
        for odd in (None, "camera", [], {}, {"type": "camera"}, {"type": "lidar", **group},
                    {"type": "camera", camerastream.STREAM_KEY: "yes"},
                    {"type": "camera", camerastream.STREAM_KEY: {"enabled": "yes"}},
                    {"type": "camera", camerastream.STREAM_KEY: {"enabled": False}}):
            self.assertFalse(camerastream.set_to_stream(odd), odd)
            self.assertFalse(camerastream.is_streaming(odd), odd)

    def test_the_edges_of_the_reserved_ports(self):
        for low, high, owner in camerastream.RESERVED_PORTS:
            self.assertEqual((camerastream.reserved_port(low), camerastream.reserved_port(high)), (owner, owner))
            self.assertEqual((camerastream.reserved_port(low - 1), camerastream.reserved_port(high + 1)), ("", ""), owner)
        self.assertEqual(camerastream.reserved_port(camerastream.DEFAULTS["port"]), "")

    def test_only_whole_numbers_can_be_reserved(self):
        for odd in (True, 5762.0, "5762", None, [5762]):
            self.assertEqual(camerastream.reserved_port(odd), "", odd)

    def test_the_ports_of_every_vehicle_the_launcher_can_start_are_reserved(self):
        for index in range(ports.MAX_VEHICLES):
            vp = ports.vehicle_ports(index)
            for port in (vp.sim_in, vp.sim_out, vp.dronekit, vp.monitor):
                self.assertTrue(camerastream.reserved_port(port), (index, port))


class StreamsOfOddDesigns(unittest.TestCase):
    def test_nothing_to_stream(self):
        for doc in ({}, {"sensors": None}, {"sensors": []}, {"sensors": [1, None, "x"]}):
            self.assertEqual(camerastream.streams(doc, 2), [], doc)
        self.assertEqual(camerastream.streams(design_with_stream(), 0), [])

    def test_the_streams_come_camera_by_camera_each_for_every_vehicle(self):
        found = camerastream.streams(with_second_camera(design_with_stream()), 2)
        self.assertEqual([(s["camera"], s["vehicle"]) for s in found],
                         [("DownCamera", 0), ("DownCamera", 1), ("Front", 0), ("Front", 1)])


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
        self.assertEqual([i.message for i in self.issues(design_with_stream()) if i.level == "error"], [])

    def test_the_chosen_capture_must_send_images_to_scripts(self):
        doc = design_with_stream(**{"image-type": 3})            # the DownCamera has no segmentation capture
        errors = [i for i in self.issues(doc) if i.level == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("Send images to scripts", errors[0].message)
        doc = design_with_stream()
        down_camera(doc)["capture-settings"][0]["capture-enabled"] = False
        self.assertEqual(len([i for i in self.issues(doc) if i.level == "error"]), 1)

    def test_a_port_the_simulator_uses_is_refused(self):
        for port in (8989, 5762, 9003, 14550, 5802, 5830, 9103, 9503):    # the last four belong to vehicles 4, 7, 10 and 50
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
        down_camera(doc)["enabled"] = False
        levels ={i.level for i in vd.Design(doc).issues() if "sends no pictures" in i.message}
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

    def test_every_camera_is_told_once_however_many_vehicles(self):
        self.library.create("Spy", with_second_camera(design_with_stream(port=5620)))
        self.cfg.sitl.vehicles = 3
        said = self.prepare()
        self.assertEqual(len(self.cfg.scene.camera_streams), 6)
        self.assertEqual(said.count("sends its pictures"), 2)
        self.assertIn("DownCamera sends", said)
        self.assertIn("Front sends", said)

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

        with mock.patch.object(sess.podman, "run_detached", side_effect=spy), self.assertLogs("mrrsim", level="INFO") as logs:
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
                self.assertLogs("mrrsim", level="INFO"):
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
                self.assertLogs("mrrsim", level="INFO"):
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
        with self.assertLogs("mrrsim", level="INFO") as logs:
            sess._report_camera_streams(log, [{"camera": "C", "port": 5600}])
        self.assertIn("camera stream FAILED", "\n".join(logs.output))
        self.assertIn("WARNING", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
