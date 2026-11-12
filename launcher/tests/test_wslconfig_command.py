import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config


def run(wslconfig: Path, toml: str = "", **flags) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "mrrsim.toml"
        cfg_path.write_text(toml, encoding="utf-8")
        cfg = config.load(cfg_path)
    args = argparse.Namespace(dry_run=flags.get("dry_run", False), only_networking=flags.get("only_networking", False))
    out = io.StringIO()
    with mock.patch.dict("os.environ", {"MRRSIM_WSLCONFIG": str(wslconfig)}), contextlib.redirect_stdout(out):
        assert cli.cmd_wsl_config(cfg, args) == 0
    return out.getvalue()


class WslConfigCommand(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.file = self.dir / ".wslconfig"

    def text(self) -> str:
        return self.file.read_bytes().decode("utf-8")

    def test_creates_the_file_with_mirrored_networking(self):
        run(self.file, only_networking=True)
        self.assertEqual(self.text(), "[wsl2]\r\nnetworkingMode=mirrored\r\n")

    def test_networking_only_leaves_the_caps_out(self):
        run(self.file, "[wsl]\nmemory = \"4GB\"\nprocessors = 4", only_networking=True)
        self.assertNotIn("memory", self.text())
        self.assertNotIn("processors", self.text())

    def test_without_the_flag_the_caps_are_written_too(self):
        run(self.file, "[wsl]\nmemory = \"4GB\"\nprocessors = 4")
        self.assertIn("networkingMode=mirrored", self.text())
        self.assertIn("memory=4GB", self.text())
        self.assertIn("processors=", self.text())

    def test_an_existing_file_is_kept_and_backed_up(self):
        self.file.write_bytes(b"[wsl2]\r\nnetworkingMode=nat\r\nswap=0\r\n\r\n[experimental]\r\nautoMemoryReclaim=gradual\r\n")
        out = run(self.file, only_networking=True)
        text = self.text()
        self.assertIn("networkingMode=mirrored", text)
        self.assertNotIn("networkingMode=nat", text)
        self.assertIn("swap=0", text)
        self.assertIn("autoMemoryReclaim=gradual", text)
        backup = self.dir / ".wslconfig.mrrsim.bak"
        self.assertIn("networkingMode=nat", backup.read_text())
        self.assertIn("backup of the old file", out)

    def test_an_older_backup_is_never_overwritten(self):
        backup = self.dir / ".wslconfig.mrrsim.bak"
        backup.write_text("the very first original")
        self.file.write_text("[wsl2]\nnetworkingMode=nat\n")
        run(self.file, only_networking=True)
        self.assertEqual(backup.read_text(), "the very first original")

    def test_already_mirrored_changes_nothing(self):
        self.file.write_bytes(b"[wsl2]\r\nnetworkingMode=mirrored\r\n")
        before = self.file.read_bytes()
        self.assertIn("already has", run(self.file, only_networking=True))
        self.assertEqual(self.file.read_bytes(), before)
        self.assertFalse((self.dir / ".wslconfig.mrrsim.bak").exists())

    def test_a_file_with_a_byte_order_mark_is_read(self):
        self.file.write_bytes(b"\xef\xbb\xbf[wsl2]\r\nswap=0\r\n")
        run(self.file, only_networking=True)
        self.assertIn("swap=0", self.text())
        self.assertIn("networkingMode=mirrored", self.text())

    def test_dry_run_writes_nothing(self):
        out = run(self.file, only_networking=True, dry_run=True)
        self.assertIn("would write", out)
        self.assertFalse(self.file.exists())

    def test_nothing_to_do_when_mirrored_is_not_required_and_there_are_no_caps(self):
        out = run(self.file, "[wsl]\nrequire_mirrored = false", only_networking=True)
        self.assertIn("nothing to do", out)
        self.assertFalse(self.file.exists())


if __name__ == "__main__":
    unittest.main()
