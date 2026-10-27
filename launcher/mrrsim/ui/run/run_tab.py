"""The Run tab: pick a script, press Run.

Left: the scripts (yours, project folders, the bundled examples). Right: the selected script and the options of a run
(vehicles, simulator window size, vehicle design, script arguments), the Run / Stop / Run doctor / Kill Unreal buttons and
the status line. Below: the output of the run and the Altitude and Throttle value boxes.

The tab starts the command-line launcher through the shared `Runner` (see ui/common/launcher_process.py) and receives its
output line by line from the window (`handle_line`, `handle_exit`). Where the output and the values are shown, and the
Simulator tab, are the window's business (ui/app.py), reached through `self.app`.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ...core import paths
from ...designs import vehicle_design
from ...userscripts import pipinstall, projects, scripts
from ..common.files import open_folder
from ..common.launcher_process import cli_command, kill_unreal
from ..common.output import OutputView
from ..common.values import ValueTiles, value_line
from ..simulator import embed
from .commands import (
    DOCTOR_SUMMARY, MAX_VEHICLES, PROGRESS, PROVIDED_CHOICE, RESOLUTIONS, build_run_command, doctor_status, pip_status,
    valid_resolution,
)


class RunTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent)
        self.app = app
        self.root, self.cfg, self.runner, self.library = app.root, app.cfg, app.runner, app.library
        self.px = app.px
        self.font, self.font_bold, self.font_title, self.font_status = app.font, app.font_bold, app.font_title, app.font_status
        self.infos: dict[str, scripts.ScriptInfo] = {}
        self.project_nodes: dict[str, Path] = {}  # tree item of a project's heading -> the project folder
        self.stop_presses = 0
        self.running_doctor = False  # a utility run (doctor, package install) rather than a simulation
        self.utility = "doctor"       # which utility, while running_doctor is set
        self.doctor_summary: tuple[int, int, int] | None = None  # (checks, failed, warnings) of the last doctor run
        self._build()

    # ---------------------------------------------------------------------------------- layout
    def _build(self) -> None:
        gap = self.px(14)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=0)  # the scripts and the Run panel take the height they need, never less
        self.rowconfigure(1, weight=1)  # the output gets whatever is left

        top = ttk.Frame(self, padding=(gap, gap, gap, self.px(8)))
        top.grid(row=0, column=0, sticky="nsew")
        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=2, minsize=self.px(340), uniform="top")
        top.columnconfigure(1, weight=3, minsize=self.px(560), uniform="top")

        # ---- scripts
        left = ttk.LabelFrame(top, text="Scripts")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, gap))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(left, show="tree", selectmode="browse", height=6)
        self.tree.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(left, command=self.tree.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.tag_configure("group", font=self.font_bold)
        self.tree.tag_configure("hint", foreground="#5e5c64")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.on_select())
        self.tree.bind("<Double-1>", lambda _e: self.run())
        row = ttk.Frame(left)
        row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(self.px(12), 0))
        row.columnconfigure((0, 1), weight=1, uniform="scriptbuttons")
        ttk.Button(row, text="Refresh", command=self.refresh).grid(row=0, column=0, sticky="ew", padx=(0, self.px(6)))
        self.copy_button = ttk.Button(row, text="Copy to my scripts", command=self.copy_example)
        self.copy_button.grid(row=0, column=1, sticky="ew", padx=(self.px(6), 0))
        ttk.Button(row, text="Add project folder...", command=self.add_project).grid(
            row=1, column=0, sticky="ew", padx=(0, self.px(6)), pady=(self.px(8), 0))
        self.remove_button = ttk.Button(row, text="Remove project", command=self.remove_project)
        self.remove_button.grid(row=1, column=1, sticky="ew", padx=(self.px(6), 0), pady=(self.px(8), 0))
        ttk.Button(row, text="Open my scripts folder", command=self.open_scripts_folder).grid(
            row=2, column=0, sticky="ew", padx=(0, self.px(6)), pady=(self.px(8), 0))
        self.install_button = ttk.Button(row, text="Install package...", command=self.install_package)
        self.install_button.grid(row=2, column=1, sticky="ew", padx=(self.px(6), 0), pady=(self.px(8), 0))

        # ---- run
        right = ttk.LabelFrame(top, text="Run")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        self.title_label = ttk.Label(right, text="", font=self.font_title)
        self.title_label.grid(row=0, column=0, sticky="w")
        # plain tk labels: they can be given a fixed number of lines, so the buttons below stay where they are
        # whichever script is selected (and however long its description is)
        panel = ttk.Style(self.root).lookup("TFrame", "background") or "#f0f0f0"
        text_kw = dict(justify="left", anchor="nw", bg=panel, font=self.font, borderwidth=0, padx=0, pady=0)
        self.details = tk.Label(right, text="", height=3, **text_kw)
        self.details.grid(row=1, column=0, sticky="ew", pady=(self.px(8), 0))
        self.notes = tk.Label(right, text="", height=3, fg="#5e5c64", **text_kw)  # notes, then the file path
        self.notes.grid(row=2, column=0, sticky="ew", pady=(self.px(6), 0))
        ttk.Separator(right).grid(row=4, column=0, sticky="ew", pady=self.px(10))

        options = ttk.Frame(right)  # two columns of settings side by side, so nothing is squeezed
        options.grid(row=5, column=0, sticky="ew")
        options.columnconfigure((0, 1, 2), weight=1, uniform="options")
        vehicles_box = ttk.Frame(options)
        vehicles_box.grid(row=0, column=0, sticky="ew", padx=(0, self.px(18)))
        ttk.Label(vehicles_box, text="Vehicles").pack(anchor="w")
        self.vehicles = tk.IntVar(value=1)
        ttk.Spinbox(vehicles_box, from_=1, to=MAX_VEHICLES, width=8, textvariable=self.vehicles).pack(
            anchor="w", fill="x", pady=(self.px(4), 0), ipady=self.px(3))
        window_box = ttk.Frame(options)
        window_box.grid(row=0, column=1, sticky="ew", padx=(self.px(18), 0))
        ttk.Label(window_box, text="Simulator window").pack(anchor="w")
        self.resolution = tk.StringVar(value=f"{self.cfg.unreal.width}x{self.cfg.unreal.height}")
        ttk.Combobox(window_box, textvariable=self.resolution, values=RESOLUTIONS, width=14).pack(
            anchor="w", fill="x", pady=(self.px(4), 0), ipady=self.px(3))
        args_box = ttk.Frame(options)
        args_box.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(self.px(12), 0))
        ttk.Label(args_box, text="Script arguments (optional, for example -s)").pack(anchor="w")
        self.script_args = tk.StringVar(value="")
        ttk.Entry(args_box, textvariable=self.script_args).pack(
            anchor="w", fill="x", pady=(self.px(4), 0), ipady=self.px(3))
        design_box = ttk.Frame(options)
        design_box.grid(row=0, column=2, sticky="ew", padx=(self.px(18), 0))
        ttk.Label(design_box, text="Vehicle (see the Vehicles tab)").pack(anchor="w")
        self.design_var = tk.StringVar(value=PROVIDED_CHOICE)
        self.design_combo = ttk.Combobox(design_box, textvariable=self.design_var, values=[PROVIDED_CHOICE], state="readonly")
        self.design_combo.pack(anchor="w", fill="x", pady=(self.px(4), 0), ipady=self.px(3))
        self.design_combo.bind("<<ComboboxSelected>>", lambda _e: self._design_chosen())
        self.cameras_off = tk.BooleanVar(value=False)
        self.show_sim = tk.BooleanVar(value=embed.available())
        checks = ttk.Frame(right)
        checks.grid(row=6, column=0, sticky="w", pady=(self.px(12), 0))
        ttk.Checkbutton(
            checks, text="Turn the cameras off (faster on a weak graphics card)", variable=self.cameras_off
        ).pack(anchor="w")
        self.show_sim_check = ttk.Checkbutton(
            checks, text="Show the simulator inside this window (Simulator tab)", variable=self.show_sim
        )
        if embed.available():
            self.show_sim_check.pack(anchor="w", pady=(self.px(6), 0))
        ttk.Separator(right).grid(row=7, column=0, sticky="ew", pady=self.px(10))

        buttons = ttk.Frame(right)
        buttons.grid(row=8, column=0, sticky="ew")
        buttons.columnconfigure(2, weight=1)  # pushes the safety buttons to the right edge
        self.run_button = ttk.Button(buttons, text="Run", command=self.run, style="Accent.TButton")
        self.run_button.grid(row=0, column=0)
        self.stop_button = ttk.Button(buttons, text="Stop (land)", command=self.stop)
        self.stop_button.grid(row=0, column=1, padx=(self.px(10), 0))
        self.check_button = ttk.Button(buttons, text="Run doctor", command=self.check)
        self.check_button.grid(row=0, column=3, padx=(0, self.px(10)))
        self.kill_button = ttk.Button(buttons, text="Kill Unreal", command=self.kill, style="Danger.TButton")
        self.kill_button.grid(row=0, column=4)
        self.status = ttk.Label(right, text="", font=self.font_status, justify="left")
        self.status.grid(row=9, column=0, sticky="ew", pady=(self.px(10), 0))
        # long texts wrap at the panel's width, whatever size the window is
        self._wrapped = [self.details, self.notes, self.status]
        right.bind("<Configure>", lambda e: [w.configure(wraplength=max(self.px(240), e.width - self.px(60)))
                                              for w in self._wrapped])

        # ---- output and values
        bottom = ttk.LabelFrame(self, text="Output")
        bottom.grid(row=1, column=0, sticky="nsew", padx=gap, pady=(0, self.px(8)))
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(0, weight=1)
        self.output = OutputView(bottom, self.app)
        self.output.grid(row=0, column=0, sticky="nsew")
        self.values = ValueTiles(bottom, self.app)
        self.values.grid(row=0, column=1, sticky="n")
        foot = ttk.Frame(self, padding=(gap, 0, gap, gap))
        foot.grid(row=2, column=0, sticky="ew")
        ttk.Button(foot, text="Open flight logs", command=self.open_logs_folder).pack(side="left")
        ttk.Button(foot, text="Clear output", command=self.app.clear_output).pack(side="left", padx=(self.px(10), 0))

    # ------------------------------------------------------------------------------------ scripts
    def refresh(self, select: Path | None = None) -> None:
        """Re-read every folder. `select` is a file (or project folder) to pick afterwards; by default the
        current choice is kept."""
        current = self.selected()
        keep = select or (current.path if current else self._selected_project())
        self.tree.delete(*self.tree.get_children())
        self.infos.clear()
        self.project_nodes.clear()
        found = scripts.discover(paths.user_scripts_dir(), self.cfg.scripts.dir)
        for group, title in ((scripts.MINE, "My scripts"), (scripts.EXAMPLES, "Examples")):
            if group == scripts.EXAMPLES:
                self._add_projects()
            node = self.tree.insert("", "end", text=title, open=True, tags=("group",))
            members = [i for i in found if i.group == group]
            if not members and group == scripts.MINE:
                self.tree.insert(node, "end", text="  (none yet: copy an example, or save a .py here)", tags=("hint",))
            for info in members:
                item = self.tree.insert(node, "end", text="  " + info.name)  # a little air left of the name
                self.infos[item] = info
        heading = next((i for i, folder in self.project_nodes.items() if keep and projects._same(folder, keep)), None)
        first_file = next((c for c in self.tree.get_children(heading) if c in self.infos), None) if heading else None
        target = (next((i for i, info in self.infos.items() if info.path == keep), None)
                  or (first_file if select else None)  # a project just added: its first file, ready to run
                  or heading
                  or next(iter(self.infos), None))
        if target:
            self.tree.selection_set(target)
            self.tree.see(target)
        self.on_select()

    def _add_projects(self) -> None:
        """One heading per project folder, with the files that can be run inside it."""
        for folder in projects.load():
            if not folder.is_dir():
                node = self.tree.insert("", "end", text=f"Project: {folder.name} (folder not found)", open=False,
                                        tags=("group", "hint"))
                self.project_nodes[node] = folder
                continue
            scan = projects.scan(folder)
            node = self.tree.insert("", "end", text=f"Project: {folder.name}", open=True, tags=("group",))
            self.project_nodes[node] = folder
            if not scan.entries:
                self.tree.insert(node, "end", text="  (no Python files in this folder)", tags=("hint",))
            for info in scan.entries:
                item = self.tree.insert(node, "end", text="  " + info.name)
                self.infos[item] = info
            if scan.truncated:
                self.tree.insert(node, "end", text=f"  (only the first {projects.MAX_FILES} files were searched)",
                                 tags=("hint",))

    def selected(self) -> scripts.ScriptInfo | None:
        chosen = self.tree.selection()
        return self.infos.get(chosen[0]) if chosen else None

    def _selected_project(self) -> Path | None:
        """The project folder of the selected file or heading, if the selection belongs to one."""
        chosen = self.tree.selection()
        if not chosen:
            return None
        if chosen[0] in self.project_nodes:
            return self.project_nodes[chosen[0]]
        info = self.infos.get(chosen[0])
        return info.project if info else None

    def on_select(self) -> None:
        info = self.selected()
        project = self._selected_project()
        self.remove_button.state(["!disabled"] if project else ["disabled"])
        if info is None:
            self.script_args.set("")
        if info is None and project is not None:  # a project's heading
            self.title_label.configure(text=f"Project: {project.name}")
            self.details.configure(text="Pick a file of the project to run it. The whole folder is available "
                                        "to the script, so its imports work.")
            self.notes.configure(text=f"Folder: {project}" + ("" if project.is_dir() else "\nThis folder was not found."))
            self.copy_button.state(["disabled"])
            return
        if info is None:
            self.title_label.configure(text="No script selected")
            self.details.configure(text=f"Put your .py files in:\n{paths.user_scripts_dir()}")
            self.notes.configure(text="")
            self.copy_button.state(["disabled"])
            return
        notes = []
        if info.needs_airsim:
            notes.append("Uses the Project AirSim client (cameras and sensors)." if not info.project else
                         "This project uses the Project AirSim client (cameras and sensors).")
        if info.vehicles:
            notes.append(f"Written for {info.vehicles} vehicle(s).")
            self.vehicles.set(min(info.vehicles, MAX_VEHICLES))
        if info.problem:
            notes.append(f"Warning: {info.problem}")
        self.title_label.configure(text=info.name)
        self.details.configure(text=info.description or "No description. A docstring at the top of the file shows up here.")
        self.script_args.set(scripts.load_args(info.path))
        where = f"Project folder: {info.project}" if info.project else f"File: {info.path}"
        self.notes.configure(text="\n".join(notes + [where]))
        self.copy_button.state(["!disabled"] if info.group == scripts.EXAMPLES else ["disabled"])

    def add_project(self) -> None:
        folder = filedialog.askdirectory(parent=self.root, mustexist=True, title="Choose your project folder",
                                         initialdir=str(Path.home()))
        if not folder:
            return
        try:
            stored = projects.add(Path(folder))
        except OSError as exc:
            messagebox.showerror("MRRSimulator", f"Could not add the folder:\n{exc}")
            return
        self.app.note(f"Added project folder {stored}")
        self.refresh(select=stored)

    def remove_project(self) -> None:
        folder = self._selected_project()
        if folder is None:
            return
        if not messagebox.askyesno("MRRSimulator", f"Remove the project \"{folder.name}\" from this list?\n\n"
                                                   f"Your files in {folder} are not touched."):
            return
        projects.remove(folder)
        self.app.note(f"Removed project {folder}")
        self.refresh(select=paths.user_scripts_dir())

    def copy_example(self) -> None:
        info = self.selected()
        if info is None or info.group != scripts.EXAMPLES:
            return
        target = scripts.copy_to(info, paths.user_scripts_dir())
        self.app.note(f"Copied {info.name} to {target}")
        self.refresh()
        chosen = next((i for i, s in self.infos.items() if s.path == target), None)
        if chosen:
            self.tree.selection_set(chosen)

    # ------------------------------------------------------------------------------------ running
    def run(self) -> None:
        info = self.selected()
        if self.runner.running or info is None:
            return
        try:
            count = int(self.vehicles.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("MRRSimulator", f"Vehicles must be a number from 1 to {MAX_VEHICLES}.")
            return
        if not 1 <= count <= MAX_VEHICLES:
            messagebox.showerror("MRRSimulator", f"Vehicles must be a number from 1 to {MAX_VEHICLES}.")
            return
        size = self.resolution.get().strip()
        if size and not valid_resolution(size):
            messagebox.showerror("MRRSimulator", "The simulator window size must look like 960x540.")
            return
        typed = self.script_args.get().strip()
        try:
            scripts.split_args(typed)
        except ValueError as exc:
            messagebox.showerror("MRRSimulator", f"The script arguments are not valid ({exc}).\nCheck the quotes.")
            return
        scripts.save_args(info.path, typed)  # remembered for the next time this script is picked
        design = self.library.selected()  # None: the provided vehicle
        command = build_run_command(self.cfg.source, info.path, count, self.cameras_off.get(), size or None,
                                    project=info.project, script_args=typed, design=design)
        self.app.note(f"--- running {info.name} with {count} vehicle(s)" + (f" of the design \"{design}\"" if design else "") + " ---")
        self.stop_presses = 0
        self.running_doctor = False
        self.app.reset_values()
        self._set_running("Starting...")
        self._start(command)
        if self.show_sim.get():
            self.app.simulator_tab.watch(
                wanted=lambda: self.runner.running and not self.running_doctor and self.show_sim.get() and not self.app.closing,
                on_attached=lambda: self.status.configure(text="The simulator is in the Simulator tab"),
            )

    # ------------------------------------------------------------------------------------ vehicle designs
    def refresh_design_choices(self) -> None:
        """The Run tab's list of vehicles: the provided one and every design that can fly with this launcher."""
        names = []
        for name in self.library.names():
            try:
                if not self.library.load(name).runnable_problem():
                    names.append(name)
            except vehicle_design.DesignError:
                continue
        self.design_combo.configure(values=[PROVIDED_CHOICE] + names)
        chosen = self.library.selected()
        self.design_var.set(chosen if chosen in names else PROVIDED_CHOICE)

    def _design_chosen(self) -> None:
        """The student picked a vehicle in the Run tab."""
        name = self.design_var.get()
        self.library.select(None if name == PROVIDED_CHOICE else name)
        self.app.vehicles_tab.refresh_designs()

    # ------------------------------------------------------------------------------------ utilities
    def check(self) -> None:
        if self.runner.running:
            return
        self.app.note("--- running doctor ---")
        self.running_doctor, self.utility = True, "doctor"
        self.doctor_summary = None
        self._set_running("Running doctor...")
        self._start(cli_command() + ["-c", str(self.cfg.source), "doctor"])

    def install_package(self) -> None:
        """Ask for package names, then run `mrrsim pip install` (or `remove`) like the doctor: output below, no simulation."""
        if self.runner.running:
            messagebox.showinfo("MRRSimulator", "Wait for the current run to finish first.")
            return
        text = simpledialog.askstring(
            "Install Python package",
            "Package name(s), for example  tqdm  or  requests==2.32.3\n"
            "Several names can be separated by spaces. Needs the internet once;\n"
            "afterwards the package works offline in every script.\n\n"
            "To take packages out again, type  remove  and their names.",
            parent=self.root)
        if not text or not text.strip():
            return
        words = pipinstall.split_names(text)
        removing = words[0].lower() in ("remove", "uninstall")
        try:
            names = pipinstall.validate_names(words[1:]) if removing else pipinstall.validate(words)
        except ValueError as exc:
            messagebox.showerror("MRRSimulator", str(exc))
            return
        self.app.note(f"--- {'removing' if removing else 'installing'} {' '.join(names)} ---")
        self.running_doctor, self.utility = True, "pip-remove" if removing else "pip"  # a utility run, not a simulation
        self.doctor_summary = None
        self._set_running("Removing packages..." if removing else "Installing packages (needs the internet)...")
        self._start(cli_command() + ["-c", str(self.cfg.source), "pip", "remove" if removing else "install", *names])

    def _start(self, command: list[str]) -> None:
        try:
            self.runner.start(command, paths.work_dir())
        except OSError as exc:
            self._set_idle("Could not start")
            messagebox.showerror("MRRSimulator", f"Could not start the launcher:\n{exc}")

    def kill(self) -> None:
        """Force-close Unreal. If a run is going, the rest of it is stopped too: with the simulator gone
        there is nothing left for the script to fly in or land."""
        active = self.runner.running and not self.running_doctor
        if active and not messagebox.askyesno(
            "MRRSimulator", "Kill Unreal now?\n\nThe simulation that is running will be stopped."
        ):
            return
        count = kill_unreal()
        if not count:
            self.app.note("Unreal was not running.")
            self.status.configure(text="Unreal is not running")
            return
        self.app.note(f"Unreal was killed ({count} process{'es' if count != 1 else ''}).")
        if not active:
            self.status.configure(text="Unreal killed")
            return
        # the first request lets the script give up gracefully, the second (a moment later) skips its grace time
        self.stop_presses = 2
        self.runner.request_stop()
        self.root.after(1500, self.runner.request_stop)
        self.stop_button.configure(text="Stop now")
        self.status.configure(text="Unreal killed: stopping everything...")

    def stop(self) -> None:
        if not self.runner.running or self.running_doctor:
            return
        self.stop_presses += 1
        self.runner.request_stop()
        if self.stop_presses == 1:
            self.status.configure(text="Stopping: the script is landing the vehicle...")
            self.stop_button.configure(text="Stop now")
        else:
            self.status.configure(text="Stopping now...")

    # ------------------------------------------------------------------------------------ the launcher's output
    def handle_line(self, text: str) -> None:
        """One line the launcher printed: a value box, or the output (coloured by what kind of line it is)."""
        shown = value_line(text)
        if shown is not None:  # a value box, not a line of the output
            self.app.set_value(*shown)
            return
        tag = "script" if text.startswith("[script]") else (
            "error" if " ERROR " in text or text.startswith("[FAIL]") else (
                "warning" if " WARNING " in text or text.startswith("[WARN]") else (
                    "ok" if text.startswith("[ OK ]") else None)))
        self.app.show_output(text, tag)
        summary = DOCTOR_SUMMARY.search(text) if self.running_doctor else None
        if summary:
            self.doctor_summary = tuple(int(n) for n in summary.groups())  # type: ignore[assignment]
        if not self.running_doctor and not self.stop_presses:
            for fragment, message in PROGRESS:
                if fragment in text:
                    self.status.configure(text=message)

    def handle_exit(self, code: int) -> None:
        """The launcher ended: say how it went."""
        if self.running_doctor and self.utility in ("pip", "pip-remove"):
            self._set_idle(pip_status(code, self.output.contents(), self.utility == "pip-remove"))
        elif self.running_doctor:
            self._set_idle(doctor_status(code, self.doctor_summary))
        elif code == 0:
            self._set_idle("Finished")
        elif code == 130:
            self._set_idle("Stopped")
        else:
            self._set_idle(f"Stopped with an error (code {code}): see the output")
        self.app.note(f"--- finished (exit code {code}) ---")

    def _set_running(self, status: str) -> None:
        self.run_button.state(["disabled"])
        self.check_button.state(["disabled"])
        self.install_button.state(["disabled"])
        self.stop_button.configure(text="Stop (land)")
        self.stop_button.state(["!disabled"] if not self.running_doctor else ["disabled"])
        self.status.configure(text=status)

    def _set_idle(self, status: str) -> None:
        self.run_button.state(["!disabled"])
        self.check_button.state(["!disabled"])
        self.install_button.state(["!disabled"])
        self.stop_button.configure(text="Stop (land)")
        self.stop_button.state(["disabled"])
        self.status.configure(text=status)

    # ------------------------------------------------------------------------------------ folders
    def open_scripts_folder(self) -> None:
        open_folder(paths.user_scripts_dir())

    def open_logs_folder(self) -> None:
        open_folder(paths.work_dir() / "logs")
