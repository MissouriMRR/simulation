import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
from mrrsim.userscripts import scripts


def write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


class Describe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_description_is_the_first_docstring_line(self):
        info = scripts.describe(write(self.dir, "a.py", '"""Fly a square.\n\nMore detail."""\nprint(1)\n'))
        self.assertEqual(info.description, "Fly a square.")
        self.assertFalse(info.needs_airsim)
        self.assertIsNone(info.vehicles)

    def test_a_paragraph_over_several_lines_is_one_description(self):
        info = scripts.describe(write(self.dir, "p.py", '"""Fly a square:\narm, go, land.\n\nSecond paragraph."""\n'))
        self.assertEqual(info.description, "Fly a square: arm, go, land.")

    def test_projectairsim_imports_are_detected(self):
        for source in ("import projectairsim\n", "from projectairsim import World\n", "import projectairsim.utils as u\n",
                       "def f():\n    from projectairsim.types import Pose\n"):
            self.assertTrue(scripts.describe(write(self.dir, "b.py", source)).needs_airsim, source)
        self.assertFalse(scripts.describe(write(self.dir, "c.py", "import dronekit  # projectairsim\n")).needs_airsim)

    def test_vehicles_header(self):
        self.assertEqual(scripts.describe(write(self.dir, "d.py", "# mrrsim: vehicles=3\nx = 1\n")).vehicles, 3)
        self.assertEqual(scripts.describe(write(self.dir, "e.py", "#MRRSIM: Vehicles = 2\n")).vehicles, 2)
        self.assertIsNone(scripts.describe(write(self.dir, "f.py", "# mrrsim: vehicles=lots\n")).vehicles)
        self.assertIsNone(scripts.describe(write(self.dir, "g.py", "# mrrsim: vehicles=0\n")).vehicles)
        late = "\n" * 40 + "# mrrsim: vehicles=4\n"
        self.assertIsNone(scripts.describe(write(self.dir, "h.py", late)).vehicles)

    def test_a_script_with_a_syntax_error_is_still_listed(self):
        info = scripts.describe(write(self.dir, "bad.py", "import projectairsim\ndef (:\n"))
        self.assertIn("syntax error", info.problem)
        self.assertTrue(info.needs_airsim)

    def test_a_byte_order_mark_is_not_a_problem(self):
        bom = b"\xef\xbb\xbf"
        fine = self.dir / "bom.py"
        fine.write_bytes(bom + b'"""Fly a square."""\nimport projectairsim\n')
        info = scripts.describe(fine)
        self.assertEqual((info.problem, info.description, info.needs_airsim), ("", "Fly a square.", True))
        broken = self.dir / "bom_bad.py"
        broken.write_bytes(bom + b"import projectairsim\nx = (\n")
        info = scripts.describe(broken)
        self.assertIn("syntax error", info.problem)
        self.assertTrue(info.needs_airsim)  # the first line is still read as an import

    def test_a_vehicles_header_that_is_not_a_plain_number_is_ignored(self):
        for value in ("\u00b2", "\u00bd", "1.5", "-2", "two"):
            self.assertIsNone(scripts.describe(write(self.dir, "v.py", f"# mrrsim: vehicles={value}\n")).vehicles, value)

    def test_the_imports_and_the_main_guard_are_read(self):
        source = "import os\nimport cv2\nfrom numpy import array\nfrom . import sibling\nfrom .other import x\n"
        plain = scripts.describe(write(self.dir, "p.py", source))
        self.assertEqual((plain.imports, plain.has_main), (frozenset({"cv2", "numpy"}), False))
        guarded = scripts.describe(write(self.dir, "g.py", source + 'if __name__ == "__main__":\n    pass\n'))
        self.assertTrue(guarded.has_main)
        broken = scripts.describe(write(self.dir, "b.py", "import cv2\nif __name__ == '__main__':\n    (\n"))
        self.assertEqual((broken.imports, broken.has_main), (frozenset({"cv2"}), True))

    def test_a_relative_import_named_projectairsim_is_the_scripts_own_module(self):
        self.assertFalse(scripts.describe(write(self.dir, "r.py", "from .projectairsim import x\n")).needs_airsim)

    def test_a_file_that_cannot_be_read_says_why(self):
        for path in (self.dir / "gone.py", self.dir):  # missing, and a folder
            info = scripts.describe(path)
            self.assertTrue(info.problem, path)
            self.assertEqual((info.description, info.needs_airsim, info.vehicles), ("", False, None))


