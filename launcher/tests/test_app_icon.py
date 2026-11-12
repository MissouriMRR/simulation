"""The program's icon: the file is a real multi-size .ico where `paths` says it is, and the exes and the installer use it."""
import re
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import paths

SIZES = {16, 24, 32, 48, 64, 128, 256}


def ico_sizes(data: bytes) -> set[int]:
    """The square sizes an .ico file holds (its header says 0 for 256)."""
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1), "not an .ico file"
    sizes = set()
    for index in range(count):
        width, height = struct.unpack_from("<BB", data, 6 + 16 * index)
        assert width == height, "an icon image must be square"
        sizes.add(width or 256)
    return sizes


class Icon(unittest.TestCase):
    def test_the_file_is_a_multi_size_icon(self):
        icon = paths.app_icon()
        self.assertTrue(icon.is_file(), icon)
        self.assertEqual(ico_sizes(icon.read_bytes()), SIZES)

    def test_installed_it_is_in_the_assets_folder_next_to_the_exe(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", str(Path("C:/Program Files/MRRSimulator/MRRSimulatorGUI.exe"))):
            self.assertEqual(paths.app_icon(), Path("C:/Program Files/MRRSimulator/assets/mrrsim.ico").resolve())


class Packaging(unittest.TestCase):
    """Nothing here builds anything; it only checks that the build files still name the icon, which would
    otherwise be noticed only when someone looks at the installed program."""

    def text(self, *parts) -> str:
        return paths.repo_root().joinpath(*parts).read_text(encoding="utf-8-sig")

    def test_both_programs_are_built_with_it(self):
        spec = self.text("launcher", "mrrsim.spec")
        self.assertIn('"assets", "mrrsim.ico"', spec)
        self.assertEqual(len(re.findall(r"EXE\(.*icon=icon\)", spec)), 2)

    def test_the_installer_has_it_and_ships_it_for_the_window(self):
        iss = self.text("MRRSimulationLauncher.iss")
        self.assertRegex(iss, r"(?m)^SetupIconFile=assets\\mrrsim\.ico\s*$")
        self.assertRegex(iss, r'(?m)^Source: "assets\\\*"; DestDir: "\{app\}\\assets"')


if __name__ == "__main__":
    unittest.main()
