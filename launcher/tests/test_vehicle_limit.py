import shutil
import tempfile
import unittest
from pathlib import Path

from mrrsim.core import config, paths, ports
from mrrsim.scene import scenecheck, scenegen
from mrrsim.ui.run import commands


def load(text: str) -> config.Config:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mrrsim.toml"
        path.write_text(text, encoding="utf-8")
        return config.load(path)


class Limit(unittest.TestCase):
    def test_up_to_a_hundred_vehicles(self):
        self.assertEqual(ports.MAX_VEHICLES, 100)
        self.assertEqual(commands.MAX_VEHICLES, 100)  # the window follows the launcher's limit
        self.assertEqual(load("[sitl]\nvehicles = 100").sitl.vehicles, 100)
        self.assertEqual(load("[sitl]\nvehicles = 1").sitl.vehicles, 1)

    def test_outside_one_to_a_hundred_is_refused(self):
        for count in (0, 101, -3):
            with self.assertRaises(config.ConfigError, msg=count):
                load(f"[sitl]\nvehicles = {count}")

    def test_no_two_vehicles_share_a_port_and_none_touches_unreal(self):
        seen: dict[tuple[str, int], int] = {("tcp", 8989): -1, ("tcp", 8990): -1, ("udp", 14550): -1}
        for vp in ports.all_vehicle_ports(100):
            for key in [("udp", vp.sim_in), ("udp", vp.sim_out), ("tcp", vp.dronekit), ("tcp", vp.monitor),
                        ("tcp", 5760 + 10 * vp.index), ("udp", 5501 + 10 * vp.index)]:  # ArduPilot's own, for -I index
                self.assertNotIn(key, seen, f"vehicle {vp.index} reuses {key} (taken by {seen.get(key)})")
                seen[key] = vp.index
        self.assertEqual(ports.vehicle_ports(99).sysid, 100)
        self.assertEqual(ports.vehicle_ports(99).sim_in, 9993)


class Layout(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for name in ("scene_ardupilot_quadrotor.jsonc", "robot_ardu_quadrotor.jsonc"):
            shutil.copy(paths.repo_root() / "sim_config" / name, self.dir / name)

    def test_vehicles_wrap_into_rows_of_ten(self):
        name = scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 23, spacing_m=3)
        actors = scenecheck.load_jsonc(self.dir / name)["actors"]
        spots = [tuple(float(v) for v in a["origin"]["xyz"].split()) for a in actors]
        self.assertEqual(spots[0], (0.0, 0.0, -15.0))
        self.assertEqual(spots[9], (0.0, 27.0, -15.0))    # the end of the first row
        self.assertEqual(spots[10], (3.0, 0.0, -15.0))    # the second row starts one spacing along x
        self.assertEqual(spots[22], (6.0, 6.0, -15.0))
        self.assertEqual(len(set(spots)), 23)               # nobody spawns on top of anybody

    def test_a_hundred_vehicles_make_a_valid_scene(self):
        name = scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 100)
        self.assertEqual(name, "scene_ardupilot_100quadrotors.jsonc")
        self.assertEqual(scenecheck.check_scene(self.dir, name, 100), [])
        actors = scenecheck.load_jsonc(self.dir / name)["actors"]
        self.assertEqual([a["name"] for a in actors][-1], "Drone100")
        self.assertEqual(len({tuple(a["origin"]["xyz"].split()) for a in actors}), 100)

    def test_a_hundred_and_one_is_refused(self):
        with self.assertRaises(scenegen.SceneGenError):
            scenegen.generate(self.dir, "scene_ardupilot_quadrotor.jsonc", 101)


if __name__ == "__main__":
    unittest.main()
