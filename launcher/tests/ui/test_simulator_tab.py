"""The Simulator tab (ui/simulator): Unreal's window moved into it (embed.py) and the tab's layout."""
import os
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config, paths
from mrrsim.ui import app as window
from mrrsim.ui.simulator import embed, freecam
from mrrsim.ui.simulator import simulator_tab as sim_module

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


class Flying(unittest.TestCase):
    """freecam.step: where the keys and mouse take the free camera."""

    START = freecam.CameraPose(0.0, 0.0, -20.0)

    def test_it_flies_where_it_looks(self):
        ahead = freecam.step(self.START, 1.0, 1, 0, 0, False, 0, 0)
        self.assertAlmostEqual(ahead.x, freecam.SPEED_M_S)
        fast = freecam.step(self.START, 1.0, 1, 0, 0, True, 0, 0)
        self.assertAlmostEqual(fast.x, freecam.SPEED_M_S * freecam.FAST_FACTOR)
        east = freecam.step(freecam.CameraPose(0.0, 0.0, -20.0, yaw=90.0), 1.0, 1, 0, 0, False, 0, 0)
        self.assertAlmostEqual(east.y, freecam.SPEED_M_S)
        right = freecam.step(self.START, 1.0, 0, 1, 0, False, 0, 0)
        self.assertAlmostEqual(right.y, freecam.SPEED_M_S)
        up = freecam.step(self.START, 1.0, 0, 0, 1, False, 0, 0)
        self.assertAlmostEqual(up.z, -20.0 - freecam.SPEED_M_S)               # NED: up is -z
        down_the_view = freecam.step(freecam.CameraPose(0.0, 0.0, -20.0, pitch=-90.0), 1.0, 1, 0, 0, False, 0, 0)
        self.assertGreater(down_the_view.z, -20.0)

    def test_the_wheel_speeds_it_up_and_down_within_limits_and_speed_scales_the_flight(self):
        self.assertAlmostEqual(freecam.wheel_speed(4.0, 1), 4.0 * freecam.WHEEL_STEP)
        self.assertAlmostEqual(freecam.wheel_speed(4.0, -1), 4.0 / freecam.WHEEL_STEP)
        self.assertEqual(freecam.wheel_speed(freecam.MAX_SPEED_M_S, 5), freecam.MAX_SPEED_M_S)
        self.assertEqual(freecam.wheel_speed(freecam.MIN_SPEED_M_S, -5), freecam.MIN_SPEED_M_S)
        self.assertAlmostEqual(freecam.step(self.START, 1.0, 1, 0, 0, False, 0, 0, speed=12.0).x, 12.0)

    def test_the_mouse_turns_it_and_it_never_looks_past_straight_up_or_down(self):
        turned = freecam.step(self.START, 0.0, 0, 0, 0, False, 100, 0)
        self.assertAlmostEqual(turned.yaw, 100 * freecam.LOOK_DEG_PER_PX)
        self.assertEqual(freecam.step(self.START, 0.0, 0, 0, 0, False, 0, 10_000).pitch, -freecam.PITCH_LIMIT_DEG)
        self.assertEqual(freecam.step(self.START, 0.0, 0, 0, 0, False, 0, 0), self.START)  # nothing held: no move


