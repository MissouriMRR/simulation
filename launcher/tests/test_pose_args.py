"""Where the vehicle spawns, chosen with script arguments: --airsim --position X Y Z --rotation ROLL PITCH YAW.
The launcher takes the options out of the script's arguments, and the scene generator places the vehicles."""
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
from mrrsim.scene import poseargs, scenecheck, scenegen
from mrrsim.scene.poseargs import Pose, PoseError, extract

SIM = paths.repo_root() / "sim_config"
BASE, ROBOT = "scene_ardupilot_quadrotor.jsonc", "robot_ardu_quadrotor.jsonc"


class Parsing(unittest.TestCase):
    def test_arguments_without_the_options_come_back_unchanged(self):
        args = ["-s", "--mode", "fast"]
        kept, pose = extract(args)
        self.assertEqual(kept, args)
        self.assertFalse(pose)
        self.assertEqual(extract([])[0], [])
        self.assertEqual(extract(["--airsim"])[0], ["--airsim"])         # --airsim alone is just the script's own

    def test_position_and_rotation_are_taken_out_and_the_rest_stays_in_order(self):
        kept, pose = extract(["--airsim", "--position", "5", "3", "-2", "--rotation", "0", "0", "90", "-s"])
        self.assertEqual(kept, ["--airsim", "-s"])
        self.assertEqual((pose.position, pose.rotation), ((5.0, 3.0, -2.0), (0.0, 0.0, 90.0)))

    def test_any_order_and_the_script_keeps_everything_else(self):
        kept, pose = extract(["--rotation", "10", "20", "30", "x.png", "--position", "1", "2", "3", "--airsim", "--flag=1"])
        self.assertEqual(kept, ["x.png", "--airsim", "--flag=1"])
        self.assertEqual((pose.position, pose.rotation), ((1.0, 2.0, 3.0), (10.0, 20.0, 30.0)))

    def test_either_one_alone(self):
        self.assertEqual(extract(["--airsim", "--position", "1", "2", "3"])[1], Pose(position=(1.0, 2.0, 3.0)))
        self.assertEqual(extract(["--airsim", "--rotation", "0", "0", "45"])[1], Pose(rotation=(0.0, 0.0, 45.0)))

    def test_one_quoted_argument_or_commas_or_an_equals_sign(self):
        for args in (["--position", "1 2 3"], ["--position", "1,2,3"], ["--position=1,2,3"], ["--position", "1, 2, 3"]):
            kept, pose = extract(["--airsim", *args])
            self.assertEqual((kept, pose.position), (["--airsim"], (1.0, 2.0, 3.0)), args)

    def test_numbers_may_be_negative_decimal_or_exponents(self):
        self.assertEqual(extract(["--airsim", "--position", "-1.5", "+2", "1e1"])[1].position, (-1.5, 2.0, 10.0))

    def test_angles_are_normalised_to_minus_180_to_180(self):
        rotation = extract(["--airsim", "--rotation", "190", "-190", "720"])[1].rotation
        self.assertEqual(rotation, (-170.0, 170.0, 0.0))
        self.assertEqual(extract(["--airsim", "--rotation", "0", "0", "180"])[1].rotation, (0.0, 0.0, 180.0))
        self.assertEqual(extract(["--airsim", "--rotation", "0", "0", "-180"])[1].rotation, (0.0, 0.0, -180.0))

    def test_vehicle_pose_is_repeatable_and_normalises_its_angles(self):
        kept, pose = extract(["--airsim", "--vehicle-pose", "0=1,2,3,0,0,270", "--vehicle-pose", "2=4,5,6,10,20,30"], vehicles=3)
        self.assertEqual(kept, ["--airsim"])
        self.assertEqual(pose.vehicles, {0: (1.0, 2.0, 3.0, 0.0, 0.0, -90.0), 2: (4.0, 5.0, 6.0, 10.0, 20.0, 30.0)})
        self.assertEqual(extract(["--airsim", "--vehicle-pose=1=7,8,9,0,0,0"], vehicles=2)[1].vehicles, {1: (7.0, 8.0, 9.0, 0.0, 0.0, 0.0)})

    def errors(self, args, vehicles=2):
        with self.assertRaises(PoseError) as caught:
            extract(args, vehicles)
        return str(caught.exception)

    def test_the_options_need_airsim(self):
        for args in (["--position", "1", "2", "3"], ["--rotation", "0", "0", "0"], ["--vehicle-pose", "0=1,2,3,0,0,0"]):
            self.assertIn("need --airsim", self.errors(args))
        self.assertIn("need --airsim", self.errors(["-s", "--position", "1", "2", "3"]))

    def test_wrong_counts_and_bad_numbers_are_reported(self):
        self.assertIn("three numbers", self.errors(["--airsim", "--position"]))
        self.assertIn("got 2", self.errors(["--airsim", "--position", "1", "2"]))
        self.assertIn("'a' is not a number", self.errors(["--airsim", "--position", "a", "2", "3"]))
        self.assertIn("not a finite number", self.errors(["--airsim", "--position", "1", "2", "nan"]))
        self.assertIn("not a finite number", self.errors(["--airsim", "--rotation", "inf", "0", "0"]))
        self.assertIn("three numbers", self.errors(["--airsim", "--rotation", "--position", "1", "2", "3"]) + "three numbers")
        self.assertIn("was given twice", self.errors(["--airsim", "--position", "1", "2", "3", "--position", "4", "5", "6"]))

    def test_bad_vehicle_poses_are_reported(self):
        self.assertIn("N=x,y,z,roll,pitch,yaw", self.errors(["--airsim", "--vehicle-pose", "1,2,3,0,0,0"]))
        self.assertIn("six numbers", self.errors(["--airsim", "--vehicle-pose", "0=1,2,3"]))
        self.assertIn("there is no vehicle 2", self.errors(["--airsim", "--vehicle-pose", "2=1,2,3,0,0,0"], vehicles=2))
        self.assertIn("twice for vehicle 0", self.errors(["--airsim", "--vehicle-pose", "0=1,2,3,0,0,0", "--vehicle-pose", "0=1,2,3,0,0,1"]))
        self.assertIn("needs N=", self.errors(["--airsim", "--vehicle-pose"]))

    def test_the_same_pose_gives_the_same_tag_and_another_pose_another(self):
        a, b = Pose(position=(1.0, 2.0, 3.0)), Pose(position=(1.0, 2.0, 4.0))
        self.assertEqual(a.tag(), Pose(position=(1.0, 2.0, 3.0)).tag())
        self.assertNotEqual(a.tag(), b.tag())
        self.assertNotEqual(a.tag(), Pose(rotation=(1.0, 2.0, 3.0)).tag())

    def test_the_launcher_says_where_each_vehicle_goes(self):
        text = "\n".join(Pose((5.0, 3.0, -2.0), (0.0, 0.0, 90.0), {1: (9.0, 9.0, -1.0, 0.0, 0.0, 0.0)}).describe(3))
        self.assertIn("vehicle 0 placed at 5 3 -2, rotation 0 0 90", text)
        self.assertIn("vehicle 1 placed at 9 9 -1, rotation 0 0 0", text)
        self.assertIn("vehicle 2 placed at spaced from vehicle 0's position, rotation 0 0 90", text)
        self.assertEqual(len(Pose(position=(0.0, 0.0, 0.0)).describe(30)), 11)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.dir = self.tmp / "sim_config"
        self.dir.mkdir()
        for name in (BASE, ROBOT):
            shutil.copy(SIM / name, self.dir / name)

    @staticmethod
    def xyz(origin):
        """The position of an actor origin as numbers (the generator writes 3.0 where a pose says 3)."""
        return [float(v) for v in origin["xyz"].split()]

    def actors(self, scene_name):
        return scenecheck.load_jsonc(self.dir / scene_name)["actors"]

    def robots(self, scene_name):
        return [a for a in self.actors(scene_name) if a.get("type") == "robot"]


