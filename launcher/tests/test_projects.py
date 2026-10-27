import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
from mrrsim.ui.run import commands
from mrrsim.userscripts import projects, scripts

GUARD = 'if __name__ == "__main__":\n    main()\n'


def write(root: Path, rel: str, text: str = "x = 1\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.store = self.tmp / "projects.json"
        patch = mock.patch.dict("os.environ", {"MRRSIM_PROJECTS_FILE": str(self.store)})
        patch.start()
        self.addCleanup(patch.stop)
        self.project = self.tmp / "codebase"
        self.project.mkdir()


class Store(Base):
    def test_added_projects_are_remembered_once(self):
        projects.add(self.project)
        projects.add(self.project)
        self.assertEqual(projects.load(), [self.project])

    def test_remove_forgets_the_project_but_not_its_files(self):
        keep = write(self.project, "main.py")
        projects.add(self.project)
        projects.remove(self.project)
        self.assertEqual(projects.load(), [])
        self.assertTrue(keep.is_file())

    def test_a_missing_or_damaged_store_is_an_empty_list(self):
        self.assertEqual(projects.load(), [])
        self.store.write_text("{not json", encoding="utf-8")
        self.assertEqual(projects.load(), [])
        self.store.write_text('{"projects": [3, "", null]}', encoding="utf-8")
        self.assertEqual(projects.load(), [])

    def test_adding_something_that_is_not_a_folder_is_refused(self):
        with self.assertRaises(NotADirectoryError):
            projects.add(self.tmp / "nope")

    def test_contains(self):
        self.assertTrue(projects.contains(self.project, self.project / "a" / "b.py"))
        self.assertFalse(projects.contains(self.project, self.tmp / "other" / "b.py"))
        self.assertFalse(projects.contains(self.project, self.tmp / "codebase2" / "b.py"))


class Scan(Base):
    def test_files_with_a_main_block_are_found_at_any_depth(self):
        write(self.project, "flight/mission.py", '"""Fly the mission."""\n' + GUARD)
        write(self.project, "flight/helpers.py")
        write(self.project, "tools/deep/report.py", GUARD)
        names = [i.name for i in projects.scan(self.project).entries]
        self.assertEqual(names, ["flight/mission.py", "tools/deep/report.py"])
        entry = projects.scan(self.project).entries[0]
        self.assertEqual((entry.group, entry.project, entry.description), (scripts.PROJECT, self.project, "Fly the mission."))

    def test_top_level_files_are_offered_even_without_a_main_block(self):
        write(self.project, "fly.py")
        write(self.project, "lib/util.py")
        self.assertEqual([i.name for i in projects.scan(self.project).entries], ["fly.py"])

    def test_a_project_with_no_obvious_entry_offers_every_file(self):
        write(self.project, "pkg/a.py")
        write(self.project, "pkg/b.py")
        self.assertEqual([i.name for i in projects.scan(self.project).entries], ["pkg/a.py", "pkg/b.py"])

    def test_noise_is_left_out(self):
        write(self.project, "__init__.py", GUARD)
        write(self.project, "setup.py", GUARD)
        write(self.project, "conftest.py", GUARD)
        write(self.project, ".git/hooks/x.py", GUARD)
        write(self.project, "__pycache__/x.py", GUARD)
        write(self.project, "venv/lib/x.py", GUARD)
        write(self.project, "myenv/pyvenv.cfg", "home = x\n")
        write(self.project, "myenv/lib/x.py", GUARD)
        write(self.project, "pkg/_private.py", GUARD)
        write(self.project, "real.py", GUARD)
        self.assertEqual([i.name for i in projects.scan(self.project).entries], ["real.py"])

    def test_one_file_importing_projectairsim_makes_the_whole_project_need_it(self):
        write(self.project, "run.py", "from vision import look\n" + GUARD)
        write(self.project, "vision.py", "from projectairsim import World\n")
        scan = projects.scan(self.project)
        self.assertTrue(scan.needs_airsim)
        self.assertTrue(all(i.needs_airsim for i in scan.entries))

    def test_no_airsim_import_means_the_light_image(self):
        write(self.project, "run.py", "import dronekit\n" + GUARD)
        self.assertFalse(projects.scan(self.project).needs_airsim)

    def test_vehicle_header_is_read_from_project_files(self):
        write(self.project, "sub/swarm.py", "# mrrsim: vehicles=4\n" + GUARD)
        self.assertEqual(projects.scan(self.project).entries[0].vehicles, 4)

    def test_a_huge_folder_stops_at_the_limit(self):
        with mock.patch.object(projects, "MAX_FILES", 5):
            for n in range(9):
                write(self.project, f"f{n}.py", GUARD)
            scan = projects.scan(self.project)
        self.assertTrue(scan.truncated)
        self.assertEqual(scan.files, 5)


class RunInAProject(Base):
    def setUp(self):
        super().setUp()
        self.cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        write(self.project, "mylib/__init__.py")
        write(self.project, "flight/mission.py", "import mylib\n" + GUARD)

    def test_the_whole_project_folder_becomes_the_scripts_folder(self):
        info = cli._override_script(self.cfg, str(self.project / "flight" / "mission.py"), str(self.project))
        self.assertEqual(self.cfg.scripts.dir, self.project.resolve())
        self.assertEqual(self.cfg.scripts.main, "flight/mission.py")
        self.assertFalse(info.needs_airsim)

    def test_a_path_relative_to_the_project_works_too(self):
        cli._override_script(self.cfg, "flight/mission.py", str(self.project))
        self.assertEqual(self.cfg.scripts.main, "flight/mission.py")

    def test_the_airsim_image_is_chosen_from_the_whole_project(self):
        write(self.project, "mylib/vision.py", "import projectairsim\n")
        before = self.cfg.scripts.image
        self.assertNotEqual(before, self.cfg.scene.image)
        info = cli._override_script(self.cfg, "flight/mission.py", str(self.project))
        self.assertTrue(info.needs_airsim)
        self.assertEqual(self.cfg.scripts.image, self.cfg.scene.image)

    def test_a_file_outside_the_project_is_refused(self):
        outside = write(self.tmp, "elsewhere.py")
        with self.assertRaises(config.ConfigError):
            cli._override_script(self.cfg, str(outside), str(self.project))

    def test_a_missing_project_or_file_is_refused(self):
        with self.assertRaises(config.ConfigError):
            cli._override_script(self.cfg, "flight/mission.py", str(self.tmp / "gone"))
        with self.assertRaises(config.ConfigError):
            cli._override_script(self.cfg, "flight/nothing.py", str(self.project))

    def test_the_window_passes_the_project_on(self):
        with mock.patch.object(commands, "cli_command", return_value=["mrrsim"]):
            command = commands.build_run_command(Path("c.toml"), self.project / "a.py", 1, False, None, project=self.project)
            plain = commands.build_run_command(Path("c.toml"), self.project / "a.py", 1, False, None)
        self.assertEqual(command[command.index("--project") + 1], str(self.project))
        self.assertNotIn("--project", plain)

    def test_dry_run_mounts_the_project_and_puts_it_on_the_import_path(self):
        import io
        from contextlib import redirect_stdout

        self.addCleanup(cli.session.SHUTTING_DOWN.clear)  # a whole session ran: its shut-down flag must not leak into other tests
        out = io.StringIO()
        argv = ["-c", str(paths.repo_root() / "config" / "mrrsim.toml"), "run", str(self.project / "flight" / "mission.py"),
                "--project", str(self.project), "--dry-run", "--no-unreal", "--no-scene", "--vehicles", "1"]
        with mock.patch.dict("os.environ", {"MRRSIM_DATA_DIR": str(self.tmp / "data")}), redirect_stdout(out):
            with self.assertLogs(level="INFO") as logs:
                cli.main(argv)
        text = "\n".join(logs.output) + out.getvalue()
        self.assertIn("PYTHONPATH=/work", text)
        self.assertIn("/work/flight/mission.py", text)
        self.assertIn(self.project.name, text)
        # the project may write next to its code, into a scratch layer: the folder itself is mounted as an overlay
        self.assertRegex(text, r":/work:O,upperdir=/tmp/mrrsim-overlay/\S+/up,workdir=/tmp/mrrsim-overlay/\S+/wk")

    def test_a_single_script_gets_the_overlay_too(self):
        """The Project AirSim client writes projectairsim_client.log into its working folder, so a read-only
        folder made every script that uses it fail on start."""
        import io
        from contextlib import redirect_stdout

        self.addCleanup(cli.session.SHUTTING_DOWN.clear)
        single = write(self.tmp, "single.py", "print(1)\n")
        argv = ["-c", str(paths.repo_root() / "config" / "mrrsim.toml"), "run", str(single),
                "--dry-run", "--no-unreal", "--no-scene", "--vehicles", "1"]
        with mock.patch.dict("os.environ", {"MRRSIM_DATA_DIR": str(self.tmp / "data")}), redirect_stdout(io.StringIO()):
            with self.assertLogs(level="INFO") as logs:
                cli.main(argv)
        text = "\n".join(logs.output)
        self.assertRegex(text, r":/work:O,upperdir=/tmp/mrrsim-overlay/\S+/up,workdir=/tmp/mrrsim-overlay/\S+/wk")
        self.assertNotIn(":/work:ro", text)


if __name__ == "__main__":
    unittest.main()
