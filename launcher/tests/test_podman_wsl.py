import tempfile
import unittest
from pathlib import Path

from mrrsim.runtime import wsl
from mrrsim.runtime.podman import Mount, Podman, run_args


class RunArgs(unittest.TestCase):
    def test_detached_sitl_container(self):
        args = run_args(
            "localhost/mrrsim-sitl:copter-4.7.0",
            name="mrrsim-sitl-1",
            detach=True,
            env={"INSTANCE": "1", "SYSID": "2"},
            labels={"mrrsim": "1", "mrrsim.session": "s"},
            stop_timeout=5,
        )
        self.assertEqual(args[:5], ["run", "-d", "--rm", "--init", "--network"])
        self.assertIn("host", args)
        self.assertEqual(args[args.index("--name") + 1], "mrrsim-sitl-1")
        self.assertIn("INSTANCE=1", args)
        self.assertIn("mrrsim.session=s", args)
        self.assertEqual(args[args.index("--stop-timeout") + 1], "5")
        self.assertEqual(args[-1], "localhost/mrrsim-sitl:copter-4.7.0")

    def test_command_comes_after_the_image(self):
        args = run_args("img", name="n", command=["python", "-u", "/work/x.py"], mounts=[Mount("/mnt/c/x", "/work")])
        self.assertEqual(args[-4:], ["img", "python", "-u", "/work/x.py"])
        self.assertIn("/mnt/c/x:/work:ro", args)

    def test_init_and_rm_can_be_disabled(self):
        args = run_args("img", name="n", rm=False, init=False)
        self.assertNotIn("--rm", args)
        self.assertNotIn("--init", args)


class DryRun(unittest.TestCase):
    def test_mutating_commands_are_not_executed(self):
        podman = Podman("no-such-distro", dry_run=True)
        # would raise (wsl.exe missing or distro missing) if it really ran
        self.assertEqual(podman.run(["stop", "x"]).returncode, 0)
        self.assertEqual(podman.run(["rm", "-f", "x"]).returncode, 0)
        self.assertIsNone(podman.run_attached("img", "n"))
        self.assertIsNone(podman.follow_logs("n", Path("unused.log")))
        self.assertIn("--exec podman stop x", podman.display(["stop", "x"]))


class WslHelpers(unittest.TestCase):
    def test_drive_path_to_wsl(self):
        self.assertEqual(wsl.to_wsl_path(Path("C:/Users/Zach/Downloads/BakedSim")), "/mnt/c/Users/Zach/Downloads/BakedSim")
        self.assertEqual(wsl.to_wsl_path(Path("D:/data")), "/mnt/d/data")

    def test_unc_path_is_rejected(self):
        with self.assertRaises(ValueError):
            wsl.to_wsl_path(Path("//server/share/x"))

    def test_parse_distro_list(self):
        text = (
            "  NAME              STATE           VERSION\n"
            "* mrr               Running         2\n"
            "  Ubuntu            Stopped         2\n"
            "  old               Stopped         1\n"
        )
        distros = wsl.parse_distro_list(text)
        self.assertEqual([d.name for d in distros], ["mrr", "Ubuntu", "old"])
        self.assertTrue(distros[0].default)
        self.assertEqual([d.version for d in distros], [2, 2, 1])

    def test_decode_handles_utf16(self):
        self.assertEqual(wsl.decode("NAME STATE".encode("utf-16-le")), "NAME STATE")
        self.assertEqual(wsl.decode(b"plain"), "plain")

    def test_networking_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".wslconfig"
            self.assertEqual(wsl.networking_mode(path), "nat")
            path.write_text("[wsl2]\nnetworkingMode=mirrored\n", encoding="utf-8")
            self.assertEqual(wsl.networking_mode(path), "mirrored")
            path.write_text("[wsl2]\nmemory=8GB\n", encoding="utf-8")
            self.assertEqual(wsl.networking_mode(path), "nat")

    def test_exec_argv_has_no_shell(self):
        self.assertEqual(wsl.exec_argv("mrr", ["podman", "ps"]), ["wsl.exe", "-d", "mrr", "--exec", "podman", "ps"])


if __name__ == "__main__":
    unittest.main()
