"""The window: pick a script, press Run.

`App` is the shell. It owns the Tk window, the fonts and spacing, the three tabs (Run, Vehicles, Simulator), the one
`Runner` that talks to the command-line launcher, and the things the tabs share: the library of vehicle designs and the
places output and values are shown (the Run tab and the Simulator tab each show a copy). Each tab's own behaviour is in its
folder: ui/run, ui/vehicles, ui/simulator. Shared pieces are in ui/common.

The window does not start the simulator itself. It runs the command-line launcher (`mrrsim run <script> ...`) as a
background process and shows what it prints, so the window and the command line always behave the same way.
"""
from __future__ import annotations

import os
import queue
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from ..core import config, paths
from ..designs import vehicle_design
from .common.launcher_process import Runner
from .run.run_tab import RunTab
from .simulator.simulator_tab import SimulatorTab
from .vehicles.vehicle_tab import VehiclesTab


class App:
    def __init__(self, root: tk.Tk, cfg: config.Config, runner: Runner | None = None) -> None:
        self.root, self.cfg = root, cfg
        self.runner = runner or Runner()
        self.closing = False

        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)  # 1.25 at 125% screen scaling
        root.title("MRRSimulator")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self.run_tab.refresh()
        self.run_tab._set_idle("Ready")
        self._fit_to_screen()
        root.after(100, self._poll)

    # ---------------------------------------------------------------------------------- layout
    def px(self, n: float) -> int:
        """A size in pixels that grows with the screen's scaling (125%, 150% ...), so spacing stays roomy."""
        return int(round(n * self.scale))

    def _style(self) -> None:
        base = tkfont.nametofont("TkDefaultFont")
        base.configure(family="Segoe UI", size=10)
        tkfont.nametofont("TkTextFont").configure(family="Segoe UI", size=10)
        self.font = base
        self.font_bold = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.font_title = tkfont.Font(family="Segoe UI", size=14, weight="bold")
        self.font_status = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        style = ttk.Style(self.root)
        # a row is the text plus generous air above and below, so neighbouring rows never touch
        style.configure("Treeview", rowheight=self.font.metrics("linespace") + self.px(18), font=self.font)
        style.configure("Dense.Treeview", rowheight=self.font.metrics("linespace") + self.px(9), font=self.font)  # the Vehicles tab
        style.configure("TButton", padding=(self.px(14), self.px(8)))
        style.configure("Accent.TButton", padding=(self.px(22), self.px(10)), font=self.font_bold)
        style.configure("Small.TButton", padding=(self.px(9), self.px(5)))  # the Vehicles tab packs many buttons
        style.configure("SmallAccent.TButton", padding=(self.px(9), self.px(5)), font=self.font_bold)
        style.configure("Danger.TButton", padding=(self.px(14), self.px(8)), foreground="#c01c28", font=self.font_bold)
        style.configure("TNotebook.Tab", padding=(self.px(20), self.px(6)), font=self.font_bold)
        style.configure("TLabelframe", padding=self.px(14))
        style.configure("TLabelframe.Label", font=self.font_bold)
        style.configure("TCheckbutton", padding=(0, self.px(4)))
        style.configure("Muted.TLabel", foreground="#5e5c64")

    def _fit_to_screen(self) -> None:
        """Start at a comfortable size, but never taller or wider than the screen; the window cannot get
        smaller than its contents need (the output pane is what gives way first)."""
        root = self.root
        root.update_idletasks()
        room_w, room_h = root.winfo_screenwidth() - self.px(40), root.winfo_screenheight() - self.px(90)
        need_w, need_h = min(root.winfo_reqwidth(), room_w), min(root.winfo_reqheight(), room_h)
        root.minsize(need_w, need_h)
        root.geometry(f"{min(max(self.px(1160), need_w), room_w)}x{min(max(self.px(800), need_h), room_h)}")

    def _build(self) -> None:
        root = self.root
        self._style()
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.tabs = ttk.Notebook(root)  # Run | Vehicles | Simulator
        self.tabs.grid(row=0, column=0, sticky="nsew")
        self.library = vehicle_design.Library(paths.vehicles_dir(), self.cfg.scene.config_dir)
        self.run_tab = RunTab(self.tabs, self)
        self.tabs.add(self.run_tab, text="Run")
        self.vehicles_tab = VehiclesTab(self.tabs, self, self.library, on_selection_changed=self.run_tab.refresh_design_choices)
        self.tabs.add(self.vehicles_tab, text="Vehicles")
        self.simulator_tab = SimulatorTab(self.tabs, self)
        self.tabs.add(self.simulator_tab, text="Simulator")
        self.run_tab.refresh_design_choices()

    # ------------------------------------------------------------------------------------ shared by the tabs
    def show_output(self, text: str, tag: str | None = None) -> None:
        """A line of output, in the Run tab and in the Simulator tab."""
        for view in (self.run_tab.output, self.simulator_tab.output):
            view.append(text, tag)

    def note(self, text: str) -> None:
        self.show_output(text, "note")

    def clear_output(self) -> None:
        for view in (self.run_tab.output, self.simulator_tab.output):
            view.clear()

    def set_value(self, key: str, number: float | None) -> None:
        """A new Altitude or Throttle, in the value boxes of both tabs."""
        for tiles in (self.run_tab.values, self.simulator_tab.values):
            tiles.set(key, number)

    def reset_values(self) -> None:
        for tiles in (self.run_tab.values, self.simulator_tab.values):
            tiles.reset()

    # ------------------------------------------------------------------------------------ the launcher's output
    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.runner.events.get_nowait()
                if kind == "line":
                    self.run_tab.handle_line(str(payload))
                else:
                    self._finished(int(payload))
        except queue.Empty:
            pass
        if self.closing and self.runner.running:
            self._closing_deadline -= 0.1
            if self._closing_deadline <= 0:  # it will not stop: the launcher's Job Object takes Unreal with it
                self.runner.kill()
        self.root.after(100, self._poll)

    def _finished(self, code: int) -> None:
        self.simulator_tab.release()
        if self.closing:
            self.root.destroy()
            return
        self.run_tab.handle_exit(code)

    def on_close(self) -> None:
        if not self.vehicles_tab.confirm_discard():  # unsaved changes to a vehicle design
            return
        if self.runner.running:
            if not messagebox.askyesno("MRRSimulator", "A simulation is still running. Stop it and close this window?"):
                return
            self.closing = True
            self.run_tab.stop()
            self._closing_deadline = 60
            return
        self.simulator_tab.release()
        self.root.destroy()


def main(cfg: config.Config | None = None) -> int:
    if os.name == "nt":
        try:  # sharp text on high-DPI screens
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    try:
        cfg = cfg or config.load(paths.find_config())
    except (FileNotFoundError, config.ConfigError, ValueError) as exc:
        _error_box(f"MRRSimulator could not read its settings:\n\n{exc}")
        return 2
    root = tk.Tk()
    App(root, cfg)
    root.mainloop()
    return 0


def _error_box(message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("MRRSimulator", message)
    root.destroy()
