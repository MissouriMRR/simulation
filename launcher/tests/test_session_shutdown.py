"""How a session shuts down (what it says at each step, and that one failing
step does not stop the rest), the run-state file, and the script helpers."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config
from mrrsim.runtime import session
from mrrsim.runtime.podman import Mount, PodmanError


def load() -> config.Config:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mrrsim.toml"
        path.write_text("", encoding="utf-8")
        return config.load(path)


class Shutdown(unittest.TestCase):
    def setUp(self):
        self.addCleanup(session.SHUTTING_DOWN.clear)
        self.sess = session.Session(load(), dry_run=True)
        self.podman = mock.Mock(dry_run=True)
        self.podman.stop.return_value = True
        self.podman.reap.return_value = []
        self.sess.podman = self.podman

    def down(self) -> str:
        with self.assertLogs("mrrsim", level="INFO") as logs:
            self.sess.down()
        return "\n".join(logs.output)

    def test_it_says_what_it_stops_and_that_it_worked(self):
        text = self.down()
        for step in (
            "stopping the script container (mrrsim-script)",
            "stopping the scene loader container (mrrsim-scene)",
            "stopping the SITL container(s) (mrrsim-sitl-0)",
            "removing the containers",
        ):
            with self.subTest(step=step):
                self.assertIn(f"{step}...", text)
                self.assertIn(f"{step}: done", text)
        self.assertIn("shut down: everything stopped", text)
        self.assertNotIn("FAILED", text)

    def test_a_stop_that_podman_reports_as_failed_is_reported(self):
        self.podman.stop.side_effect = lambda names, timeout: (
            "mrrsim-sitl-0" not in names
        )
        text = self.down()
        self.assertIn("stopping the SITL container(s) (mrrsim-sitl-0): FAILED", text)
        self.assertIn("stopping the script container (mrrsim-script): done", text)
        self.assertIn("shut down with 1 problem(s)", text)

    def test_a_step_that_raises_is_reported_and_the_rest_still_run(self):
        self.podman.stop.side_effect = PodmanError("boom")
        text = self.down()
        self.assertIn(
            "stopping the script container (mrrsim-script): FAILED: boom", text
        )
        self.assertIn("removing the containers: done", text)
        self.assertIn("shut down with 3 problem(s)", text)

    def test_every_log_reader_is_stopped_even_if_one_fails(self):
        bad = mock.Mock(stop=mock.Mock(side_effect=OSError("handle gone")))
        good = mock.Mock()
        self.sess._followers = [bad, good]
        text = self.down()
        good.stop.assert_called_once()
        self.assertIn("closing the container log readers: FAILED: handle gone", text)

    def test_a_stuck_wsl_leaves_the_containers_alone_and_says_so(self):
        self.sess._wsl_stuck = True
        text = self.down()
        self.podman.stop.assert_not_called()
        self.podman.reap.assert_not_called()
        self.assertIn("WSL is not answering", text)
        self.assertIn("containers not stopped (WSL not answering)", text)

    def test_unreal_is_named_with_its_pid_and_reported_done_when_it_exits(self):
        self.sess.unreal = mock.Mock(pid=4242, stop=mock.Mock(return_value=True))
        text = self.down()
        self.assertIn("stopping Unreal (pid 4242): done", text)

    def test_unreal_that_will_not_exit_is_reported_as_failed(self):
        self.sess.unreal = mock.Mock(pid=4242, stop=mock.Mock(return_value=False))
        text = self.down()
        self.assertIn("stopping Unreal (pid 4242): FAILED", text)
        self.assertIn("shut down with 1 problem(s)", text)

    def test_the_second_call_does_nothing(self):
        self.down()
        with self.assertNoLogs("mrrsim", level="INFO"):
            self.sess.down()


class RunState(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.dict("os.environ", {"MRRSIM_DATA_DIR": tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.file = Path(tmp.name) / "current.json"

    def test_no_file_means_no_run_and_no_warning(self):
        with self.assertNoLogs("mrrsim", level="WARNING"):
            self.assertIsNone(session.read_state())

    def test_a_good_file_is_read(self):
        self.file.write_text('{"session": "s1"}', encoding="utf-8")
        self.assertEqual(session.read_state(), {"session": "s1"})

    def test_a_file_that_cannot_be_used_is_reported_not_hidden(self):
        for text in ("{not json", "[1, 2]", ""):
            self.file.write_text(text, encoding="utf-8")
            with self.subTest(text=text):
                with self.assertLogs("mrrsim", level="WARNING") as logs:
                    self.assertIsNone(session.read_state())
                self.assertIn("ignoring the run-state file", logs.output[0])
                self.assertIn(str(self.file), logs.output[0])


class ScriptHelpers(unittest.TestCase):
    def setUp(self):
        self.sess = session.Session(load(), dry_run=True)

    def test_python_finds_the_scripts_folder_then_the_library(self):
        env = self.sess._script_env([Mount("/c/s", "/work", "ro")])
        self.assertEqual(env["PYTHONPATH"], "/work:" + session.SCRIPT_LIB)

    def test_extra_packages_come_after_the_scripts_own_folder(self):
        mounts = [Mount("/c/s", "/work", "ro"), Mount("/c/p", "/packages", "ro")]
        env = self.sess._script_env(mounts)
        self.assertEqual(env["PYTHONPATH"], "/work:/packages:" + session.SCRIPT_LIB)

    def test_every_vehicle_gets_its_own_connection_string(self):
        env = self.sess._script_env([])
        self.assertEqual(env["SIM_CONNECT"], env["SIM_CONNECT_0"])
        self.assertEqual(env["SIM_VEHICLE_COUNT"], "1")

    def test_the_scripts_folder_and_this_runs_folder_are_mounted(self):
        mounts = self.sess._script_mounts()
        self.assertEqual([m.dst for m in mounts[:2]], ["/work", "/out"])

    def test_a_failure_to_stop_the_script_after_ctrl_c_does_not_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.sess.dir = Path(tmp)
            self.sess.podman = mock.Mock()
            self.sess.podman.stop.side_effect = PodmanError("boom")
            proc = mock.Mock(stdout=iter([]))
            proc.poll.return_value = None  # the script never exits by itself
            sleeps = mock.patch.object(
                session.time, "sleep", side_effect=[KeyboardInterrupt] * 2
            )
            with sleeps, self.assertLogs("mrrsim", level="INFO") as logs:
                interrupted = self.sess._wait_for_script(proc)
            self.assertTrue((Path(tmp) / "script.log").is_file())
        self.assertTrue(interrupted)
        self.assertIn(
            "stopping the script container (mrrsim-script): FAILED",
            "\n".join(logs.output),
        )


if __name__ == "__main__":
    unittest.main()
