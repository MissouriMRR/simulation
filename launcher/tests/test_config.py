import tempfile
import unittest
from pathlib import Path

from mrrsim.core import config, paths


def _write(directory: Path, text: str) -> Path:
    path = directory / "mrrsim.toml"
    path.write_text(text, encoding="utf-8")
    return path


class ShippedConfig(unittest.TestCase):
    def test_example_config_loads_and_resolves_paths(self):
        cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        self.assertEqual(cfg.sitl.vehicles, 1)
        self.assertEqual(cfg.sitl.image_ref, "localhost/mrrsim-sitl:copter-4.7.0")
        self.assertEqual(cfg.unreal.exe.name, "Simulation-Win64-Shipping.exe")
        self.assertEqual(cfg.scene.config_dir, (paths.repo_root() / "sim_config").resolve())
        self.assertEqual(cfg.scripts.dir, (paths.repo_root() / "scripts").resolve())

    def test_the_shipped_script_exists(self):
        cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        self.assertTrue((cfg.scripts.dir / cfg.scripts.main).is_file())


class Validation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults_when_file_is_empty(self):
        cfg = config.load(_write(self.dir, ""))
        self.assertEqual(cfg.wsl.distro, "mrr")
        self.assertTrue(cfg.wsl.require_mirrored)
        self.assertEqual(cfg.unreal.exe, (self.dir / "../Windows/Simulation/Binaries/Win64/Simulation-Win64-Shipping.exe").resolve())

    def test_unknown_key_suggests_the_right_one(self):
        with self.assertRaisesRegex(config.ConfigError, "did you mean 'vehicles'"):
            config.load(_write(self.dir, "[sitl]\nvehicals = 2\n"))

    def test_unknown_section(self):
        with self.assertRaisesRegex(config.ConfigError, r"unknown section \[sitll\]"):
            config.load(_write(self.dir, "[sitll]\nvehicles = 2\n"))

    def test_vehicle_count_bounds(self):
        with self.assertRaisesRegex(config.ConfigError, "vehicles"):
            config.load(_write(self.dir, "[sitl]\nvehicles = 0\n"))
        with self.assertRaisesRegex(config.ConfigError, "vehicles"):
            config.load(_write(self.dir, "[sitl]\nvehicles = 999\n"))

    def test_bad_home(self):
        with self.assertRaisesRegex(config.ConfigError, "home"):
            config.load(_write(self.dir, '[sitl]\nhome = "37.9,-91.7"\n'))

    def test_gcs_out_must_be_host_port(self):
        with self.assertRaisesRegex(config.ConfigError, "gcs_out"):
            config.load(_write(self.dir, '[sitl]\ngcs_out = ""\n'))

    def test_explicit_image_beats_derived_tag(self):
        cfg = config.load(_write(self.dir, '[sitl]\nimage = "ghcr.io/x/sitl:1"\n'))
        self.assertEqual(cfg.sitl.image_ref, "ghcr.io/x/sitl:1")

    def test_sitl_params_table(self):
        cfg = config.load(_write(self.dir, "[sitl.params]\nSCHED_LOOP_RATE = 100\nSIM_SPEEDUP = 1.5\n"))
        self.assertEqual(cfg.sitl.params, {"SCHED_LOOP_RATE": 100, "SIM_SPEEDUP": 1.5})

    def test_sitl_params_reject_bad_names_and_values(self):
        with self.assertRaisesRegex(config.ConfigError, "not an ArduPilot parameter name"):
            config.load(_write(self.dir, '[sitl.params]\n"bad name" = 1\n'))
        with self.assertRaisesRegex(config.ConfigError, "must be a number"):
            config.load(_write(self.dir, '[sitl.params]\nSCHED_LOOP_RATE = "fast"\n'))

    def test_broken_toml(self):
        with self.assertRaises(config.ConfigError):
            config.load(_write(self.dir, "[sitl\n"))


if __name__ == "__main__":
    unittest.main()
