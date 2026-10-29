"""Running and closing the Unreal build: starting it, waiting until it is ready,
asking it to close and forcing it, and finding its display settings file."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core.config import UnrealCfg
from mrrsim.runtime import unreal


class Clock:
    """A fake clock: sleeping moves it forward, so waits finish at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class DisplayFile(unittest.TestCase):
    def test_it_is_in_the_projects_saved_config(self):
        exe = Path("C:/Games/Win/Sim/Binaries/Win64/Sim-Win64-Shipping.exe")
        with mock.patch.dict("os.environ", {"LOCALAPPDATA": "C:/Local"}):
            path = unreal.display_settings_file(exe)
        self.assertEqual(
            path, Path("C:/Local/Sim/Saved/Config/Windows/GameUserSettings.ini")
        )

    def test_an_exe_that_is_not_in_a_project_layout_has_none(self):
        for exe in ("C:/a.exe", "a.exe", "C:/x/y/z.exe"):
            with self.subTest(exe=exe):
                self.assertIsNone(unreal.display_settings_file(Path(exe)))

    def test_without_one_the_settings_are_skipped_with_a_warning(self):
        cfg = UnrealCfg(exe=Path("C:/a.exe"))
        with (
            mock.patch.object(unreal.inifile, "set_keys") as set_keys,
            self.assertLogs("mrrsim", level="WARNING") as logs,
        ):
            unreal.apply_display_settings(cfg)
        set_keys.assert_not_called()
        self.assertIn("cannot tell which project", logs.output[0])


class EndProcess(unittest.TestCase):
    def test_on_windows_taskkill_asks_then_forces_with_a_time_limit(self):
        with (
            mock.patch.object(unreal, "IS_WINDOWS", True),
            mock.patch.object(unreal.subprocess, "run") as run,
        ):
            unreal._end_process(7, force=False)
            unreal._end_process(7, force=True)
        self.assertEqual(
            run.call_args_list,
            [
                mock.call(
                    ["taskkill", "/PID", "7", "/T"],
                    capture_output=True,
                    timeout=unreal.TASKKILL_TIMEOUT_S,
                ),
                mock.call(
                    ["taskkill", "/PID", "7", "/T", "/F"],
                    capture_output=True,
                    timeout=unreal.TASKKILL_TIMEOUT_S,
                ),
            ],
        )

    def test_a_taskkill_that_hangs_is_a_warning_not_a_hang(self):
        hang = subprocess.TimeoutExpired("taskkill", unreal.TASKKILL_TIMEOUT_S)
        with (
            mock.patch.object(unreal, "IS_WINDOWS", True),
            mock.patch.object(unreal.subprocess, "run", side_effect=hang),
            self.assertLogs("mrrsim", level="WARNING") as logs,
        ):
            unreal._end_process(7, force=True)
        self.assertIn("did not finish", logs.output[0])

    def test_elsewhere_it_sends_sigterm_or_sigkill(self):
        with (
            mock.patch.object(unreal, "IS_WINDOWS", False),
            mock.patch.object(unreal.signal, "SIGKILL", 9, create=True),
            mock.patch.object(unreal.os, "kill") as kill,
        ):
            unreal._end_process(7, force=False)
            unreal._end_process(7, force=True)
        self.assertEqual(
            kill.call_args_list,
            [mock.call(7, unreal.signal.SIGTERM), mock.call(7, 9)],
        )

    def test_a_process_that_is_already_gone_is_fine(self):
        with (
            mock.patch.object(unreal, "IS_WINDOWS", False),
            mock.patch.object(unreal.os, "kill", side_effect=ProcessLookupError),
        ):
            unreal._end_process(7, force=False)


