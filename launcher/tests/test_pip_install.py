import _thread
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
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

    def test_the_message_names_the_requirement_and_what_is_accepted(self):
        with self.assertRaises(ValueError) as caught:
            pipinstall.validate(["tqdm", "https://example.com/x.whl"])
        self.assertIn("'https://example.com/x.whl' is not a package name (use a name like", str(caught.exception))

    def test_nothing_at_all_is_refused(self):
        for empty in ([], [""], ["  "]):
            with self.assertRaises(ValueError):
                pipinstall.validate(empty)
            with self.assertRaises(ValueError):
                pipinstall.validate_names(empty)

    def test_remove_takes_names_only_no_versions_paths_or_options(self):
        self.assertEqual(pipinstall.validate_names([" numpy ", "scikit-learn"]), ["numpy", "scikit-learn"])
        for bad in ("numpy==2", "../x", "a b", "-r", ""):
            with self.assertRaises(ValueError, msg=bad):
                pipinstall.validate_names([bad])

    def test_what_is_typed_in_the_window_is_split_on_spaces(self):
        self.assertEqual(pipinstall.split_names("  tqdm  requests==2.32.3 "), ["tqdm", "requests==2.32.3"])
        self.assertEqual(pipinstall.split_names(""), [])


class Base(unittest.TestCase):
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


