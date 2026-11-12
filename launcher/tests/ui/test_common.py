"""ui/common: the launcher process, the file manager, the value lines, the output view and the value tiles."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import paths
from mrrsim.ui.common import files, launcher_process
from mrrsim.ui.common.output import OutputView
from mrrsim.ui.common.values import NO_VALUE, ValueTiles, value_line

GUI = os.environ.get("MRRSIM_GUI_TESTS") == "1"


class LauncherProcess(unittest.TestCase):
    def test_kill_unreal_ends_the_named_exe_and_counts_what_taskkill_ended(self):
        out = ('SUCCESS: The process "Sim.exe" with PID 10 has been terminated.\n'
               'SUCCESS: The process "Sim.exe" with PID 11 has been terminated.\n')
        done = mock.Mock(stdout=out, returncode=0)
        with mock.patch.object(launcher_process.os, "name", "nt"), \
                mock.patch.object(launcher_process.subprocess, "run", return_value=done) as run:
            self.assertEqual(launcher_process.kill_unreal("Sim.exe"), 2)
        self.assertEqual(run.call_args.args[0], ["taskkill", "/F", "/T", "/IM", "Sim.exe"])
        gone = mock.Mock(stdout='ERROR: The process "Sim.exe" not found.', returncode=128)
        with mock.patch.object(launcher_process.os, "name", "nt"), \
                mock.patch.object(launcher_process.subprocess, "run", return_value=gone):
            self.assertEqual(launcher_process.kill_unreal("Sim.exe"), 0)

    def test_kill_unreal_does_nothing_outside_windows(self):
        with mock.patch.object(launcher_process.os, "name", "posix"), \
                mock.patch.object(launcher_process.subprocess, "run") as run:
            self.assertEqual(launcher_process.kill_unreal("Sim.exe"), 0)
        run.assert_not_called()

    def test_source_tree_child_can_import_the_package(self):
        env = launcher_process.launcher_env()
        self.assertIn(str(paths.repo_root() / "launcher"), env["PYTHONPATH"])
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")

    def test_the_installed_launcher_keeps_the_import_path_as_it_is(self):
        with mock.patch.object(launcher_process.sys, "frozen", True, create=True), \
                mock.patch.dict("os.environ", {"PYTHONPATH": "X"}):
            self.assertEqual(launcher_process.launcher_env()["PYTHONPATH"], "X")

    def test_the_command_is_the_sibling_exe_when_installed_and_the_module_from_source(self):
        with mock.patch.object(launcher_process.sys, "frozen", True, create=True), \
                mock.patch.object(launcher_process.sys, "executable", str(Path("C:/app/MRRSimulatorGUI.exe"))):
            self.assertEqual(launcher_process.cli_command(), [str(Path("C:/app/MRRSimulator.exe"))])
        command = launcher_process.cli_command()
        self.assertEqual(command[1:], ["-B", "-m", "mrrsim"])

    def test_the_stop_file_is_in_the_data_folder(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {"MRRSIM_DATA_DIR": tmp}):
            self.assertEqual(launcher_process.stop_file(), Path(tmp) / "stop.request")


class RunnerProcess(unittest.TestCase):
    """A real child process stands in for the launcher."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        patch = mock.patch.dict("os.environ", {"MRRSIM_DATA_DIR": str(self.tmp / "data")})
        patch.start()
        self.addCleanup(patch.stop)
        self.runner = launcher_process.Runner()
        self.addCleanup(self.runner.kill)

    def events_until_exit(self) -> list:
        seen = []
        while True:
            kind, payload = self.runner.events.get(timeout=30)
            seen.append((kind, payload))
            if kind == "exit":
                return seen

    def test_every_line_and_then_the_exit_code_arrive(self):
        self.runner.start([sys.executable, "-c", "print('one'); print('two \u00e9'); raise SystemExit(3)"], self.tmp / "work")
        self.assertEqual(self.events_until_exit(), [("line", "one"), ("line", "two \u00e9"), ("exit", 3)])
        self.assertFalse(self.runner.running)

    def test_a_leftover_stop_request_is_removed_before_the_start(self):
        launcher_process.stop_file().parent.mkdir(parents=True)
        launcher_process.stop_file().write_text("old", encoding="utf-8")
        self.runner.start([sys.executable, "-c", "pass"], self.tmp)
        self.assertFalse(launcher_process.stop_file().exists())
        self.events_until_exit()

    def test_a_stop_request_touches_the_file_each_time(self):
        self.runner.request_stop()
        stop = launcher_process.stop_file()
        self.assertTrue(stop.is_file())
        os.utime(stop, ns=(10**9, 10**9))  # as if the first request was long ago
        self.runner.request_stop()
        self.assertGreater(stop.stat().st_mtime_ns, 10**9)

    def test_kill_ends_the_process(self):
        self.runner.start([sys.executable, "-c", "import time; time.sleep(60)"], self.tmp)
        self.assertTrue(self.runner.running)
        self.runner.kill()
        self.assertEqual(self.events_until_exit()[-1][0], "exit")
        self.assertFalse(self.runner.running)


