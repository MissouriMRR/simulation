"""The Simulator tab: Unreal's window inside this window, with the Altitude and Throttle boxes and a copy of the output.

While a windowed run is going, the tab holds Unreal's own window (moved in by embed.py). Above the divider: the simulator
on the left and the value boxes stacked on the right; below it: the output. The Run tab starts the watching (`watch`),
the window ends it (`release`).

The simulator is display only: it shows whatever camera was chosen last. Tab switches to flying the free camera (a green
dot shows it took the keyboard) and back; the keys and mouse are read here, Unreal never gets them (freecam.py).
"""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..common import colors
from ..common.output import OutputView
from ..common.values import ValueTiles
from . import embed, freecam

KEEP_SIZE_MS = 1500  # how often the simulator is put back to the tab's size (Unreal resizes itself when a vehicle spawns)
FLY_MS = 33          # how often the free camera reads the keys and moves (about 30 times a second)
DOT_MS = 900         # how long the green dot shows
NO_ANSWER_S = 3.0    # no answer from the free camera this long: the run has none
VK_W, VK_A, VK_S, VK_D, VK_Q, VK_E, VK_SHIFT, VK_RBUTTON = 0x57, 0x41, 0x53, 0x44, 0x51, 0x45, 0x10, 0x02
NO_FREECAM = "This run has no free camera (it uses a scene of your own)."


