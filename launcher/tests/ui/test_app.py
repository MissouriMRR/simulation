"""ui/app.py: the window shell and what it shares between the tabs (output lines and values shown in two places)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config, paths
from mrrsim.ui import app as window
from mrrsim.ui.common.values import NO_VALUE

GUI = os.environ.get("MRRSIM_GUI_TESTS") == "1"


@unittest.skipUnless(GUI, "set MRRSIM_GUI_TESTS=1 (and run on a hidden desktop) to test the real window")
class Shared(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        patches = mock.patch.dict("os.environ", {"MRRSIM_USER_SCRIPTS": str(tmp / "mine"), "MRRSIM_DATA_DIR": str(tmp / "data"),
                                                 "MRRSIM_VEHICLES_DIR": str(tmp / "vehicles")})
        patches.start()
        self.addCleanup(patches.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        wd = mock.patch.object(paths, "work_dir", return_value=tmp / "work")
        wd.start()
        self.addCleanup(wd.stop)
        self.app = window.App(self.root, config.load(paths.repo_root() / "config" / "mrrsim.toml"))
        self.run_tab, self.simulator_tab = self.app.run_tab, self.app.simulator_tab

    def run_text(self):
        return self.run_tab.output.contents()

    def sim_text(self):
        return self.simulator_tab.output.contents()

    def test_the_tabs_are_run_then_vehicles_then_the_simulator(self):
        texts = [self.app.tabs.tab(i, "text") for i in range(self.app.tabs.index("end"))]
        self.assertEqual(texts, ["Run", "Vehicles", "Simulator"])
        self.assertEqual(self.app.tabs.index(self.app.tabs.select()), 0)

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
        self.assertIn("error", self.simulator_tab.output.text.tag_names("end - 2 lines"))   # and coloured the same way
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


if __name__ == "__main__":
    unittest.main()
