import shutil
import tempfile
import unittest
from pathlib import Path

from mrrsim.core import paths, ports
from mrrsim.scene import scenecheck, scenegen

BASE = "scene_ardupilot_quadrotor.jsonc"
ROBOT = "robot_ardu_quadrotor.jsonc"


class Generate(unittest.TestCase):
    """Runs against copies of the real files in sim_config/."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for name in (BASE, ROBOT):
            shutil.copy(paths.repo_root() / "sim_config" / name, self.dir / name)

    def test_one_vehicle_uses_the_original_and_writes_nothing(self):
        self.assertEqual(scenegen.generate(self.dir, BASE, 1), BASE)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), sorted([BASE, ROBOT]))

    def test_n_vehicles_pass_the_scene_check(self):
        for count in (2, 3, 5):
            name = scenegen.generate(self.dir, BASE, count)
            self.assertEqual(name, f"scene_ardupilot_{count}quadrotors.jsonc")
            self.assertEqual(scenecheck.check_scene(self.dir, name, count), [])

    def test_actors_are_named_and_spaced(self):
        name = scenegen.generate(self.dir, BASE, 3, spacing_m=4)
        actors = scenecheck.load_jsonc(self.dir / name)["actors"]
        self.assertEqual([a["name"] for a in actors], ["Drone1", "Drone2", "Drone3"])
        self.assertEqual([a["origin"]["xyz"] for a in actors], ["0.0 0.0 -15.0", "0.0 4.0 -15.0", "0.0 8.0 -15.0"])
        self.assertEqual(actors[0]["robot-config"], ROBOT)
        self.assertEqual(actors[2]["robot-config"], "robot_ardu_quadrotor_3.jsonc")

    def test_robot_configs_differ_from_the_template_only_in_ports(self):
        scenegen.generate(self.dir, BASE, 2)
        template = scenecheck.load_jsonc(self.dir / ROBOT)
        copy = scenecheck.load_jsonc(self.dir / "robot_ardu_quadrotor_2.jsonc")
        vp = ports.vehicle_ports(1)
        settings = copy["controller"]["ardupilot-settings"]
        self.assertEqual((settings["ardupilot-udp-port"], settings["local-host-udp-port"]), (vp.sim_in, vp.sim_out))
        settings.update(template["controller"]["ardupilot-settings"])
        self.assertEqual(copy, template)

    def test_scene_settings_come_from_the_base(self):
        name = scenegen.generate(self.dir, BASE, 2)
        base, scene = scenecheck.load_jsonc(self.dir / BASE), scenecheck.load_jsonc(self.dir / name)
        for key in ("clock", "home-geo-point", "scene-type", "id"):
            self.assertEqual(scene[key], base[key])

    def test_regenerating_overwrites_generated_files(self):
        scenegen.generate(self.dir, BASE, 2)
        scenegen.generate(self.dir, BASE, 2)  # no error

    def test_never_overwrites_a_hand_written_file(self):
        (self.dir / "scene_ardupilot_2quadrotors.jsonc").write_text("{ \"mine\": true }")
        with self.assertRaises(scenegen.SceneGenError):
            scenegen.generate(self.dir, BASE, 2)
        self.assertIn("mine", (self.dir / "scene_ardupilot_2quadrotors.jsonc").read_text())

    def test_template_robot_must_be_vehicle_zero(self):
        text = (self.dir / ROBOT).read_text().replace("9003", "9013").replace("9002", "9012")
        (self.dir / ROBOT).write_text(text)
        with self.assertRaises(scenegen.SceneGenError):
            scenegen.generate(self.dir, BASE, 2)

    def test_template_scene_with_two_robots_is_refused(self):
        scenegen.generate(self.dir, BASE, 2)
        with self.assertRaises(scenegen.SceneGenError):
            scenegen.generate(self.dir, "scene_ardupilot_2quadrotors.jsonc", 3)

    def test_no_cameras_for_one_vehicle(self):
        name = scenegen.generate(self.dir, BASE, 1, cameras=False)
        self.assertEqual(name, "scene_ardupilot_1quadrotors_nocam.jsonc")
        self.assertEqual(scenecheck.check_scene(self.dir, name, 1), [])
        actors = scenecheck.load_jsonc(self.dir / name)["actors"]
        self.assertEqual(actors[0]["robot-config"], "robot_ardu_quadrotor_nocam.jsonc")
        robot = scenecheck.load_jsonc(self.dir / "robot_ardu_quadrotor_nocam.jsonc")
        cameras = [s for s in robot["sensors"] if s["type"] == "camera"]
        self.assertEqual(len(cameras), 2)
        for camera in cameras:
            self.assertFalse(camera["enabled"])
            for setting in camera["capture-settings"]:
                self.assertFalse(setting["capture-enabled"] or setting["streaming-enabled"])
        # everything that is not a camera is untouched
        original = scenecheck.load_jsonc(self.dir / ROBOT)
        self.assertEqual([s for s in robot["sensors"] if s["type"] != "camera"],
                         [s for s in original["sensors"] if s["type"] != "camera"])
        self.assertEqual(robot["actuators"], original["actuators"])

    def test_no_cameras_for_several_vehicles_leaves_the_originals_alone(self):
        before = {n: (self.dir / n).read_text() for n in (BASE, ROBOT)}
        name = scenegen.generate(self.dir, BASE, 3, cameras=False)
        self.assertEqual(name, "scene_ardupilot_3quadrotors_nocam.jsonc")
        self.assertEqual(scenecheck.check_scene(self.dir, name, 3), [])
        configs = [a["robot-config"] for a in scenecheck.load_jsonc(self.dir / name)["actors"]]
        self.assertEqual(configs, ["robot_ardu_quadrotor_nocam.jsonc", "robot_ardu_quadrotor_nocam_2.jsonc",
                                   "robot_ardu_quadrotor_nocam_3.jsonc"])
        for config_name in configs:
            robot = scenecheck.load_jsonc(self.dir / config_name)
            self.assertTrue(all(not s["enabled"] for s in robot["sensors"] if s["type"] == "camera"))
        self.assertEqual(before, {n: (self.dir / n).read_text() for n in (BASE, ROBOT)})

    def test_cameras_stay_on_by_default(self):
        scenegen.generate(self.dir, BASE, 2)
        robot = scenecheck.load_jsonc(self.dir / "robot_ardu_quadrotor_2.jsonc")
        self.assertTrue(all(s["enabled"] for s in robot["sensors"] if s["type"] == "camera"))

    def test_window_size_is_matched_by_a_copy_with_the_streamed_camera_resized(self):
        name = scenegen.generate(self.dir, BASE, 1, window=(640, 360))
        self.assertEqual(name, "scene_ardupilot_1quadrotors_640x360.jsonc")
        self.assertEqual(scenecheck.check_scene(self.dir, name, 1), [])
        robot = scenecheck.load_jsonc(self.dir / "robot_ardu_quadrotor_640x360.jsonc")
        self.assertEqual(scenegen.streaming_sizes(robot), {(640, 360)})
        by_id = {sensor["id"]: sensor for sensor in robot["sensors"]}
        down = by_id["DownCamera"]["capture-settings"]  # not streamed: its size is the script's business
        self.assertEqual({(c["width"], c["height"]) for c in down}, {(640, 480)})
        self.assertTrue(by_id["DownCamera"]["enabled"] and by_id["Chase"]["enabled"])
        self.assertEqual(scenegen.streaming_sizes(scenecheck.load_jsonc(self.dir / ROBOT)), {(1280, 720)})

    def test_a_window_the_size_of_the_stream_needs_no_copy(self):
        self.assertFalse(scenegen.display_resize_needed(self.dir, BASE, (1280, 720)))
        self.assertEqual(scenegen.generate(self.dir, BASE, 1, window=(1280, 720)), BASE)
        self.assertTrue(scenegen.display_resize_needed(self.dir, BASE, (960, 540)))

    def test_unreadable_scene_never_asks_for_a_copy(self):
        (self.dir / "broken.jsonc").write_text("{ nope")
        self.assertFalse(scenegen.display_resize_needed(self.dir, "broken.jsonc", (640, 360)))
        self.assertFalse(scenegen.display_resize_needed(self.dir, "missing.jsonc", (640, 360)))

    def test_window_size_with_several_vehicles(self):
        name = scenegen.generate(self.dir, BASE, 2, window=(960, 540))
        self.assertEqual(name, "scene_ardupilot_2quadrotors_960x540.jsonc")
        configs = [a["robot-config"] for a in scenecheck.load_jsonc(self.dir / name)["actors"]]
        self.assertEqual(configs, ["robot_ardu_quadrotor_960x540.jsonc", "robot_ardu_quadrotor_960x540_2.jsonc"])
        for config_name in configs:
            self.assertEqual(scenegen.streaming_sizes(scenecheck.load_jsonc(self.dir / config_name)), {(960, 540)})
        self.assertEqual(scenecheck.check_scene(self.dir, name, 2), [])

    def test_without_cameras_the_window_size_is_irrelevant(self):
        name = scenegen.generate(self.dir, BASE, 1, cameras=False, window=(640, 360))
        self.assertEqual(name, "scene_ardupilot_1quadrotors_nocam.jsonc")

    def test_count_out_of_range(self):
        with self.assertRaises(scenegen.SceneGenError):
            scenegen.generate(self.dir, BASE, 0)


if __name__ == "__main__":
    unittest.main()