class SimulatorTab(ttk.Frame):
    """The Simulator tab: the frame Unreal's window is moved into, the value boxes and a copy of the output."""

    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent)
        self.app = app
        self.embedder: embed.Embedder | None = None
        self._timers: dict[str, str] = {}  # pending timers by name: "poll" (waiting for Unreal), "keep_size", "fly", "dot"
        self.flying = False
        self.link: freecam.Link | None = None
        self.camera: freecam.CameraPose | None = None  # where the free camera is (from the scene loader, then flown)
        self._start_camera: freecam.CameraPose | None = None
        self._asked_at = 0.0
        self._look_from: tuple[int, int] | None = None  # where the cursor is held while the right button turns the view
        self._last_tick = 0.0
        self.speed = freecam.SPEED_M_S
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        # the simulator and the value boxes above, a copy of the output below; the divider can be dragged
        split = ttk.PanedWindow(self, orient="vertical")
        split.grid(row=0, column=0, sticky="nsew", padx=app.px(8), pady=app.px(8))
        upper = ttk.Frame(split)
        upper.columnconfigure(0, weight=1)
        upper.rowconfigure(0, weight=1)
        split.add(upper, weight=5)
        self.frame = tk.Frame(upper, background=colors.SIM_BACKGROUND, highlightthickness=0, borderwidth=0)
        self.frame.grid(row=0, column=0, sticky="nsew")
        side = ttk.Frame(upper)  # the value boxes, and the camera buttons stacked under them
        side.grid(row=0, column=1, sticky="n")
        self.values = ValueTiles(side, app)
        self.values.grid(row=0, column=0, sticky="n")
        self.drone_button = ttk.Button(side, text="Drone view", command=self.drone_view)
        self.reset_button = ttk.Button(side, text="Reset camera", command=self.reset_camera)
        self.chase_button = ttk.Button(side, text="Chase camera", command=self.chase_camera)
        for row, button in enumerate((self.drone_button, self.reset_button, self.chase_button), start=1):
            button.grid(row=row, column=0, sticky="ew", padx=app.px(8), pady=(app.px(8), 0))
        self._show_controls()
        lower = ttk.LabelFrame(split, text="Output")
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(0, weight=1)
        split.add(lower, weight=3)
        self.output = OutputView(lower, app)
        self.output.grid(row=0, column=0, sticky="nsew")
        self._split, self._divider_set = split, False
        split.bind("<Configure>", self._place_divider)
        self.note = tk.Label(self.frame, background=colors.SIM_BACKGROUND, foreground=colors.SIM_NOTE, font=app.font, justify="center",
                             text="The simulator appears here while a run is going.\n"
                                  "(Run tab: \"Show the simulator inside this window\")")
        self.note.place(relx=0.5, rely=0.5, anchor="center")
        self.frame.bind("<Configure>", lambda _e: self.resize())
        parent.bind("<<NotebookTabChanged>>", lambda _e: self._tab_changed(), add="+")
        self.winfo_toplevel().bind("<Tab>", self._on_tab, add="+")
        self.winfo_toplevel().bind("<MouseWheel>", self._on_wheel, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _place_divider(self, event) -> None:
        """Once the tab has a size: the simulator gets the upper 62% (the output copy the rest)."""
        if self._divider_set or event.height < 200:
            return
        self._divider_set = True
        self._split.sashpos(0, int(event.height * 0.62))

    # ------------------------------------------------------------------------------------ timers
    def _later(self, name: str, ms: int, fn: Callable[[], None]) -> None:
        """Run `fn` in `ms` as the timer `name`; a pending timer of that name is replaced."""
        self._cancel(name)

        def fire() -> None:
            self._timers.pop(name, None)
            fn()

        self._timers[name] = self.after(ms, fire)

    def _cancel(self, *names: str) -> None:
        """Cancel the pending timers of these names (all of them when no name is given)."""
        for name in names or tuple(self._timers):
            pending = self._timers.pop(name, None)
            if pending is not None:
                self.after_cancel(pending)

    def _on_destroy(self, event: tk.Event) -> None:
        """The tab is gone, so its timers must not fire again (they would run inside another window's event loop)."""
        if event.widget is self:
            self._cancel()
            if self.link is not None:
                self.link.close()

    # ------------------------------------------------------------------------------------ the free camera
    def _show_controls(self) -> None:
        """The camera buttons work while the simulator is shown here."""
        shown = self.embedder is not None
        for button in (self.drone_button, self.reset_button, self.chase_button):
            button.state(["!disabled"] if shown else ["disabled"])

    def _on_wheel(self, event) -> str | None:
        """While flying, the scroll wheel sets how fast the camera flies; otherwise it is left alone."""
        if not self.flying:
            return None
        notches = round(event.delta / 120) or (1 if event.delta > 0 else -1)
        self.speed = freecam.wheel_speed(self.speed, notches)
        return "break"

    def _on_tab(self, _event) -> str | None:
        """Tab, while the simulator is showing here, switches between showing and flying; elsewhere it is left alone."""
        if self.embedder is None or self.app.tabs.select() != str(self):
            return None
        if self.flying:
            self.stop_flying()
        else:
            self.start_flying()
        return "break"

    def _tab_changed(self) -> None:
        self.resize()
        if self.flying and self.app.tabs.select() != str(self):
            self.stop_flying()

    def start_flying(self) -> None:
        """Take the keyboard and mouse for the free camera and show it."""
        self.flying = True
        if self.link is None:
            self.link = freecam.Link()
        if self.camera is None:
            self._asked_at = time.monotonic()
            self.link.send({"cmd": "get"})
        self.link.send({"cmd": "view", "to": "freecam"})
        self.frame.focus_set()  # keys typed now go nowhere else in the window
        self._show_dot()
        self._show_controls()
        self._last_tick = time.monotonic()
        self._later("fly", FLY_MS, self._fly)

    def stop_flying(self) -> None:
        """Back to showing: the view stays on the camera it shows."""
        self.flying = False
        self._look_from = None
        self._cancel("fly")
        self._show_controls()

    def _show_dot(self) -> None:
        """A small green dot over the simulator for a moment: the free camera has the keyboard."""
        size, margin = self.app.px(28), self.app.px(12)
        dot = tk.Toplevel(self)
        dot.overrideredirect(True)
        try:
            dot.attributes("-topmost", True, "-transparentcolor", colors.OVERLAY_KEY)
        except tk.TclError:
            pass  # (not on Windows) the dot shows on a square
        dot.geometry(f"{size}x{size}+{self.frame.winfo_rootx() + self.frame.winfo_width() - size - margin}"
                     f"+{self.frame.winfo_rooty() + margin}")
        canvas = tk.Canvas(dot, width=size, height=size, background=colors.OVERLAY_KEY, highlightthickness=0)
        canvas.create_oval(2, 2, size - 2, size - 2, fill=colors.FLYING_DOT, outline=colors.FLYING_DOT)
        canvas.pack()
        self._later("dot", DOT_MS, dot.destroy)

    def _fly(self) -> None:
        """Read the keys and mouse, move the free camera, and come back in FLY_MS."""
        now = time.monotonic()
        dt = min(max(now - self._last_tick, 0.001), 0.1)  # a stall does not become a jump
        self._last_tick = now
        while (answer := self.link.reply()) is not None:
            if "pose" in answer and self.camera is None:
                self.camera = freecam.CameraPose(*answer["pose"])
                self._start_camera = self._start_camera or self.camera  # where it was first: Reset camera's place
        if self.camera is None:
            if now - self._asked_at > NO_ANSWER_S:
                self.stop_flying()
                self.app.note(NO_FREECAM)
                return
            self._later("fly", FLY_MS, self._fly)
            return
        if embed.foreground_window() == int(self.winfo_toplevel().wm_frame(), 16):  # keys typed elsewhere do not fly it
            self._move(dt)
        self._later("fly", FLY_MS, self._fly)

    def _move(self, dt: float) -> None:
        def axis(plus: int, minus: int) -> int:
            return int(embed.key_down(plus)) - int(embed.key_down(minus))

        dx = dy = 0
        if embed.key_down(VK_RBUTTON):  # turn: the cursor stays put, how far it was pushed is the turn
            x, y = embed.cursor_pos()
            if self._look_from is None:
                self._look_from = (x, y)
            dx, dy = x - self._look_from[0], y - self._look_from[1]
            embed.set_cursor_pos(*self._look_from)
        else:
            self._look_from = None
        moved = freecam.step(self.camera, dt, axis(VK_W, VK_S), axis(VK_D, VK_A), axis(VK_E, VK_Q),
                             embed.key_down(VK_SHIFT), dx, dy, self.speed)
        if moved != self.camera:
            self.camera = moved
            self.link.send({"cmd": "pose", "pose": moved.as_list()})

    def reset_camera(self) -> None:
        """The free camera back where it started, and shown."""
        if self.link is None:
            self.link = freecam.Link()
        if self._start_camera is not None:
            self.camera = self._start_camera
            self.link.send({"cmd": "pose", "pose": self.camera.as_list()})
        self.link.send({"cmd": "view", "to": "freecam"})

    def drone_view(self) -> None:
        """Show the first drone's camera (and stop flying)."""
        if self.flying:
            self.stop_flying()
        if self.link is None:
            self.link = freecam.Link()
        self.link.send({"cmd": "view", "to": "vehicle"})

    def chase_camera(self) -> None:
        """The free camera follows the first drone from behind and above (until it is flown or another view is
        chosen)."""
        if self.flying:
            self.stop_flying()
        if self.link is None:
            self.link = freecam.Link()
        self.camera = None  # it moves without us now: flying again starts from where it is then
        self.link.send({"cmd": "chase"})

    # ------------------------------------------------------------------------------------ the simulator's window
    def resize(self) -> None:
        """Make the simulator fill the tab again (the tab changed size, or was selected)."""
        if self.embedder is not None:
            self.embedder.resize()

    def watch(self, wanted: Callable[[], bool], on_attached: Callable[[], None]) -> None:
        """After a run starts: wait for Unreal's window (while `wanted()`), then move it into this tab and call
        `on_attached`. Does nothing off Windows or when Unreal is not set to run in a window."""
        if not (embed.available() and self.app.cfg.unreal.windowed):
            return
        self.release()  # an earlier run's simulator, and its watching, end here
        self.update_idletasks()
        embed.wait_for_window(self.frame.winfo_id(), self.app.cfg.unreal.exe.name,
                              lambda ms, fn: self._later("poll", ms, fn),
                              lambda embedder: self._attached(embedder, on_attached), wanted)

    def _attached(self, embedder: embed.Embedder, on_attached: Callable[[], None]) -> None:
        """Unreal's window is in the tab: show the tab and keep the window its size."""
        self.embedder = embedder
        self.note.place_forget()
        self.app.tabs.select(self)
        self._show_controls()
        on_attached()
        self._keep_size()

    def _keep_size(self) -> None:
        """Unreal resizes its own window when the vehicle spawns; put it back to the tab's size."""
        embedder = self.embedder
        if embedder is None:
            return
        if not embedder.attached:
            self.release()
            return
        embedder.resize()
        self._later("keep_size", KEEP_SIZE_MS, self._keep_size)

    def release(self) -> None:
        """Give Unreal's window back (it is about to close, or the run is over), stop watching, show the note again."""
        self.flying, self._look_from = False, None
        self._cancel()
        self.camera = self._start_camera = None  # the next run's free camera starts where its scene puts it
        embedder, self.embedder = self.embedder, None
        if embedder is not None:
            embedder.release()
        self.note.place(relx=0.5, rely=0.5, anchor="center")
        self._show_controls()
