"""runtime/bundle.py: `mrrsim bundle`. WSL, podman and the preflight report are replaced by fakes and every file is in a
temporary folder, so these run anywhere and write nothing of the user's."""
import contextlib
import io
import re
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
from mrrsim.runtime import bundle
from mrrsim.runtime.doctor import OK, Check


def done(out="", err="", code=0):
    return subprocess.CompletedProcess([], code, out, err)


class Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class Probes(unittest.TestCase):
    def test_what_a_probe_returns_is_kept(self):
        self.assertEqual(bundle._capture(lambda: "text"), "text")

    def test_a_probe_that_fails_becomes_a_line_saying_so(self):
        def broken():
            raise RuntimeError("boom")
        self.assertEqual(bundle._capture(broken), "(failed: boom)")

    def test_ctrl_c_is_not_swallowed(self):
        def interrupted():
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            bundle._capture(interrupted)

    def test_wsl_text_is_output_and_errors_together_without_the_ends(self):
        with mock.patch.object(bundle.wsl, "wsl_command", return_value=done("  out\n", "err\n")) as command:
            self.assertEqual(bundle._wsl_text(["--status"]), "out\nerr")
        command.assert_called_once_with(["--status"])

    def test_podman_text_asks_for_a_minute_and_never_raises_for_a_failure(self):
        podman = mock.Mock()
        podman.run.return_value = done("", "no such thing\n", code=125)
        self.assertEqual(bundle._podman_text(podman, ["info"]), "no such thing")
        podman.run.assert_called_once_with(["info"], timeout=60, check=False)


class Adding(Folder):
    def setUp(self):
        super().setUp()
        self.omitted = []

    def made(self, fill):
        """The zip after `fill(zipped)`."""
        path = self.tmp / "out.zip"
        with zipfile.ZipFile(path, "w") as zipped:
            fill(zipped)
        zipped = zipfile.ZipFile(path)
        self.addCleanup(zipped.close)
        return zipped

    def write(self, name, size=3):
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        return path

    def test_a_file_goes_in_under_the_name_given(self):
        source = self.write("a.log", 5)
        zipped = self.made(lambda z: bundle._add_file(z, source, "logs/one.txt", self.omitted))
        self.assertEqual(zipped.namelist(), ["logs/one.txt"])
        self.assertEqual(zipped.read("logs/one.txt"), b"xxxxx")
        self.assertEqual(self.omitted, [])

    def test_a_file_that_is_not_there_is_just_not_there_and_not_a_loss(self):
        zipped = self.made(lambda z: bundle._add_file(z, self.tmp / "nothing.log", "x", self.omitted))
        self.assertEqual((zipped.namelist(), self.omitted), ([], []))

    def test_a_folder_is_not_a_file(self):
        (self.tmp / "dir").mkdir()
        zipped = self.made(lambda z: bundle._add_file(z, self.tmp / "dir", "x", self.omitted))
        self.assertEqual((zipped.namelist(), self.omitted), ([], []))

    def test_a_file_bigger_than_the_limit_is_left_out_and_noted_and_one_exactly_at_it_is_not(self):
        with mock.patch.object(bundle, "_MAX_FILE", 3 * 1024**2):
            big, exact = self.write("big.log", 3 * 1024**2 + 1), self.write("exact.log", 3 * 1024**2)
            zipped = self.made(lambda z: (bundle._add_file(z, big, "logs/big", self.omitted),
                                          bundle._add_file(z, exact, "logs/exact", self.omitted)))
        self.assertEqual(zipped.namelist(), ["logs/exact"])
        self.assertEqual(self.omitted, ["logs/big: 3 MB is over the 3 MB limit"])

    def test_a_file_that_cannot_be_read_is_left_out_and_noted_with_the_reason(self):
        source = self.write("locked.log")

        def fill(z):
            with mock.patch.object(z, "write", side_effect=PermissionError("in use by another process")):
                bundle._add_file(z, source, "logs/locked", self.omitted)
        self.assertEqual(self.made(fill).namelist(), [])
        self.assertEqual(self.omitted, ["logs/locked: could not be read (in use by another process)"])

    def test_a_tree_goes_in_below_the_prefix_with_forward_slashes_in_order(self):
        self.write("root/b.txt")
        self.write("root/sub/deeper/a.txt")
        self.write("root/sub/c.txt")
        zipped = self.made(lambda z: bundle._add_tree(z, self.tmp / "root", "logs/run", self.omitted))
        self.assertEqual(zipped.namelist(), ["logs/run/b.txt", "logs/run/sub/c.txt", "logs/run/sub/deeper/a.txt"])

    def test_the_files_of_a_tree_that_are_left_out_are_noted_by_their_names_in_the_zip(self):
        self.write("root/small.txt")
        self.write("root/sub/huge.log", 2 * 1024**2)
        with mock.patch.object(bundle, "_MAX_FILE", 1024**2):
            zipped = self.made(lambda z: bundle._add_tree(z, self.tmp / "root", "logs/run", self.omitted))
        self.assertEqual(zipped.namelist(), ["logs/run/small.txt"])
        self.assertEqual(self.omitted, ["logs/run/sub/huge.log: 2 MB is over the 1 MB limit"])

    def test_a_tree_that_is_not_there_adds_nothing(self):
        zipped = self.made(lambda z: bundle._add_tree(z, self.tmp / "missing", "x", self.omitted))
        self.assertEqual((zipped.namelist(), self.omitted), ([], []))


