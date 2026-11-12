"""The stop file: the window asks a running launcher to stop by touching a file, which acts like Ctrl+C
(`cli._watch_stop_file`)."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import paths


class StopFileAfterShutdownBegan(unittest.TestCase):
    def test_requests_are_ignored_once_the_shut_down_has_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stop.request"
            hits = []
            real_sleep = time.sleep
            cli.session.SHUTTING_DOWN.set()
            self.addCleanup(cli.session.SHUTTING_DOWN.clear)
            with mock.patch.object(cli._thread, "interrupt_main", side_effect=lambda *a: hits.append(1)), \
                    mock.patch.object(cli.time, "sleep", side_effect=lambda s: real_sleep(0.02)):
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
                os.utime(path, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
                for _ in range(100):
                    if len(hits) > 1:
                        break
                    real_sleep(0.02)
                self.assertEqual(len(hits), 2)


class EveryCommandTheWindowCanStop(unittest.TestCase):
    """The window's Stop button works for runs, the doctor and package installs alike: each takes --stop-file."""

    CONFIG = str(paths.repo_root() / "config" / "mrrsim.toml")

    def main(self, *argv: str):
        with mock.patch.object(cli, "_watch_stop_file") as watch:
            code = cli.main(["-c", self.CONFIG, *argv])
        return code, watch

    def test_run_doctor_and_pip_watch_the_stop_file(self):
        with mock.patch.object(cli, "cmd_run", return_value=0), \
                mock.patch.object(cli.doctor, "run_checks", return_value=[]), \
                mock.patch.object(cli.pipinstall, "cli", return_value=0), mock.patch("builtins.print"):
            for argv in (("run", "fly.py"), ("doctor",), ("pip", "install", "tqdm"), ("pip", "list")):
                code, watch = self.main(*argv, "--stop-file", "stop.request")
                self.assertEqual(code, 0, argv)
                watch.assert_called_once_with(Path("stop.request"))
                code, watch = self.main(*argv)
                watch.assert_not_called()

    def test_a_stopped_doctor_ends_with_the_ctrl_c_code(self):
        with mock.patch.object(cli.doctor, "run_checks", side_effect=KeyboardInterrupt):
            code, _ = self.main("doctor", "--stop-file", "stop.request")
        self.assertEqual(code, 130)


if __name__ == "__main__":
    unittest.main()