class FileManager(unittest.TestCase):
    def test_a_folder_is_made_and_opened(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "new" / "logs"
            with mock.patch.object(files.os, "name", "nt"), \
                    mock.patch.object(files.os, "startfile", create=True) as start:
                files.open_folder(folder)
            self.assertTrue(folder.is_dir())
            start.assert_called_once_with(folder)

    def test_a_file_is_shown_selected_in_explorer(self):
        path = Path("C:/designs/Quad.jsonc")
        with mock.patch.object(files.os, "name", "nt"), mock.patch.object(files.subprocess, "Popen") as popen:
            files.show_file(path)
        popen.assert_called_once_with(["explorer", f"/select,{path}"])

    def test_a_failure_is_said_in_an_error_box(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(files.os, "name", "nt"), \
                mock.patch.object(files.os, "startfile", side_effect=OSError("no file manager"), create=True), \
                mock.patch.object(files.messagebox, "showerror") as error:
            files.open_folder(Path(tmp))
        self.assertIn("no file manager", error.call_args.args[1])
        with mock.patch.object(files.os, "name", "nt"), \
                mock.patch.object(files.subprocess, "Popen", side_effect=OSError("no explorer")), \
                mock.patch.object(files.messagebox, "showerror") as error:
            files.show_file(Path("C:/x.jsonc"))
        self.assertIn("no explorer", error.call_args.args[1])


class ValueLines(unittest.TestCase):
    """Altitude and throttle are shown in value boxes, not in the output."""

    def test_the_lines_a_script_prints_are_recognized(self):
        for text, expected in (
            ("[script]  Altitude:  0.391", ("altitude", 0.391)),
            ("[script] Altitude: 19.925", ("altitude", 19.925)),
            ("[script] altitude = -2 m", ("altitude", -2.0)),
            ("[script] Altitude (m): 5", ("altitude", 5.0)),
            ("[script] Throttle %: 53", ("throttle", 53.0)),
            ("[script] throttle: 41.5%", ("throttle", 41.5)),
        ):
            self.assertEqual(value_line(text), expected, text)

    def test_numbers_as_python_prints_them(self):
        for number, expected in (("1e-05", 1e-05), ("-2.5E+3", -2500.0), (".5", 0.5), ("2.", 2.0), ("-0.0", 0.0)):
            self.assertEqual(value_line(f"[script] Altitude: {number}"), ("altitude", expected), number)

    def test_other_lines_stay_in_the_output(self):
        for text in ("[script] Reached target altitude", "[script] Altitude reached: yes", "[script] Throttle %: high",
                     "[script] Altitude: 3 m above home", "[script] 53", "[script] LocationLocal:north=1,down=-2",
                     "12:00:00 INFO  Altitude: 5", "Throttle %: 50", "[script] ", "[script] Altitude: 1e",
                     "[script] Altitude: .", "[script] Altitude: 1.2.3"):
            self.assertIsNone(value_line(text), text)


@unittest.skipUnless(GUI, "set MRRSIM_GUI_TESTS=1 (and run on a hidden desktop) to test the real window")
class Widgets(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = mock.Mock(px=lambda n: int(n), font=("Segoe UI", 10))

    def test_the_output_view_adds_lines_with_their_color_and_clears(self):
        view = OutputView(self.root, self.app)
        view.append("plain")
        view.append("bad", "error")
        self.assertEqual(view.contents().strip().splitlines(), ["plain", "bad"])
        self.assertIn("error", view.text.tag_names("2.0"))
        view.clear()
        self.assertEqual(view.contents().strip(), "")

    def test_the_output_view_cannot_be_typed_into(self):
        view = OutputView(self.root, self.app)
        self.assertEqual(str(view.text.cget("state")), "disabled")

    def test_the_value_tiles_show_numbers_in_their_format_and_reset(self):
        tiles = ValueTiles(self.root, self.app)
        self.assertEqual({k: v.cget("text") for k, v in tiles.labels.items()}, {"altitude": NO_VALUE, "throttle": NO_VALUE})
        tiles.set("altitude", 12.3456)
        tiles.set("throttle", 53.4)
        self.assertEqual((tiles.labels["altitude"].cget("text"), tiles.labels["throttle"].cget("text")), ("12.35", "53"))
        tiles.reset()
        self.assertEqual(tiles.labels["altitude"].cget("text"), NO_VALUE)


if __name__ == "__main__":
    unittest.main()
