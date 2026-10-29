"""Talking to wsl.exe: running commands without ever raising, listing distros,
reading .wslconfig, asking the running distro, and mapping Windows paths."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config
from mrrsim.runtime import doctor, wsl


def load_config() -> config.Config:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mrrsim.toml"
        path.write_text("", encoding="utf-8")
        return config.load(path)


def completed(code=0, out="", err="") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, out, err)


class RunningCommands(unittest.TestCase):
    def test_output_is_decoded_whatever_the_encoding(self):
        raw = subprocess.CompletedProcess([], 3, "ok".encode("utf-16-le"), b"plain")
        with mock.patch.object(wsl.subprocess, "run", return_value=raw):
            result = wsl.run("mrr", ["true"])
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (3, "ok", "plain")
        )

    def test_run_asks_for_the_command_inside_the_distro_and_sends_input(self):
        with mock.patch.object(
            wsl.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ) as run:
            wsl.run("mrr", ["cat"], timeout=5, input_text="hi")
        self.assertEqual(
            run.call_args.args[0], ["wsl.exe", "-d", "mrr", "--exec", "cat"]
        )
        self.assertEqual(run.call_args.kwargs["input"], b"hi")
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        self.assertEqual(run.call_args.kwargs["env"]["WSL_UTF8"], "1")

    def test_a_command_that_does_not_answer_is_reported_not_raised(self):
        hang = subprocess.TimeoutExpired("wsl.exe", 5)
        with mock.patch.object(wsl.subprocess, "run", side_effect=hang):
            for result in (
                wsl.run("mrr", ["true"], timeout=5),
                wsl.wsl_command(["-l"], timeout=5),
            ):
                self.assertEqual(result.returncode, wsl.TIMED_OUT)
                self.assertEqual(result.stderr, "no answer from WSL within 5 s")

    def test_a_missing_wsl_exe_is_reported_not_raised(self):
        gone = FileNotFoundError(2, "The system cannot find the file specified")
        with mock.patch.object(wsl.subprocess, "run", side_effect=gone):
            for result in (wsl.run("mrr", ["true"]), wsl.wsl_command(["--status"])):
                self.assertEqual(result.returncode, wsl.NOT_FOUND)
                self.assertIn("could not run wsl.exe", result.stderr)

    def test_wsl_command_runs_wsl_exe_itself(self):
        with mock.patch.object(
            wsl.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, b"x", b""),
        ) as run:
            self.assertEqual(wsl.wsl_command(["--status"]).stdout, "x")
        self.assertEqual(run.call_args.args[0], ["wsl.exe", "--status"])

    def test_popen_and_call_use_the_same_command_line_and_environment(self):
        with mock.patch.object(wsl.subprocess, "Popen") as popen:
            wsl.popen("mrr", ["podman", "ps"], stdout=1)
        self.assertEqual(
            popen.call_args.args[0], ["wsl.exe", "-d", "mrr", "--exec", "podman", "ps"]
        )
        self.assertEqual(popen.call_args.kwargs["stdout"], 1)
        self.assertEqual(popen.call_args.kwargs["env"]["WSL_UTF8"], "1")
        with mock.patch.object(wsl.subprocess, "call", return_value=5) as call:
            self.assertEqual(wsl.call("mrr", ["podman", "build"]), 5)
        self.assertEqual(call.call_args.args[0][-2:], ["podman", "build"])


class Distros(unittest.TestCase):
    def test_the_default_one_is_marked_and_the_header_is_skipped(self):
        text = (
            "  NAME      STATE           VERSION\n"
            "* Ubuntu    Running         2\n"
            "  mrr       Stopped         2\n"
        )
        self.assertEqual(
            wsl.parse_distro_list(text),
            [
                wsl.Distro("Ubuntu", "Running", 2, True),
                wsl.Distro("mrr", "Stopped", 2, False),
            ],
        )

    def test_a_header_in_another_language_is_skipped_too(self):
        text = "  NOM       ÉTAT            VERSION\n  mrr       Stopped         2\n"
        self.assertEqual([d.name for d in wsl.parse_distro_list(text)], ["mrr"])

    def test_nothing_installed(self):
        self.assertEqual(wsl.parse_distro_list(""), [])
        self.assertEqual(wsl.parse_distro_list("  NAME   STATE   VERSION\n"), [])

    def test_list_distros_is_empty_when_wsl_fails_or_is_missing(self):
        for result in (
            completed(1, "", "boom"),
            completed(wsl.NOT_FOUND),
            completed(wsl.TIMED_OUT),
        ):
            with mock.patch.object(wsl, "wsl_command", return_value=result):
                self.assertEqual(wsl.list_distros(), [])

    def test_list_distros_reads_the_table(self):
        out = "  NAME   STATE   VERSION\n* mrr    Running 2\n"
        with mock.patch.object(
            wsl, "wsl_command", return_value=completed(0, out)
        ) as command:
            self.assertEqual(
                wsl.list_distros(), [wsl.Distro("mrr", "Running", 2, True)]
            )
        command.assert_called_once_with(["-l", "-v"])


class WslConfig(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = Path(tmp.name) / ".wslconfig"

    def write(self, text: str) -> Path:
        self.file.write_text(text, encoding="utf-8")
        return self.file

    def test_the_path_can_be_redirected_or_is_in_the_profile_folder(self):
        with mock.patch.dict("os.environ", {"MRRSIM_WSLCONFIG": "C:/x/wsl.cfg"}):
            self.assertEqual(wsl.wslconfig_path(), Path("C:/x/wsl.cfg"))
        env = {"MRRSIM_WSLCONFIG": "", "USERPROFILE": "C:/Users/me"}
        with mock.patch.dict("os.environ", env):
            self.assertEqual(wsl.wslconfig_path(), Path("C:/Users/me/.wslconfig"))

    def test_a_setting_is_found_whatever_its_capitalisation(self):
        path = self.write("[wsl2]\nMemory = 8GB\n")
        self.assertEqual(wsl.wsl2_setting("memory", path), "8GB")
        self.assertEqual(wsl.wsl2_setting("MEMORY", path), "8GB")

    def test_a_comment_after_the_value_is_not_part_of_it(self):
        path = self.write("[wsl2]\nmemory=8GB # for builds\nprocessors=4 ; half\n")
        self.assertEqual(wsl.wsl2_setting("memory", path), "8GB")
        self.assertEqual(wsl.wsl2_setting("processors", path), "4")

    def test_a_missing_file_section_key_or_a_broken_file_gives_none(self):
        self.assertIsNone(wsl.wsl2_setting("memory", self.file))
        self.assertIsNone(wsl.wsl2_setting("memory", self.write("[other]\nmemory=1\n")))
        self.assertIsNone(wsl.wsl2_setting("swap", self.write("[wsl2]\nmemory=1\n")))
        self.assertIsNone(wsl.wsl2_setting("memory", self.write("memory=1\n")))

    def test_the_networking_mode_defaults_to_nat(self):
        self.assertEqual(wsl.networking_mode(self.file), "nat")
        self.assertEqual(wsl.networking_mode(self.write("[wsl2]\nmemory=1\n")), "nat")
        self.assertEqual(
            wsl.networking_mode(self.write("[wsl2]\nnetworkingMode=\n")), "nat"
        )
        self.assertEqual(wsl.networking_mode(self.write("garbage")), "nat")

    def test_the_networking_mode_is_read_lower_cased_without_comments(self):
        path = self.write("[wsl2]\nnetworkingMode=Mirrored # needed\n")
        self.assertEqual(wsl.networking_mode(path), "mirrored")

    def test_keys_are_set_in_the_wsl2_section(self):
        self.assertEqual(
            wsl.set_wsl2_keys("", {"memory": "8GB"}), "[wsl2]\nmemory=8GB\n"
        )


class Processors(unittest.TestCase):
    def effective(self, logical: int, requested: int) -> int:
        with mock.patch.object(wsl, "logical_processors", return_value=logical):
            return wsl.effective_processors(requested)

    def test_none_requested_means_no_limit(self):
        self.assertEqual(self.effective(16, 0), 0)

    def test_at_most_half_of_the_machine(self):
        self.assertEqual(self.effective(16, 4), 4)
        self.assertEqual(self.effective(16, 20), 8)

    def test_at_least_two_when_the_machine_has_them(self):
        self.assertEqual(self.effective(4, 8), 2)
        self.assertEqual(self.effective(2, 8), 2)

    def test_never_more_than_the_machine_has(self):
        self.assertEqual(self.effective(1, 8), 1)

    def test_the_machine_has_at_least_one(self):
        with mock.patch.object(wsl.os, "cpu_count", return_value=None):
            self.assertEqual(wsl.logical_processors(), 1)


class EffectiveNetworking(unittest.TestCase):
    def ask(self, result):
        with mock.patch.object(wsl, "run", return_value=result):
            return wsl.effective_networking_mode("mrr")

    def test_the_mode_the_vm_reports(self):
        self.assertEqual(self.ask(completed(0, "Mirrored\n")), ("mirrored", ""))

    def test_a_wsl_without_wslinfo_cannot_say(self):
        self.assertEqual(self.ask(completed(wsl.NOT_FOUND, "", "")), (None, ""))
        self.assertEqual(self.ask(completed(0, "\n")), (None, ""))

    def test_a_fallback_is_explained_with_wsls_own_words_and_error_code(self):
        err = (
            "wsl: Failed to configure network (networkingMode Mirrored), "
            "falling back to networkingMode None.\n"
            "Error code: Wsl/Service/CreateInstance/CreateVm/"
            "ConfigureNetworking/0x8007054f\n"
        )
        mode, problem = self.ask(completed(0, "none\n", err))
        self.assertEqual(mode, "none")
        self.assertIn("falling back to networkingMode None", problem)
        self.assertTrue(problem.endswith("ConfigureNetworking/0x8007054f]"))

    def test_a_wsl_that_does_not_answer_says_so(self):
        self.assertEqual(
            self.ask(completed(wsl.TIMED_OUT, "", "no answer from WSL within 60 s")),
            (None, "no answer from WSL within 60 s"),
        )


class DoctorNetworkingCheck(unittest.TestCase):
    def check(self, answer):
        with mock.patch.object(wsl, "effective_networking_mode", return_value=answer):
            return doctor._effective_networking(load_config())

    def test_a_vm_that_could_not_be_asked_because_wsl_hung_is_a_warning(self):
        check = self.check((None, "no answer from WSL within 60 s"))
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("no answer from WSL", check.detail)

    def test_a_vm_without_wslinfo_is_fine(self):
        self.assertEqual(self.check((None, "")).status, doctor.OK)

    def test_mirrored_is_fine_and_anything_else_fails(self):
        self.assertEqual(self.check(("mirrored", "")).status, doctor.OK)
        self.assertEqual(self.check(("none", "fell back")).status, doctor.FAIL)


class CanConnect(unittest.TestCase):
    def test_no_bash_or_timeout_in_the_distro_cannot_be_tested(self):
        with mock.patch.object(wsl, "run", return_value=completed(wsl.NOT_FOUND)):
            self.assertIsNone(wsl.can_connect("mrr", "127.0.0.1", 8989))


class MountRoot(unittest.TestCase):
    def setUp(self):
        wsl._AUTOMOUNT_ROOTS.clear()
        self.addCleanup(wsl._AUTOMOUNT_ROOTS.clear)

    def root(self, result):
        with mock.patch.object(wsl, "run", return_value=result) as run:
            root = wsl.automount_root("mrr")
        return root, run

    def test_it_is_slash_mnt_unless_the_distro_says_otherwise(self):
        self.assertEqual(self.root(completed(1, "", "no such file"))[0], "/mnt")
        self.assertEqual(self.root(completed(0, "[boot]\nsystemd=true\n"))[0], "/mnt")

    def test_the_root_comes_from_the_automount_section_of_wsl_conf(self):
        for text in (
            "[automount]\nroot = /windir/\n",
            "[automount]\nroot=/windir # drives\n",
        ):
            wsl._AUTOMOUNT_ROOTS.clear()
            with self.subTest(text=text):
                self.assertEqual(self.root(completed(0, text))[0], "/windir")

    def test_a_wsl_conf_that_cannot_be_parsed_gives_the_default(self):
        self.assertEqual(self.root(completed(0, "root=/x\n"))[0], "/mnt")

    def test_it_is_asked_once_per_distro(self):
        with mock.patch.object(
            wsl, "run", return_value=completed(0, "[automount]\nroot=/w\n")
        ) as run:
            self.assertEqual(wsl.automount_root("mrr"), "/w")
            self.assertEqual(wsl.automount_root("mrr"), "/w")
        run.assert_called_once()
        self.assertEqual(run.call_args.args, ("mrr", ["cat", "/etc/wsl.conf"]))

    def test_a_distro_that_did_not_answer_is_asked_again_later(self):
        root, _ = self.root(completed(wsl.TIMED_OUT, "", "no answer"))
        self.assertEqual(root, "/mnt")
        self.assertEqual(self.root(completed(0, "[automount]\nroot=/w\n"))[0], "/w")

    def test_paths_use_the_distros_root_when_given_one(self):
        path = Path("C:/Users/me/x")
        self.assertEqual(wsl.to_wsl_path(path), "/mnt/c/Users/me/x")
        with mock.patch.object(wsl, "automount_root", return_value="/windir"):
            self.assertEqual(wsl.to_wsl_path(path, "mrr"), "/windir/c/Users/me/x")


if __name__ == "__main__":
    unittest.main()
