"""The Simulator tab: Unreal's window inside this window, with the Altitude and Throttle boxes and a copy of the output.

While a windowed run is going, the tab holds Unreal's own window (moved in by embed.py). Above the divider: the simulator
on the left and the value boxes stacked on the right; below it: the output. The Run tab starts the watching (`watch`),
the window ends it (`release`).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..common.output import OutputView
from ..common.values import ValueTiles
from . import embed


class SimulatorTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent)
        self.app = app
        self.embedder: embed.Embedder | None = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        # the simulator and the value boxes above, a copy of the output below; the divider can be dragged
        split = ttk.PanedWindow(self, orient="vertical")
        split.grid(row=0, column=0, sticky="nsew", padx=app.px(8), pady=app.px(8))
        upper = ttk.Frame(split)
        upper.columnconfigure(0, weight=1)
        upper.rowconfigure(0, weight=1)
        split.add(upper, weight=5)
        self.frame = tk.Frame(upper, background="#202020", highlightthickness=0, borderwidth=0)
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
        self.note = tk.Label(self.frame, background="#202020", foreground="#b0b0b0", font=app.font, justify="center",
                             text="The simulator appears here while a run is going.\n"
                                  "(Run tab: \"Show the simulator inside this window\")")
        self.note.place(relx=0.5, rely=0.5, anchor="center")
        self.frame.bind("<Configure>", lambda _e: self.resize())
        parent.bind("<<NotebookTabChanged>>", lambda _e: self.resize(), add="+")

    def _place_divider(self, event) -> None:
        """Once the tab has a size: the simulator gets the upper 62% (the output copy the rest)."""
        if self._divider_set or event.height < 200:
            return
        self._divider_set = True
        self._split.sashpos(0, int(event.height * 0.62))

    def resize(self) -> None:
        if self.embedder is not None:
            self.embedder.resize()

    def watch(self, wanted: Callable[[], bool], on_attached: Callable[[], None]) -> None:
        """After a run starts: wait for Unreal's window (while `wanted()`), then move it into this tab and call
        `on_attached`. Does nothing off Windows or when Unreal is not set to run in a window."""
        if not (embed.available() and self.app.cfg.unreal.windowed):
            return
        self.release()
        self.update_idletasks()
        embed.wait_for_window(self.frame.winfo_id(), self.after, lambda embedder: self._attached(embedder, on_attached), wanted)

    def _attached(self, embedder: embed.Embedder, on_attached: Callable[[], None]) -> None:
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
        self.after(1500, self._keep_size)

    def release(self) -> None:
        """Give Unreal's window back (it is about to close, or the run is over) and show the note again."""
        embedder, self.embedder = self.embedder, None
        if embedder is not None:
            embedder.release()
        self.note.place(relx=0.5, rely=0.5, anchor="center")