class LightImage(unittest.TestCase):
    def test_local_names_are_the_py_files_and_folders_of_a_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write(folder, "mylib.py", "")
            write(folder, "notes.txt", "")
            (folder / "pkg").mkdir()
            self.assertEqual(scripts.local_names(folder), frozenset({"mylib", "pkg"}))
        self.assertEqual(scripts.local_names(Path("Z:/nope")), frozenset())

    def test_only_what_the_light_image_lacks_and_the_script_does_not_have_itself_needs_the_full_one(self):
        imports = frozenset({"cv2", "dronekit", "mylib"})
        self.assertEqual(scripts.needs_full_image(imports, frozenset({"mylib"})), frozenset({"cv2"}))
        self.assertEqual(scripts.needs_full_image(frozenset({"dronekit", "pymavlink"})), frozenset())

    def test_the_light_image_list_has_every_package_the_image_installs(self):
        requirements = (paths.repo_root() / "containers" / "script" / "requirements.txt").read_text(encoding="utf-8")
        listed = [re.match(r"[A-Za-z0-9_.-]+", line).group(0).lower().replace("-", "_")
                  for line in requirements.splitlines() if line.strip() and line[0] not in "#-"]
        self.assertTrue(listed)
        for name in listed:
            self.assertIn(name, scripts.LIGHT_IMAGE_MODULES, name)


class Discover(unittest.TestCase):
    def test_groups_order_and_filtering(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            mine, examples = Path(a), Path(b)
            write(mine, "zeta.py", "")
            write(mine, "Alpha.py", "")
            write(mine, "_helper.py", "")
            write(mine, "notes.txt", "")
            write(examples, "demo.py", "")
            found = scripts.discover(mine, examples)
            self.assertEqual([(i.name, i.group) for i in found],
                             [("Alpha.py", "mine"), ("zeta.py", "mine"), ("demo.py", "examples")])

    def test_a_folder_named_like_a_script_is_not_a_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "folder.py").mkdir()
            write(Path(tmp), "real.py", "")
            self.assertEqual([i.name for i in scripts.discover(Path(tmp), None)], ["real.py"])

    def test_missing_folders_are_fine(self):
        self.assertEqual(scripts.discover(Path("Z:/nope"), None), [])

    def test_copy_never_overwrites(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            source = write(Path(b), "demo.py", "print('x')\n")
            info = scripts.describe(source, scripts.EXAMPLES)
            mine = Path(a) / "scripts"  # does not exist yet
            names = [scripts.copy_to(info, mine).name for _ in range(3)]
            self.assertEqual(names, ["demo.py", "demo_copy.py", "demo_copy2.py"])
            self.assertEqual((mine / "demo_copy.py").read_text(), "print('x')\n")


class RunCommandChoices(unittest.TestCase):
    """What `run` / `up --script` work out from the script file itself."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")

    def test_airsim_script_switches_the_image(self):
        script = write(self.dir, "cam.py", "from projectairsim import World\n")
        self.assertNotEqual(self.cfg.scripts.image, self.cfg.scene.image)
        info = cli._override_script(self.cfg, str(script))
        self.assertTrue(info.needs_airsim)
        self.assertEqual(self.cfg.scripts.image, self.cfg.scene.image)
        self.assertEqual((self.cfg.scripts.dir, self.cfg.scripts.main), (self.dir.resolve(), "cam.py"))

    def test_plain_script_keeps_the_light_image(self):
        before = self.cfg.scripts.image
        cli._override_script(self.cfg, str(write(self.dir, "plain.py", "import dronekit\n")))
        self.assertEqual(self.cfg.scripts.image, before)

    def test_a_name_is_found_in_the_students_folder(self):
        write(self.dir, "mine_only.py", "x = 1\n")
        with mock.patch.dict("os.environ", {"MRRSIM_USER_SCRIPTS": str(self.dir)}):
            cli._override_script(self.cfg, "mine_only.py")
        self.assertEqual((self.cfg.scripts.dir, self.cfg.scripts.main), (self.dir, "mine_only.py"))

    def test_unknown_script_is_a_config_error(self):
        with self.assertRaises(config.ConfigError):
            cli._override_script(self.cfg, "no_such_script_anywhere.py")

    def test_shipped_two_vehicle_example_asks_for_two(self):
        info = scripts.describe(paths.repo_root() / "scripts" / "takeoff_land_all.py")
        self.assertEqual(info.vehicles, 2)


if __name__ == "__main__":
    unittest.main()