@unittest.skipUnless(embed.available(), "the window is only embedded on Windows")
class EmbedderOnWindows(unittest.TestCase):
    """What Embedder asks of Windows, with user32 replaced: no real window is touched."""

    STYLE, EX_STYLE, PLACE = 0x16CF0000, 0x00040100, (10, 20, 800, 600)

    def setUp(self):
        self.calls = {}
        for name in ("SetWindowLongPtrW", "SetParent", "SetWindowPos", "ShowWindow", "GetClientRect", "EnableWindow"):
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
        self.calls["EnableWindow"].assert_called_once_with(99, False)  # display only: no mouse or keyboard for Unreal

    def test_releasing_gives_the_window_back_as_it_was_and_where_it_was(self):
        embedder = embed.Embedder(1)
        embedder.attach(99)
        for call in self.calls.values():
            call.reset_mock()
        embedder.release()
        self.assertIsNone(embedder.child)
        self.calls["SetParent"].assert_called_once_with(99, None)
        self.calls["EnableWindow"].assert_called_once_with(99, True)
        self.assertEqual([c.args for c in self.calls["SetWindowLongPtrW"].call_args_list],
                         [(99, embed.GWL_STYLE, self.STYLE), (99, embed.GWL_EXSTYLE, self.EX_STYLE)])
        self.assertEqual(self.calls["SetWindowPos"].call_args.args[2:6], self.PLACE)

    def test_the_window_in_front_is_named_by_its_top_level_window(self):
        # Windows can name the embedded simulator (a child once moved in) as the foreground window: its top-level
        # ancestor, the launcher, is what counts
        with mock.patch.object(embed.user32, "GetForegroundWindow", return_value=99), \
                mock.patch.object(embed.user32, "GetAncestor", return_value=1) as ancestor:
            self.assertEqual(embed.foreground_window(), 1)
        ancestor.assert_called_once_with(99, embed.GA_ROOT)
        with mock.patch.object(embed.user32, "GetForegroundWindow", return_value=None):
            self.assertEqual(embed.foreground_window(), 0)

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

    def showing(self):
        """A simulator in the tab (a stand-in for Unreal's window), with the free camera's link faked."""
        self.link = mock.Mock()
        self.link.reply.side_effect = [{"pose": [0, 0, -20, 0, 0, 0]}] + [None] * 1000
        patch = mock.patch.object(freecam, "Link", return_value=self.link)
        patch.start()
        self.addCleanup(patch.stop)
        self.tab._attached(mock.Mock(attached=True), lambda: None)
        self.root.update()

    def test_tab_does_nothing_without_a_simulator_or_on_another_tab(self):
        self.assertIsNone(self.tab._on_tab(None))
        self.showing()
        self.app.tabs.select(self.app.run_tab)
        self.assertIsNone(self.tab._on_tab(None))
        self.assertFalse(self.tab.flying)

    def test_tab_switches_between_showing_and_flying_with_a_green_dot(self):
        self.showing()
        self.assertEqual(self.tab._on_tab(None), "break")                   # Tab moves no focus while it is ours
        self.assertTrue(self.tab.flying)
        sent = [c.args[0] for c in self.link.send.call_args_list]
        self.assertIn({"cmd": "get"}, sent)
        self.assertIn({"cmd": "view", "to": "freecam"}, sent)
        dots = [w for w in self.tab.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertEqual(len(dots), 1)
        self.assertIn("dot", self.tab._timers)                                   # and it goes away
        self.tab._on_tab(None)
        self.assertFalse(self.tab.flying)
        self.assertNotIn("fly", self.tab._timers)

    def test_held_keys_fly_the_camera_only_while_this_window_is_in_front(self):
        self.showing()
        self.tab.start_flying()
        held = {sim_module.VK_W}
        front = int(self.root.wm_frame(), 16)
        with mock.patch.object(embed, "key_down", side_effect=lambda vk: vk in held), \
                mock.patch.object(embed, "foreground_window", return_value=front):
            self.tab._fly()                                                       # the start pose arrives
            self.tab._fly()
        poses = [c.args[0]["pose"] for c in self.link.send.call_args_list if c.args[0]["cmd"] == "pose"]
        self.assertTrue(poses and poses[-1][0] > 0)                            # forward is +x at yaw 0
        self.link.send.reset_mock()
        with mock.patch.object(embed, "key_down", side_effect=lambda vk: vk in held), \
                mock.patch.object(embed, "foreground_window", return_value=0):
            self.tab._fly()
        self.assertEqual([c for c in self.link.send.call_args_list if c.args[0]["cmd"] == "pose"], [])

    def test_leaving_the_tab_or_the_run_stops_flying(self):
        self.showing()
        self.tab.start_flying()
        self.app.tabs.select(self.app.run_tab)
        self.root.update()
        self.assertFalse(self.tab.flying)
        self.app.tabs.select(self.tab)
        self.tab.start_flying()
        self.tab.release()
        self.assertFalse(self.tab.flying)
        self.assertIn("disabled", self.tab.drone_button.state())
        self.assertIsNone(self.tab.camera)

    def test_the_buttons_show_the_drone_or_put_the_camera_back(self):
        self.showing()
        self.tab.start_flying()
        self.tab._fly()                                                           # the start pose arrives
        self.tab.camera = freecam.CameraPose(50.0, 0.0, -20.0)
        self.tab.reset_camera()
        self.assertEqual(self.tab.camera, freecam.CameraPose(0.0, 0.0, -20.0))
        self.tab.drone_view()
        self.assertFalse(self.tab.flying)
        self.assertEqual(self.link.send.call_args.args[0], {"cmd": "view", "to": "vehicle"})

    def test_the_chase_camera_follows_the_drone_and_flying_again_starts_from_where_it_is(self):
        self.showing()
        self.tab.start_flying()
        self.tab._fly()                                                           # the start pose arrives
        self.tab.chase_camera()
        self.assertFalse(self.tab.flying)
        self.assertEqual(self.link.send.call_args.args[0], {"cmd": "chase"})
        self.assertIsNone(self.tab.camera)
        self.link.send.reset_mock()
        self.tab.start_flying()
        self.assertIn({"cmd": "get"}, [c.args[0] for c in self.link.send.call_args_list])
        self.assertEqual(self.tab._start_camera, freecam.CameraPose(0.0, 0.0, -20.0))  # Reset still goes to the start

    def test_a_run_without_a_free_camera_stops_flying_and_says_so(self):
        self.showing()
        self.link.reply.side_effect = None
        self.link.reply.return_value = None                                     # nobody answers
        self.tab.start_flying()
        self.tab._asked_at -= sim_module.NO_ANSWER_S + 1
        with mock.patch.object(self.app, "note") as note:
            self.tab._fly()
        self.assertFalse(self.tab.flying)
        note.assert_called_once_with(sim_module.NO_FREECAM)

    def test_the_scroll_wheel_sets_the_speed_only_while_flying(self):
        self.showing()
        self.assertIsNone(self.tab._on_wheel(mock.Mock(delta=120)))
        self.assertEqual(self.tab.speed, freecam.SPEED_M_S)
        self.tab.start_flying()
        self.assertEqual(self.tab._on_wheel(mock.Mock(delta=240)), "break")
        self.assertAlmostEqual(self.tab.speed, freecam.SPEED_M_S * freecam.WHEEL_STEP ** 2)
        for _ in range(100):
            self.tab._on_wheel(mock.Mock(delta=-120))
        self.assertEqual(self.tab.speed, freecam.MIN_SPEED_M_S)

    def test_the_camera_buttons_are_stacked_under_the_value_boxes(self):
        self.app.tabs.select(self.tab)
        self.root.deiconify()
        for _ in range(8):
            self.root.update()
        buttons = [self.tab.drone_button, self.tab.reset_button, self.tab.chase_button]
        self.assertEqual([b.cget("text") for b in buttons], ["Drone view", "Reset camera", "Chase camera"])
        tops = [b.winfo_rooty() for b in buttons]
        self.assertEqual(tops, sorted(tops))                                     # one above the other, in this order
        self.assertEqual(len({b.winfo_rootx() for b in buttons}), 1)
        self.assertGreater(tops[0], self.tab.values.labels["throttle"].winfo_rooty())
        self.assertGreater(buttons[0].winfo_rootx(), self.tab.frame.winfo_rootx() + self.tab.frame.winfo_width() - 5)

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