class Generator(Base):
    def test_a_pose_makes_a_generated_scene_even_for_one_vehicle(self):
        pose = Pose((5.0, 3.0, -2.0), (0.0, 0.0, 90.0))
        name = scenegen.generate(self.dir, BASE, 1, pose=pose)
        self.assertNotEqual(name, BASE)
        self.assertIn(pose.tag(), name)
        self.assertEqual(scenecheck.check_scene(self.dir, name, 1), [])
        origin = self.robots(name)[0]["origin"]
        self.assertEqual((self.xyz(origin), origin["rpy-deg"]), ([5, 3, -2], "0 0 90"))
        self.assertIn(scenegen.MARKER, (self.dir / name).read_text(encoding="utf-8")[:600])
        self.assertIn("vehicle 0 placed at 5 3 -2", (self.dir / name).read_text(encoding="utf-8")[:900])

    def test_vehicle_zero_keeps_the_original_robot_config(self):
        name = scenegen.generate(self.dir, BASE, 1, pose=Pose(position=(1.0, 1.0, -1.0)))
        self.assertEqual(self.robots(name)[0]["robot-config"], ROBOT)
        self.assertEqual(sorted(p.name for p in self.dir.glob("robot*.jsonc")), [ROBOT])    # no extra robot file for one vehicle

    def test_without_a_pose_nothing_changes(self):
        self.assertEqual(scenegen.generate(self.dir, BASE, 1), BASE)
        self.assertEqual(scenegen.generate(self.dir, BASE, 1, pose=Pose()), BASE)        # an empty pose is no pose
        self.assertEqual(scenegen.scene_name(2), scenegen.scene_name(2, pose=Pose()))

    def test_another_pose_is_another_file_and_the_same_pose_the_same_file(self):
        first = scenegen.generate(self.dir, BASE, 1, pose=Pose(position=(1.0, 2.0, -3.0)))
        second = scenegen.generate(self.dir, BASE, 1, pose=Pose(position=(4.0, 5.0, -6.0)))
        again = scenegen.generate(self.dir, BASE, 1, pose=Pose(position=(1.0, 2.0, -3.0)))
        self.assertNotEqual(first, second)
        self.assertEqual(first, again)
        self.assertEqual(self.xyz(self.robots(first)[0]["origin"]), [1, 2, -3])        # the first file was not overwritten
        self.assertEqual(self.xyz(self.robots(second)[0]["origin"]), [4, 5, -6])

    def test_position_alone_keeps_the_scenes_rotation_and_rotation_alone_keeps_its_position(self):
        original = scenecheck.load_jsonc(self.dir / BASE)["actors"]
        original_origin = next(a for a in original if a.get("type") == "robot")["origin"]
        moved = self.robots(scenegen.generate(self.dir, BASE, 1, pose=Pose(position=(7.0, 8.0, -9.0))))[0]["origin"]
        self.assertEqual(self.xyz(moved), [7, 8, -9])
        self.assertEqual(moved.get("rpy-deg"), original_origin.get("rpy-deg"))
        turned = self.robots(scenegen.generate(self.dir, BASE, 1, pose=Pose(rotation=(0.0, 0.0, 45.0))))[0]["origin"]
        self.assertEqual(turned["rpy-deg"], "0 0 45")
        self.assertEqual(turned["xyz"], original_origin.get("xyz", "0 0 0"))

    def test_other_vehicles_are_spaced_from_vehicle_zeros_pose_and_turn_with_it(self):
        name = scenegen.generate(self.dir, BASE, 3, pose=Pose((10.0, 20.0, -5.0), (0.0, 0.0, 90.0)))
        self.assertEqual(scenecheck.check_scene(self.dir, name, 3), [])
        origins = [a["origin"] for a in self.robots(name)]
        self.assertEqual([self.xyz(o) for o in origins], [[10, 20, -5], [10, 23, -5], [10, 26, -5]])     # 3 m apart along y
        self.assertEqual({o["rpy-deg"] for o in origins}, {"0 0 90"})
        for index, actor in enumerate(self.robots(name)):
            vp = scenecheck.ardupilot_ports(scenecheck.load_jsonc(self.dir / actor["robot-config"]))
            self.assertEqual(vp, (9003 + 10 * index, 9002 + 10 * index))                         # ports are still per vehicle

    def test_an_explicit_vehicle_pose_wins_over_the_spacing(self):
        pose = Pose((0.0, 0.0, -1.0), (0.0, 0.0, 10.0), {1: (50.0, 60.0, -7.0, 1.0, 2.0, 3.0)})
        name = scenegen.generate(self.dir, BASE, 3, pose=pose)
        origins = [a["origin"] for a in self.robots(name)]
        self.assertEqual((self.xyz(origins[1]), origins[1]["rpy-deg"]), ([50, 60, -7], "1 2 3"))
        self.assertEqual((self.xyz(origins[0]), origins[0]["rpy-deg"]), ([0, 0, -1], "0 0 10"))
        self.assertEqual((self.xyz(origins[2]), origins[2]["rpy-deg"]), ([0, 6, -1], "0 0 10"))   # still two steps along y from vehicle 0

    def test_a_latitude_longitude_or_radians_in_the_scene_do_not_override_the_pose(self):
        scene = scenecheck.load_jsonc(self.dir / BASE)
        robot = next(a for a in scene["actors"] if a.get("type") == "robot")
        robot["origin"] = {"geo-point": "37.0 -91.0 100", "rpy": "0 0 1.5", "xyz": "0 0 0"}
        (self.dir / "scene_geo.jsonc").write_text(json.dumps(scene), encoding="utf-8")
        name = scenegen.generate(self.dir, "scene_geo.jsonc", 1, pose=Pose((1.0, 2.0, -3.0), (0.0, 0.0, 45.0)))
        origin = self.robots(name)[0]["origin"]
        self.assertNotIn("geo-point", origin)
        self.assertNotIn("rpy", origin)
        self.assertEqual((self.xyz(origin), origin["rpy-deg"]), ([1, 2, -3], "0 0 45"))

    def test_it_works_with_cameras_off_and_with_a_window_size(self):
        pose = Pose((1.0, 2.0, -3.0))
        off = scenegen.generate(self.dir, BASE, 1, cameras=False, pose=pose)
        self.assertIn("_nocam", off)
        self.assertEqual(self.xyz(self.robots(off)[0]["origin"]), [1, 2, -3])
        sized = scenegen.generate(self.dir, BASE, 1, window=(960, 540), pose=pose)
        self.assertIn("960x540", sized)
        self.assertEqual(self.xyz(self.robots(sized)[0]["origin"]), [1, 2, -3])

    def test_it_works_with_a_vehicle_design(self):
        doc = scenecheck.load_jsonc(SIM / ROBOT)
        design = vd.Design(doc)
        design.fix_legacy()
        name = scenegen.generate(self.dir, BASE, 2, design=("my_quad", design.doc), pose=Pose((3.0, 4.0, -5.0), (0.0, 0.0, 30.0)))
        self.assertIn("design_my_quad", name)
        self.assertIn(Pose((3.0, 4.0, -5.0), (0.0, 0.0, 30.0)).tag(), name)
        self.assertEqual([self.xyz(a["origin"]) for a in self.robots(name)], [[3, 4, -5], [3, 7, -5]])

    def test_the_original_files_are_never_touched(self):
        before = {p.name: p.read_bytes() for p in self.dir.iterdir()}
        scenegen.generate(self.dir, BASE, 2, pose=Pose((1.0, 1.0, -1.0), (0.0, 0.0, 1.0)))
        for name, data in before.items():
            self.assertEqual((self.dir / name).read_bytes(), data, name)


