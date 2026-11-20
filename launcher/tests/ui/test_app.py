"""ui/app.py: the window shell and what it shares between the tabs (output lines and values shown in two places)."""
import io
import os
import queue
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest import mock

import mrrsim
from mrrsim.core import config, paths
from mrrsim.ui import app as window
from mrrsim.ui.common.values import NO_VALUE

GUI = os.environ.get("MRRSIM_GUI_TESTS") == "1"


class Main(unittest.TestCase):
    """Starting the window; no window is opened here."""

    def test_settings_that_cannot_be_read_give_an_error_box_and_code_2(self):
        for where, error in (("find_config", FileNotFoundError("mrrsim.toml not found")),
                             ("load", PermissionError("access is denied")), ("load", config.ConfigError("bad value"))):
            with mock.patch.object(window.os, "name", "posix"), \
                    mock.patch.object(window.paths, "find_config", return_value=Path("mrrsim.toml")), \
                    mock.patch.object(window.paths if where == "find_config" else window.config, where, side_effect=error), \
                    mock.patch.object(window, "_error_box") as box, mock.patch.object(window.tk, "Tk") as root:
                self.assertEqual(window.main(), 2, where)
            self.assertIn(str(error), box.call_args.args[0])
            root.assert_not_called()


@unittest.skipUnless(GUI, "set MRRSIM_GUI_TESTS=1 (and run on a hidden desktop) to test the real window")
class Shared(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        patches = mock.patch.dict("os.environ", {"MRRSIM_USER_SCRIPTS": str(tmp / "mine"), "MRRSIM_DATA_DIR": str(tmp / "data"),
                                                 "MRRSIM_VEHICLES_DIR": str(tmp / "vehicles")})
        patches.start()
        self.addCleanup(patches.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.destroy)
        wd = mock.patch.object(paths, "work_dir", return_value=tmp / "work")
        wd.start()
        self.addCleanup(wd.stop)
        self.app = window.App(self.root, config.load(paths.repo_root() / "config" / "mrrsim.toml"))
        self.run_tab, self.simulator_tab = self.app.run_tab, self.app.simulator_tab

    def destroy(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass  # the test closed the window itself

    def run_text(self):
        return self.run_tab.output.contents()

    def sim_text(self):
        return self.simulator_tab.output.contents()

    def poll_now(self):
        """Run the window's poll at once, in place of its pending timer."""
        self.root.after_cancel(self.app._poll_id)
        self.app._poll()

    def fake_run(self) -> mock.Mock:
        runner = self.app.runner = mock.Mock(events=queue.Queue(), running=True)
        return runner

    def close_during_the_run(self):
        with mock.patch.object(window.messagebox, "askyesno", return_value=True), \
                mock.patch.object(self.app.vehicles_tab, "confirm_discard", return_value=True), \
                mock.patch.object(self.run_tab, "stop") as stop:
            self.app.on_close()
        stop.assert_called_once()
        self.assertTrue(self.app.closing)

    def test_the_title_says_the_version(self):
        self.assertEqual(self.root.title(), f"MRRSimulator - v{mrrsim.__version__}")

    def test_the_tabs_are_run_then_vehicles_then_the_simulator(self):
        texts = [self.app.tabs.tab(i, "text") for i in range(self.app.tabs.index("end"))]
        self.assertEqual(texts, ["Run", "Vehicles", "Simulator"])
        self.assertEqual(self.app.tabs.index(self.app.tabs.select()), 0)

    def test_the_shipped_iarc_drone_is_in_both_vehicle_lists_from_the_start(self):
        self.assertIn("IARC Drone", self.app.library.names())
        self.assertIn("IARC Drone", self.app.run_tab.design_combo.cget("values"))  # any script may fly it

    def test_the_run_tab_starts_ready(self):
        self.assertEqual(self.run_tab.status.cget("text"), "Ready")

    def test_the_boxes_start_empty_in_both_tabs(self):
        for tiles in (self.run_tab.values, self.simulator_tab.values):
            self.assertEqual({k: v.cget("text") for k, v in tiles.labels.items()}, {"altitude": NO_VALUE, "throttle": NO_VALUE})

    def test_values_go_to_the_boxes_and_not_to_the_output(self):
        for line in ("[script] taking off", "[script]  Altitude:  12.3456", "[script] Throttle %: 53", "[script] Altitude: 19.9"):
            self.run_tab.handle_line(line)
        for tiles in (self.run_tab.values, self.simulator_tab.values):
            self.assertEqual((tiles.labels["altitude"].cget("text"), tiles.labels["throttle"].cget("text")), ("19.90", "53"))
        self.assertIn("taking off", self.run_text())
        self.assertNotIn("Altitude", self.run_text())
        self.assertNotIn("Throttle", self.run_text())

    def test_the_simulator_tab_has_a_copy_of_the_output(self):
        for line in ("[script] taking off", "[script]  Altitude:  7.5", "[script] Throttle %: 61", "21:00:00 ERROR something"):
            self.run_tab.handle_line(line)
        self.assertEqual(self.sim_text(), self.run_text())                       # the same text
        self.assertNotIn("Altitude", self.sim_text())                            # values are in the boxes only, there too
        self.assertIn("error", self.simulator_tab.output.text.tag_names("end - 2 lines"))   # and colored the same way
        self.app.clear_output()
        self.assertEqual((self.run_text().strip(), self.sim_text().strip()), ("", ""))

    def test_a_new_run_clears_the_boxes_in_both_tabs(self):
        self.run_tab.handle_line("[script] Altitude: 4")
        self.app.reset_values()
        for tiles in (self.run_tab.values, self.simulator_tab.values):
            self.assertEqual(tiles.labels["altitude"].cget("text"), NO_VALUE)

    def test_a_finished_run_gives_the_simulator_window_back(self):
        embedder = mock.Mock()
        self.simulator_tab.embedder = embedder
        self.app._finished(0)
        embedder.release.assert_called_once()
        self.assertIsNone(self.simulator_tab.embedder)

    def test_the_window_is_never_smaller_than_its_contents(self):
        self.root.update()
        min_w, min_h = self.root.minsize()
        # the window may not be taller or wider than the screen less room for the taskbar (see App._fit_to_screen)
        room_w = self.root.winfo_screenwidth() - self.app.px(40)
        room_h = self.root.winfo_screenheight() - self.app.px(90)
        self.assertGreaterEqual(min_w, min(self.root.winfo_reqwidth(), room_w) - 1)
        self.assertGreaterEqual(min_h, min(self.root.winfo_reqheight(), room_h) - 1)

    def test_the_vehicles_tab_and_the_run_tab_share_the_design_library(self):
        self.assertIs(self.app.vehicles_tab.library, self.app.library)
        self.assertIs(self.run_tab.library, self.app.library)

    def test_destroying_the_window_stops_its_timer(self):
        pending = self.app._poll_id
        self.assertIsNotNone(pending)
        self.root.destroy()
        self.assertIsNone(self.app._poll_id)
        self.assertNotIn(pending, self.root.tk.splitlist(self.root.tk.call("after", "info")))

    def test_closing_during_a_run_stops_it_and_the_end_of_the_run_closes_the_window(self):
        runner = self.fake_run()
        self.close_during_the_run()
        self.poll_now()
        self.assertTrue(self.app._alive)  # the launcher is still shutting down
        runner.running = False
        runner.events.put(("exit", 0))
        self.poll_now()
        self.assertFalse(self.app._alive)
        self.assertIsNone(self.app._poll_id)  # and nothing is left to fire
        runner.kill.assert_not_called()

    def test_a_run_that_will_not_stop_is_killed_after_the_wait(self):
        runner = self.fake_run()
        self.close_during_the_run()
        self.assertEqual(self.app._closing_left_s, window.CLOSE_WAIT_S)
        self.app._closing_left_s = window.POLL_MS / 1000 / 2
        self.poll_now()
        runner.kill.assert_called_once()

    def test_a_failing_button_is_shown_in_a_box(self):
        button = ttk.Button(self.root, command=lambda: 1 / 0)
        with mock.patch.object(window.messagebox, "showerror") as error, mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            button.invoke()
        self.assertIn("ZeroDivisionError", error.call_args.args[1])
        self.assertIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