class Command(Base):
    def dry(self, *argv: str) -> tuple[int, str]:
        with redirect_stdout(io.StringIO()), self.assertLogs("mrrsim", level="INFO") as logs:
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

    def test_remove_runs_the_helper_with_names_only(self):
        code, text = self.dry("pip", "remove", "opencv-python", "numpy", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("/packages/.mrrsim-pip.py remove opencv-python numpy", text)

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


class HasPackages(Base):
    def test_only_something_besides_the_helper_counts(self):
        folder = self.tmp / "packages"
        self.assertFalse(pipinstall.has_packages())  # no folder
        folder.mkdir()
        self.assertFalse(pipinstall.has_packages())  # empty
        (folder / pipinstall.DRIVER_NAME).write_text("x", encoding="utf-8")
        self.assertFalse(pipinstall.has_packages())  # the helper alone
        (folder / "tqdm").mkdir()
        self.assertTrue(pipinstall.has_packages())


class Actions(unittest.TestCase):
    def test_each_action_goes_to_the_right_place(self):
        cfg = mock.Mock()
        with mock.patch.object(pipinstall, "run", return_value=0) as run:
            self.assertEqual(pipinstall.cli(cfg, "list", []), 0)
            run.assert_called_with(cfg, "list", None, False)
            pipinstall.cli(cfg, "install", [" tqdm "], True)
            run.assert_called_with(cfg, "install", ["tqdm"], True)
            pipinstall.cli(cfg, "remove", ["tqdm"])
            run.assert_called_with(cfg, "remove", ["tqdm"], False)
            pipinstall.cli(cfg, "mystery", [])
            run.assert_called_with(cfg, "list", None, False)
        with mock.patch.object(pipinstall, "reset", return_value=0) as reset, mock.patch.object(pipinstall, "run") as run:
            self.assertEqual(pipinstall.cli(cfg, "reset", []), 0)
        reset.assert_called_once_with()
        run.assert_not_called()

    def test_a_name_that_is_not_accepted_starts_nothing(self):
        for action, names in (("install", ["-r", "x"]), ("remove", ["numpy==2"]), ("install", [])):
            with mock.patch.object(pipinstall, "run") as run, mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertEqual(pipinstall.cli(mock.Mock(), action, names), 2, (action, names))
            run.assert_not_called()
            self.assertIn("error:", err.getvalue())


class RunInTheContainer(Base):
    def setUp(self):
        super().setUp()
        self.podman = mock.Mock()
        for patch in (mock.patch.object(pipinstall, "Podman", return_value=self.podman),
                      mock.patch.object(pipinstall.wsl, "to_wsl_path", return_value="/mnt/packages")):
            patch.start()
            self.addCleanup(patch.stop)
        self.helper = self.tmp / "packages" / pipinstall.DRIVER_NAME

    @staticmethod
    def process(lines, code=0):
        proc = mock.Mock()
        proc.stdout = iter(lines)
        proc.wait.return_value = code
        return proc

    def test_the_output_is_shown_and_the_exit_code_comes_back(self):
        self.podman.run_attached.return_value = self.process(["one\n", "two   \n"], code=3)
        out = io.StringIO()
        with redirect_stdout(out):
            code = pipinstall.run(self.cfg, "install", ["x==1"])
        self.assertEqual((code, out.getvalue()), (3, "one\ntwo\n"))
        self.assertEqual(self.podman.run_attached.call_args.kwargs["command"],
                         ["python", "-u", "/packages/.mrrsim-pip.py", "install", "x==1"])
        self.assertFalse(self.helper.exists())

    def test_ctrl_c_stops_the_container_and_gives_130(self):
        self.podman.run_attached.return_value = self.process([])
        with mock.patch.object(pipinstall, "_lines", side_effect=KeyboardInterrupt):
            self.assertEqual(pipinstall.run(self.cfg, "install", ["x"]), 130)
        self.podman.stop.assert_called_once_with([pipinstall.CONTAINER_NAME], timeout=5)
        self.assertFalse(self.helper.exists())

    def test_the_helper_is_there_while_podman_runs_and_gone_even_when_it_fails(self):
        seen = []

        def fails(*args, **kwargs):
            seen.append(self.helper.exists())
            raise RuntimeError("podman is gone")

        self.podman.run_attached.side_effect = fails
        with self.assertRaises(RuntimeError):
            pipinstall.run(self.cfg, "list")
        self.assertEqual(seen, [True])
        self.assertFalse(self.helper.exists())


class ReadingTheOutput(unittest.TestCase):
    def test_every_line_comes_through_in_order(self):
        proc = subprocess.Popen([sys.executable, "-c", "print('one'); print('two')"], stdout=subprocess.PIPE, text=True)
        self.assertEqual(list(pipinstall._lines(proc)), ["one\n", "two\n"])
        proc.wait()

    def test_ctrl_c_gets_through_while_the_container_prints_nothing(self):
        """On Windows a blocking read is only interrupted when the process ends; a hung pip prints nothing."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        timer = threading.Timer(0.5, _thread.interrupt_main)
        started = time.monotonic()
        timer.start()
        with self.assertRaises(KeyboardInterrupt):
            for _line in pipinstall._lines(proc):
                pass
        self.assertLess(time.monotonic() - started, 5)


class Driver(unittest.TestCase):
    def test_the_container_script_is_valid_python(self):
        compile(pipinstall.DRIVER, "driver", "exec")
        compile(pipinstall.DRIVER.split('CHECK = """')[1].split('"""')[0], "check", "exec")


def make_wheel(folder: Path, name: str, requires=(), init: str = "x = 1\n", top_level: bool = True) -> None:
    """A minimal wheel (a zip with the package and its metadata), so pip can install it with no internet."""
    dist = f"{name}-1.0.dist-info"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n" + "".join(f"Requires-Dist: {r}\n" for r in requires)
    files = {f"{name}/__init__.py": init, f"{dist}/METADATA": metadata,
             f"{dist}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    if top_level:
        files[f"{dist}/top_level.txt"] = f"{name}\n"
    files[f"{dist}/RECORD"] = "".join(f"{path},,\n" for path in [*files, f"{dist}/RECORD"])
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / f"{name}-1.0-py3-none-any.whl", "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)


@unittest.skipUnless(importlib.util.find_spec("pip"), "needs pip in the Python that runs the tests")
class DriverRun(unittest.TestCase):
    """The helper that runs in the container, run here as a real program: pip is pointed at wheels made for the test
    (no internet) and the packages folder at a temporary one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.wheels, self.packages = self.tmp / "wheels", self.tmp / "packages"
        self.driver = self.tmp / "driver.py"
        self.driver.write_text(pipinstall.DRIVER, encoding="utf-8")
        self.env = dict(os.environ, MRRSIM_PACKAGES=str(self.packages), PIP_NO_INDEX="1",
                        PIP_FIND_LINKS=str(self.wheels), PYTHONPATH="")

    def driver_run(self, *args: str) -> tuple[int, str]:
        done = subprocess.run([sys.executable, str(self.driver), *args], capture_output=True, text=True, env=self.env,
                              timeout=300)
        return done.returncode, done.stdout + done.stderr

    def test_install_again_another_package_list_and_remove(self):
        make_wheel(self.wheels, "demo_a", requires=["demo_dep"])
        make_wheel(self.wheels, "demo_dep")
        make_wheel(self.wheels, "demo_b", requires=["demo_dep"])
        code, text = self.driver_run("install", "demo-a")
        self.assertEqual(code, 0, text)
        self.assertIn("Installing: demo_a==1.0, demo_dep==1.0", text)
        self.assertIn("[ OK ] import demo_a works (demo_a 1.0)", text)
        self.assertTrue((self.packages / "demo_dep" / "__init__.py").is_file())
        code, text = self.driver_run("install", "demo-a")  # what the folder holds is not downloaded again
        self.assertEqual(code, 0, text)
        self.assertIn("nothing to install", text)
        self.assertNotIn("Installing:", text)
        code, text = self.driver_run("install", "demo-b")  # and a dependency it already has is left alone
        self.assertEqual(code, 0, text)
        self.assertIn("Installing: demo_b==1.0", text)
        self.assertNotIn("demo_dep==1.0", text)
        _, text = self.driver_run("list")
        self.assertEqual(sorted(text.split("\n")[:-1]), ["demo_a 1.0", "demo_b 1.0", "demo_dep 1.0"])
        code, text = self.driver_run("remove", "demo-a", "missing")
        self.assertEqual(code, 0, text)
        self.assertIn("Removed demo_a 1.0", text)
        self.assertIn("missing is not installed", text)
        self.assertFalse((self.packages / "demo_a").exists())
        self.assertTrue((self.packages / "demo_dep").is_dir() and (self.packages / "demo_b").is_dir())

    def test_a_package_that_cannot_be_found_installs_nothing(self):
        code, text = self.driver_run("install", "demo-nowhere")
        self.assertEqual(code, 1, text)
        self.assertIn("Nothing was installed.", text)
        self.assertFalse(list(self.packages.glob("*.dist-info")))

    def test_a_package_that_does_not_import_is_reported_and_one_without_top_level_txt_is_still_tested(self):
        make_wheel(self.wheels, "demo_bad", init="raise ImportError('libmissing.so: cannot open')\n")
        make_wheel(self.wheels, "demo_nt", top_level=False)
        code, text = self.driver_run("install", "demo-bad", "demo-nt")
        self.assertEqual(code, 0, text)  # installed, which is why the warning only says what to do
        self.assertIn("[WARN] import demo_bad", text)
        self.assertIn("does not import", text)
        self.assertIn("[ OK ] import demo_nt works", text)

    def test_remove_deletes_only_what_the_package_lists(self):
        make_wheel(self.wheels, "demo_a")
        self.assertEqual(self.driver_run("install", "demo-a")[0], 0)
        outside = self.tmp / "outside.txt"
        outside.write_text("must survive", encoding="utf-8")
        record = self.packages / "demo_a-1.0.dist-info" / "RECORD"
        record.write_text(record.read_text(encoding="utf-8") + "../outside.txt,,\n", encoding="utf-8")
        (self.packages / "other").mkdir()
        (self.packages / "other" / "keep.py").write_text("y", encoding="utf-8")
        code, text = self.driver_run("remove", "demo_a")
        self.assertEqual(code, 0, text)
        self.assertFalse((self.packages / "demo_a").exists())
        self.assertFalse((self.packages / "demo_a-1.0.dist-info").exists())
        self.assertTrue((self.packages / "other" / "keep.py").exists())
        self.assertEqual(outside.read_text(encoding="utf-8"), "must survive")  # RECORD paths cannot leave the folder


if __name__ == "__main__":
    unittest.main()
