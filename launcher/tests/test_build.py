"""launcher/mrrsim.spec: PyInstaller builds both programs from it, and the command-line one runs.

Off by default (a build takes about 20 s): MRRSIM_BUILD_TESTS=1 turns it on. It builds into a temporary folder, so the
real build (build\\MRRSimulator, made by build.cmd) is not touched, and it needs PyInstaller (pip install pyinstaller).
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mrrsim import __version__
from mrrsim.core import paths

SPEC = paths.repo_root() / "launcher" / "mrrsim.spec"


@unittest.skipUnless(os.environ.get("MRRSIM_BUILD_TESTS") == "1", "set MRRSIM_BUILD_TESTS=1 to build the exes")
@unittest.skipUnless(importlib.util.find_spec("PyInstaller"), "needs PyInstaller (pip install pyinstaller)")
@unittest.skipUnless(os.name == "nt", "the programs are Windows exes")
class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        done = subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(tmp / "dist"),
                               "--workpath", str(tmp / "work"), str(SPEC)], capture_output=True, text=True, timeout=600)
        cls.log = done.stdout + done.stderr
        cls.code = done.returncode
        cls.folder = tmp / "dist" / "MRRSimulator"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(self.folder / "MRRSimulator.exe"), *args], capture_output=True, text=True, timeout=120)

    def test_both_programs_are_built_into_one_folder_sharing_one_runtime(self):
        self.assertEqual(self.code, 0, self.log[-3000:])
        for exe in ("MRRSimulator.exe", "MRRSimulatorGUI.exe"):
            self.assertTrue((self.folder / exe).is_file(), exe)
        self.assertTrue((self.folder / "_internal").is_dir())  # MERGE: one runtime for the two programs

    def test_the_window_has_tk_with_it(self):
        self.assertTrue((self.folder / "_internal" / "_tkinter.pyd").is_file())

    def test_the_command_line_program_runs_and_knows_its_version(self):
        version = self.cli("--version")
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertIn(__version__, version.stdout)
        listed = self.cli("-c", str(paths.repo_root() / "config" / "mrrsim.toml"), "scripts")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("takeoff_land.py", listed.stdout)


if __name__ == "__main__":
    unittest.main()
