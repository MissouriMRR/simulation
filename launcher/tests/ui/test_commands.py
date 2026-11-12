"""ui/run/commands.py: what the Run tab says to the launcher and how it reads the answers. Always runs (no window)."""
import unittest
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
from mrrsim.ui.run import commands

SOURCE = paths.repo_root() / "launcher" / "mrrsim"


class RunCommand(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.object(commands, "cli_command", return_value=["mrrsim"]),
                      mock.patch.dict("os.environ", {"MRRSIM_DATA_DIR": "D:/data"})):
            patch.start()
            self.addCleanup(patch.stop)
        self.stop = str(Path("D:/data/stop.request"))

    def test_run_command(self):
        command = commands.build_run_command(Path("C:/c/mrrsim.toml"), Path("C:/s/a.py"), 2, True, "960x540")
        self.assertEqual(command[:5], ["mrrsim", "-c", str(Path("C:/c/mrrsim.toml")), "run", str(Path("C:/s/a.py"))])
        self.assertEqual(command[command.index("--vehicles") + 1], "2")
        self.assertEqual(command[command.index("--stop-file") + 1], self.stop)
        self.assertIn("--no-cameras", command)
        self.assertEqual(command[command.index("--resolution") + 1], "960x540")

    def test_options_left_out_when_not_asked_for(self):
        command = commands.build_run_command(Path("c.toml"), Path("a.py"), 1, False, None)
        self.assertNotIn("--no-cameras", command)
        self.assertNotIn("--resolution", command)

    def test_the_doctor_and_package_commands_can_be_stopped_too(self):
        self.assertEqual(commands.doctor_command(Path("c.toml")),
                         ["mrrsim", "-c", "c.toml", "doctor", "--stop-file", self.stop])
        self.assertEqual(commands.pip_command(Path("c.toml"), False, ["tqdm", "pyzbar"]),
                         ["mrrsim", "-c", "c.toml", "pip", "install", "tqdm", "pyzbar", "--stop-file", self.stop])
        self.assertEqual(commands.pip_command(Path("c.toml"), True, ["numpy"])[3:5], ["pip", "remove"])

    def test_the_launcher_accepts_every_command_the_window_builds(self):
        parser = cli.build_parser()
        for command in (commands.build_run_command(Path("c.toml"), Path("a.py"), 2, True, "960x540", project=Path("p"),
                                                   script_args="-s", design="Quad"),
                        commands.doctor_command(Path("c.toml")), commands.pip_command(Path("c.toml"), False, ["tqdm"]),
                        commands.pip_command(Path("c.toml"), True, ["tqdm"])):
            args = parser.parse_args(command[1:])
            self.assertEqual(args.stop_file, self.stop, command)

    def test_resolution_check(self):
        for good in ("960x540", "640X360", "1280x720", f"{config.MIN_PIXELS}x{config.MAX_PIXELS}"):
            self.assertTrue(commands.valid_resolution(good), good)
        for bad in ("", "960", "10x10", "axb", "960x540x1", "99999x1", f"{config.MIN_PIXELS - 1}x540",
                    f"960x{config.MAX_PIXELS + 1}"):
            self.assertFalse(commands.valid_resolution(bad), bad)


class ReadingTheAnswers(unittest.TestCase):
    def test_doctor_status_line(self):
        self.assertEqual(commands.doctor_status(0, (16, 0, 0)), "Doctor: all 16 checks passed")
        self.assertIn("1 warning", commands.doctor_status(0, (16, 0, 1)))
        self.assertIn("2 of 16 checks failed", commands.doctor_status(1, (16, 2, 0)))
        self.assertIn("did not finish", commands.doctor_status(2, None))
        self.assertEqual(commands.doctor_status(commands.STOPPED, None), "Doctor stopped")
        self.assertEqual(commands.DOCTOR_SUMMARY.search("16 checks: 0 failed, 1 warnings").groups(), ("16", "0", "1"))

    def test_pip_status_line(self):
        self.assertIn("failed", commands.pip_status(1, ""))
        self.assertIn("does not import", commands.pip_status(0, "x\n[WARN] import pyzbar.pyzbar fails: ImportError\n"))
        self.assertIn("available to every script", commands.pip_status(0, "[ OK ] import tqdm works"))
        self.assertEqual(commands.pip_status(0, "Removed x 1.0", removing=True), "Packages removed")
        self.assertIn("failed", commands.pip_status(1, "", removing=True))
        for removing in (False, True):
            self.assertTrue(commands.pip_status(commands.STOPPED, "", removing).startswith("Stopped"))

    def test_how_each_kind_of_line_is_colored(self):
        for text, tag in (("[script] hello", "script"), ("12:00:00 ERROR bad", "error"), ("[FAIL] WSL: none", "error"),
                          ("12:00:00 WARNING odd", "warning"), ("[WARN] GPS", "warning"), ("[ OK ] Windows", "ok"),
                          ("12:00:00 INFO  started Unreal", None), ("ERRORS happen", None)):
            self.assertEqual(commands.line_tag(text), tag, text)

    def test_the_progress_messages_cover_the_wsl_wake_up_the_student_waits_for(self):
        fragments = dict(commands.PROGRESS)
        self.assertIn("Checking that WSL is responsive", fragments)
        self.assertIn("Waking up WSL", fragments["Checking that WSL is responsive"])

    def test_every_progress_fragment_is_still_something_the_launcher_logs(self):
        source = "\n".join(p.read_text(encoding="utf-8") for p in SOURCE.rglob("*.py") if "ui" not in p.parts)
        for fragment, _message in commands.PROGRESS:
            self.assertIn(fragment, source, "the launcher no longer logs this, so the status line never shows it")

    def test_the_window_follows_the_launchers_vehicle_limit(self):
        self.assertEqual(commands.MAX_VEHICLES, 100)


if __name__ == "__main__":
    unittest.main()