class Creating(Folder):
    def setUp(self):
        super().setUp()
        self.cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        self.settings = self.tmp / "mrrsim.toml"
        self.settings.write_bytes(b"# the settings\n")
        self.cfg.source = self.settings
        sim = self.tmp / "Windows" / "Sim"
        (sim / "Binaries" / "Win64").mkdir(parents=True)
        (sim / "Saved" / "Logs").mkdir(parents=True)
        (sim / "projectairsim_server.log").write_text("server log", encoding="utf-8")
        (sim / "Saved" / "Logs" / "Sim.log").write_text("unreal log", encoding="utf-8")
        self.cfg.unreal.exe = sim / "Binaries" / "Win64" / "Sim-Win64-Shipping.exe"
        self.session = self.tmp / "sessions" / "20260101-120000"
        self.session.mkdir(parents=True)
        (self.session / "launcher.log").write_text("launcher log", encoding="utf-8")
        (self.session / "sitl-0.log").write_text("sitl log", encoding="utf-8")
        (self.tmp / "current.json").write_text("{}", encoding="utf-8")
        self.wslconfig = self.tmp / ".wslconfig"
        self.wslconfig.write_text("[wsl2]\n", encoding="utf-8")
        self.podman = mock.Mock()
        self.podman.run.side_effect = lambda args, **kw: done(f"podman {' '.join(args)}")

    def create(self, session="latest", podman=None, checks=None, say=None, wsl_answer=None):
        patches = [
            mock.patch.object(bundle.paths, "bundles_dir", return_value=self.tmp / "bundles"),
            mock.patch.object(bundle.paths, "state_file", return_value=self.tmp / "current.json"),
            mock.patch.object(bundle.paths, "latest_session", return_value=self.session if session == "latest" else None),
            mock.patch.object(bundle.wsl, "wslconfig_path", return_value=self.wslconfig),
            mock.patch.object(bundle.wsl, "wsl_command", side_effect=wsl_answer or (lambda args: done(f"wsl {' '.join(args)}"))),
            mock.patch.object(bundle.doctor, "run_checks", **(checks or {"return_value": [Check("RAM", OK, "32 GB")]})),
            mock.patch.object(bundle, "Podman", return_value=podman or self.podman),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        out = bundle.create_bundle(self.cfg, say=say)
        zipped = zipfile.ZipFile(out)
        self.addCleanup(zipped.close)
        return out, zipped

    def test_the_zip_is_made_in_the_bundles_folder_which_is_created_when_needed(self):
        out, _ = self.create()
        self.assertEqual(out.parent, self.tmp / "bundles")
        self.assertRegex(out.name, r"^mrrsim-support-\d{8}-\d{6}\.zip$")
        self.assertTrue(out.is_file())

    def test_everything_a_maintainer_needs_is_in_it_in_a_fixed_order(self):
        _, zipped = self.create()
        self.assertEqual(zipped.namelist(), [
            "README.txt", "doctor.txt",
            "system/wsl-status.txt", "system/wsl-distros.txt", "system/podman-version.txt", "system/podman-info.txt",
            "system/podman-ps.txt", "system/podman-images.txt",
            "config/mrrsim.toml", "config/wslconfig.txt", "config/current.json",
            "sessions/20260101-120000/launcher.log", "sessions/20260101-120000/sitl-0.log",
            "unreal/projectairsim_server.log", "unreal/Saved-Logs/Sim.log",
        ])

    def test_what_the_probes_printed_is_what_is_written(self):
        _, zipped = self.create()
        self.assertEqual(zipped.read("system/wsl-status.txt"), b"wsl --status")
        self.assertEqual(zipped.read("system/wsl-distros.txt"), b"wsl -l -v")
        self.assertEqual(zipped.read("system/podman-ps.txt"), b"podman ps -a")
        self.assertEqual(zipped.read("config/mrrsim.toml"), b"# the settings\n")
        self.assertIn(b"[ OK ] RAM: 32 GB", zipped.read("doctor.txt"))

    def test_a_probe_that_fails_does_not_stop_the_bundle(self):
        broken = mock.Mock()
        broken.run.side_effect = RuntimeError("podman is gone")
        _, zipped = self.create(podman=broken)
        self.assertEqual(zipped.read("system/podman-info.txt"), b"(failed: podman is gone)")
        self.assertIn("sessions/20260101-120000/launcher.log", zipped.namelist())

    def test_a_failing_preflight_report_does_not_stop_it_either(self):
        _, zipped = self.create(checks={"side_effect": OSError("no powershell")})
        self.assertEqual(zipped.read("doctor.txt"), b"(failed: no powershell)")

    def test_the_probes_are_asked_at_the_same_time(self):
        def slow(*args, **kwargs):
            time.sleep(0.3)
            return done("late")
        self.podman.run.side_effect = slow
        started = time.monotonic()
        _, zipped = self.create(checks={"side_effect": lambda cfg: (time.sleep(0.3), [])[1]}, wsl_answer=slow)
        self.assertLess(time.monotonic() - started, 1.2)  # seven probes of 0.3 s: 2.1 s one after another
        self.assertEqual(zipped.read("system/podman-ps.txt"), b"late")

    def test_it_says_what_it_is_doing(self):
        said = []
        self.create(say=said.append)
        self.assertEqual(len(said), 2)
        self.assertIn("asking WSL, podman and the preflight checks", said[0])
        self.assertIn("a minute", said[0])
        self.assertIn("settings and the logs", said[1])

    def test_without_anyone_listening_it_says_nothing(self):
        self.create()  # no `say`: nothing to fail on

    def test_without_any_run_there_are_no_session_logs(self):
        _, zipped = self.create(session=None)
        self.assertFalse([n for n in zipped.namelist() if n.startswith("sessions/")])

    def test_an_exe_too_shallow_to_have_a_folder_of_logs_adds_none(self):
        self.cfg.unreal.exe = SimpleNamespace(parents=[Path("a"), Path("b")])
        _, zipped = self.create()
        self.assertFalse([n for n in zipped.namelist() if n.startswith("unreal/")])

    def test_nothing_left_out_means_no_list_of_what_was_left_out(self):
        _, zipped = self.create()
        self.assertNotIn("omitted.txt", zipped.namelist())

    def test_what_was_left_out_is_listed_with_the_reasons(self):
        with open(self.session / "unreal.log", "wb") as handle:
            handle.truncate(2 * 1024**2)
        with mock.patch.object(bundle, "_MAX_FILE", 1024**2):
            _, zipped = self.create()
        self.assertNotIn("sessions/20260101-120000/unreal.log", zipped.namelist())
        self.assertEqual(
            zipped.read("omitted.txt").decode("utf-8"),
            "Left out of this bundle:\nsessions/20260101-120000/unreal.log: 2 MB is over the 1 MB limit\n",
        )

    def test_the_readme_describes_only_what_is_there_and_what_may_be(self):
        _, zipped = self.create()
        readme = zipped.read("README.txt").decode("utf-8")
        for entry in re.findall(r"^(\S+?)\s{2,}", readme, re.M):
            if entry != "omitted.txt":  # only there when something was left out
                self.assertTrue(any(name.startswith(entry) for name in zipped.namelist()), entry)
        self.assertIn("omitted.txt", readme)
        self.assertIn("Nothing is uploaded", readme)
        self.assertEqual(readme, bundle._README)


class Command(Folder):
    def run_command(self, **create):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.bundle, "create_bundle", **create), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_bundle(mock.Mock(), mock.Mock())
        return code, out.getvalue(), err.getvalue()

    def test_it_reports_where_the_zip_is(self):
        code, out, _ = self.run_command(return_value=Path("C:/b/x.zip"))
        self.assertEqual(code, 0)
        self.assertIn("support bundle written to", out)
        self.assertIn("x.zip", out)

    def test_it_hands_the_progress_messages_to_the_console(self):
        def create(cfg, say):
            say("working on it")
            return Path("x.zip")
        _, out, _ = self.run_command(side_effect=create)
        self.assertIn("working on it", out)

    def test_a_folder_that_cannot_be_written_is_an_error_message_not_a_traceback(self):
        blocker = self.tmp / "bundles"
        blocker.write_text("a file is in the way", encoding="utf-8")
        cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(paths, "bundles_dir", return_value=blocker), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_bundle(cfg, mock.Mock())
        self.assertEqual(code, 1)
        self.assertIn("error: could not write the support bundle:", err.getvalue())
        self.assertNotIn("written to", out.getvalue())


if __name__ == "__main__":
    unittest.main()
