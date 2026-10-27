"""The Simulator tab (ui/simulator): Unreal's window moved into it (embed.py) and the tab's layout."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config, paths
from mrrsim.ui import app as window
from mrrsim.ui.simulator import embed

GUI = os.environ.get("MRRSIM_GUI_TESTS") == "1"


class Embedding(unittest.TestCase):
    """The logic around embedding Unreal's window; the real thing (SetParent) is checked by hand on a hidden desktop."""

    def test_polling_waits_for_the_window_then_embeds_once(self):
        calls, scheduled, attached = [], [], []
        found = iter([None, None, 4242])
        embedder = mock.Mock()
        with mock.patch.object(embed, "find_window", side_effect=lambda exe: next(found)), \
                mock.patch.object(embed, "Embedder", return_value=embedder) as make:
            embed.wait_for_window(77, lambda ms, fn: scheduled.append(fn), attached.append, lambda: True)
            while scheduled:
                scheduled.pop(0)()
                calls.append(1)
        self.assertEqual(len(calls), 3)               # two empty polls, then the window
        make.assert_called_once_with(77)
        embedder.attach.assert_called_once_with(4242)
        self.assertEqual(attached, [embedder])

    def test_polling_stops_when_the_run_is_over_or_the_option_is_off(self):
        scheduled = []
        with mock.patch.object(embed, "find_window", return_value=4242) as find:
            embed.wait_for_window(1, lambda ms, fn: scheduled.append(fn), lambda e: None, lambda: False)
            scheduled.pop(0)()
        find.assert_not_called()
        self.assertEqual(scheduled, [])

    def test_the_embedder_leaves_a_window_that_is_gone(self):
        embedder = embed.Embedder(1)
        embedder.child = 99
        with mock.patch.object(embed.user32, "IsWindow", return_value=0):
            self.assertFalse(embedder.attached)
            embedder.release()                        # nothing to give back, and no error
        self.assertIsNone(embedder.child)


@unittest.skipUnless(GUI, "set MRRSIM_GUI_TESTS=1 (and run on a hidden desktop) to test the real window")
class Tab(unittest.TestCase):
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
        self.tab = self.app.simulator_tab

    def test_it_starts_with_its_note_and_the_option_on_the_run_tab_is_on(self):
        self.assertEqual(self.app.tabs.tab(self.tab, "text"), "Simulator")
        self.assertTrue(self.app.run_tab.show_sim.get())
        self.assertIsNone(self.tab.embedder)
        self.assertTrue(self.tab.note.winfo_manager())

    def test_with_the_option_off_nothing_is_watched(self):
        self.app.run_tab.show_sim.set(False)
        started = []
        with mock.patch.object(self.app.run_tab, "_start", side_effect=started.append), \
                mock.patch.object(embed, "wait_for_window") as wait:
            item = next(i for i, info in self.app.run_tab.infos.items() if info.name == "takeoff_land.py")
            self.app.run_tab.tree.selection_set(item)
            self.app.run_tab.run()
        wait.assert_not_called()
        self.app.run_tab._set_idle("Ready")

    def test_with_the_option_on_a_run_starts_watching_for_unreals_window(self):
        item = next(i for i, info in self.app.run_tab.infos.items() if info.name == "takeoff_land.py")
        self.app.run_tab.tree.selection_set(item)
        with mock.patch.object(self.app.run_tab, "_start"), mock.patch.object(embed, "available", return_value=True), \
                mock.patch.object(embed, "wait_for_window") as wait:
            self.app.run_tab.run()
        wait.assert_called_once()
        self.assertEqual(wait.call_args.args[0], self.tab.frame.winfo_id())
        self.app.run_tab._set_idle("Ready")

    def test_attaching_selects_the_tab_hides_the_note_and_tells_the_run_tab(self):
        told = []
        embedder = mock.Mock(attached=True)
        self.tab._attached(embedder, lambda: told.append("attached"))
        self.assertIs(self.tab.embedder, embedder)
        self.assertEqual(told, ["attached"])
        self.assertEqual(self.app.tabs.select(), str(self.tab))
        self.assertFalse(self.tab.note.winfo_manager())
        self.tab.release()
        embedder.release.assert_called_once()
        self.assertTrue(self.tab.note.winfo_manager())

    def test_the_simulator_is_on_top_the_output_below_and_the_boxes_stacked_on_the_right(self):
        self.app.tabs.select(self.tab)
        self.root.deiconify()
        for _ in range(8):
            self.root.update()
        frame, output, labels = self.tab.frame, self.tab.output, self.tab.values.labels
        self.assertLess(frame.winfo_rooty(), output.winfo_rooty())                          # the output is below the simulator
        self.assertGreater(labels["altitude"].winfo_rootx(), frame.winfo_rootx() + frame.winfo_width() - 5)   # boxes on the right
        self.assertGreater(frame.winfo_height(), output.winfo_height())                      # the simulator gets more room
        self.assertLess(labels["altitude"].winfo_rooty(), labels["throttle"].winfo_rooty())  # stacked


if __name__ == "__main__":
    unittest.main()
