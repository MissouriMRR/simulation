"""The version number has one home: `#define AppVersion` in MRRSimulationLauncher.iss. Everything else reads it."""
import importlib
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import mrrsim
from mrrsim.core import paths

ROOT = paths.repo_root()
ISS = ROOT / "MRRSimulationLauncher.iss"


def sync_module():
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        return importlib.import_module("sync_version")
    finally:
        sys.path.remove(str(ROOT / "tools"))


class OneHome(unittest.TestCase):
    def test_the_installer_script_defines_exactly_one_version(self):
        defines = re.findall(r'^\s*#define\s+AppVersion\s+"([^"]+)"', ISS.read_text(encoding="utf-8-sig"), re.MULTILINE)
        self.assertEqual(len(defines), 1)
        self.assertRegex(defines[0], r"^\d+\.\d+\.\d+$")

    def test_the_package_reports_the_installer_scripts_version(self):
        self.assertEqual(mrrsim.__version__, mrrsim.version_from_iss(ISS))
        self.assertNotEqual(mrrsim.__version__, "unknown")

    def test_the_command_line_reports_it(self):
        from mrrsim import cli

        with self.assertRaises(SystemExit), mock.patch("sys.stdout") as out:
            cli.build_parser().parse_args(["--version"])
        self.assertIn(f"mrrsim {mrrsim.__version__}", "".join(call.args[0] for call in out.write.call_args_list))

    def test_the_project_file_does_not_keep_a_copy(self):
        project = tomllib.loads((ROOT / "launcher" / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertNotIn("version", project)
        self.assertIn("version", project["dynamic"])

    def test_no_python_file_assigns_a_literal_version(self):
        for path in (ROOT / "launcher" / "mrrsim").glob("*.py"):
            if path.name == "_version.py":
                continue                      # generated, and not committed
            self.assertIsNone(re.search(r'^__version__\s*=\s*"\d', path.read_text(encoding="utf-8"), re.MULTILINE), path.name)


class Reading(unittest.TestCase):
    def test_the_define_is_found_wherever_it_sits_in_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.iss"
            path.write_text('; comment\n#define AppName "X"\n  #define AppVersion "4.5.6"\n#define Other "1.0.0"\n', encoding="utf-8")
            self.assertEqual(mrrsim.version_from_iss(path), "4.5.6")

    def test_a_missing_file_or_define_gives_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(mrrsim.version_from_iss(Path(tmp) / "nope.iss"))
            empty = Path(tmp) / "empty.iss"
            empty.write_text("#define AppName \"X\"\n", encoding="utf-8")
            self.assertIsNone(mrrsim.version_from_iss(empty))

    def test_a_commented_out_define_is_not_the_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.iss"
            path.write_text('; #define AppVersion "9.9.9"\n#define AppVersion "1.2.3"\n', encoding="utf-8")
            self.assertEqual(mrrsim.version_from_iss(path), "1.2.3")

    def test_a_built_exe_without_the_script_uses_the_generated_file(self):
        fake = mock.Mock(__version__="7.8.9")
        with mock.patch.object(mrrsim, "version_from_iss", return_value=None), \
                mock.patch.dict(sys.modules, {"mrrsim._version": fake}):
            self.assertEqual(mrrsim._find_version(), "7.8.9")

    def test_with_neither_it_says_unknown_rather_than_failing(self):
        with mock.patch.object(mrrsim, "version_from_iss", return_value=None), \
                mock.patch.dict(sys.modules, {"mrrsim._version": None}):
            self.assertEqual(mrrsim._find_version(), "unknown")

    def test_the_script_wins_over_a_stale_generated_file(self):
        fake = mock.Mock(__version__="0.0.1")
        with mock.patch.dict(sys.modules, {"mrrsim._version": fake}):
            self.assertEqual(mrrsim._find_version(), mrrsim.version_from_iss(ISS))


class Sync(unittest.TestCase):
    def test_the_generated_file_holds_the_scripts_version(self):
        sync = sync_module()
        with tempfile.TemporaryDirectory() as tmp:
            iss, out = Path(tmp) / "a.iss", Path(tmp) / "_version.py"
            iss.write_text('#define AppVersion "3.2.1"\n', encoding="utf-8")
            self.assertEqual(sync.main(iss, out), 0)
            namespace: dict = {}
            exec(out.read_text(encoding="utf-8"), namespace)
            self.assertEqual(namespace["__version__"], "3.2.1")
            self.assertIn("Do not edit", out.read_text(encoding="utf-8"))

    def test_a_script_without_a_version_fails_and_writes_nothing(self):
        sync = sync_module()
        with tempfile.TemporaryDirectory() as tmp:
            iss, out = Path(tmp) / "a.iss", Path(tmp) / "_version.py"
            iss.write_text("; nothing here\n", encoding="utf-8")
            with mock.patch("sys.stderr"):
                self.assertEqual(sync.main(iss, out), 1)
            self.assertFalse(out.exists())

    def test_the_launcher_build_runs_the_sync_before_pyinstaller(self):
        text = (ROOT / "launcher" / "build.cmd").read_text(encoding="utf-8")
        self.assertLess(text.index("sync_version.py"), text.index("-m PyInstaller"))

    def test_the_generated_file_is_not_committed(self):
        self.assertIn("/launcher/mrrsim/_version.py", (ROOT / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
