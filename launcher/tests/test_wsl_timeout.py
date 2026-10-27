"""A slow or stuck WSL must give a clear message, never a Python traceback.

A student's first command after a restart (`podman ps`, 30 s limit) timed out and the launcher crashed with a
raw TimeoutExpired. Timeouts are now ordinary failed commands, and the session waits for WSL to wake up first."""
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
from mrrsim.runtime import podman, session, wsl


def timeout_error(*args, **kwargs):
    raise subprocess.TimeoutExpired(cmd="wsl.exe", timeout=kwargs.get("timeout", 30))


class RunTimeout(unittest.TestCase):
    def test_a_timeout_is_a_failed_command_not_an_exception(self):
        with mock.patch.object(wsl.subprocess, "run", side_effect=timeout_error):
            result = wsl.run("mrr", ["podman", "ps"], timeout=30)
        self.assertEqual(result.returncode, wsl.TIMED_OUT)
        self.assertIn("30 s", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_podman_reads_treat_it_as_nothing_found(self):
        with mock.patch.object(wsl.subprocess, "run", side_effect=timeout_error):
            tool = podman.Podman("mrr")
            self.assertEqual(tool.ps(), [])
            self.assertEqual(tool.reap(), [])
            self.assertFalse(tool.image_exists("x"))
            self.assertIsNone(tool.version())

    def test_podman_commands_that_must_succeed_explain_the_timeout(self):
        with mock.patch.object(wsl.subprocess, "run", side_effect=timeout_error):
            with self.assertRaises(podman.PodmanError) as caught:
                podman.Podman("mrr").run(["info"], timeout=5)
        self.assertIn("no answer from WSL", str(caught.exception))


class WarmUp(unittest.TestCase):
    def completed(self, code):
        return subprocess.CompletedProcess([], code, "", "")

    def test_a_distro_that_answers_is_ok(self):
        with mock.patch.object(wsl, "run", return_value=self.completed(0)) as run:
            state, seconds = wsl.warm_up("mrr", timeout=5)
        self.assertEqual(state, "ok")
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["timeout"], 5)

    def test_one_slow_start_is_retried(self):
        with mock.patch.object(wsl, "run", side_effect=[self.completed(wsl.TIMED_OUT), self.completed(0)]) as run:
            self.assertEqual(wsl.warm_up("mrr")[0], "ok")
        self.assertEqual(run.call_count, 2)

    def test_two_timeouts_in_a_row_are_a_timeout(self):
        with mock.patch.object(wsl, "run", return_value=self.completed(wsl.TIMED_OUT)) as run:
            self.assertEqual(wsl.warm_up("mrr")[0], "timeout")
        self.assertEqual(run.call_count, 2)

    def test_a_missing_distro_is_not_a_timeout(self):
        with mock.patch.object(wsl, "run", return_value=self.completed(1)) as run:
            self.assertEqual(wsl.warm_up("nope")[0], "failed")   # the doctor explains that one
        run.assert_called_once()

    def test_the_wait_is_long_enough_for_a_cold_start(self):
        with mock.patch.object(wsl, "run", return_value=self.completed(0)) as run:
            wsl.warm_up("mrr")
        self.assertGreaterEqual(run.call_args.kwargs["timeout"], 60)


class StuckWsl(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patch = mock.patch.dict("os.environ", {"MRRSIM_DATA_DIR": str(Path(self._tmp.name) / "data")})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(session.SHUTTING_DOWN.clear)
        self.cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")

    def test_a_stuck_wsl_ends_the_run_with_a_clear_message_and_no_traceback(self):
        sess = session.Session(self.cfg, dry_run=True)
        calls = []
        with mock.patch.object(wsl, "warm_up", return_value=("timeout", 180.0)), \
                mock.patch.object(podman.Podman, "run", side_effect=lambda *a, **k: calls.append(a) or subprocess.CompletedProcess([], 0, "", "")), \
                self.assertLogs(level="INFO") as logs:
            code = sess.up(script=True, keep=False, unreal=False, scene=False)
        text = "\n".join(logs.output)
        self.assertEqual(code, 2)
        self.assertIn("WSL did not answer in 180 s", text)
        self.assertIn("wsl --shutdown", text)
        self.assertEqual(calls, [])        # nothing was sent to the stuck WSL, not even while shutting down

    def test_a_slow_start_is_reported_and_the_run_goes_on(self):
        sess = session.Session(self.cfg, dry_run=True)
        with mock.patch.object(wsl, "warm_up", return_value=("ok", 45.0)), mock.patch.object(session.Session, "_preflight",
                                                                                            side_effect=session.SessionError("stop here")), \
                mock.patch.object(podman.Podman, "ps", return_value=[]), self.assertLogs(level="INFO") as logs:
            sess.up(script=False, keep=False, unreal=False, scene=False)
        text = "\n".join(logs.output)
        self.assertIn("Checking that WSL is responsive", text)
        self.assertIn("WSL answered after 45 s", text)
        self.assertIn("stop here", text)

    def test_the_window_shows_what_is_happening(self):
        from mrrsim.ui.run import commands

        fragments = dict(commands.PROGRESS)
        self.assertIn("Checking that WSL is responsive", fragments)
        self.assertIn("Waking up WSL", fragments["Checking that WSL is responsive"])

    def test_the_command_line_never_prints_a_traceback_for_a_stuck_wsl(self):
        script = Path(self._tmp.name) / "s.py"
        script.write_text("print(1)\n", encoding="utf-8")
        argv = ["-c", str(paths.repo_root() / "config" / "mrrsim.toml"), "run", str(script), "--no-unreal", "--no-scene"]
        with mock.patch.object(wsl, "warm_up", return_value=("timeout", 180.0)), \
                mock.patch.object(wsl.subprocess, "run", side_effect=timeout_error), \
                redirect_stdout(io.StringIO()), self.assertLogs(level="INFO"):
            code = cli.main(argv)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
