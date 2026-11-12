"""--per-vehicle: one copy of a program that flies ONE vehicle (and takes its number as -i N) for each vehicle. The option
is parsed, applied to the settings, and turned into the command of the script container (containers/script/mrrsim_fleet.py
runs the copies)."""
import argparse
import unittest
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
from mrrsim.runtime import session


def default_config() -> config.Config:
    return config.load(paths.repo_root() / "config" / "mrrsim.toml")


def options(**kw) -> argparse.Namespace:
    base = dict(script=None, project=None, vehicles=None, script_args=None, resolution=None, fullscreen=False)
    base.update(kw)
    return argparse.Namespace(**base)


class PerVehicleOption(unittest.TestCase):
    def test_run_and_up_accept_it(self):
        parser = cli.build_parser()
        self.assertTrue(parser.parse_args(["run", "fly.py", "--per-vehicle"]).per_vehicle)
        self.assertFalse(parser.parse_args(["run", "fly.py"]).per_vehicle)
        self.assertTrue(parser.parse_args(["up", "--script", "fly.py", "--per-vehicle"]).per_vehicle)

    def test_it_switches_the_setting_on_and_says_so(self):
        cfg = default_config()
        with mock.patch("builtins.print") as shown:
            cli._apply_script_and_vehicles(cfg, options(vehicles=4, per_vehicle=True))
        self.assertTrue(cfg.scripts.per_vehicle)
        self.assertIn("one copy of the script per vehicle (4)", shown.call_args.args[0])

    def test_without_it_the_setting_stays_off(self):
        cfg = default_config()
        cli._apply_script_and_vehicles(cfg, options(vehicles=4))
        self.assertFalse(cfg.scripts.per_vehicle)

    def test_the_settings_file_can_turn_it_on_too(self):
        cfg = default_config()
        cfg.scripts.per_vehicle = True
        with mock.patch("builtins.print"):
            cli._apply_script_and_vehicles(cfg, options(per_vehicle=False))
        self.assertTrue(cfg.scripts.per_vehicle)


class ScriptCommand(unittest.TestCase):
    def command_of(self, per_vehicle: bool) -> list[str]:
        cfg = default_config()
        cfg.scripts.main = "fly.py"
        cfg.scripts.args = ["--airsim", "-s"]
        cfg.scripts.per_vehicle = per_vehicle
        sess = session.Session(cfg, dry_run=True)
        sess.podman = mock.Mock()
        sess.podman.run_attached.return_value = None
        with mock.patch.object(sess, "_script_mounts", return_value=[]), \
                mock.patch.object(sess, "_script_env", return_value={}):
            self.assertEqual(sess._run_script(), 0)
        return sess.podman.run_attached.call_args.kwargs["command"]

    def test_a_script_is_run_as_it_always_was(self):
        self.assertEqual(self.command_of(False), ["python", "-u", "/work/fly.py", "--airsim", "-s"])

    def test_per_vehicle_hands_the_script_to_the_fleet_runner(self):
        self.assertEqual(self.command_of(True), ["python", "-u", "-m", "mrrsim_fleet", "/work/fly.py", "--airsim", "-s"])


if __name__ == "__main__":
    unittest.main()
