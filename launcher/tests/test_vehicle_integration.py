"""A vehicle design as the template of the scene: scenegen, the command line and the container command."""
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
from mrrsim.core import config, paths
from mrrsim.designs import vehicle_design as vd, vehicle_schema as vs
from mrrsim.scene import scenecheck, scenegen

SIM = paths.repo_root() / "sim_config"


def design_doc() -> dict:
    """The provided vehicle with a LiDAR added and the streamed camera left at 1280x720."""
    d = vd.Design(scenecheck.load_jsonc(SIM / "robot_ardu_quadrotor.jsonc"))
    d.add_block(vs.LIDAR)
    d.fix_legacy()
    return d.doc


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.dir = self.tmp / "sim_config"
        self.dir.mkdir()
        for name in ("scene_ardupilot_quadrotor.jsonc", "robot_ardu_quadrotor.jsonc"):
            shutil.copy(SIM / name, self.dir / name)


class Generator(Base):
    def test_a_design_becomes_every_vehicle_of_the_scene(self):
        name = scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 3, design=("my_quad", design_doc()))
        self.assertEqual(name, "scene_ardupilot_3quadrotors_design_my_quad.jsonc")
        self.assertEqual(scenecheck.check_scene(self.dir, name, 3), [])
        actors = scenecheck.load_jsonc(self.dir / name)["actors"]
        self.assertEqual([a["robot-config"] for a in actors],
                         ["robot_design_my_quad.jsonc", "robot_design_my_quad_2.jsonc", "robot_design_my_quad_3.jsonc"])
        for index, actor in enumerate(actors):
            robot = scenecheck.load_jsonc(self.dir / actor["robot-config"])
            self.assertEqual(scenecheck.ardupilot_ports(robot), (9003 + 10 * index, 9002 + 10 * index))
            self.assertIn("lidar", [s["type"] for s in robot["sensors"]])     # the design's own part is on every vehicle

    def test_one_vehicle_with_a_design_is_still_generated(self):
        name = scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 1, design=("q", design_doc()))
        self.assertEqual(name, "scene_ardupilot_1quadrotors_design_q.jsonc")
        robot = scenecheck.load_jsonc(self.dir / "robot_design_q.jsonc")
        self.assertEqual(scenecheck.ardupilot_ports(robot), (9003, 9002))

    def test_the_original_files_are_never_touched(self):
        before = {p.name: p.read_bytes() for p in self.dir.iterdir()}
        scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 2, design=("q", design_doc()))
        for name, data in before.items():
            self.assertEqual((self.dir / name).read_bytes(), data, name)

    def test_generated_files_say_where_they_came_from(self):
        scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 2, design=("q", design_doc()))
        text = (self.dir / "robot_design_q_2.jsonc").read_text(encoding="utf-8")
        self.assertIn(scenegen.MARKER, text)
        self.assertIn("Vehicles tab", text)
        self.assertIn("Vehicles tab", (self.dir / "scene_ardupilot_2quadrotors_design_q.jsonc").read_text(encoding="utf-8"))

    def test_running_again_overwrites_its_own_files_but_not_others(self):
        scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 2, design=("q", design_doc()))
        scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 2, design=("q", design_doc()))   # fine: its own
        (self.dir / "robot_design_z.jsonc").write_text("{}", encoding="utf-8")                           # somebody's own file
        with self.assertRaises(scenegen.SceneGenError):
            scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 1, design=("z", design_doc()))

    def test_without_cameras_the_design_loses_every_camera(self):
        name = scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 1, cameras=False, design=("q", design_doc()))
        robot = scenecheck.load_jsonc(self.dir / "robot_design_q_nocam.jsonc")
        self.assertTrue(all(not s["enabled"] for s in robot["sensors"] if s["type"] == "camera"))
        self.assertTrue(any(s["type"] == "lidar" and s["enabled"] for s in robot["sensors"]))   # other sensors stay on
        self.assertIn("_nocam", name)

    def test_a_streamed_camera_is_sized_to_the_window(self):
        scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 1, window=(960, 540), design=("q", design_doc()))
        robot = scenecheck.load_jsonc(self.dir / "robot_design_q_960x540.jsonc")
        sizes = scenegen.streaming_sizes(robot)
        self.assertEqual(sizes, {(960, 540)})

    def test_a_design_without_ardupilot_is_refused(self):
        doc = design_doc()
        doc["controller"] = {"id": "S", "type": "simple-flight-api", "simple-flight-api-settings": {"actuator-order": []}}
        with self.assertRaises(scenegen.SceneGenError):
            scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 1, design=("q", doc))

    def test_the_design_is_not_changed_by_generating(self):
        doc = design_doc()
        before = copy.deepcopy(doc)
        scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 2, cameras=False, window=(640, 360), design=("q", doc))
        self.assertEqual(doc, before)

    def test_a_hundred_vehicles_of_a_design_pass_the_scene_check(self):
        name = scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 100, design=("q", design_doc()))
        self.assertEqual(scenecheck.check_scene(self.dir, name, 100), [])


