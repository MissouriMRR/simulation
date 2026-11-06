import json
import tempfile
import unittest
from pathlib import Path

from mrrsim.core import config, paths
from mrrsim.scene import scenecheck

BS = chr(92)  # a backslash
NL = chr(10)  # a line break


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


class LoadJsonc(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, content) -> Path:
        path = self.dir / "file.jsonc"
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return path

    def test_comments_and_trailing_commas_are_fine(self):
        path = self.write('// note' + NL + '{"a": [1, 2, // two' + NL + '], /* x */ "b": {"c": 1,},}')
        self.assertEqual(scenecheck.load_jsonc(path), {"a": [1, 2], "b": {"c": 1}})

    def test_a_string_ending_in_an_escaped_backslash_does_not_hide_the_comment_after_it(self):
        path = self.write('{"p": "C:' + BS * 2 + 'dir' + BS * 2 + '", "q": 1} // end')
        self.assertEqual(scenecheck.load_jsonc(path), {"p": "C:" + BS + "dir" + BS, "q": 1})

    def test_a_byte_order_mark_is_not_an_error(self):
        path = self.write(bytes([0xEF, 0xBB, 0xBF]) + b'{"a": 1}')
        self.assertEqual(scenecheck.load_jsonc(path), {"a": 1})

    def test_a_file_that_is_not_utf8_is_a_parse_error_that_says_so(self):
        path = self.write(b'{"a": "' + bytes([0xE9]) + b'"}')
        with self.assertRaises(scenecheck.SceneParseError) as caught:
            scenecheck.load_jsonc(path)
        self.assertIn("UTF-8", str(caught.exception))

    def test_something_other_than_an_object_is_a_parse_error(self):
        for text in ("[1, 2]", '"text"', "7", "null"):
            with self.assertRaises(scenecheck.SceneParseError, msg=text) as caught:
                scenecheck.load_jsonc(self.write(text))
            self.assertIn("JSON object", str(caught.exception))

    def test_a_missing_file_and_broken_json_are_parse_errors(self):
        with self.assertRaises(scenecheck.SceneParseError):
            scenecheck.load_jsonc(self.dir / "nope.jsonc")
        with self.assertRaises(scenecheck.SceneParseError):
            scenecheck.load_jsonc(self.write("{ not json"))


class RobotActors(unittest.TestCase):
    def test_only_the_robots_are_returned(self):
        robot = {"type": "robot", "name": "D1"}
        scene = {"actors": [robot, {"type": "env-actor"}, "stray", None, 3, {"name": "no type"}]}
        self.assertEqual(scenecheck.robot_actors(scene), [robot])

    def test_actors_that_are_missing_or_not_a_list_mean_no_robots(self):
        for scene in ({}, {"actors": None}, {"actors": "x"}, {"actors": {"type": "robot"}}):
            self.assertEqual(scenecheck.robot_actors(scene), [], scene)


class ArdupilotPorts(unittest.TestCase):
    @staticmethod
    def config(settings, kind="ardupilot-api"):
        return {"controller": {"type": kind, "ardupilot-settings": settings}}

    def test_both_ports_as_whole_numbers(self):
        settings = {"ardupilot-udp-port": 9003, "local-host-udp-port": 9002}
        self.assertEqual(scenecheck.ardupilot_ports(self.config(settings)), (9003, 9002))

    def test_anything_else_is_none(self):
        good = {"ardupilot-udp-port": 9003, "local-host-udp-port": 9002}
        cases = {
            "another controller": self.config(good, kind="simple-flight-api"),
            "no controller": {},
            "controller that is not an object": {"controller": []},
            "no settings": {"controller": {"type": "ardupilot-api"}},
            "settings that are not an object": {"controller": {"type": "ardupilot-api", "ardupilot-settings": 5}},
            "no ports": self.config({}),
            "one port": self.config({"ardupilot-udp-port": 9003}),
            "ports as text": self.config({"ardupilot-udp-port": "9003", "local-host-udp-port": "9002"}),
            "ports that are booleans": self.config({"ardupilot-udp-port": True, "local-host-udp-port": False}),
        }
        for label, config in cases.items():
            self.assertIsNone(scenecheck.ardupilot_ports(config), label)


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

    def test_robots_that_share_their_ports_are_named(self):
        (self.dir / "r0.jsonc").write_text(robot(9003, 9002))
        (self.dir / "r1.jsonc").write_text(robot(9003, 9002))
        self.scene(("D1", "r0.jsonc"), ("D2", "r1.jsonc"))
        problems = scenecheck.check_scene(self.dir, "scene.jsonc", 2)
        self.assertTrue(any("robots D1, D2 use the same ports" in p and "9003" in p for p in problems), problems)
        self.assertFalse(any("scene has" in p for p in problems), problems)  # there are two robots, as many as vehicles

    def test_an_ardupilot_robot_without_both_ports_is_explained(self):
        (self.dir / "r0.jsonc").write_text(json.dumps({"controller": {"type": "ardupilot-api"}}))
        self.scene(("D1", "r0.jsonc"))
        problems = scenecheck.check_scene(self.dir, "scene.jsonc", 1)
        self.assertTrue(any("robot D1" in p and "ardupilot-udp-port" in p and "r0.jsonc" in p for p in problems), problems)

    def test_a_scene_with_actors_that_are_not_a_list_has_no_robots(self):
        (self.dir / "scene.jsonc").write_text('{"actors": null}')
        problems = scenecheck.check_scene(self.dir, "scene.jsonc", 1)
        self.assertTrue(any("0 ArduPilot robot(s)" in p for p in problems), problems)

    def test_a_scene_or_robot_config_that_is_not_an_object_is_a_parse_error(self):
        (self.dir / "scene.jsonc").write_text("[1]")
        with self.assertRaises(scenecheck.SceneParseError):
            scenecheck.check_scene(self.dir, "scene.jsonc", 1)
        (self.dir / "r0.jsonc").write_text("[1]")
        self.scene(("D1", "r0.jsonc"))
        with self.assertRaises(scenecheck.SceneParseError):
            scenecheck.check_scene(self.dir, "scene.jsonc", 1)

    def test_unparseable_scene(self):
        (self.dir / "scene.jsonc").write_text("{ not json")
        with self.assertRaises(scenecheck.SceneParseError):
            scenecheck.check_scene(self.dir, "scene.jsonc", 1)


