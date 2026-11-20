"""The window: pick a script, press Run.

`App` is the shell. Each of the app's tabs own behavior is in its
folder: ui/run, ui/vehicles, ui/simulator. Shared pieces are in ui/common.

The window does not start the simulator itself. It runs the command-line launcher (`mrrsim run <script> ...`) as a
background process and shows what it prints.
"""
from __future__ import annotations

import os
import queue
import sys
import tkinter as tk
import traceback
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from .. import __version__
from ..core import config, paths
from ..designs import vehicle_design
from .common import colors
from .common.launcher_process import Runner
from .run.run_tab import RunTab
from .simulator.simulator_tab import SimulatorTab
from .vehicles.vehicle_tab import VehiclesTab

POLL_MS = 100       # how often the window takes what the launcher printed
CLOSE_WAIT_S = 60   # closing during a run: how long the launcher gets to shut everything down before it is killed


class App:
    """The window: the tabs, the fonts and styles they use, and the launcher process they share."""

    def __init__(self, root: tk.Tk, cfg: config.Config, runner: Runner | None = None) -> None:
        self.root, self.cfg = root, cfg
        self.runner = runner or Runner()
        self.closing = False           # the window closes as soon as the run has stopped
        self._closing_left_s = 0.0     # while closing: the time left before the launcher is killed
        self._poll_id: str | None = None
        self._alive = True             # False once the window is destroyed

        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)  # 1.25 at 125% screen scaling
        root.title(f"MRRSimulator - v{__version__}")
        self._set_icon()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self._callback_failed
        root.bind("<Destroy>", self._on_destroy, add="+")
        self._build()
        self.run_tab.refresh()
        self._fit_to_screen()
        self._poll_id = root.after(POLL_MS, self._poll)

    # ---------------------------------------------------------------------------------- layout
    def _set_icon(self) -> None:
        """The program's icon for the window and for every dialog it opens (the exe's own icon does not reach Tk)."""
        icon = paths.app_icon()
        if os.name != "nt" or not icon.is_file():
            return
        try:
            self.root.iconbitmap(default=str(icon))
        except tk.TclError:
            pass  # the window works without its icon

    def px(self, n: float) -> int:
        """A size in pixels that grows with the screen's scaling (125%, 150% ...), so spacing stays roomy."""
        return int(round(n * self.scale))

    def _style(self) -> None:
        """The fonts and the ttk styles every tab uses."""
        base = tkfont.nametofont("TkDefaultFont")
        base.configure(family="Segoe UI", size=10)
        tkfont.nametofont("TkTextFont").configure(family="Segoe UI", size=10)
        self.font = base
        self.font_bold = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.font_title = tkfont.Font(family="Segoe UI", size=14, weight="bold")
        self.font_status = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        style = ttk.Style(self.root)
        # a row is the text plus generous air above and below, so neighboring rows never touch
        style.configure("Treeview", rowheight=self.font.metrics("linespace") + self.px(18), font=self.font)
        style.configure("Dense.Treeview", rowheight=self.font.metrics("linespace") + self.px(9), font=self.font)  # the Vehicles tab
        style.configure("TButton", padding=(self.px(14), self.px(8)))
        style.configure("Accent.TButton", padding=(self.px(22), self.px(10)), font=self.font_bold)
        style.configure("Small.TButton", padding=(self.px(9), self.px(5)))  # the Vehicles tab packs many buttons
        style.configure("SmallAccent.TButton", padding=(self.px(9), self.px(5)), font=self.font_bold)
        style.configure("Danger.TButton", padding=(self.px(14), self.px(8)), foreground=colors.ERROR, font=self.font_bold)
        style.configure("TNotebook.Tab", padding=(self.px(20), self.px(6)), font=self.font_bold)
        style.configure("TLabelframe", padding=self.px(14))
        style.configure("TLabelframe.Label", font=self.font_bold)
        style.configure("TCheckbutton", padding=(0, self.px(4)))

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
        """The styles, then the three tabs and the design library they share."""
        root = self.root
        self._style()
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.tabs = ttk.Notebook(root)  # Run | Vehicles | Simulator
        self.tabs.grid(row=0, column=0, sticky="nsew")
        self.library = vehicle_design.Library(paths.vehicles_dir(), self.cfg.scene.config_dir)
        try:
            self.library.ensure_shipped()  # the IARC Drone and any other shipped design: in every list from the start
        except OSError:
            pass  # the folder cannot be written now; Run IARC tries again and says why
        self.run_tab = RunTab(self.tabs, self)
        self.tabs.add(self.run_tab, text="Run")
        self.vehicles_tab = VehiclesTab(self.tabs, self, self.library, on_selection_changed=self.run_tab.refresh_design_choices)
        self.tabs.add(self.vehicles_tab, text="Vehicles")
        self.simulator_tab = SimulatorTab(self.tabs, self)
        self.tabs.add(self.simulator_tab, text="Simulator")
        self.run_tab.refresh_design_choices()

    # ------------------------------------------------------------------------------------ shared by the tabs
    @property
    def _outputs(self) -> tuple:
        """The two output views: the Run tab's and the Simulator tab's."""
        return self.run_tab.output, self.simulator_tab.output

    @property
    def _value_tiles(self) -> tuple:
        """The two sets of value boxes: the Run tab's and the Simulator tab's."""
        return self.run_tab.values, self.simulator_tab.values

    def show_output(self, text: str, tag: str | None = None) -> None:
        """A line of output, in the Run tab and in the Simulator tab."""
        for view in self._outputs:
            view.append(text, tag)

    def note(self, text: str) -> None:
        """A line of the window's own (not the launcher's), shown in the muted color."""
        self.show_output(text, "note")

    def clear_output(self) -> None:
        """Empty both output views."""
        for view in self._outputs:
            view.clear()

    def set_value(self, key: str, number: float | None) -> None:
        """A new Altitude or Throttle, in the value boxes of both tabs."""
        for tiles in self._value_tiles:
            tiles.set(key, number)

    def reset_values(self) -> None:
        """Empty the value boxes of both tabs (a new run starts)."""
        for tiles in self._value_tiles:
            tiles.reset()

    # ------------------------------------------------------------------------------------ the launcher's output
    def _poll(self) -> None:
        """Take what the launcher printed since the last look; while closing, kill a launcher that will not stop.
        Runs every POLL_MS until the window is destroyed."""
        self._poll_id = None
        try:
            while self._alive:
                kind, payload = self.runner.events.get_nowait()
                if kind == "line":
                    self.run_tab.handle_line(str(payload))
                else:
                    self._finished(int(payload))
        except queue.Empty:
            pass
        if not self._alive:  # the run's end closed the window
            return
        if self.closing and self.runner.running:
            self._closing_left_s -= POLL_MS / 1000
            if self._closing_left_s <= 0:  # it will not stop: the launcher's Job Object takes Unreal with it
                self.runner.kill()
        self._poll_id = self.root.after(POLL_MS, self._poll)

    def _finished(self, code: int) -> None:
        """The launcher ended: give the simulator window back, then close (if that was asked) or say how it went."""
        self.simulator_tab.release()
        if self.closing:
            self.root.destroy()
            return
        self.run_tab.handle_exit(code)

    def _on_destroy(self, event: tk.Event) -> None:
        """The window is gone, so its timer must not fire again (it would run inside another window's event loop)."""
        if event.widget is not self.root:
            return
        self._alive = False
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
            self._poll_id = None

    def _callback_failed(self, kind: type, error: BaseException, trace) -> None:
        """A button or timer of the window failed. The windowed exe has no console for the traceback, so the student is
        told in a box (and the traceback still goes to the console when there is one)."""
        if sys.stderr is not None:
            traceback.print_exception(kind, error, trace)
        messagebox.showerror("MRRSimulator", "Something went wrong in the window:\n\n"
                             + "".join(traceback.format_exception_only(kind, error)).strip())

    def on_close(self) -> None:
        """The window's close button. Unsaved design changes are asked about; a running simulation is stopped first
        (the script is killed and the launcher shuts everything down), and the window closes when it has."""
        if not self.vehicles_tab.confirm_discard():  # unsaved changes to a vehicle design
            return
        if self.runner.running:
            if not messagebox.askyesno("MRRSimulator", "A simulation is still running. Stop it and close this window?"):
                return
            self.closing = True
            self.run_tab.stop()
            self._closing_left_s = CLOSE_WAIT_S
            return
        self.simulator_tab.release()
        self.root.destroy()


def main(cfg: config.Config | None = None) -> int:
    """Open the window (`mrrsim gui`, MRRSimulatorGUI.exe). Returns the exit code: 2 when the settings cannot be read."""
    if os.name == "nt":
        try:  # sharp text on high-DPI screens
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    try:
        cfg = cfg or config.load(paths.find_config())
    except (OSError, ValueError) as exc:  # not found, not readable, or not valid (ConfigError is a ValueError)
        _error_box(f"MRRSimulator could not read its settings:\n\n{exc}")
        return 2
    root = tk.Tk()
    App(root, cfg)
    root.mainloop()
    return 0


def _error_box(message: str) -> None:
    """An error box with no window behind it (before there is one)."""
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("MRRSimulator", message)
    root.destroy()
