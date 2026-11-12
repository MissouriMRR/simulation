"""tools/build_distro.py: the parts that need no WSL (the version stamp and where it is written)."""
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config, paths

TOOLS = paths.repo_root() / "tools"


def build_distro():
    sys.path.insert(0, str(TOOLS))
    try:
        return importlib.import_module("build_distro")
    finally:
        sys.path.remove(str(TOOLS))


class Stamp(unittest.TestCase):
    def setUp(self):
        self.tool = build_distro()
        self.cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")

    def stamp(self, output: Path) -> str:
        images = mock.Mock(stdout="sha256aaa\nsha256bbb\nsha256aaa\n")
        with mock.patch.object(self.tool, "run_in", return_value=images):
            return self.tool.stamp_distro(self.cfg, output)

    def test_a_fresh_clone_has_no_payload_folder_and_the_stamp_is_still_written(self):
        """payload/ is not in git, so on another computer the folder does not exist yet."""
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "payload" / "mrr-rootfs.tar.gz"
            self.assertFalse(output.parent.exists())
            digest = self.stamp(output)
            include = output.parent / "distro_version.iss"
            self.assertEqual(include.read_text(encoding="utf-8"), f'#define DistroVersion "{digest}"\n')

    def test_the_stamp_is_twelve_hex_digits_and_follows_the_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "mrr-rootfs.tar.gz"
            first = self.stamp(output)
            self.assertRegex(first, r"^[0-9a-f]{12}$")
            self.assertEqual(self.stamp(output), first)              # the same images give the same stamp
            other = mock.Mock(stdout="sha256ccc\n")
            with mock.patch.object(self.tool, "run_in", return_value=other):
                self.assertNotEqual(self.tool.stamp_distro(self.cfg, output), first)

    def test_main_makes_the_output_folder_before_the_long_build_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "payload" / "mrr-rootfs.tar.gz"
            argv = ["build_distro.py", "--output", str(output), "--work", str(Path(tmp) / "work"), "--verify-only"]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(self.tool, "verify") as verify:
                self.tool.main()
            self.assertTrue(output.parent.is_dir())
            verify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
