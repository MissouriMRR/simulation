import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
from mrrsim.ui.run import commands
from mrrsim.userscripts import pipinstall


class Validation(unittest.TestCase):
    def test_plain_requirements_are_accepted(self):
        for good in ("tqdm", "requests==2.32.3", "pyzbar", "numpy>=2,<3", "scikit-learn", "Pillow", "ruamel.yaml",
                     "uvicorn[standard]", "pkg_name~=1.4", "a1"):
            self.assertEqual(pipinstall.validate([good]), [good], good)

    def test_anything_that_could_change_how_pip_runs_is_refused(self):
        for bad in ("--index-url=http://evil.example/simple", "-r requirements.txt", "-e .", "https://example.com/x.whl",
                    "git+https://github.com/a/b", "C:\\evil\\x.whl", "./local", "../up", "x; rm -rf /", "x && y", "$(id)",
                    "a b", "pkg==", "==1.0", "-", ""):
            with self.assertRaises(ValueError, msg=bad):
                pipinstall.validate([bad])

    def test_nothing_at_all_is_refused(self):
        for empty in ([], [""], ["  "]):
            with self.assertRaises(ValueError):
                pipinstall.validate(empty)

    def test_what_is_typed_in_the_window_is_split_on_spaces(self):
        self.assertEqual(pipinstall.split_names("  tqdm  requests==2.32.3 "), ["tqdm", "requests==2.32.3"])
        self.assertEqual(pipinstall.split_names(""), [])


class Command(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        patch = mock.patch.dict("os.environ", {"MRRSIM_PACKAGES_DIR": str(self.tmp / "packages"),
                                               "MRRSIM_DATA_DIR": str(self.tmp / "data")})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(cli.session.SHUTTING_DOWN.clear)
        self.cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")

    def dry(self, *argv: str) -> tuple[int, str]:
        with redirect_stdout(io.StringIO()), self.assertLogs(level="INFO") as logs:
            code = cli.main(["-c", str(self.cfg.source), *argv])
        return code, "\n".join(logs.output)

    def test_install_runs_pip_in_the_full_image_with_the_folder_mounted_writable(self):
        code, text = self.dry("pip", "install", "tqdm", "pyzbar", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn(self.cfg.scene.image, text)  # the full image: what it has already is not downloaded again
        self.assertNotIn(self.cfg.scripts.image + " ", text)
        self.assertIn(":/packages:rw", text)
        self.assertIn("/packages/.mrrsim-pip.py install tqdm pyzbar", text)
        self.assertIn("--network host", text)  # pip needs the internet; every container here uses the host network

    def test_the_helper_script_is_not_left_in_the_folder(self):
        self.dry("pip", "install", "tqdm", "--dry-run")
        self.assertFalse((self.tmp / "packages" / pipinstall.DRIVER_NAME).exists())

    def test_a_bad_name_is_refused_before_anything_starts(self):
        with mock.patch.object(pipinstall, "run") as run, redirect_stdout(io.StringIO()), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = cli.main(["-c", str(self.cfg.source), "pip", "install", "https://example.com/x.whl"])
        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertIn("not a package name", err.getvalue())

    def test_an_option_in_place_of_a_name_is_refused_by_the_parser(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", new_callable=io.StringIO):
            cli.main(["-c", str(self.cfg.source), "pip", "install", "--index-url=http://evil.example"])

    def test_reset_deletes_only_the_packages_folder(self):
        folder = self.tmp / "packages"
        (folder / "tqdm").mkdir(parents=True)
        (folder / "tqdm" / "x.py").write_text("x", encoding="utf-8")
        sibling = self.tmp / "keep.txt"
        sibling.write_text("keep", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(pipinstall.reset(), 0)
            self.assertEqual(pipinstall.reset(), 0)  # nothing to remove the second time
        self.assertFalse(folder.exists())
        self.assertTrue(sibling.exists())

    def test_scripts_get_the_folder_once_something_is_installed(self):
        script = self.tmp / "s.py"
        script.write_text("print(1)\n", encoding="utf-8")
        argv = ["run", str(script), "--dry-run", "--no-unreal", "--no-scene", "--vehicles", "1"]
        _, before = self.dry(*argv)
        self.assertNotIn("/packages", before)
        (self.tmp / "packages" / "tqdm").mkdir(parents=True)
        _, after = self.dry(*argv)
        self.assertIn(":/packages:ro", after)
        self.assertIn("PYTHONPATH=/work:/packages", after)

    def test_the_helper_script_alone_does_not_count_as_installed_packages(self):
        script = self.tmp / "s.py"
        script.write_text("print(1)\n", encoding="utf-8")
        (self.tmp / "packages").mkdir()
        (self.tmp / "packages" / pipinstall.DRIVER_NAME).write_text("x", encoding="utf-8")
        _, text = self.dry("run", str(script), "--dry-run", "--no-unreal", "--no-scene", "--vehicles", "1")
        self.assertNotIn("/packages", text)


class StatusLine(unittest.TestCase):
    def test_pip_status(self):
        self.assertIn("failed", commands.pip_status(1, ""))
        self.assertIn("does not import", commands.pip_status(0, "x\n[WARN] import pyzbar.pyzbar fails: ImportError\n"))
        self.assertIn("available to every script", commands.pip_status(0, "[ OK ] import tqdm works"))
        self.assertEqual(commands.pip_status(0, "Removed x 1.0", removing=True), "Packages removed")
        self.assertIn("failed", commands.pip_status(1, "", removing=True))
        self.assertEqual(commands.pip_status(0, "Removed x 1.0", removing=True), "Packages removed")
        self.assertIn("failed", commands.pip_status(1, "", removing=True))


class Driver(unittest.TestCase):
    def test_the_container_script_is_valid_python(self):
        compile(pipinstall.DRIVER, "driver", "exec")
        compile(pipinstall.DRIVER.split('CHECK = """')[1].split('"""')[0], "check", "exec")


if __name__ == "__main__":
    unittest.main()
