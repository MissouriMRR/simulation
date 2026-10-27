"""ui/run/commands.py: what the Run tab says to the launcher and how it reads the answers. Always runs (no window)."""
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.ui.run import commands


class RunCommand(unittest.TestCase):
    def test_run_command(self):
        with mock.patch.object(commands, "cli_command", return_value=["mrrsim"]), \
                mock.patch.dict("os.environ", {"MRRSIM_DATA_DIR": "D:/data"}):
            command = commands.build_run_command(Path("C:/c/mrrsim.toml"), Path("C:/s/a.py"), 2, True, "960x540")
        self.assertEqual(command[:5], ["mrrsim", "-c", str(Path("C:/c/mrrsim.toml")), "run", str(Path("C:/s/a.py"))])
        self.assertEqual(command[command.index("--vehicles") + 1], "2")
        self.assertEqual(command[command.index("--stop-file") + 1], str(Path("D:/data/stop.request")))
        self.assertIn("--no-cameras", command)
        self.assertEqual(command[command.index("--resolution") + 1], "960x540")

    def test_options_left_out_when_not_asked_for(self):
        with mock.patch.object(commands, "cli_command", return_value=["mrrsim"]):
            command = commands.build_run_command(Path("c.toml"), Path("a.py"), 1, False, None)
        self.assertNotIn("--no-cameras", command)
        self.assertNotIn("--resolution", command)

    def test_resolution_check(self):
        for good in ("960x540", "640X360", "1280x720"):
            self.assertTrue(commands.valid_resolution(good), good)
        for bad in ("", "960", "10x10", "axb", "960x540x1", "99999x1"):
            self.assertFalse(commands.valid_resolution(bad), bad)


class ReadingTheAnswers(unittest.TestCase):
    def test_doctor_status_line(self):
        self.assertEqual(commands.doctor_status(0, (16, 0, 0)), "Doctor: all 16 checks passed")
        self.assertIn("1 warning", commands.doctor_status(0, (16, 0, 1)))
        self.assertIn("2 of 16 checks failed", commands.doctor_status(1, (16, 2, 0)))
        self.assertIn("did not finish", commands.doctor_status(2, None))
        self.assertEqual(commands.DOCTOR_SUMMARY.search("16 checks: 0 failed, 1 warnings").groups(), ("16", "0", "1"))

    def test_pip_status_line(self):
        self.assertIn("failed", commands.pip_status(1, ""))
        self.assertIn("does not import", commands.pip_status(0, "x\n[WARN] import pyzbar.pyzbar fails: ImportError\n"))
        self.assertIn("available to every script", commands.pip_status(0, "[ OK ] import tqdm works"))
        self.assertEqual(commands.pip_status(0, "Removed x 1.0", removing=True), "Packages removed")
        self.assertIn("failed", commands.pip_status(1, "", removing=True))

    def test_the_progress_messages_cover_the_wsl_wake_up_the_student_waits_for(self):
        fragments = dict(commands.PROGRESS)
        self.assertIn("checking that WSL answers", fragments)
        self.assertIn("Waking up WSL", fragments["checking that WSL answers"])

    def test_the_window_follows_the_launchers_vehicle_limit(self):
        self.assertEqual(commands.MAX_VEHICLES, 100)


if __name__ == "__main__":
    unittest.main()