class CommandLine(Base):
    def setUp(self):
        super().setUp()
        self.vehicles = self.tmp / "vehicles"
        patch = mock.patch.dict("os.environ", {"MRRSIM_VEHICLES_DIR": str(self.vehicles), "MRRSIM_DATA_DIR": str(self.tmp / "data")})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(cli.session.SHUTTING_DOWN.clear)
        self.cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        self.cfg.scene.config_dir = self.dir
        self.library = vd.Library(self.vehicles, self.dir)

    def args(self, **kw):
        base = dict(scene=None, no_scene=False, no_cameras=False, design=None)
        base.update(kw)
        return mock.Mock(**base)

    def prepare(self, **kw):
        self.cfg.scene.scene = "scene_ardupilot_quadrotor.jsonc"
        with redirect_stdout(io.StringIO()):
            cli._prepare_scene(self.cfg, self.args(**kw))
        return self.cfg.scene.scene

    def test_design_flag_selects_the_designs_scene(self):
        self.library.create("My quad", design_doc())
        self.cfg.sitl.vehicles = 2
        self.assertEqual(self.prepare(design="My quad"), "scene_ardupilot_2quadrotors_design_my_quad.jsonc")
        self.assertTrue((self.dir / "robot_design_my_quad_2.jsonc").is_file())

    def test_without_the_flag_nothing_changes(self):
        self.library.create("My quad", design_doc())
        self.assertEqual(self.prepare(), "scene_ardupilot_quadrotor.jsonc")

    def test_an_unknown_design_is_refused(self):
        with self.assertRaises(config.ConfigError) as caught:
            self.prepare(design="Ghost")
        self.assertIn("Ghost", str(caught.exception))

    def test_a_design_with_errors_is_refused_and_the_errors_are_listed(self):
        doc = design_doc()
        del doc["sensors"][2]["accelerometer"]                       # the IMU loses a required group
        self.library.create("Broken", doc)
        with self.assertRaises(config.ConfigError) as caught:
            self.prepare(design="Broken")
        self.assertIn("accelerometer", str(caught.exception))

    def test_a_design_that_cannot_fly_here_is_refused(self):
        doc = design_doc()
        doc["sensors"] = [s for s in doc["sensors"] if s["type"] != "imu"]
        self.library.create("NoImu", doc)
        with self.assertRaises(config.ConfigError) as caught:
            self.prepare(design="NoImu")
        self.assertIn("IMU", str(caught.exception))

    def test_design_and_scene_do_not_mix(self):
        self.library.create("Q", design_doc())
        with self.assertRaises(config.ConfigError):
            self.prepare(design="Q", scene="scene_ardupilot_quadrotor.jsonc")

    def test_designs_command_lists_designs_and_the_provided_vehicle(self):
        self.library.create("Good", design_doc())
        bad = design_doc()
        bad["controller"] = {"id": "S", "type": "simple-flight-api", "simple-flight-api-settings": {"actuator-order": []}}
        self.library.create("Other controller", bad)
        self.library.select("Good")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.cmd_designs(self.cfg, mock.Mock()), 0)
        text = out.getvalue()
        self.assertIn("Good", text)
        self.assertIn("selected in the window", text)
        self.assertIn("cannot fly here", text)
        self.assertIn("robot_ardu_quadrotor", text)

    def test_end_to_end_dry_run_uses_the_designs_scene(self):
        """`mrrsim run script --design NAME --dry-run`: the planned scene load names the design's scene file."""
        self.library.create("My quad", design_doc())
        script = self.tmp / "s.py"
        script.write_text("print(1)\n", encoding="utf-8")
        argv = ["-c", str(paths.repo_root() / "config" / "mrrsim.toml"), "run", str(script), "--design", "My quad",
                "--dry-run", "--no-unreal", "--vehicles", "2"]
        # the scene folder is the repository's sim_config; a copy is used so the generated files stay out of it
        cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        cfg.scene.config_dir = self.dir
        with redirect_stdout(io.StringIO()), mock.patch.object(cli.config, "load", return_value=cfg), \
                self.assertLogs(level="INFO") as logs:
            code = cli.main(argv)
        self.assertEqual(code, 0)
        self.assertIn("scene_ardupilot_2quadrotors_design_my_quad.jsonc", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
