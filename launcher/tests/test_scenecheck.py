import json
import tempfile
import unittest
from pathlib import Path

from mrrsim.core import paths
from mrrsim.scene import scenecheck


def robot(sim_in, sim_out, kind="ardupilot-api"):
    return json.dumps({"controller": {"type": kind, "ardupilot-settings": {
        "ardupilot-udp-port": sim_in, "local-host-udp-port": sim_out}}})


class StripJsonc(unittest.TestCase):
    def test_removes_comments_but_not_strings(self):
        text = '{"url": "http://x//y", /* block */ "a": 1, // line\n "b": "/* not a comment */"}'
        self.assertEqual(json.loads(scenecheck.strip_jsonc(text)),
                         {"url": "http://x//y", "a": 1, "b": "/* not a comment */"})

    def test_escaped_quote_inside_string(self):
        self.assertEqual(json.loads(scenecheck.strip_jsonc(r'{"a": "q\" // still string"}')),
                         {"a": 'q" // still string'})


class CheckScene(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def scene(self, *robots):
        actors = [{"type": "robot", "name": n, "robot-config": c} for n, c in robots]
        (self.dir / "scene.jsonc").write_text("// c\n" + json.dumps({"actors": actors}))

    def test_one_vehicle_one_robot_is_fine(self):
        (self.dir / "r0.jsonc").write_text(robot(9003, 9002))
        self.scene(("D1", "r0.jsonc"))
        self.assertEqual(scenecheck.check_scene(self.dir, "scene.jsonc", 1), [])

    def test_second_vehicle_without_a_robot(self):
        (self.dir / "r0.jsonc").write_text(robot(9003, 9002))
        self.scene(("D1", "r0.jsonc"))
        problems = scenecheck.check_scene(self.dir, "scene.jsonc", 2)
        self.assertTrue(any("1 ArduPilot robot(s) but [sitl] vehicles = 2" in p for p in problems), problems)
        self.assertTrue(any("vehicle 1 (sysid 2)" in p and "9013" in p and "9012" in p for p in problems), problems)

    def test_two_vehicles_correct(self):
        (self.dir / "r0.jsonc").write_text(robot(9003, 9002))
        (self.dir / "r1.jsonc").write_text(robot(9013, 9012))
        self.scene(("D1", "r0.jsonc"), ("D2", "r1.jsonc"))
        self.assertEqual(scenecheck.check_scene(self.dir, "scene.jsonc", 2), [])

    def test_copied_robot_config_with_unchanged_ports(self):
        (self.dir / "r0.jsonc").write_text(robot(9003, 9002))
        (self.dir / "r1.jsonc").write_text(robot(9003, 9002))
        self.scene(("D1", "r0.jsonc"), ("D2", "r1.jsonc"))
        problems = scenecheck.check_scene(self.dir, "scene.jsonc", 2)
        self.assertTrue(any("vehicle 1" in p for p in problems), problems)

    def test_extra_robot_is_reported(self):
        (self.dir / "r0.jsonc").write_text(robot(9003, 9002))
        (self.dir / "r1.jsonc").write_text(robot(9013, 9012))
        self.scene(("D1", "r0.jsonc"), ("D2", "r1.jsonc"))
        problems = scenecheck.check_scene(self.dir, "scene.jsonc", 1)
        self.assertTrue(any("only 1 vehicle" in p for p in problems), problems)

    def test_non_ardupilot_robot_is_ignored(self):
        (self.dir / "r0.jsonc").write_text(robot(9003, 9002))
        (self.dir / "simple.jsonc").write_text(robot(1, 2, kind="simple-flight-api"))
        self.scene(("D1", "r0.jsonc"), ("Other", "simple.jsonc"))
        self.assertEqual(scenecheck.check_scene(self.dir, "scene.jsonc", 1), [])

    def test_missing_robot_config(self):
        self.scene(("D1", "nope.jsonc"))
        problems = scenecheck.check_scene(self.dir, "scene.jsonc", 1)
        self.assertTrue(any("nope.jsonc not found" in p for p in problems), problems)

    def test_unparseable_scene(self):
        (self.dir / "scene.jsonc").write_text("{ not json")
        with self.assertRaises(scenecheck.SceneParseError):
            scenecheck.check_scene(self.dir, "scene.jsonc", 1)


class ShippedFiles(unittest.TestCase):
    def test_the_repo_scenes_match_their_vehicle_counts(self):
        sim_config = paths.repo_root() / "sim_config"
        self.assertEqual(scenecheck.check_scene(sim_config, "scene_ardupilot_quadrotor.jsonc", 1), [])


if __name__ == "__main__":
    unittest.main()
