import argparse
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
from mrrsim.userscripts import scripts


def write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


class Describe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_description_is_the_first_docstring_line(self):
        info = scripts.describe(write(self.dir, "a.py", '"""Fly a square.\n\nMore detail."""\nprint(1)\n'))
        self.assertEqual(info.description, "Fly a square.")
        self.assertFalse(info.needs_airsim)
        self.assertIsNone(info.vehicles)

    def test_a_paragraph_over_several_lines_is_one_description(self):
        info = scripts.describe(write(self.dir, "p.py", '"""Fly a square:\narm, go, land.\n\nSecond paragraph."""\n'))
        self.assertEqual(info.description, "Fly a square: arm, go, land.")

    def test_projectairsim_imports_are_detected(self):
        for source in ("import projectairsim\n", "from projectairsim import World\n", "import projectairsim.utils as u\n",
                       "def f():\n    from projectairsim.types import Pose\n"):
            self.assertTrue(scripts.describe(write(self.dir, "b.py", source)).needs_airsim, source)
        self.assertFalse(scripts.describe(write(self.dir, "c.py", "import dronekit  # projectairsim\n")).needs_airsim)

    def test_vehicles_header(self):
        self.assertEqual(scripts.describe(write(self.dir, "d.py", "# mrrsim: vehicles=3\nx = 1\n")).vehicles, 3)
        self.assertEqual(scripts.describe(write(self.dir, "e.py", "#MRRSIM: Vehicles = 2\n")).vehicles, 2)
        self.assertIsNone(scripts.describe(write(self.dir, "f.py", "# mrrsim: vehicles=lots\n")).vehicles)
        self.assertIsNone(scripts.describe(write(self.dir, "g.py", "# mrrsim: vehicles=0\n")).vehicles)
        late = "\n" * 40 + "# mrrsim: vehicles=4\n"
        self.assertIsNone(scripts.describe(write(self.dir, "h.py", late)).vehicles)

    def test_a_script_with_a_syntax_error_is_still_listed(self):
        info = scripts.describe(write(self.dir, "bad.py", "import projectairsim\ndef (:\n"))
        self.assertIn("syntax error", info.problem)
        self.assertTrue(info.needs_airsim)


class Discover(unittest.TestCase):
    def test_groups_order_and_filtering(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            mine, examples = Path(a), Path(b)
            write(mine, "zeta.py", "")
            write(mine, "Alpha.py", "")
            write(mine, "_helper.py", "")
            write(mine, "notes.txt", "")
            write(examples, "demo.py", "")
            found = scripts.discover(mine, examples)
            self.assertEqual([(i.name, i.group) for i in found],
                             [("Alpha.py", "mine"), ("zeta.py", "mine"), ("demo.py", "examples")])

    def test_missing_folders_are_fine(self):
        self.assertEqual(scripts.discover(Path("Z:/nope"), None), [])

    def test_copy_never_overwrites(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            source = write(Path(b), "demo.py", "print('x')\n")
            info = scripts.describe(source, scripts.EXAMPLES)
            mine = Path(a) / "scripts"  # does not exist yet
            names = [scripts.copy_to(info, mine).name for _ in range(3)]
            self.assertEqual(names, ["demo.py", "demo_copy.py", "demo_copy2.py"])
            self.assertEqual((mine / "demo_copy.py").read_text(), "print('x')\n")


class RunCommandChoices(unittest.TestCase):
    """What `run` / `up --script` work out from the script file itself."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")

    def test_airsim_script_switches_the_image(self):
        script = write(self.dir, "cam.py", "from projectairsim import World\n")
        self.assertNotEqual(self.cfg.scripts.image, self.cfg.scene.image)
        info = cli._override_script(self.cfg, str(script))
        self.assertTrue(info.needs_airsim)
        self.assertEqual(self.cfg.scripts.image, self.cfg.scene.image)
        self.assertEqual((self.cfg.scripts.dir, self.cfg.scripts.main), (self.dir.resolve(), "cam.py"))

    def test_plain_script_keeps_the_light_image(self):
        before = self.cfg.scripts.image
        cli._override_script(self.cfg, str(write(self.dir, "plain.py", "import dronekit\n")))
        self.assertEqual(self.cfg.scripts.image, before)

    def test_a_name_is_found_in_the_students_folder(self):
        write(self.dir, "mine_only.py", "x = 1\n")
        with mock.patch.dict("os.environ", {"MRRSIM_USER_SCRIPTS": str(self.dir)}):
            cli._override_script(self.cfg, "mine_only.py")
        self.assertEqual((self.cfg.scripts.dir, self.cfg.scripts.main), (self.dir, "mine_only.py"))

    def test_unknown_script_is_a_config_error(self):
        with self.assertRaises(config.ConfigError):
            cli._override_script(self.cfg, "no_such_script_anywhere.py")

    def test_shipped_two_vehicle_example_asks_for_two(self):
        info = scripts.describe(paths.repo_root() / "scripts" / "takeoff_land_all.py")
        self.assertEqual(info.vehicles, 2)


class StopFileAfterShutdownBegan(unittest.TestCase):
    def test_requests_are_ignored_once_the_shut_down_has_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stop.request"
            hits = []
            real_sleep = time.sleep
            cli.session.SHUTTING_DOWN.set()
            self.addCleanup(cli.session.SHUTTING_DOWN.clear)
            with mock.patch.object(cli._thread, "interrupt_main", side_effect=lambda *a: hits.append(1)),                     mock.patch.object(cli.time, "sleep", side_effect=lambda s: real_sleep(0.02)):
                cli._watch_stop_file(path)
                path.write_text("stop")
                real_sleep(0.3)
            self.assertEqual(hits, [])


class StopFile(unittest.TestCase):
    def test_touching_the_file_acts_like_ctrl_c_each_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stop.request"
            path.write_text("old request")  # a leftover from an earlier run must be ignored
            hits = []
            real_sleep = time.sleep
            with mock.patch.object(cli._thread, "interrupt_main", side_effect=lambda *a: hits.append(1)), \
                    mock.patch.object(cli.time, "sleep", side_effect=lambda s: real_sleep(0.02)):
                cli._watch_stop_file(path)
                real_sleep(0.2)
                self.assertEqual(hits, [])
                path.write_text("stop")
                for _ in range(100):
                    if hits:
                        break
                    real_sleep(0.02)
                self.assertEqual(len(hits), 1)
                real_sleep(0.1)
                path.write_text("stop again")
                import os
                os.utime(path, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
                for _ in range(100):
                    if len(hits) > 1:
                        break
                    real_sleep(0.02)
                self.assertEqual(len(hits), 2)


if __name__ == "__main__":
    unittest.main()
