"""ui/common: the launcher process, the value lines, the output view and the value tiles."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import paths
from mrrsim.ui.common import launcher_process
from mrrsim.ui.common.values import NO_VALUE, value_line

GUI = os.environ.get("MRRSIM_GUI_TESTS") == "1"


class LauncherProcess(unittest.TestCase):
    def test_kill_unreal_counts_what_taskkill_ended(self):
        out = ('SUCCESS: The process "Simulation-Win64-Shipping.exe" with PID 10 has been terminated.\n'
               'SUCCESS: The process "Simulation-Win64-Shipping.exe" with PID 11 has been terminated.\n')
        done = mock.Mock(stdout=out, returncode=0)
        with mock.patch.object(launcher_process.os, "name", "nt"), \
                mock.patch.object(launcher_process.subprocess, "run", return_value=done) as run:
            self.assertEqual(launcher_process.kill_unreal(), 2)
        self.assertEqual(run.call_args.args[0], ["taskkill", "/F", "/T", "/IM", "Simulation-Win64-Shipping.exe"])
        gone = mock.Mock(stdout='ERROR: The process "Simulation-Win64-Shipping.exe" not found.', returncode=128)
        with mock.patch.object(launcher_process.os, "name", "nt"), \
                mock.patch.object(launcher_process.subprocess, "run", return_value=gone):
            self.assertEqual(launcher_process.kill_unreal(), 0)

    def test_source_tree_child_can_import_the_package(self):
        env = launcher_process.launcher_env()
        self.assertIn(str(paths.repo_root() / "launcher"), env["PYTHONPATH"])
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")

    def test_the_command_is_the_sibling_exe_when_installed_and_the_module_from_source(self):
        with mock.patch.object(launcher_process.sys, "frozen", True, create=True), \
                mock.patch.object(launcher_process.sys, "executable", str(Path("C:/app/MRRSimulatorGUI.exe"))):
            self.assertEqual(launcher_process.cli_command(), [str(Path("C:/app/MRRSimulator.exe"))])
        command = launcher_process.cli_command()
        self.assertEqual(command[1:], ["-B", "-m", "mrrsim"])

    def test_the_stop_file_is_in_the_data_folder(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {"MRRSIM_DATA_DIR": tmp}):
            self.assertEqual(launcher_process.stop_file(), Path(tmp) / "stop.request")


class ValueLines(unittest.TestCase):
    """Altitude and throttle are shown in value boxes, not in the output."""

    def test_the_lines_a_script_prints_are_recognised(self):
        for text, expected in (
            ("[script]  Altitude:  0.391", ("altitude", 0.391)),
            ("[script] Altitude: 19.925", ("altitude", 19.925)),
            ("[script] altitude = -2 m", ("altitude", -2.0)),
            ("[script] Altitude (m): 5", ("altitude", 5.0)),
            ("[script] Throttle %: 53", ("throttle", 53.0)),
            ("[script] throttle: 41.5%", ("throttle", 41.5)),
        ):
            self.assertEqual(value_line(text), expected, text)

    def test_other_lines_stay_in_the_output(self):
        for text in ("[script] Reached target altitude", "[script] Altitude reached: yes", "[script] Throttle %: high",
                     "[script] Altitude: 3 m above home", "[script] 53", "[script] LocationLocal:north=1,down=-2",
                     "12:00:00 INFO  Altitude: 5", "Throttle %: 50", "[script] "):
            self.assertIsNone(value_line(text), text)


@unittest.skipUnless(GUI, "set MRRSIM_GUI_TESTS=1 (and run on a hidden desktop) to test the real window")
class Widgets(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = mock.Mock(px=lambda n: int(n), font=("Segoe UI", 10))

    def test_the_output_view_adds_lines_with_their_colour_and_clears(self):
        from mrrsim.ui.common.output import OutputView

        view = OutputView(self.root, self.app)
        view.append("plain")
        view.append("bad", "error")
        self.assertEqual(view.contents().strip().splitlines(), ["plain", "bad"])
        self.assertIn("error", view.text.tag_names("2.0"))
        view.clear()
        self.assertEqual(view.contents().strip(), "")

    def test_the_output_view_cannot_be_typed_into(self):
        from mrrsim.ui.common.output import OutputView

        view = OutputView(self.root, self.app)
        self.assertEqual(str(view.text.cget("state")), "disabled")

    def test_the_value_tiles_show_numbers_in_their_format_and_reset(self):
        from mrrsim.ui.common.values import ValueTiles

        tiles = ValueTiles(self.root, self.app)
        self.assertEqual({k: v.cget("text") for k, v in tiles.labels.items()}, {"altitude": NO_VALUE, "throttle": NO_VALUE})
        tiles.set("altitude", 12.3456)
        tiles.set("throttle", 53.4)
        self.assertEqual((tiles.labels["altitude"].cget("text"), tiles.labels["throttle"].cget("text")), ("12.35", "53"))
        tiles.reset()
        self.assertEqual(tiles.labels["altitude"].cget("text"), NO_VALUE)


if __name__ == "__main__":
    unittest.main()