class TerminatePid(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.alive = True
        self.calls = []
        for patch in (
            mock.patch.object(unreal.time, "monotonic", self.clock.monotonic),
            mock.patch.object(unreal.time, "sleep", self.clock.sleep),
            mock.patch.object(unreal, "pid_alive", lambda pid: self.alive),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def end(self, pid, force):
        self.calls.append(force)

    def test_a_process_that_is_not_running_is_left_alone(self):
        self.alive = False
        with mock.patch.object(unreal, "_end_process", self.end):
            unreal.terminate_pid(7)
        self.assertEqual(self.calls, [])

    def test_one_that_closes_when_asked_is_not_forced(self):
        def end(pid, force):
            self.calls.append(force)
            self.alive = False

        with mock.patch.object(unreal, "_end_process", end):
            unreal.terminate_pid(7, grace=4)
        self.assertEqual(self.calls, [False])

    def test_one_that_does_not_close_is_forced_after_the_grace_time(self):
        with (
            mock.patch.object(unreal, "_end_process", self.end),
            self.assertLogs("mrrsim", level="WARNING") as logs,
        ):
            unreal.terminate_pid(7, grace=4)
        self.assertEqual(self.calls, [False, True])
        self.assertGreaterEqual(self.clock.now, 4)
        self.assertIn("did not close within 4 s", logs.output[0])


class PidIs(unittest.TestCase):
    def check(self, alive, name, expected):
        with (
            mock.patch.object(unreal, "pid_alive", return_value=alive),
            mock.patch.object(unreal, "image_name", return_value=name),
        ):
            self.assertIs(
                unreal.pid_is(7, Path("C:/x/Sim-Win64-Shipping.exe")), expected
            )

    def test_it_must_be_alive_and_have_the_executables_name(self):
        self.check(True, "sim-win64-shipping.EXE", True)
        self.check(True, "notepad.exe", False)
        self.check(True, None, False)
        self.check(False, "Sim-Win64-Shipping.exe", False)


class Process(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name) / "Sim" / "Binaries" / "Win64"
        folder.mkdir(parents=True)
        self.exe = folder / "Sim-Win64-Shipping.exe"
        self.exe.write_bytes(b"")
        self.log = Path(tmp.name) / "unreal.log"
        self.cfg = UnrealCfg(exe=self.exe)

    def process(self, **kwargs) -> unreal.UnrealProcess:
        process = unreal.UnrealProcess(self.cfg, self.log, **kwargs)
        self.addCleanup(process._close_log)
        return process

    def test_before_it_starts_there_is_no_pid_and_it_is_not_alive(self):
        process = self.process()
        self.assertIsNone(process.pid)
        self.assertFalse(process.alive())
        self.assertEqual(process.command[0], str(self.exe))

    def test_a_dry_run_only_logs_the_command(self):
        process = self.process(dry_run=True)
        with self.assertLogs("mrrsim", level="INFO") as logs:
            process.start()
        self.assertIn("[dry-run] start", logs.output[0])
        self.assertFalse(self.log.exists())
        self.assertIsNone(process.proc)

    def test_a_missing_executable_is_an_unreal_error(self):
        self.exe.unlink()
        with self.assertRaisesRegex(unreal.UnrealError, "not found"):
            self.process().start()

    def test_it_starts_in_its_own_process_group_at_the_chosen_priority(self):
        process = self.process()
        job = mock.Mock()
        popen = mock.Mock(return_value=mock.Mock(pid=42))
        with (
            mock.patch.object(unreal, "IS_WINDOWS", True),
            mock.patch.object(
                unreal.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True
            ),
            mock.patch.object(unreal.subprocess, "Popen", popen),
            mock.patch.object(unreal, "apply_display_settings") as apply,
            self.assertLogs("mrrsim", level="INFO") as logs,
        ):
            process.start(job)
        apply.assert_called_once_with(self.cfg)
        kwargs = popen.call_args.kwargs
        self.assertEqual(popen.call_args.args[0], process.command)
        self.assertEqual(kwargs["cwd"], self.exe.parent)
        self.assertEqual(kwargs["creationflags"], 0x200 | 0x8000)  # above normal
        self.assertTrue(self.log.is_file())
        job.assign.assert_called_once_with(popen.return_value)
        self.assertEqual(process.pid, 42)
        self.assertIn("started Unreal (pid 42)", logs.output[-1])

    def test_a_process_that_cannot_start_is_an_unreal_error_and_leaves_no_open_log(
        self,
    ):
        process = self.process()
        with (
            mock.patch.object(
                unreal.subprocess, "Popen", side_effect=OSError("blocked")
            ),
            mock.patch.object(unreal, "apply_display_settings"),
            self.assertRaisesRegex(unreal.UnrealError, "could not start .*blocked"),
        ):
            process.start()
        self.assertIsNone(process._log_handle)
        self.assertIsNone(process.proc)

    def test_ready_when_both_ports_accept_connections(self):
        process = self.process()
        process.proc = mock.Mock(poll=mock.Mock(return_value=None))
        with (
            mock.patch.object(unreal.health, "wait_tcp", return_value=[]) as wait,
            self.assertLogs("mrrsim", level="INFO") as logs,
        ):
            process.wait_ready(timeout=5)
        self.assertEqual(wait.call_args.args[:3], ("127.0.0.1", [8989, 8990], 5))
        self.assertIn("accepting connections", logs.output[-1])

    def test_a_dry_run_does_not_wait(self):
        with mock.patch.object(unreal.health, "wait_tcp") as wait:
            self.process(dry_run=True).wait_ready()
        wait.assert_not_called()

    def test_unreal_that_exits_while_starting_is_reported_with_its_exit_code(self):
        process = self.process()
        process.proc = mock.Mock(poll=mock.Mock(return_value=3), returncode=3)
        with mock.patch.object(unreal.health, "wait_tcp", return_value=[8989]):
            with self.assertRaisesRegex(unreal.UnrealError, "exit code 3"):
                process.wait_ready(timeout=1)

    def test_unreal_that_runs_but_never_opens_its_ports_is_reported(self):
        process = self.process()
        process.proc = mock.Mock(poll=mock.Mock(return_value=None))
        with mock.patch.object(unreal.health, "wait_tcp", return_value=[8990]):
            with self.assertRaisesRegex(unreal.UnrealError, r"\[8990\] did not open"):
                process.wait_ready(timeout=1)

    def test_stopping_what_never_started_is_fine(self):
        self.assertTrue(self.process().stop())

    def test_stop_closes_it_with_the_configured_grace_and_the_log_file(self):
        process = self.process()
        process.proc = mock.Mock(pid=42, poll=mock.Mock(return_value=None))
        log = process._log_handle = mock.Mock()
        with mock.patch.object(unreal, "terminate_pid") as terminate:
            self.assertTrue(process.stop())
        terminate.assert_called_once_with(42, self.cfg.close_grace_s)
        log.close.assert_called_once()
        self.assertIsNone(process._log_handle)

    def test_a_grace_given_to_stop_is_used(self):
        process = self.process()
        process.proc = mock.Mock(pid=42, poll=mock.Mock(return_value=None))
        with mock.patch.object(unreal, "terminate_pid") as terminate:
            process.stop(grace=1)
        terminate.assert_called_once_with(42, 1)

    def test_unreal_that_will_not_exit_makes_stop_return_false(self):
        process = self.process()
        process.proc = mock.Mock(pid=42, poll=mock.Mock(return_value=None))
        process.proc.wait.side_effect = subprocess.TimeoutExpired("Sim", 5)
        log = process._log_handle = mock.Mock()
        with (
            mock.patch.object(unreal, "terminate_pid"),
            self.assertLogs("mrrsim", level="WARNING") as logs,
        ):
            self.assertFalse(process.stop())
        self.assertIn("Unreal did not exit", logs.output[0])
        log.close.assert_called_once()

    def test_unreal_that_already_exited_is_not_closed_again(self):
        process = self.process()
        process.proc = mock.Mock(pid=42, poll=mock.Mock(return_value=0))
        with mock.patch.object(unreal, "terminate_pid") as terminate:
            self.assertTrue(process.stop())
        terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