class HomeProblem(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def scene(self, **home_point):
        doc = {"actors": []}
        if home_point:
            doc["home-geo-point"] = home_point
        (self.dir / "scene.jsonc").write_text("// c\n" + json.dumps(doc))

    def problem(self, home):
        return scenecheck.home_problem(self.dir, "scene.jsonc", home)

    def test_the_same_point_is_fine_even_written_shorter(self):
        self.scene(latitude=37.94894101091474, longitude=-91.78455965055593, altitude=15.0)
        self.assertIsNone(self.problem("37.94894101,-91.78455965,15,0"))
        self.assertIsNone(self.problem("37.94894101091474,-91.78455965055593,15.005,270"))   # the heading is the vehicle's, not the scene's

    def test_another_point_is_reported_with_the_line_to_use_and_the_heading_kept(self):
        self.scene(latitude=37.94894101091474, longitude=-91.78455965055593, altitude=15.0)
        for home in ("37.9490953,-91.7848293,15,90", "37.94894101,-91.78455965,0,90", "37.94894101,-91.7846,15,90"):
            said = self.problem(home)
            self.assertIn("home-geo-point is 37.94894101,-91.78455965,15 but", said, home)
            self.assertIn(f"is {home}", said)
            self.assertIn('[sitl] home = "37.94894101,-91.78455965,15,90"', said, home)

    def test_a_scene_without_a_home_point_or_with_a_broken_one_is_not_a_problem(self):
        self.scene()
        self.assertIsNone(self.problem("37.9,-91.7,0,0"))
        self.scene(latitude="north", longitude=-91.7, altitude=0)
        self.assertIsNone(self.problem("37.9,-91.7,0,0"))
        self.scene(latitude=37.9)
        self.assertIsNone(self.problem("37.9,-91.7,0,0"))

    def test_a_home_that_cannot_be_read_is_left_to_the_config_check(self):
        self.scene(latitude=37.9, longitude=-91.7, altitude=0)
        for home in ("", "37.9,-91.7", "a,b,c,d"):
            self.assertIsNone(self.problem(home), home)

    def test_a_scene_that_cannot_be_read_raises(self):
        (self.dir / "scene.jsonc").write_text("{ not json")
        with self.assertRaises(scenecheck.SceneParseError):
            self.problem("37.9,-91.7,0,0")


class ShippedFiles(unittest.TestCase):
    def test_the_repo_scenes_match_their_vehicle_counts(self):
        sim_config = paths.repo_root() / "sim_config"
        self.assertEqual(scenecheck.check_scene(sim_config, "scene_ardupilot_quadrotor.jsonc", 1), [])

    def test_the_shipped_home_point_is_the_shipped_scenes(self):
        cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        self.assertIsNone(scenecheck.home_problem(cfg.scene.config_dir, cfg.scene.scene, cfg.sitl.home))


if __name__ == "__main__":
    unittest.main()
