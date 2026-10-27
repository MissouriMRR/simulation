import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
from mrrsim.ui.run import commands
from mrrsim.userscripts import scripts


class Split(unittest.TestCase):
    def test_a_command_line_style_split(self):
        self.assertEqual(scripts.split_args("-s"), ["-s"])
        self.assertEqual(scripts.split_args('--mode fast --name "two words"'), ["--mode", "fast", "--name", "two words"])
        self.assertEqual(scripts.split_args("   "), [])

    def test_an_unclosed_quote_is_an_error(self):
        with self.assertRaises(ValueError):
            scripts.split_args('--name "oops')


class Remembered(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patch = mock.patch.dict("os.environ", {"MRRSIM_SCRIPT_ARGS_FILE": str(Path(self._tmp.name) / "args.json")})
        patch.start()
        self.addCleanup(patch.stop)

    def test_arguments_are_remembered_per_script(self):
        a, b = Path(self._tmp.name) / "a.py", Path(self._tmp.name) / "b.py"
        self.assertEqual(scripts.load_args(a), "")
        scripts.save_args(a, " -s ")
        scripts.save_args(b, "--airsim")
        self.assertEqual((scripts.load_args(a), scripts.load_args(b)), ("-s", "--airsim"))

    def test_empty_text_forgets_them(self):
        a = Path(self._tmp.name) / "a.py"
        scripts.save_args(a, "-s")
        scripts.save_args(a, "  ")
        self.assertEqual(scripts.load_args(a), "")

    def test_a_damaged_file_is_ignored_and_replaced(self):
        a = Path(self._tmp.name) / "a.py"
        scripts.args_file().write_text("{broken", encoding="utf-8")
        self.assertEqual(scripts.load_args(a), "")
        scripts.save_args(a, "-s")
        self.assertEqual(scripts.load_args(a), "-s")


class OnTheCommandLine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        (self.tmp / "run.py").write_text("import sys\nprint(sys.argv)\n", encoding="utf-8")
        self.addCleanup(cli.session.SHUTTING_DOWN.clear)

    def dry_run(self, *extra: str) -> str:
        argv = ["-c", str(paths.repo_root() / "config" / "mrrsim.toml"), "run", str(self.tmp / "run.py"),
                "--dry-run", "--no-unreal", "--no-scene", "--vehicles", "1", *extra]
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"MRRSIM_DATA_DIR": str(self.tmp / "data")}), redirect_stdout(out), \
                self.assertLogs(level="INFO") as logs:
            code = cli.main(argv)
        self.assertEqual(code, 0)
        return "\n".join(logs.output)

    def test_the_arguments_follow_the_script_in_the_container_command(self):
        text = self.dry_run("--script-args=-s --fast")
        self.assertIn("/work/run.py -s --fast", text)

    def test_without_arguments_the_command_ends_with_the_script(self):
        text = [l for l in self.dry_run().splitlines() if "podman run" in l and "mrrsim-script" in l][0]
        self.assertTrue(text.rstrip().endswith("/work/run.py"), text[-80:])

    def test_a_broken_quote_is_reported_as_a_settings_error(self):
        cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        args = mock.Mock(script=None, vehicles=1, script_args='--x "oops', resolution=None, fullscreen=False)
        with self.assertRaises(config.ConfigError):
            cli.cmd_up(cfg, args)


class InTheWindowCommand(unittest.TestCase):
    def test_the_window_passes_them_as_one_equals_argument(self):
        with mock.patch.object(commands, "cli_command", return_value=["mrrsim"]):
            command = commands.build_run_command(Path("c.toml"), Path("a.py"), 1, False, None, script_args=" -s ")
            plain = commands.build_run_command(Path("c.toml"), Path("a.py"), 1, False, None)
        self.assertIn("--script-args=-s", command)
        self.assertFalse([c for c in plain if c.startswith("--script-args")])


if __name__ == "__main__":
    unittest.main()
