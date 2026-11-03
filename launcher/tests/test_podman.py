"""runtime/podman.py: what the `Podman` wrapper asks of podman, and what it makes of the answers. WSL is replaced by a
fake, so nothing here needs podman, WSL or even Windows. (run_args and the dry run are in test_podman_wsl.py.)"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.runtime import podman as podman_module
from mrrsim.runtime import wsl
from mrrsim.runtime.podman import LogFollower, Mount, Podman, PodmanError, run_args


def done(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class Fake(unittest.TestCase):
    """A Podman whose wsl.run, wsl.popen and wsl.call are fakes; `self.run` is the wsl.run one."""

    def setUp(self):
        self.podman = Podman("mrr")
        patches = {name: mock.patch.object(podman_module.wsl, name) for name in ("run", "popen", "call")}
        for name, patch in patches.items():
            setattr(self, name, patch.start())
            self.addCleanup(patch.stop)
        self.run.return_value = done()

    def asked(self) -> list[str]:
        """The podman arguments of the last wsl.run."""
        argv = self.run.call_args.args[1]
        self.assertEqual(argv[0], "podman")
        return argv[1:]


class Running(Fake):
    def test_it_runs_podman_in_the_distro_with_a_timeout(self):
        self.run.return_value = done(0, "out")
        result = self.podman.run(["info"], timeout=5)
        self.assertEqual(result.stdout, "out")
        self.assertEqual(self.run.call_args.args, ("mrr", ["podman", "info"]))
        self.assertEqual(self.run.call_args.kwargs, {"timeout": 5})

    def test_a_failure_raises_with_the_exit_code_and_what_podman_said(self):
        self.run.return_value = done(125, "", "  no such image  ")
        with self.assertRaises(PodmanError) as caught:
            self.podman.run(["run", "x"])
        self.assertEqual(str(caught.exception), "podman run failed (exit 125): no such image")

    def test_what_podman_printed_is_used_when_it_said_nothing_on_stderr(self):
        self.run.return_value = done(1, "only stdout", "")
        with self.assertRaises(PodmanError) as caught:
            self.podman.run(["x"])
        self.assertIn("only stdout", str(caught.exception))

    def test_a_stuck_wsl_is_reported_by_its_own_reason(self):
        self.run.return_value = done(wsl.TIMED_OUT, "", "no answer from WSL within 30 s")
        with self.assertRaises(PodmanError) as caught:
            self.podman.run(["ps"])
        self.assertIn("exit 124", str(caught.exception))
        self.assertIn("no answer from WSL", str(caught.exception))

    def test_without_check_the_failure_is_the_callers_to_look_at(self):
        self.run.return_value = done(3)
        self.assertEqual(self.podman.run(["x"], check=False).returncode, 3)

    def test_the_dry_run_only_skips_what_changes_something(self):
        dry = Podman("mrr", dry_run=True)
        for command in ("run", "stop", "rm", "kill", "build", "cp"):
            self.assertEqual(dry.run([command, "x"]).returncode, 0)
        self.run.assert_not_called()
        self.run.return_value = done(0, "podman version 5")
        self.assertEqual(dry.version(), "podman version 5")  # a question is still asked
        self.run.assert_called_once()

    def test_the_command_line_shown_is_the_one_windows_would_run(self):
        shown = self.podman.display(["stop", "a b"])
        self.assertTrue(shown.startswith("wsl.exe -d mrr --exec podman stop"))
        self.assertIn("'a b'", shown)


class Asking(Fake):
    def test_version(self):
        self.run.return_value = done(0, "podman version 4.9.3\n")
        self.assertEqual(self.podman.version(), "podman version 4.9.3")
        self.run.return_value = done(1)
        self.assertIsNone(self.podman.version())

    def test_rootless_says_yes_no_or_nothing(self):
        for out, wanted in (("true\n", True), ("false\n", False), ("TRUE", True)):
            self.run.return_value = done(0, out)
            self.assertIs(self.podman.rootless(), wanted, out)
        self.run.return_value = done(1)
        self.assertIsNone(self.podman.rootless())

    def test_the_init_program_is_looked_for_without_a_shell_of_ours(self):
        self.assertTrue(self.podman.has_init_binary())
        self.assertEqual(self.run.call_args.args[1][:2], ["sh", "-c"])
        self.assertIn("catatonit", self.run.call_args.args[1][2])
        self.run.return_value = done(1)
        self.assertFalse(self.podman.has_init_binary())

    def test_a_wsl_that_did_not_answer_is_an_error_not_podmans_no(self):
        for code, text in ((wsl.TIMED_OUT, "no answer from WSL within 30 s"), (wsl.NOT_FOUND, "could not run wsl.exe")):
            self.run.return_value = done(code, "", text + "\n")
            with self.assertRaises(PodmanError) as caught:
                self.podman.version()
            self.assertEqual(str(caught.exception), f"podman --version: {text}")
            with self.assertRaises(PodmanError) as caught:
                self.podman.image_exists("img")
            self.assertEqual(str(caught.exception), f"podman image: {text}")

    def test_podman_that_answers_no_is_still_none_and_false(self):
        self.run.return_value = done(125, "", "no such image")
        self.assertIsNone(self.podman.version())
        self.assertFalse(self.podman.image_exists("img"))

    def test_image_exists(self):
        self.assertTrue(self.podman.image_exists("img"))
        self.assertEqual(self.asked(), ["image", "exists", "img"])
        self.run.return_value = done(1)
        self.assertFalse(self.podman.image_exists("img"))

    def test_is_running_needs_podman_to_answer_true(self):
        self.run.return_value = done(0, "true\n")
        self.assertTrue(self.podman.is_running("c"))
        self.run.return_value = done(0, "false\n")
        self.assertFalse(self.podman.is_running("c"))
        self.run.return_value = done(125, "true\n")  # no such container: whatever it printed
        self.assertFalse(self.podman.is_running("c"))

    def test_the_tail_of_the_log_is_stdout_and_stderr_together(self):
        self.run.return_value = done(0, "one\n", "two\n")
        self.assertEqual(self.podman.logs_tail("c", 7), "one\ntwo")
        self.assertEqual(self.asked(), ["logs", "--tail", "7", "c"])

    def test_ps_lists_the_containers_with_the_label(self):
        rows = [{"Names": ["a"]}, {"Names": ["b"]}]
        self.run.return_value = done(0, json.dumps(rows))
        self.assertEqual(self.podman.ps(), rows)
        self.assertIn("label=mrrsim", self.asked())
        self.podman.ps("other")
        self.assertIn("label=other", self.asked())

    def test_ps_gives_an_empty_list_whatever_goes_wrong_and_says_so(self):
        failures = (
            (done(125, "", "cannot connect to podman"), "podman ps failed (exit 125): cannot connect to podman"),
            (done(1, "[]"), "podman ps failed (exit 1)"),
            (done(0, "not json"), "not a list: not json"),
            (done(0, '{"a": 1}'), "not a list"),
            (done(0, "null"), "not a list"),
        )
        for answer, warning in failures:
            self.run.return_value = answer
            with self.assertLogs("mrrsim", level="WARNING") as logs:
                self.assertEqual(self.podman.ps(), [], answer)
            self.assertIn(warning, "\n".join(logs.output))

    def test_no_containers_is_no_warning(self):
        for out in ("[]", "", "  \n"):
            self.run.return_value = done(0, out)
            with self.assertNoLogs("mrrsim", level="WARNING"):
                self.assertEqual(self.podman.ps(), [])


class Starting(Fake):
    def test_a_detached_container_returns_its_id_and_gets_time_to_unpack_its_image(self):
        self.run.return_value = done(0, "abc123\n")
        mount = Mount("/mnt/c/x", "/work")
        container = self.podman.run_detached("img", "n", command=["c"], env={"A": "1"}, mounts=[mount],
                                             labels={"mrrsim": "1"}, stop_timeout=5)
        self.assertEqual(container, "abc123")
        args = self.asked()
        self.assertEqual(args[:3], ["run", "-d", "--rm"])
        self.assertEqual(args[-2:], ["img", "c"])
        for wanted in ("A=1", "mrrsim=1", "/mnt/c/x:/work:ro"):
            self.assertIn(wanted, args)
        self.assertEqual(self.run.call_args.kwargs["timeout"], 300)

    def test_the_wrappers_init_setting_decides_about_init(self):
        Podman("mrr", init=False).run_detached("img", "n")
        self.assertNotIn("--init", self.asked())
        self.podman.run_detached("img", "n")
        self.assertIn("--init", self.asked())

    def test_a_dry_run_starts_nothing(self):
        dry = Podman("mrr", dry_run=True)
        self.assertEqual(dry.run_detached("img", "n"), "")
        self.assertIsNone(dry.run_attached("img", "n"))
        self.run.assert_not_called()
        self.popen.assert_not_called()

    def test_an_attached_container_has_its_output_merged_for_reading(self):
        proc = self.podman.run_attached("img", "n", command=["python", "-u", "x.py"])
        self.assertIs(proc, self.popen.return_value)
        self.assertEqual(self.popen.call_args.args[0], "mrr")
        argv = self.popen.call_args.args[1]
        self.assertEqual(argv[:2], ["podman", "run"])
        self.assertNotIn("-d", argv)
        self.assertEqual(argv[-4:], ["img", "python", "-u", "x.py"])
        kwargs = self.popen.call_args.kwargs
        self.assertIs(kwargs["stdout"], subprocess.PIPE)
        self.assertIs(kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual((kwargs["encoding"], kwargs["errors"], kwargs["bufsize"]), ("utf-8", "replace", 1))


class Stopping(Fake):
    def test_nothing_to_stop_is_a_success_without_asking_podman(self):
        self.assertTrue(self.podman.stop([]))
        self.run.assert_not_called()

    def test_containers_that_do_not_exist_are_fine_and_the_wait_grows_with_their_number(self):
        self.assertTrue(self.podman.stop(["a", "b", "c"], timeout=5))
        self.assertEqual(self.asked(), ["stop", "--ignore", "-t", "5", "a", "b", "c"])
        self.assertEqual(self.run.call_args.kwargs["timeout"], 5 * 3 + 30)

    def test_a_failure_is_returned_and_logged_not_raised(self):
        self.run.return_value = done(125, "", "boom\n")
        with self.assertLogs("mrrsim", level="DEBUG") as logs:
            self.assertFalse(self.podman.stop(["a", "b"]))
        self.assertIn("podman stop a b failed: boom", "\n".join(logs.output))

    def test_removing_without_names_asks_nothing_and_with_names_forces_it(self):
        self.podman.rm_force([])
        self.run.assert_not_called()
        self.podman.rm_force(["a", "b"])
        self.assertEqual(self.asked(), ["rm", "-f", "--ignore", "a", "b"])

    def test_reap_removes_every_container_with_the_label_and_names_them(self):
        rows = [{"Names": ["a", "b"]}, {"Names": []}, {}, {"Names": None}, {"Names": ["c"]}]
        self.run.side_effect = [done(0, json.dumps(rows)), done(0)]
        self.assertEqual(self.podman.reap(), ["a", "b", "c"])
        self.assertEqual(self.asked(), ["rm", "-f", "--ignore", "a", "b", "c"])

    def test_reap_with_nothing_to_remove_removes_nothing(self):
        self.run.return_value = done(0, "[]")
        self.assertEqual(self.podman.reap(), [])
        self.assertEqual(self.run.call_count, 1)  # only the question

    def test_a_removal_that_podman_refuses_is_an_error_not_a_success(self):
        with self.assertRaises(PodmanError) as caught:
            self.run.side_effect = [done(0, json.dumps([{"Names": ["stuck"]}])), done(125, "", "container is busy")]
            self.podman.reap()
        self.assertEqual(str(caught.exception), "podman rm failed (exit 125): container is busy")

    def test_rm_force_raises_when_podman_refuses_and_is_quiet_when_it_works(self):
        self.podman.rm_force(["a"])
        self.run.return_value = done(1, "", "busy")
        with self.assertRaises(PodmanError):
            self.podman.rm_force(["a"])

    def test_a_dry_run_reap_removes_nothing_and_raises_nothing(self):
        dry = Podman("mrrsim-no-such-distro", dry_run=True)
        self.run.return_value = done(0, json.dumps([{"Names": ["a"]}]))
        self.assertEqual(dry.reap(), ["a"])  # the question is asked; the removal is only printed


class CopyingBuildingAndLogs(Fake):
    def test_copying_out_of_a_container(self):
        self.assertTrue(self.podman.copy_from("c", "/x/y", "/tmp/z"))
        self.assertEqual(self.asked(), ["cp", "c:/x/y", "/tmp/z"])
        self.run.return_value = done(1, "", "no such file")
        with self.assertLogs("mrrsim", level="DEBUG") as logs:
            self.assertFalse(self.podman.copy_from("c", "/x/y", "/tmp/z"))
        self.assertIn("podman cp c:/x/y failed: no such file", "\n".join(logs.output))

    def test_a_build_shows_its_output_and_returns_podmans_exit_code(self):
        self.call.return_value = 3
        self.assertEqual(self.podman.build(["-t", "x", "."]), 3)
        self.assertEqual(self.call.call_args.args, ("mrr", ["podman", "build", "-t", "x", "."]))

    def test_a_dry_run_build_builds_nothing(self):
        self.assertEqual(Podman("mrr", dry_run=True).build(["."]), 0)
        self.call.assert_not_called()

    def test_following_a_log_writes_into_the_file_and_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.log"
            follower = self.podman.follow_logs("c", path)
            self.assertIsInstance(follower, LogFollower)
            self.assertEqual(self.popen.call_args.args, ("mrr", ["podman", "logs", "-f", "c"]))
            self.assertTrue(path.exists())
            handle = self.popen.call_args.kwargs["stdout"]
            self.assertFalse(handle.closed)
            self.popen.return_value.poll.return_value = None
            follower.stop()
            self.assertTrue(handle.closed)
        self.popen.return_value.terminate.assert_called_once()

    def test_a_wsl_exe_that_cannot_be_started_is_a_podman_error_everywhere(self):
        self.popen.side_effect = FileNotFoundError("wsl.exe")
        self.call.side_effect = FileNotFoundError("wsl.exe")
        with self.assertRaises(PodmanError) as caught:
            self.podman.run_attached("img", "n")
        self.assertEqual(str(caught.exception), "podman run: could not run wsl.exe: wsl.exe")
        with self.assertRaises(PodmanError) as caught:
            self.podman.build(["."])
        self.assertIn("podman build: could not run wsl.exe", str(caught.exception))
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(PodmanError) as caught:
            self.podman.follow_logs("c", Path(tmp) / "c.log")
        self.assertIn("podman logs: could not run wsl.exe", str(caught.exception))

    def test_the_log_file_is_closed_when_the_follower_cannot_be_started(self):
        self.popen.side_effect = OSError("no wsl")
        opened = []

        def spy(*args, **kwargs):
            opened.append(open(*args, **kwargs))
            return opened[-1]

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(podman_module, "open", spy, create=True):
            with self.assertRaises(PodmanError):
                self.podman.follow_logs("c", Path(tmp) / "c.log")
            self.assertTrue(opened[0].closed)

    def test_a_dry_run_follows_nothing(self):
        self.assertIsNone(Podman("mrr", dry_run=True).follow_logs("c", Path("unused.log")))
        self.popen.assert_not_called()


class Follower(unittest.TestCase):
    def follower(self, poll):
        proc, handle = mock.Mock(), mock.Mock()
        proc.poll.return_value = poll
        return LogFollower(proc, handle), proc, handle

    def test_a_follower_that_already_ended_is_only_closed(self):
        follower, proc, handle = self.follower(poll=0)
        follower.stop()
        proc.terminate.assert_not_called()
        handle.close.assert_called_once()

    def test_a_running_follower_is_asked_to_end(self):
        follower, proc, handle = self.follower(poll=None)
        follower.stop()
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once_with(timeout=3)
        proc.kill.assert_not_called()
        handle.close.assert_called_once()

    def test_one_that_will_not_end_is_killed(self):
        follower, proc, handle = self.follower(poll=None)
        proc.wait.side_effect = subprocess.TimeoutExpired("x", 3)
        follower.stop()
        proc.kill.assert_called_once()
        handle.close.assert_called_once()


class Labels(unittest.TestCase):
    def labels(self, args) -> list[str]:
        return [args[i + 1] for i, word in enumerate(args) if word == "--label"]

    def test_every_container_carries_the_label_without_being_asked(self):
        self.assertEqual(self.labels(run_args("img", name="n")), ["mrrsim=1"])

    def test_the_labels_of_the_caller_come_with_it_and_cannot_take_it_away(self):
        args = run_args("img", name="n", labels={"mrrsim.session": "s"})
        self.assertEqual(self.labels(args), ["mrrsim=1", "mrrsim.session=s"])
        self.assertEqual(self.labels(run_args("img", name="n", labels={"mrrsim": "1"})), ["mrrsim=1"])


class Mounts(unittest.TestCase):
    def test_the_mount_is_the_value_of_dash_v(self):
        self.assertEqual(Mount("/mnt/c/x", "/work").arg(), "/mnt/c/x:/work:ro")
        self.assertEqual(Mount("/a", "/b", "rw").arg(), "/a:/b:rw")
        self.assertEqual(Mount("/a", "/work", "O,upperdir=/u,workdir=/w").arg(), "/a:/work:O,upperdir=/u,workdir=/w")


if __name__ == "__main__":
    unittest.main()
