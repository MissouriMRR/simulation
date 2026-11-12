"""The Simulator tab: Unreal's window inside this window, with the Altitude and Throttle boxes and a copy of the output.

While a windowed run is going, the tab holds Unreal's own window (moved in by embed.py). Above the divider: the simulator
on the left and the value boxes stacked on the right; below it: the output. The Run tab starts the watching (`watch`),
the window ends it (`release`).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..common import colors
from ..common.output import OutputView
from ..common.values import ValueTiles
from . import embed

KEEP_SIZE_MS = 1500  # how often the simulator is put back to the tab's size (Unreal resizes itself when a vehicle spawns)


class SimulatorTab(ttk.Frame):
    """The Simulator tab: the frame Unreal's window is moved into, the value boxes and a copy of the output."""

    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent)
        self.app = app
        self.embedder: embed.Embedder | None = None
        self._timers: dict[str, str] = {}  # the tab's pending timers by name: "poll" (waiting for Unreal), "keep_size"
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
        self.values = ValueTiles(upper, app)
        self.values.grid(row=0, column=1, sticky="n")
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
        parent.bind("<<NotebookTabChanged>>", lambda _e: self.resize(), add="+")
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
        self._cancel()
        embedder, self.embedder = self.embedder, None
        if embedder is not None:
            embedder.release()
        self.note.place(relx=0.5, rely=0.5, anchor="center")