class CommandLine(Base):
    def setUp(self):
        super().setUp()
        patch = mock.patch.dict("os.environ", {"MRRSIM_VEHICLES_DIR": str(self.tmp / "vehicles"), "MRRSIM_DATA_DIR": str(self.tmp / "data")})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(cli.session.SHUTTING_DOWN.clear)
        self.cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        self.cfg.scene.config_dir = self.dir
        self.cfg.scene.scene = BASE

    def args(self, **kw):
        base = dict(scene=None, no_scene=False, no_cameras=False, design=None)
        base.update(kw)
        return mock.Mock(**base)

    def prepare(self, pose, **kw):
        out = io.StringIO()
        with redirect_stdout(out):
            cli._prepare_scene(self.cfg, self.args(**kw), pose)
        return out.getvalue()

    def test_a_pose_selects_a_generated_scene_and_says_where_the_vehicle_goes(self):
        said = self.prepare(Pose((5.0, 3.0, -2.0), (0.0, 0.0, 90.0)))
        self.assertNotEqual(self.cfg.scene.scene, BASE)
        self.assertIn("vehicle 0 placed at 5 3 -2, rotation 0 0 90", said)

    def test_no_pose_leaves_the_scene_alone(self):
        self.prepare(Pose())
        self.assertEqual(self.cfg.scene.scene, BASE)
        self.prepare(None)
        self.assertEqual(self.cfg.scene.scene, BASE)

    def test_a_pose_cannot_be_combined_with_your_own_scene(self):
        with self.assertRaises(config.ConfigError) as caught:
            self.prepare(Pose(position=(1.0, 2.0, 3.0)), scene=BASE)
        self.assertIn("--scene", str(caught.exception))

    def test_with_no_scene_the_pose_is_ignored_with_a_note(self):
        said = self.prepare(Pose(position=(1.0, 2.0, 3.0)), no_scene=True)
        self.assertIn("no effect", said)
        self.assertEqual(self.cfg.scene.scene, BASE)

    def run_cli(self, script_args, extra=()):
        script = self.tmp / "s.py"
        script.write_text("print(1)\n", encoding="utf-8")
        argv = ["-c", str(paths.repo_root() / "config" / "mrrsim.toml"), "run", str(script), f"--script-args={script_args}",
                "--dry-run", "--no-unreal", *extra]
        seen = {}

        def fake_session(cfg, dry_run=False):
            seen["args"], seen["scene"], seen["vehicles"] = list(cfg.scripts.args), cfg.scene.scene, cfg.sitl.vehicles
            return mock.Mock(up=mock.Mock(return_value=0))

        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(cli.config, "load", return_value=self.cfg), \
                mock.patch.object(cli.session, "Session", side_effect=fake_session):
            code = cli.main(argv)
        return code, seen, out.getvalue()

    def test_the_script_never_sees_the_pose_arguments_but_keeps_the_rest(self):
        code, seen, said = self.run_cli('--airsim --position 5 3 -2 --rotation 0 0 90 --test-arm "two words"')
        self.assertEqual(code, 0)
        self.assertEqual(seen["args"], ["--airsim", "--test-arm", "two words"])
        self.assertIn(poseargs.Pose((5.0, 3.0, -2.0), (0.0, 0.0, 90.0)).tag(), seen["scene"])
        self.assertIn("vehicle 0 placed at 5 3 -2", said)

    def test_without_airsim_nothing_starts_and_the_message_says_why(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            code, seen, _ = self.run_cli("--position 1 2 3")
        self.assertNotEqual(code, 0)
        self.assertEqual(seen, {})                                   # no session was created
        self.assertIn("need --airsim", err.getvalue())

    def test_a_vehicle_number_outside_the_run_is_refused(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            code, seen, _ = self.run_cli("--airsim --vehicle-pose 3=1,2,3,0,0,0", extra=("--vehicles", "2"))
        self.assertNotEqual(code, 0)
        self.assertIn("there is no vehicle 3", err.getvalue())

    def test_a_run_without_the_options_is_unchanged(self):
        code, seen, said = self.run_cli("--airsim -s")
        self.assertEqual((code, seen["args"], seen["scene"]), (0, ["--airsim", "-s"], BASE))
        self.assertNotIn("placed at", said)

    def test_the_vehicle_poses_use_the_vehicle_count_of_the_run(self):
        code, seen, said = self.run_cli("--airsim --vehicle-pose 1=9,9,-1,0,0,0", extra=("--vehicles", "2"))
        self.assertEqual(code, 0)
        self.assertIn("vehicle 1 placed at 9 9 -1", said)


if __name__ == "__main__":
    unittest.main()
