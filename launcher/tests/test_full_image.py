"""Which image a script or project runs in, and `mrrsim pip remove`.

The light image has dronekit and pymavlink; the full image has those, OpenCV, numpy, Project AirSim and the
packages installed with `mrrsim pip install`. A script that imports anything the light image lacks runs in
the full image, so an `import cv2` just works and a package installed for it is found.
"""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
from mrrsim.userscripts import pipinstall, projects, scripts

GUARD = 'if __name__ == "__main__":\n    pass\n'


def write(folder: Path, name: str, text: str = "x = 1\n") -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        self.assertNotEqual(self.cfg.scripts.image, self.cfg.scene.image)


class SingleScripts(Base):
    def test_a_script_that_needs_more_than_the_light_image_has_runs_in_the_full_image(self):
        for source in ("import cv2\n", "import numpy as np\n", "from pyzbar.pyzbar import decode\n",
                       "import dronekit, tqdm\n", "import projectairsim\n"):
            cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
            cli._override_script(cfg, str(write(self.dir, "x.py", source)))
            self.assertEqual(cfg.scripts.image, cfg.scene.image, source)

    def test_the_light_image_stays_for_scripts_that_need_nothing_else(self):
        write(self.dir, "helper.py", "VALUE = 1\n")
        write(self.dir, "pkg/__init__.py", "")
        source = ("import os, sys, json\nimport helper\nfrom pkg import thing\nfrom dronekit import connect\n"
                  "from pymavlink import mavutil\nfrom . import sibling\n")
        before = self.cfg.scripts.image
        cli._override_script(self.cfg, str(write(self.dir, "plain.py", source)))
        self.assertEqual(self.cfg.scripts.image, before)

    def test_the_imports_are_listed_without_the_standard_library(self):
        info = scripts.describe(write(self.dir, "i.py", "import os, cv2.dnn\nfrom numpy import array\nfrom . import x\n"))
        self.assertEqual(info.imports, frozenset({"cv2", "numpy"}))

    def test_a_script_with_a_syntax_error_still_gets_the_right_image(self):
        info = scripts.describe(write(self.dir, "bad.py", "import cv2\ndef (:\n"))
        self.assertEqual(info.imports, frozenset({"cv2"}))


class Projects(Base):
    def test_other_third_party_imports_need_the_full_image_but_the_projects_own_modules_do_not(self):
        write(self.dir, "qr_main.py", "import cv2\nimport qr\nfrom tools.util import f\nimport dronekit\n" + GUARD)
        write(self.dir, "qr.py", "import numpy\n")
        write(self.dir, "tools/util.py", "x = 1\n")
        scan = projects.scan(self.dir)
        self.assertEqual(scan.full_image_for, frozenset({"cv2", "numpy"}))
        self.assertFalse(scan.needs_airsim)

    def test_a_project_of_its_own_modules_and_dronekit_stays_on_the_light_image(self):
        write(self.dir, "run.py", "import dronekit\nimport lib\nfrom pymavlink import mavutil\n" + GUARD)
        write(self.dir, "lib/__init__.py", "")
        self.assertEqual(projects.scan(self.dir).full_image_for, frozenset())

    def test_running_a_project_that_imports_cv2_uses_the_full_image(self):
        write(self.dir, "qr.py", "import cv2\n" + GUARD)
        cli._override_script(self.cfg, "qr.py", str(self.dir))
        self.assertEqual(self.cfg.scripts.image, self.cfg.scene.image)

    def test_running_a_plain_project_keeps_the_light_image(self):
        write(self.dir, "run.py", "import dronekit\n" + GUARD)
        before = self.cfg.scripts.image
        cli._override_script(self.cfg, "run.py", str(self.dir))
        self.assertEqual(self.cfg.scripts.image, before)


class Remove(Base):
    def setUp(self):
        super().setUp()
        patch = mock.patch.dict("os.environ", {"MRRSIM_PACKAGES_DIR": str(self.dir / "packages")})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(cli.session.SHUTTING_DOWN.clear)

    def test_remove_runs_the_helper_with_names_only(self):
        with redirect_stdout(io.StringIO()), self.assertLogs(level="INFO") as logs:
            code = cli.main(["-c", str(self.cfg.source), "pip", "remove", "opencv-python", "numpy", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("/packages/.mrrsim-pip.py remove opencv-python numpy", "\n".join(logs.output))

    def test_remove_refuses_versions_paths_and_options(self):
        for bad in ("numpy==2", "../x", "a b", "-r", ""):
            with self.assertRaises(ValueError, msg=bad):
                pipinstall.validate_names([bad])

    def test_the_removal_code_deletes_exactly_what_a_package_lists(self):
        """Runs the helper's own remove() on a fake packages folder (the helper reads /packages, so it is pointed
        at a temporary one)."""
        packages = self.dir / "fake"
        write(packages, "demo/__init__.py", "x")
        write(packages, "demo-1.0.dist-info/RECORD",
              "demo/__init__.py,,\ndemo-1.0.dist-info/RECORD,,\n../outside.txt,,\n")
        write(packages, "other/keep.py", "y")
        outside = write(self.dir, "outside.txt", "must survive")
        source = pipinstall.DRIVER.replace('pathlib.Path("/packages")', f"pathlib.Path({str(packages)!r})")
        source = source.split("action, specs = sys.argv[1]")[0]  # the definitions only, not the command dispatch
        namespace: dict = {}
        exec(compile(source, "driver", "exec"), namespace)  # noqa: S102 (our own text, run on a temporary folder)
        with redirect_stdout(io.StringIO()) as out:
            namespace["remove"](["Demo", "missing"])
        self.assertFalse((packages / "demo").exists())
        self.assertFalse((packages / "demo-1.0.dist-info").exists())
        self.assertTrue((packages / "other" / "keep.py").exists())
        self.assertEqual(outside.read_text(encoding="utf-8"), "must survive")  # RECORD paths cannot leave the folder
        self.assertIn("Removed demo 1.0", out.getvalue())
        self.assertIn("missing is not installed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
