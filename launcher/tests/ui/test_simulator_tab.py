"""The Simulator tab (ui/simulator): Unreal's window moved into it (embed.py) and the tab's layout."""
import os
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config, paths
from mrrsim.ui import app as window
from mrrsim.ui.simulator import embed

GUI = os.environ.get("MRRSIM_GUI_TESTS") == "1"


class Embedding(unittest.TestCase):
    """The logic around embedding Unreal's window; the real thing (SetParent) is checked by hand on a hidden desktop."""

    def test_polling_waits_for_the_window_of_the_named_program_then_embeds_once(self):
        calls, scheduled, attached, asked = [], [], [], []
        found = iter([None, None, 4242])
        embedder = mock.Mock()

        def find(exe):
            asked.append(exe)
            return next(found)

        with mock.patch.object(embed, "find_window", side_effect=find), \
                mock.patch.object(embed, "Embedder", return_value=embedder) as make:
            embed.wait_for_window(77, "Sim.exe", lambda ms, fn: scheduled.append(fn), attached.append, lambda: True)
            while scheduled:
                scheduled.pop(0)()
                calls.append(1)
        self.assertEqual(len(calls), 3)               # two empty polls, then the window
        self.assertEqual(set(asked), {"Sim.exe"})
        make.assert_called_once_with(77)
        embedder.attach.assert_called_once_with(4242)
        self.assertEqual(attached, [embedder])

    def test_polling_stops_when_the_run_is_over_or_the_option_is_off(self):
        scheduled = []
        with mock.patch.object(embed, "find_window", return_value=4242) as find:
            embed.wait_for_window(1, "Sim.exe", lambda ms, fn: scheduled.append(fn), lambda e: None, lambda: False)
            scheduled.pop(0)()
        find.assert_not_called()
        self.assertEqual(scheduled, [])


@unittest.skipUnless(embed.available(), "the window is only embedded on Windows")
class EmbedderOnWindows(unittest.TestCase):
    """What Embedder asks of Windows, with user32 replaced: no real window is touched."""

    STYLE, EX_STYLE, PLACE = 0x16CF0000, 0x00040100, (10, 20, 800, 600)

    def setUp(self):
        self.calls = {}
        for name in ("SetWindowLongPtrW", "SetParent", "SetWindowPos", "ShowWindow", "GetClientRect"):
            patch = mock.patch.object(embed.user32, name)
            self.calls[name] = patch.start()
            self.addCleanup(patch.stop)
        styles = {embed.GWL_STYLE: self.STYLE, embed.GWL_EXSTYLE: self.EX_STYLE}
        for patch in (mock.patch.object(embed.user32, "GetWindowLongPtrW", side_effect=lambda hwnd, which: styles[which]),
                      mock.patch.object(embed.user32, "IsWindow", return_value=1),
                      mock.patch.object(embed, "window_rect", return_value=self.PLACE)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_attaching_takes_the_frame_off_and_makes_the_window_a_child(self):
        embedder = embed.Embedder(1)
        embedder.attach(99)
        self.assertEqual(embedder.child, 99)
        style = self.calls["SetWindowLongPtrW"].call_args_list[0].args
        self.assertEqual(style[:2], (99, embed.GWL_STYLE))
        self.assertFalse(style[2] & embed.FRAME_STYLES)
        self.assertTrue(style[2] & embed.WS_CHILD)
        ex_style = self.calls["SetWindowLongPtrW"].call_args_list[1].args
        self.assertEqual(ex_style, (99, embed.GWL_EXSTYLE, self.EX_STYLE & ~embed.FRAME_EX_STYLES))
        self.calls["SetParent"].assert_called_once_with(99, 1)
        self.calls["SetWindowPos"].assert_called_once()  # made the size of the frame

    def test_releasing_gives_the_window_back_as_it_was_and_where_it_was(self):
        embedder = embed.Embedder(1)
        embedder.attach(99)
        for call in self.calls.values():
            call.reset_mock()
        embedder.release()
        self.assertIsNone(embedder.child)
        self.calls["SetParent"].assert_called_once_with(99, None)
        self.assertEqual([c.args for c in self.calls["SetWindowLongPtrW"].call_args_list],
                         [(99, embed.GWL_STYLE, self.STYLE), (99, embed.GWL_EXSTYLE, self.EX_STYLE)])
        self.assertEqual(self.calls["SetWindowPos"].call_args.args[2:6], self.PLACE)

    def test_a_window_that_is_gone_is_simply_let_go(self):
        embedder = embed.Embedder(1)
        embedder.child = 99
        with mock.patch.object(embed.user32, "IsWindow", return_value=0):
            self.assertFalse(embedder.attached)
            embedder.release()                        # nothing to give back, and no error
        self.assertIsNone(embedder.child)
        self.calls["SetParent"].assert_not_called()


@unittest.skipUnless(GUI, "set MRRSIM_GUI_TESTS=1 (and run on a hidden desktop) to test the real window")
class Tab(unittest.TestCase):
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
        self.tab = self.app.simulator_tab

    def destroy(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass  # the test closed the window itself

    def pending(self):
        return set(self.root.tk.splitlist(self.root.tk.call("after", "info")))

    def watch_without_unreal(self):
        """Start watching as a run does, while Unreal's window never appears."""
        with mock.patch.object(embed, "available", return_value=True), mock.patch.object(embed, "find_window", return_value=None):
            self.tab.watch(lambda: True, lambda: None)
        return self.tab._timers["poll"]

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

    def test_with_the_option_on_a_run_watches_for_the_window_of_the_configured_exe(self):
        item = next(i for i, info in self.app.run_tab.infos.items() if info.name == "takeoff_land.py")
        self.app.run_tab.tree.selection_set(item)
        with mock.patch.object(self.app.run_tab, "_start"), mock.patch.object(embed, "available", return_value=True), \
                mock.patch.object(embed, "wait_for_window") as wait:
            self.app.run_tab.run()
        wait.assert_called_once()
        self.assertEqual(wait.call_args.args[:2], (self.tab.frame.winfo_id(), self.app.cfg.unreal.exe.name))
        self.app.run_tab._set_idle("Ready")

    def test_a_second_run_replaces_the_first_runs_watching(self):
        first = self.watch_without_unreal()
        second = self.watch_without_unreal()
        self.assertNotEqual(first, second)
        self.assertNotIn(first, self.pending())
        self.assertIn(second, self.pending())

    def test_release_and_closing_the_window_stop_every_timer(self):
        poll = self.watch_without_unreal()
        self.tab.release()
        self.assertNotIn(poll, self.pending())
        poll = self.watch_without_unreal()
        self.root.destroy()
        self.assertEqual(self.tab._timers, {})
        self.assertNotIn(poll, self.pending())

    def test_attaching_selects_the_tab_hides_the_note_and_tells_the_run_tab(self):
        told = []
        embedder = mock.Mock(attached=True)
        self.tab._attached(embedder, lambda: told.append("attached"))
        self.assertIs(self.tab.embedder, embedder)
        self.assertEqual(told, ["attached"])
        self.assertEqual(self.app.tabs.select(), str(self.tab))
        self.assertFalse(self.tab.note.winfo_manager())
        self.assertIn("keep_size", self.tab._timers)  # the window is kept the tab's size
        self.tab.release()
        embedder.release.assert_called_once()
        self.assertTrue(self.tab.note.winfo_manager())
        self.assertEqual(self.tab._timers, {})

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
