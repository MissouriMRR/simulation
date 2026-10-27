"""The helpers `cmd_up` uses to apply the command-line options, and the pieces `config.validate` and `config.load` are
made of."""
import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths


def default_config() -> config.Config:
    return config.load(paths.repo_root() / "config" / "mrrsim.toml")


def options(**kw) -> argparse.Namespace:
    base = dict(script=None, project=None, vehicles=None, script_args=None, resolution=None, fullscreen=False)
    base.update(kw)
    return argparse.Namespace(**base)


class ApplyOptions(unittest.TestCase):
    def setUp(self):
        self.cfg = default_config()

    def test_vehicles_given_on_the_command_line_win_over_the_scripts_header(self):
        info = mock.Mock(vehicles=3)
        with mock.patch.object(cli, "_override_script", return_value=info):
            cli._apply_script_and_vehicles(self.cfg, options(script="a.py", vehicles=5))
        self.assertEqual(self.cfg.sitl.vehicles, 5)

    def test_the_script_may_ask_for_its_vehicles_and_the_launcher_says_so(self):
        info = mock.Mock(vehicles=3)
        info.name = "fleet.py"
        with mock.patch.object(cli, "_override_script", return_value=info), mock.patch("builtins.print") as shown:
            cli._apply_script_and_vehicles(self.cfg, options(script="fleet.py"))
        self.assertEqual(self.cfg.sitl.vehicles, 3)
        self.assertIn("fleet.py asks for 3 vehicle(s)", shown.call_args.args[0])

    def test_without_a_script_or_a_number_the_vehicle_count_is_left_alone(self):
        before = self.cfg.sitl.vehicles
        cli._apply_script_and_vehicles(self.cfg, options())
        self.assertEqual(self.cfg.sitl.vehicles, before)

    def test_script_arguments_are_split_and_the_spawn_pose_is_taken_out(self):
        pose = cli._apply_script_arguments(self.cfg, options(script_args='--airsim --position 1 2 3 --name "two words"'))
        self.assertEqual(self.cfg.scripts.args, ["--airsim", "--name", "two words"])
        self.assertEqual(pose.position, (1.0, 2.0, 3.0))

    def test_no_script_arguments_means_no_pose(self):
        self.cfg.scripts.args = []
        pose = cli._apply_script_arguments(self.cfg, options())
        self.assertFalse(pose)

    def test_a_broken_quote_and_a_pose_without_airsim_are_config_errors(self):
        with self.assertRaises(config.ConfigError) as caught:
            cli._apply_script_arguments(self.cfg, options(script_args='--name "oops'))
        self.assertIn("--script-args", str(caught.exception))
        with self.assertRaises(config.ConfigError) as caught:
            cli._apply_script_arguments(self.cfg, options(script_args="--position 1 2 3"))
        self.assertIn("need --airsim", str(caught.exception))

    def test_resolution_and_fullscreen(self):
        cli._apply_window_options(self.cfg, options(resolution="960x540", fullscreen=True))
        self.assertEqual((self.cfg.unreal.width, self.cfg.unreal.height, self.cfg.unreal.windowed), (960, 540, False))
        other = default_config()
        cli._apply_window_options(other, options())
        self.assertTrue(other.unreal.windowed)


class ValueChecks(unittest.TestCase):
    def test_a_bool_is_not_a_number_or_a_whole_number(self):
        for value in (True, False, "1", None, [1]):
            self.assertFalse(config._is_number(value), value)
            self.assertFalse(config._is_whole_number(value), value)
        for value in (0, 1, -3, 2.5):
            self.assertTrue(config._is_number(value), value)
        for value in (0, 1, -3):
            self.assertTrue(config._is_whole_number(value), value)
        self.assertFalse(config._is_whole_number(2.5))

    def test_a_true_port_is_refused(self):
        cfg = default_config()
        cfg.unreal.topic_port = True
        with self.assertRaises(config.ConfigError) as caught:
            cfg.validate()
        self.assertIn("topic_port must be a TCP port", str(caught.exception))

    def test_every_problem_is_reported_together_in_the_order_of_the_sections(self):
        cfg = default_config()
        cfg.sitl.speedup = -1
        cfg.wsl.processors = -3
        cfg.unreal.width = 1
        cfg.unreal.args = "n"
        with self.assertRaises(config.ConfigError) as caught:
            cfg.validate()
        text = str(caught.exception)
        self.assertLess(text.index("[sitl] speedup"), text.index("[wsl] processors"))
        self.assertLess(text.index("[wsl] processors"), text.index("[unreal] width"))
        self.assertLess(text.index("[unreal] width"), text.index("[unreal] args"))


class LoadingSteps(unittest.TestCase):
    def test_reading_names_the_file_when_it_is_not_toml_or_not_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.toml"
            bad.write_text("[wsl\n", encoding="utf-8")
            with self.assertRaises(config.ConfigError) as caught:
                config._read_toml(bad)
            self.assertIn(str(bad), str(caught.exception))
            binary = Path(tmp) / "binary.toml"
            binary.write_bytes(b"\xff\xfe[\x00w\x00")
            with self.assertRaises(config.ConfigError) as caught:
                config._read_toml(binary)
            self.assertIn("not a UTF-8 text file", str(caught.exception))

    def test_an_unknown_section_gets_a_did_you_mean_hint(self):
        with self.assertRaises(config.ConfigError) as caught:
            config._check_section_names({"sitll": {}})
        self.assertIn("did you mean '[sitl]'", str(caught.exception))
        config._check_section_names({"sitl": {}, "wsl": {}})

    def test_relative_paths_are_resolved_against_the_settings_files_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            cfg = config.Config(source=folder / "config" / "mrrsim.toml")
            cfg.scripts.dir = Path("../scripts")
            config._resolve_paths(cfg)
            self.assertEqual(cfg.scripts.dir, (folder / "scripts").resolve())
            self.assertTrue(cfg.unreal.exe.is_absolute() and cfg.scene.config_dir.is_absolute())


if __name__ == "__main__":
    unittest.main()
