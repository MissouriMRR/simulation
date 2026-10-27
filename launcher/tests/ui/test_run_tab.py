"""The Run tab (ui/run/run_tab.py): the script list, the run options, running, stopping, the doctor and package installs.
The tests that create a (hidden) Tk window only run when MRRSIM_GUI_TESTS=1, so an ordinary test run never touches the
desktop; to run them without any window appearing, start the test process on a hidden desktop."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config, paths
from mrrsim.ui import app as window
from mrrsim.ui.run import run_tab as run_module
from mrrsim.userscripts import scripts

GUI = os.environ.get("MRRSIM_GUI_TESTS") == "1"


@unittest.skipUnless(GUI, "set MRRSIM_GUI_TESTS=1 (and run on a hidden desktop) to test the real window")
class RunTab(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.mine = tmp / "mine"
        patches = mock.patch.dict("os.environ", {"MRRSIM_USER_SCRIPTS": str(self.mine), "MRRSIM_DATA_DIR": str(tmp / "data")})
        patches.start()
        self.addCleanup(patches.stop)
        self.addCleanup(self._tmp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        self.workdir = tmp / "work"
        wd = mock.patch.object(paths, "work_dir", return_value=self.workdir)
        wd.start()
        self.addCleanup(wd.stop)
        self.app = window.App(self.root, cfg)
        self.tab = self.app.run_tab

    def pump(self, until, seconds=20):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            if until():
                return True
            time.sleep(0.03)
        return False

    def text(self):
        return self.tab.output.contents()

    def select(self, name):
        item = next(i for i, info in self.tab.infos.items() if info.name == name)
        self.tab.tree.selection_set(item)
        self.root.update()

    def test_lists_examples_and_a_hint_for_my_scripts(self):
        self.assertIn("takeoff_land.py", [i.name for i in self.tab.infos.values()])
        groups = [self.tab.tree.item(i, "text") for i in self.tab.tree.get_children()]
        self.assertEqual(groups, ["My scripts", "Examples"])

    def test_the_run_panel_shows_a_scripts_title_description_and_file(self):
        self.select("takeoff_land_all.py")
        self.assertEqual(self.tab.title_label.cget("text"), "takeoff_land_all.py")
        self.assertIn("One thread per vehicle", self.tab.details.cget("text"))  # the whole first paragraph
        notes = self.tab.notes.cget("text")
        self.assertIn("Written for 2 vehicle(s)", notes)
        self.assertIn("takeoff_land_all.py", notes.splitlines()[-1])
        self.assertTrue(notes.splitlines()[-1].startswith("File: "))

    def test_every_row_of_the_script_list_has_room_for_its_text(self):
        from tkinter import font as tkfont

        style_height = int(self.tab.tree.winfo_toplevel().tk.call("ttk::style", "lookup", "Treeview", "-rowheight") or 0)
        self.assertGreaterEqual(style_height, tkfont.nametofont("TkDefaultFont").metrics("linespace") + 12)

    def test_kill_when_unreal_is_not_running(self):
        with mock.patch.object(run_module, "kill_unreal", return_value=0) as kill:
            self.tab.kill()
        kill.assert_called_once()
        self.assertEqual(self.tab.status.cget("text"), "Unreal is not running")
        self.assertIn("was not running", self.text())

    def test_kill_when_idle_just_kills_unreal_without_asking(self):
        with mock.patch.object(run_module, "kill_unreal", return_value=2), \
                mock.patch.object(run_module.messagebox, "askyesno") as ask, mock.patch.object(self.app.runner, "request_stop") as stop:
            self.tab.kill()
        ask.assert_not_called()
        stop.assert_not_called()
        self.assertEqual(self.tab.status.cget("text"), "Unreal killed")
        self.assertIn("2 processes", self.text())

    def test_kill_during_a_run_asks_first_and_then_stops_the_run(self):
        self.tab._set_running("Running")
        self.tab._start([sys.executable, "-c", "import time; time.sleep(30)"])
        self.root.update()
        with mock.patch.object(run_module, "kill_unreal") as kill, mock.patch.object(run_module.messagebox, "askyesno", return_value=False):
            self.tab.kill()  # the answer is no: nothing happens
        kill.assert_not_called()
        stops = []
        with mock.patch.object(run_module, "kill_unreal", return_value=1), mock.patch.object(run_module.messagebox, "askyesno", return_value=True), \
                mock.patch.object(self.app.runner, "request_stop", side_effect=lambda: stops.append(1)):
            self.tab.kill()
            self.assertEqual(len(stops), 1)  # the first request now, the second 1.5 s later
            self.assertTrue(self.pump(lambda: len(stops) == 2, 5))
        self.assertIn("stopping everything", self.tab.status.cget("text"))
        self.app.runner.kill()
        self.pump(lambda: not self.app.runner.running, 10)

    def test_the_kill_button_is_never_disabled(self):
        self.tab._set_running("Running")
        self.assertNotIn("disabled", self.tab.kill_button.state())
        self.tab._set_idle("Ready")
        self.assertNotIn("disabled", self.tab.kill_button.state())

    def test_selecting_a_script_fills_in_its_vehicles(self):
        self.select("takeoff_land_all.py")
        self.assertEqual(int(self.tab.vehicles.get()), 2)
        self.assertIn("2 vehicle", self.tab.notes.cget("text"))

    def make_project(self):
        project = Path(self._tmp.name) / "codebase"
        (project / "flight").mkdir(parents=True)
        (project / "mylib").mkdir()
        (project / "mylib" / "__init__.py").write_text("", encoding="utf-8")
        (project / "flight" / "mission.py").write_text(
            '"""Fly the mission."""\nimport mylib\nif __name__ == "__main__":\n    pass\n', encoding="utf-8")
        return project

    def test_a_project_folder_lists_its_files_and_runs_them_with_the_whole_folder(self):
        project = self.make_project()
        with mock.patch.object(run_module.filedialog, "askdirectory", return_value=str(project)):
            self.tab.add_project()
        headings = [self.tab.tree.item(i, "text") for i in self.tab.tree.get_children()]
        self.assertEqual(headings, ["My scripts", "Project: codebase", "Examples"])
        self.assertEqual(self.tab.selected().name, "flight/mission.py")  # the new project's file is picked
        self.assertEqual(self.tab.title_label.cget("text"), "flight/mission.py")
        self.assertIn("Fly the mission", self.tab.details.cget("text"))
        notes = self.tab.notes.cget("text")
        self.assertTrue(notes.splitlines()[-1].startswith("Project folder: "))
        self.assertNotIn("disabled", self.tab.remove_button.state())
        self.assertIn("disabled", self.tab.copy_button.state())
        started = []
        with mock.patch.object(self.tab, "_start", side_effect=started.append):
            self.tab.run()
        command = started[0]
        self.assertEqual(command[command.index("--project") + 1], str(project))
        self.assertEqual(command[command.index("run") + 1], str(project / "flight" / "mission.py"))

    def test_projects_are_remembered_and_can_be_removed(self):
        project = self.make_project()
        with mock.patch.object(run_module.filedialog, "askdirectory", return_value=str(project)):
            self.tab.add_project()
        self.tab.refresh()  # a new window reads the same list
        self.assertEqual(sum(1 for i in self.tab.tree.get_children() if "Project:" in self.tab.tree.item(i, "text")), 1)
        with mock.patch.object(run_module.messagebox, "askyesno", return_value=False):
            self.tab.remove_project()
        self.assertEqual(len(run_module.projects.load()), 1)
        with mock.patch.object(run_module.messagebox, "askyesno", return_value=True):
            self.tab.remove_project()
        self.assertEqual(run_module.projects.load(), [])
        self.assertTrue((project / "flight" / "mission.py").is_file())  # the student's files are never touched
        self.assertEqual([self.tab.tree.item(i, "text") for i in self.tab.tree.get_children()], ["My scripts", "Examples"])
        self.assertIn("disabled", self.tab.remove_button.state())  # what is selected now is an ordinary script

    def test_a_project_heading_can_be_selected_and_a_moved_folder_is_flagged(self):
        project = self.make_project()
        run_module.projects.add(project)
        self.tab.refresh()
        heading = next(i for i, f in self.tab.project_nodes.items() if f == project)
        self.tab.tree.selection_set(heading)
        self.root.update()
        self.assertEqual(self.tab.title_label.cget("text"), "Project: codebase")
        self.assertNotIn("disabled", self.tab.remove_button.state())
        shutil.rmtree(project)
        self.tab.refresh()
        self.assertTrue(any("folder not found" in self.tab.tree.item(i, "text") for i in self.tab.tree.get_children()))

    def test_script_arguments_are_passed_on_and_remembered_for_that_script(self):
        self.select("takeoff_land.py")
        self.assertEqual(self.tab.script_args.get(), "")
        self.tab.script_args.set("-s --fast")
        started = []
        with mock.patch.object(self.tab, "_start", side_effect=started.append):
            self.tab.run()
        self.assertIn("--script-args=-s --fast", started[0])
        self.select("diag_arming.py")
        self.assertEqual(self.tab.script_args.get(), "")  # another script has its own
        self.select("takeoff_land.py")
        self.assertEqual(self.tab.script_args.get(), "-s --fast")

    def test_a_broken_quote_stops_the_run_with_a_message(self):
        self.select("takeoff_land.py")
        self.tab.script_args.set('--name "oops')
        with mock.patch.object(self.tab, "_start") as start, mock.patch.object(run_module.messagebox, "showerror") as error:
            self.tab.run()
        start.assert_not_called()
        error.assert_called_once()

    def test_install_package_runs_the_pip_command_like_the_doctor(self):
        started = []
        with mock.patch.object(run_module.simpledialog, "askstring", return_value="tqdm  pyzbar"), \
                mock.patch.object(self.tab, "_start", side_effect=started.append):
            self.tab.install_package()
        self.assertEqual(started[0][-4:], ["pip", "install", "tqdm", "pyzbar"])
        self.assertTrue(self.tab.running_doctor)           # a utility run: Stop is off, the simulator is not involved
        self.assertIn("disabled", self.tab.stop_button.state())
        self.assertIn("disabled", self.tab.install_button.state())
        self.assertIn("installing tqdm pyzbar", self.text())
        self.tab.running_doctor = False
        self.tab._set_idle("Ready")
        self.assertNotIn("disabled", self.tab.install_button.state())

    def test_typing_remove_and_names_removes_packages(self):
        started = []
        with mock.patch.object(run_module.simpledialog, "askstring", return_value="remove opencv-python numpy"), \
                mock.patch.object(self.tab, "_start", side_effect=started.append):
            self.tab.install_package()
        self.assertEqual(started[0][-4:], ["pip", "remove", "opencv-python", "numpy"])
        self.assertEqual(self.tab.utility, "pip-remove")
        self.assertIn("removing opencv-python numpy", self.text())
        self.app.show_output("Removed numpy 2.5.4 (1335 files)")
        self.app._finished(0)
        self.assertEqual(self.tab.status.cget("text"), "Packages removed")
        self.tab.utility, self.tab.running_doctor = "doctor", False

    def test_install_package_does_nothing_when_cancelled_or_the_name_is_bad(self):
        for answer in (None, "", "   "):
            with mock.patch.object(run_module.simpledialog, "askstring", return_value=answer), \
                    mock.patch.object(self.tab, "_start") as start:
                self.tab.install_package()
            start.assert_not_called()
        with mock.patch.object(run_module.simpledialog, "askstring", return_value="--index-url=http://x"), \
                mock.patch.object(self.tab, "_start") as start, mock.patch.object(run_module.messagebox, "showerror") as error:
            self.tab.install_package()
        start.assert_not_called()
        error.assert_called_once()

    def test_the_finished_install_reports_in_the_status_line(self):
        self.tab.running_doctor, self.tab.utility = True, "pip"
        self.app.show_output("[WARN] import pyzbar.pyzbar fails: ImportError: Unable to find zbar shared library")
        self.app._finished(0)
        self.assertIn("does not import", self.tab.status.cget("text"))
        self.tab.utility = "doctor"
        self.tab.running_doctor = False

    def test_remove_is_disabled_for_ordinary_scripts(self):
        self.select("takeoff_land.py")
        self.assertIn("disabled", self.tab.remove_button.state())

    def test_copy_example_to_my_scripts(self):
        self.select("takeoff_land.py")
        self.tab.copy_example()
        self.assertTrue((self.mine / "takeoff_land.py").is_file())
        self.assertEqual(self.tab.selected().group, scripts.MINE)
        self.assertIn("Copied", self.text())

    def test_run_shows_output_and_returns_to_idle(self):
        self.tab._set_running("Starting...")
        self.tab._start([sys.executable, "-c", "print('hello from the launcher'); print('2026 ERROR bad')"])
        self.assertTrue(self.pump(lambda: not self.app.runner.running and "finished" in self.text()))
        self.assertIn("hello from the launcher", self.text())
        self.assertEqual(self.tab.status.cget("text"), "Finished")
        self.assertIn("disabled", self.tab.stop_button.state())
        self.assertNotIn("disabled", self.tab.run_button.state())
        self.assertIn("error", self.tab.output.text.tag_names("2.0"))  # the "ERROR" line is coloured

    def test_the_doctor_button_runs_doctor_and_reports_the_result(self):
        self.assertEqual(self.tab.check_button.cget("text"), "Run doctor")
        fake = [sys.executable, "-c", "print('[ OK ] Windows: build 1'); print('[FAIL] WSL networking: none'); "
                "print('2 checks: 1 failed, 0 warnings'); raise SystemExit(1)"]
        self.tab.running_doctor = True
        self.tab.doctor_summary = None
        self.tab._set_running("Running doctor...")
        self.assertIn("disabled", self.tab.stop_button.state())  # doctor cannot be "landed"
        self.tab._start(fake)
        self.assertTrue(self.pump(lambda: not self.app.runner.running and "finished" in self.text()))
        self.assertEqual(self.tab.status.cget("text"), "Doctor: 1 of 2 checks failed. See the red lines in the output")
        self.assertIn("ok", self.tab.output.text.tag_names("1.0"))
        self.assertIn("error", self.tab.output.text.tag_names("2.0"))
        self.assertNotIn("disabled", self.tab.check_button.state())

    def test_pressing_the_button_starts_the_real_doctor_command(self):
        with mock.patch.object(self.tab, "_start") as start:
            self.tab.check()
        command = start.call_args.args[0]
        self.assertEqual(command[-1], "doctor")
        self.assertIn("-c", command)
        self.assertEqual(self.tab.status.cget("text"), "Running doctor...")
        self.tab._set_idle("Ready")

    def test_failure_is_reported_with_the_exit_code(self):
        self.tab._set_running("Starting...")
        self.tab._start([sys.executable, "-c", "import sys; sys.exit(7)"])
        self.assertTrue(self.pump(lambda: "exit code 7" in self.text()))
        self.assertIn("code 7", self.tab.status.cget("text"))

    def test_stop_button_asks_the_process_to_stop(self):
        child = (
            "import os, sys, time; p = os.path.join(os.environ['MRRSIM_DATA_DIR'], 'stop.request'); print('up', flush=True)\n"
            "while not os.path.exists(p): time.sleep(0.05)\n"
            "print('landing', flush=True); sys.exit(130)"
        )
        self.tab.running_doctor = False
        self.tab._set_running("Starting...")
        self.tab._start([sys.executable, "-c", child])
        self.assertTrue(self.pump(lambda: "up" in self.text()))
        self.assertNotIn("disabled", self.tab.stop_button.state())
        self.tab.stop()
        self.assertIn("landing the vehicle", self.tab.status.cget("text"))
        self.assertTrue(self.pump(lambda: not self.app.runner.running and "finished" in self.text()))
        self.assertIn("landing", self.text())
        self.assertEqual(self.tab.status.cget("text"), "Stopped")

    def test_run_refuses_a_second_start_while_running(self):
        self.tab._set_running("Starting...")
        self.tab._start([sys.executable, "-c", "import time; time.sleep(30)"])
        self.root.update()
        self.select("takeoff_land.py")
        with mock.patch.object(self.app.runner, "start") as start:
            self.tab.run()
        start.assert_not_called()
        self.app.runner.kill()
        self.pump(lambda: not self.app.runner.running, 10)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
