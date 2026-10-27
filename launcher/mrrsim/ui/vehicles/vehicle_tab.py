"""The Vehicles tab: design the vehicle that flies in the simulator.

Left: your saved designs (and the provided, read-only vehicles) and the parts palette. Middle: the vehicle being
built, as a tree: its controller, its links with the sensors and actuators attached to each, its joints. Right: the
settings of the selected part, a form made from the settings catalog (vehicle_schema.py), so every setting
Project AirSim reads is there. Below: the problems found, the resulting JSON, and a raw JSON editor.

All logic that is not about widgets lives in vehicle_design.py (blocks, validation, saving) and is tested without a
window; this module only shows it and passes edits on. The window tests drive it through `commit_field`, `add_part`,
`drop_part` and the other public methods rather than through simulated mouse clicks.
"""
from __future__ import annotations

import json
import os
import subprocess
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from ...designs import vehicle_design as vd
from ...designs import vehicle_schema as vs
from ...scene import scenecheck

MUTED = "#5e5c64"
RED = "#c01c28"
AMBER = "#b25000"
PROVIDED_GROUP, MINE_GROUP = "Provided vehicles (read-only)", "My designs"


def _reveal(path: Path, is_folder: bool = False) -> None:
    """Show a file in the file manager with the file selected, or open a folder."""
    if is_folder:
        path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        if is_folder:
            os.startfile(path)  # noqa: S606 (a folder of ours)
        else:
            subprocess.Popen(["explorer", f"/select,{path}"])  # explorer returns 1 even when it worked
    else:
        subprocess.Popen(["xdg-open", str(path if is_folder else path.parent)])


@dataclass
class Field:
    """One setting shown in the form."""
    path: tuple
    setting: vs.Setting
    var: tk.StringVar
    widget: tk.Widget
    label: ttk.Label


def row_template(setting: vs.Setting) -> dict:
    """The starting values of a new row of a table setting: each required setting at its default (numbers at 0)."""
    row: dict = {}
    for child in setting.children:
        if not child.required:
            continue
        if child.default is not None:
            row[child.key] = child.default
        elif child.kind in (vs.NUMBER, vs.INTEGER):
            row[child.key] = 0
        elif child.kind == vs.BOOL:
            row[child.key] = False
        elif child.kind in (vs.VEC3,):
            row[child.key] = "0 0 0"
    return row


class VehiclesTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app, library: vd.Library,
                 on_selection_changed: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self.app, self.library = app, library
        self.px = app.px
        self.on_selection_changed = on_selection_changed or (lambda: None)
        self.design: vd.Design | None = None
        self.current: vd.BlockRef | None = None
        self.fields: dict[tuple, Field] = {}
        self.map_editors: dict[tuple, tk.Text] = {}  # the multi-line editors of name = value settings, by path
        self._block_items: dict[str, vd.BlockRef] = {}
        self._ref_items: dict[vd.BlockRef, str] = {}
        self._design_choices: list[tuple[str, str, str | Path]] = []
        self._part_items: dict[str, vs.BlockType] = {}
        self._drag: vs.BlockType | None = None
        self._ghost: tk.Label | None = None
        self._building = False
        self._build()
        self.refresh_designs()
        self._update_state()
        self._open_initial()

    # ------------------------------------------------------------------------------------------ layout
    def _build(self) -> None:
        px = self.px
        gap = px(10)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ---- top bar: which design, and what to do with it
        bar = ttk.Frame(self, padding=(gap, gap, gap, 0))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Design", font=self.app.font_bold).grid(row=0, column=0, padx=(0, px(8)))
        self.design_combo = ttk.Combobox(bar, state="readonly", values=[])
        self.design_combo.grid(row=0, column=1, sticky="ew", ipady=px(3))
        self.design_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_design_selected())
        actions = ttk.Frame(bar)
        actions.grid(row=0, column=2, padx=(px(10), 0))
        self.new_button = ttk.Button(actions, text="New...", command=self.new_design, style="Small.TButton")
        self.new_button.grid(row=0, column=0, padx=(0, px(4)))
        self.duplicate_button = ttk.Button(actions, text="Duplicate...", command=self.duplicate_design, style="Small.TButton")
        self.duplicate_button.grid(row=0, column=1, padx=px(4))
        self.rename_button = ttk.Button(actions, text="Rename...", command=self.rename_design, style="Small.TButton")
        self.rename_button.grid(row=0, column=2, padx=px(4))
        self.delete_button = ttk.Button(actions, text="Delete...", command=self.delete_design, style="Small.TButton")
        self.delete_button.grid(row=0, column=3, padx=px(4))
        self.use_button = ttk.Button(actions, text="Use when running", command=self.use_for_runs, style="Small.TButton")
        self.use_button.grid(row=0, column=4, padx=(px(10), 0))
        self.copy_json_button = ttk.Button(actions, text="Copy settings (JSON)", command=self.copy_settings, style="Small.TButton")
        self.copy_json_button.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, px(4)), pady=(px(4), 0))
        self.open_folder_button = ttk.Button(actions, text="Show file in folder", command=self.show_in_folder, style="Small.TButton")
        self.open_folder_button.grid(row=1, column=2, columnspan=2, sticky="ew", padx=px(4), pady=(px(4), 0))
        self.banner = tk.Label(bar, text="", justify="left", anchor="w", fg=AMBER, font=self.app.font, wraplength=px(900))
        self.banner.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(px(4), 0))

        # ---- the work area: [parts | vehicle | settings] above [problems / JSON]
        vertical = ttk.PanedWindow(self, orient="vertical")
        vertical.grid(row=1, column=0, sticky="nsew", padx=gap, pady=(px(6), gap))
        panes = ttk.PanedWindow(vertical, orient="horizontal")
        vertical.add(panes, weight=6)
        self._panes, self._balanced = panes, False
        panes.bind("<Configure>", self._balance_panes)

        parts = ttk.LabelFrame(panes, text="Parts")
        parts.columnconfigure(0, weight=1)
        parts.rowconfigure(0, weight=1)
        panes.add(parts, weight=2)
        self.parts_tree = ttk.Treeview(parts, show="tree", selectmode="browse", height=8, style="Dense.Treeview")
        self.parts_tree.grid(row=0, column=0, sticky="nsew")
        pscroll = ttk.Scrollbar(parts, command=self.parts_tree.yview)
        pscroll.grid(row=0, column=1, sticky="ns")
        self.parts_tree.configure(yscrollcommand=pscroll.set)
        self.parts_tree.tag_configure("group", font=self.app.font_bold)
        for group, types in vs.palette():
            node = self.parts_tree.insert("", "end", text=group, open=True, tags=("group",))
            for bt in types:
                self._part_items[self.parts_tree.insert(node, "end", text="  " + bt.title)] = bt
        self.parts_tree.bind("<Double-1>", lambda _e: self.add_selected_part())
        self.parts_tree.bind("<ButtonPress-1>", self._drag_start)
        self.parts_tree.bind("<B1-Motion>", self._drag_move)
        self.parts_tree.bind("<ButtonRelease-1>", self._drag_end)
        self.add_button = ttk.Button(parts, text="Add to vehicle", command=self.add_selected_part, style="Small.TButton")
        self.add_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(px(6), 0))
        self.parts_hint = tk.Label(parts, text="Double-click a part, or drag it onto the vehicle.", justify="left", anchor="w",
                                   fg=MUTED, font=self.app.font, wraplength=px(190))
        self.parts_hint.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(px(4), 0))

        self.vehicle_frame = ttk.LabelFrame(panes, text="Vehicle")
        self.vehicle_frame.columnconfigure(0, weight=1)
        self.vehicle_frame.rowconfigure(0, weight=1)
        panes.add(self.vehicle_frame, weight=3)
        self.vehicle_tree = ttk.Treeview(self.vehicle_frame, show="tree", selectmode="browse", height=10, style="Dense.Treeview")
        self.vehicle_tree.grid(row=0, column=0, sticky="nsew")
        vscroll = ttk.Scrollbar(self.vehicle_frame, command=self.vehicle_tree.yview)
        vscroll.grid(row=0, column=1, sticky="ns")
        self.vehicle_tree.configure(yscrollcommand=vscroll.set)
        self.vehicle_tree.tag_configure("group", font=self.app.font_bold)
        self.vehicle_tree.tag_configure("hint", foreground=MUTED)
        self.vehicle_tree.tag_configure("error", foreground=RED)
        self.vehicle_tree.tag_configure("warning", foreground=AMBER)
        self.vehicle_tree.tag_configure("off", foreground=MUTED)
        self.vehicle_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_block_selected())
        self.vehicle_tree.bind("<Delete>", lambda _e: self.remove_selected())
        tools = ttk.Frame(self.vehicle_frame)
        tools.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(px(6), 0))
        tools.columnconfigure((0, 1, 2, 3), weight=1, uniform="tools")
        small = "Small.TButton"
        self.remove_button = ttk.Button(tools, text="Remove", command=self.remove_selected, style=small)
        self.remove_button.grid(row=0, column=0, sticky="ew", padx=(0, px(3)))
        self.copy_button = ttk.Button(tools, text="Copy", command=self.duplicate_selected, style=small)
        self.copy_button.grid(row=0, column=1, sticky="ew", padx=px(3))
        self.up_button = ttk.Button(tools, text="Up", command=lambda: self.move_selected(-1), style=small)
        self.up_button.grid(row=0, column=2, sticky="ew", padx=px(3))
        self.down_button = ttk.Button(tools, text="Down", command=lambda: self.move_selected(1), style=small)
        self.down_button.grid(row=0, column=3, sticky="ew", padx=(px(3), 0))
        self.undo_button = ttk.Button(tools, text="Undo", command=self.undo, style=small)
        self.undo_button.grid(row=1, column=0, sticky="ew", padx=(0, px(3)), pady=(px(5), 0))
        self.redo_button = ttk.Button(tools, text="Redo", command=self.redo, style=small)
        self.redo_button.grid(row=1, column=1, sticky="ew", padx=px(3), pady=(px(5), 0))
        self.save_button = ttk.Button(tools, text="Save", command=self.save, style="SmallAccent.TButton")
        self.save_button.grid(row=1, column=2, sticky="ew", padx=px(3), pady=(px(5), 0))
        self.revert_button = ttk.Button(tools, text="Revert", command=self.revert, style=small)
        self.revert_button.grid(row=1, column=3, sticky="ew", padx=(px(3), 0), pady=(px(5), 0))
        self.bind_all("<Control-z>", self._shortcut(self.undo), add="+")
        self.bind_all("<Control-y>", self._shortcut(self.redo), add="+")
        self.bind_all("<Control-s>", self._shortcut(self.save), add="+")

        right = ttk.LabelFrame(panes, text="Settings")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        panes.add(right, weight=6)
        self.form_title = ttk.Label(right, text="", font=self.app.font_bold)
        self.form_title.grid(row=0, column=0, columnspan=2, sticky="w")
        self.form_canvas = tk.Canvas(right, highlightthickness=0, borderwidth=0, width=px(380), height=px(120))
        self.form_canvas.grid(row=1, column=0, sticky="nsew")
        fscroll = ttk.Scrollbar(right, command=self.form_canvas.yview)
        fscroll.grid(row=1, column=1, sticky="ns")
        self.form_canvas.configure(yscrollcommand=fscroll.set)
        self.form_inner = ttk.Frame(self.form_canvas)
        self._form_window = self.form_canvas.create_window((0, 0), window=self.form_inner, anchor="nw")
        self.form_inner.columnconfigure(1, weight=1)
        self.form_inner.bind("<Configure>", lambda _e: self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all")))
        self.form_canvas.bind("<Configure>", self._form_resized)
        self.form_canvas.bind("<Enter>", lambda _e: self.form_canvas.bind_all("<MouseWheel>", self._wheel))
        self.form_canvas.bind("<Leave>", lambda _e: self.form_canvas.unbind_all("<MouseWheel>"))
        panel = ttk.Style(self).lookup("TFrame", "background") or "#f0f0f0"
        self.form_canvas.configure(bg=panel)
        self.help_label = tk.Label(right, text="", justify="left", anchor="nw", height=3, fg=MUTED, bg=panel, font=self.app.font,
                                   wraplength=px(420))
        self.help_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(px(4), 0))
        right.bind("<Configure>", lambda e: self.help_label.configure(wraplength=max(px(240), e.width - px(40))))

        # ---- bottom: problems, JSON, JSON editor
        self.bottom = ttk.Notebook(vertical, height=px(70))
        vertical.add(self.bottom, weight=1)
        problems = ttk.Frame(self.bottom)
        problems.columnconfigure(0, weight=1)
        problems.rowconfigure(0, weight=1)
        self.problems = ttk.Treeview(problems, show="tree", selectmode="browse", height=3, style="Dense.Treeview")
        self.problems.grid(row=0, column=0, sticky="nsew")
        prscroll = ttk.Scrollbar(problems, command=self.problems.yview)
        prscroll.grid(row=0, column=1, sticky="ns")
        self.problems.configure(yscrollcommand=prscroll.set)
        self.problems.tag_configure("error", foreground=RED)
        self.problems.tag_configure("warning", foreground=AMBER)
        self.problems.tag_configure("info", foreground=MUTED)
        self.problems.bind("<Double-1>", lambda _e: self._goto_problem())
        self._issues: dict[str, vd.Issue] = {}
        self.fix_button = ttk.Button(problems, text="Fix spellings", command=self.fix_spellings, style="Small.TButton")
        self.fix_button.grid(row=1, column=0, sticky="w", pady=(px(4), 0))
        self.fix_hint = ttk.Label(problems, text="renames settings the simulator does not read to the spelling it does", foreground=MUTED)
        self.fix_hint.grid(row=1, column=0, sticky="e", pady=(px(4), 0))
        self.bottom.add(problems, text="Problems")
        preview = ttk.Frame(self.bottom)
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.preview = tk.Text(preview, height=3, wrap="none", state="disabled", font=("Consolas", 9), relief="flat", borderwidth=0)
        self.preview.grid(row=0, column=0, sticky="nsew")
        pvs = ttk.Scrollbar(preview, command=self.preview.yview)
        pvs.grid(row=0, column=1, sticky="ns")
        self.preview.configure(yscrollcommand=pvs.set)
        self.bottom.add(preview, text="JSON of this vehicle")
        editor = ttk.Frame(self.bottom)
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(0, weight=1)
        self.json_editor = tk.Text(editor, height=3, wrap="none", font=("Consolas", 9), undo=True)
        self.json_editor.grid(row=0, column=0, sticky="nsew")
        jes = ttk.Scrollbar(editor, command=self.json_editor.yview)
        jes.grid(row=0, column=1, sticky="ns")
        self.json_editor.configure(yscrollcommand=jes.set)
        self.apply_button = ttk.Button(editor, text="Apply JSON", command=self.apply_json, style="Small.TButton")
        self.apply_button.grid(row=1, column=0, sticky="w", pady=(px(4), 0))
        self.bottom.add(editor, text="Edit JSON")

    def _balance_panes(self, event) -> None:
        """Once, when the panes first get their size: start with the settings form the widest (the dividers can be dragged)."""
        if self._balanced or event.width < 500:
            return
        self._balanced = True
        total = event.width
        self._panes.sashpos(0, int(total * 0.17))
        self._panes.sashpos(1, int(total * 0.17 + total * 0.30))

    def _shortcut(self, action: Callable[[], None]):
        def handler(event) -> str | None:
            # only when this tab is the one showing (the Run tab has no use for Ctrl+Z)
            if not self.winfo_ismapped():
                return None
            if isinstance(event.widget, (tk.Text, tk.Entry, ttk.Entry)):
                return None  # the text field's own undo / the entry's own handling
            action()
            return "break"
        return handler

    def _form_resized(self, event) -> None:
        self.form_canvas.itemconfigure(self._form_window, width=event.width)
        note = getattr(self, "_form_note", None)  # the part's description wraps at the panel's width
        if note is not None and note.winfo_exists():
            note.configure(wraplength=max(self.px(200), event.width - self.px(24)))

    def _wheel(self, event) -> None:
        self.form_canvas.yview_scroll(int(-event.delta / 120), "units")

    # --------------------------------------------------------------------------------------- designs
    def _open_initial(self) -> None:
        """Start on the design that is used when running, else the first provided vehicle."""
        chosen = self.library.selected()
        provided = self.library.provided()
        if chosen is not None:
            self.open_design(chosen)
        elif provided:
            self.open_design(None, provided=provided[0])

    def refresh_designs(self, select: str | Path | None = None) -> None:
        """Re-read the design folder into the chooser. `select` is a design name or a provided vehicle's path to show."""
        active = self.library.selected()
        self._design_choices: list[tuple[str, str, str | Path]] = []  # (text shown, "provided" | "mine", name or path)
        for path in self.library.provided():
            note = ", used when running" if active is None else ""
            self._design_choices.append((f"{path.stem}   (provided, read-only{note})", "provided", path))
        for name in self.library.names():
            self._design_choices.append((name + ("   (used when running)" if name == active else ""), "mine", name))
        self.design_combo.configure(values=[c[0] for c in self._design_choices] or ["(none)"])
        shown = select if select is not None else (
            (self.design.path if self.design is not None and self.design.provided else self.design.name) if self.design else None)
        index = next((i for i, c in enumerate(self._design_choices) if c[2] == shown), None)
        if index is None:
            self.design_combo.set("")
        else:
            self.design_combo.current(index)
        self._update_state()

    def _on_design_selected(self) -> None:
        if self._building:
            return
        index = self.design_combo.current()
        if not 0 <= index < len(self._design_choices):
            return
        _text, kind, ref = self._design_choices[index]
        if self.design is not None and self.design.name == (ref.stem if kind == "provided" else ref):
            return
        if not self.confirm_discard():
            self.refresh_designs()  # puts the chooser back on the design that is still open
            return
        self.open_design(ref if kind == "mine" else None, provided=ref if kind == "provided" else None)

    def open_design(self, name: str | None = None, provided: Path | None = None) -> bool:
        """Show a saved design (by name) or a provided vehicle (by path). False if it could not be read."""
        try:
            design = self.library.load_provided(provided) if provided else self.library.load(name or "")
        except vd.DesignError as exc:
            messagebox.showerror("MRRSimulator", str(exc))
            return False
        self.design, self.current = design, vd.BlockRef("vehicle")
        self._refresh_all()
        self.refresh_designs(select=provided if provided else name)
        return True

    def new_design(self) -> None:
        if not self.confirm_discard():
            return
        name = self._ask_name("New design", "Name of the new vehicle design:", self.library.unique_name("My vehicle"))
        if name is None:
            return
        self._created(self.library.create(name, vd.blank_document()))

    def duplicate_design(self) -> None:
        if self.design is None or not self.confirm_discard():
            return
        base = self.library.unique_name(f"{self.design.name} copy" if not self.design.provided else
                                        self.design.name.replace("robot_", "").replace("_", " ").strip().capitalize() or "My vehicle")
        name = self._ask_name("Duplicate design", f"Name of the copy of {self.design.name}:", base)
        if name is None:
            return
        self._created(self.library.duplicate(self.design, name))

    def _created(self, design: vd.Design) -> None:
        self.design, self.current = design, vd.BlockRef("vehicle")
        self._refresh_all()
        self.refresh_designs(select=design.name)
        self.app.note(f"Vehicle design {design.name!r} saved.")

    def rename_design(self) -> None:
        if self.design is None or self.design.provided or not self.confirm_discard():
            return
        old = self.design.name
        name = self._ask_name("Rename design", f"New name for {old}:", old)
        if name is None or name == old:
            return
        try:
            new = self.library.rename(old, name)
        except vd.DesignError as exc:
            messagebox.showerror("MRRSimulator", str(exc))
            return
        self.open_design(new)
        self.on_selection_changed()
        self.app.note(f"Renamed vehicle design {old!r} to {new!r}.")

    def delete_design(self) -> None:
        if self.design is None or self.design.provided:
            return
        name = self.design.name
        if not messagebox.askyesno("MRRSimulator", f"Delete the vehicle design \"{name}\"?\n\nThe file is removed from your vehicles folder."):
            return
        try:
            self.library.delete(name)
        except vd.DesignError as exc:
            messagebox.showerror("MRRSimulator", str(exc))
            return
        self.design = self.current = None
        self._refresh_all()
        self.refresh_designs()
        self.on_selection_changed()
        self.app.note(f"Deleted vehicle design {name!r}.")

    def copy_settings(self) -> None:
        """Put the design's Project AirSim robot config on the clipboard, ready to paste into a .jsonc file."""
        if self.design is None:
            return
        self.clipboard_clear()
        self.clipboard_append(self.design.airsim_json())
        self.update()  # the clipboard keeps the text after the window is closed only if it is flushed
        self.copy_json_button.configure(text="Copied to the clipboard")
        self.after(1800, lambda: self.copy_json_button.configure(text="Copy settings (JSON)"))

    def show_in_folder(self) -> None:
        """Open the folder of the design's .jsonc file (with the file selected); the provided vehicles are in sim_config."""
        if self.design is None:
            return
        path = self.design.path
        if path is not None and path.is_file():
            _reveal(path)
        else:
            _reveal(self.library.folder, is_folder=True)

    def use_for_runs(self) -> None:
        """Choose the design the simulator is run with; the provided vehicle when a provided one is shown."""
        if self.design is None:
            return
        if self.design.provided:
            self.library.select(None)
        else:
            why = self.design.runnable_problem()
            if why:
                messagebox.showwarning("MRRSimulator", f"This design cannot be flown here: {why}.")
                return
            if self.design.dirty and not self.save():
                return
            self.library.select(self.design.name)
        self.refresh_designs()
        self.on_selection_changed()

    def _ask_name(self, title: str, prompt: str, initial: str) -> str | None:
        while True:
            text = simpledialog.askstring(title, prompt, initialvalue=initial, parent=self.winfo_toplevel())
            if text is None:
                return None
            try:
                name = vd.check_name(text)
                if self.library.exists(name) and name.lower() != initial.lower():
                    raise vd.DesignError(f"There is already a design called {name!r}.")
                return name
            except vd.DesignError as exc:
                messagebox.showerror("MRRSimulator", str(exc))
                initial = text

    def confirm_discard(self) -> bool:
        """Before something replaces the design on screen: offer to save unsaved changes. False means 'stay'."""
        if self.design is None or not self.design.dirty or self.design.provided:
            return True
        answer = messagebox.askyesnocancel("MRRSimulator", f"Save the changes to \"{self.design.name}\" first?")
        if answer is None:
            return False
        return self.save() if answer else True

    # ----------------------------------------------------------------------------------------- saving
    def save(self) -> bool:
        if self.design is None or self.design.provided:
            return False
        errors = [i for i in self.design.issues() if i.level == "error"]
        if errors and not messagebox.askyesno(
                "MRRSimulator", f"This design has {len(errors)} problem(s) that would stop the simulator from using it "
                                f"(see the Problems list).\n\nSave it anyway?"):
            return False
        try:
            self.library.save(self.design)
        except (vd.DesignError, OSError) as exc:
            messagebox.showerror("MRRSimulator", f"Could not save: {exc}")
            return False
        self._update_state()
        self.app.note(f"Vehicle design {self.design.name!r} saved.")
        return True

    def revert(self) -> None:
        if self.design is None or self.design.provided or not self.design.dirty:
            return
        if not messagebox.askyesno("MRRSimulator", f"Discard the unsaved changes to \"{self.design.name}\"?"):
            return
        self.open_design(self.design.name)

    # ---------------------------------------------------------------------------------------- the tree
    def _refresh_all(self) -> None:
        self._refresh_tree()
        self._render_form()
        self._refresh_side()
        self._update_state()

    def _block_text(self, ref: vd.BlockRef, bad: dict[vd.BlockRef, str]) -> tuple[str, tuple]:
        d = self.design
        assert d is not None
        item = d.get(ref)
        text = d.label(ref)
        tags = []
        if item.get("enabled") is False:
            text += "   [off]"
            tags.append("off")
        level = bad.get(ref)
        if level == "error":
            text += "   (!)"
            tags.append("error")
        elif level == "warning":
            tags.append("warning")
        return text, tuple(tags)

    def _refresh_tree(self) -> None:
        tree = self.vehicle_tree
        tree.delete(*tree.get_children())
        self._block_items.clear()
        self._ref_items.clear()
        d = self.design
        if d is None:
            tree.insert("", "end", text="  (choose a design on the left)", tags=("hint",))
            self.vehicle_frame.configure(text="Vehicle")
            return
        bad: dict[vd.BlockRef, str] = {}
        for issue in d.issues():
            if issue.ref is not None and issue.level in ("error", "warning") and bad.get(issue.ref) != "error":
                bad[issue.ref] = issue.level

        def add(parent: str, ref: vd.BlockRef, prefix: str = "") -> str:
            text, tags = self._block_text(ref, bad)
            item = tree.insert(parent, "end", text=prefix + text, open=True, tags=tags)
            self._block_items[item] = ref
            self._ref_items[ref] = item
            return item

        add("", vd.BlockRef("vehicle"))
        if d.doc.get("controller") is not None:
            add("", vd.BlockRef("controller"))
        links_node = tree.insert("", "end", text="Links and the parts on them", open=True, tags=("group",))
        link_items: dict[str, str] = {}
        for ref in d.blocks("links"):
            link_items[d.name_of(ref)] = add(links_node, ref)
        if not link_items:
            tree.insert(links_node, "end", text="  (no links yet: add one from Parts)", tags=("hint",))
        loose: list[vd.BlockRef] = []
        for section in ("actuators", "sensors"):
            for ref in d.blocks(section):
                parent = d.get(ref).get("parent-link", "")
                if parent in link_items:
                    add(link_items[parent], ref)
                else:
                    loose.append(ref)
        joints = d.blocks("joints")
        if joints:
            node = tree.insert("", "end", text="Joints", open=True, tags=("group",))
            for ref in joints:
                add(node, ref)
        if loose:
            node = tree.insert("", "end", text="Parts not attached to a link", open=True, tags=("group",))
            for ref in loose:
                add(node, ref)
        marker = " *" if d.dirty else ""
        self.vehicle_frame.configure(text=f"Vehicle: {d.name}{marker}")
        if self.current in self._ref_items:
            self._quiet(lambda: tree.selection_set(self._ref_items[self.current]))

    def _quiet(self, action: Callable[[], None]) -> None:
        """Run a selection change without the selection handlers reacting to it."""
        self._building = True
        try:
            action()
        finally:
            self._building = False

    def _on_block_selected(self) -> None:
        if self._building:
            return
        chosen = self.vehicle_tree.selection()
        ref = self._block_items.get(chosen[0]) if chosen else None
        if ref is not None and ref != self.current:
            self.current = ref
            self._render_form()
        self._update_state()

    def select_block(self, ref: vd.BlockRef) -> None:
        self.current = ref
        if ref in self._ref_items:
            self._quiet(lambda: self.vehicle_tree.selection_set(self._ref_items[ref]))
            self.vehicle_tree.see(self._ref_items[ref])
        self._render_form()
        self._update_state()

    # --------------------------------------------------------------------------------- parts / editing
    def selected_part(self) -> vs.BlockType | None:
        chosen = self.parts_tree.selection()
        return self._part_items.get(chosen[0]) if chosen else None

    def add_selected_part(self) -> None:
        bt = self.selected_part()
        if bt is not None:
            self.add_part(bt)

    def _attach_target(self) -> str | None:
        """The link a new part should go on: the selected link, or the link of the selected part."""
        d = self.design
        if d is None or self.current is None:
            return None
        if self.current.section == "links":
            return d.name_of(self.current)
        if self.current.section in ("sensors", "actuators"):
            return d.get(self.current).get("parent-link") or None
        return None

    def add_part(self, bt: vs.BlockType, parent_link: str | None = None, *, use_selection: bool = True) -> vd.BlockRef | None:
        """Put a part on the vehicle: on `parent_link`, else (when `use_selection`) on the selected link or the link of the
        selected part, else on the first link."""
        d = self.design
        if d is None:
            messagebox.showinfo("MRRSimulator", "Choose or create a design first.")
            return None
        try:
            if parent_link is None and use_selection:
                parent_link = self._attach_target()
            ref = d.add_block(bt, parent_link=parent_link)
        except vd.DesignError as exc:
            messagebox.showwarning("MRRSimulator", str(exc))
            return None
        self.current = ref
        self._refresh_all()
        self.select_block(ref)
        return ref

    def drop_part(self, bt: vs.BlockType, target_item: str | None) -> vd.BlockRef | None:
        """A part dropped on the vehicle tree: onto a link (or a part on one) it is attached to that link."""
        link = None
        ref = self._block_items.get(target_item) if target_item else None
        if ref is not None and self.design is not None:
            if ref.section == "links":
                link = self.design.name_of(ref)
            elif ref.section in ("sensors", "actuators"):
                link = self.design.get(ref).get("parent-link") or None
        return self.add_part(bt, parent_link=link, use_selection=False)  # a drop has its own target, not the selection

    def remove_selected(self) -> None:
        d, ref = self.design, self.current
        if d is None or ref is None or ref.single:
            return
        users = d.references_to(ref)
        text = f"Remove {d.label(ref)}?"
        if users:
            text += "\n\n" + ("These use it and will be left unattached or lose it: " + ", ".join(users[:6])
                              + (" and more" if len(users) > 6 else "") + ".")
        if not messagebox.askyesno("MRRSimulator", text):
            return
        try:
            d.remove_block(ref)
        except vd.DesignError as exc:
            messagebox.showwarning("MRRSimulator", str(exc))
            return
        self.current = vd.BlockRef("vehicle")
        self._refresh_all()

    def duplicate_selected(self) -> None:
        d, ref = self.design, self.current
        if d is None or ref is None or ref.single:
            return
        try:
            new = d.duplicate_block(ref)
        except vd.DesignError as exc:
            messagebox.showwarning("MRRSimulator", str(exc))
            return
        self.current = new
        self._refresh_all()

    def move_selected(self, delta: int) -> None:
        d, ref = self.design, self.current
        if d is None or ref is None or ref.single:
            return
        self.current = d.move_block(ref, delta)
        self._refresh_all()

    def fix_spellings(self) -> int:
        """Rename keys the docs spell differently from the simulator's code to what the simulator reads."""
        d = self.design
        if d is None or d.provided:
            return 0
        try:
            count = d.fix_legacy()
        except vd.DesignError as exc:
            messagebox.showwarning("MRRSimulator", str(exc))
            return 0
        if count:
            self.app.note(f"Fixed {count} setting spelling(s) in {d.name!r}.")
        self._refresh_all()
        return count

    def undo(self) -> None:
        if self.design is not None and self.design.can_undo and not self.design.provided:
            self.design.undo()
            self._after_history()

    def redo(self) -> None:
        if self.design is not None and self.design.can_redo and not self.design.provided:
            self.design.redo()
            self._after_history()

    def _after_history(self) -> None:
        d = self.design
        assert d is not None
        if self.current is None or (not self.current.single and self.current.index >= d.count(self.current.section)):
            self.current = vd.BlockRef("vehicle")
        self._refresh_all()

    # ---------------------------------------------------------------------------------- drag and drop
    def _drag_start(self, event) -> None:
        item = self.parts_tree.identify_row(event.y)
        self._drag = self._part_items.get(item)

    def _drag_move(self, event) -> None:
        if self._drag is None:
            return
        if self._ghost is None:
            self._ghost = tk.Label(self, text=self._drag.title, relief="solid", borderwidth=1, bg="#fff8c5", padx=self.px(6))
        x = event.x_root - self.winfo_rootx() + self.px(8)
        y = event.y_root - self.winfo_rooty() + self.px(8)
        self._ghost.place(x=x, y=y)
        self._ghost.lift()

    def _drag_end(self, event) -> None:
        bt, self._drag = self._drag, None
        if self._ghost is not None:
            self._ghost.destroy()
            self._ghost = None
        if bt is None:
            return
        over = self.winfo_containing(event.x_root, event.y_root)
        if over is self.vehicle_tree:
            y = event.y_root - self.vehicle_tree.winfo_rooty()
            self.drop_part(bt, self.vehicle_tree.identify_row(y) or None)

    # ------------------------------------------------------------------------------------- the form
    def _render_form(self) -> None:
        """Rebuild the settings form for the selected block."""
        first, _last = self.form_canvas.yview()
        for child in self.form_inner.winfo_children():
            child.destroy()
        self.fields.clear()
        self.map_editors.clear()
        self.help_label.configure(text="")
        d, ref = self.design, self.current
        if d is None or ref is None:
            self.form_title.configure(text="")
            tk.Label(self.form_inner, text="Choose a design on the left, then pick a part of the vehicle to edit its settings.",
                     justify="left", anchor="w", fg=MUTED, wraplength=self.px(380)).grid(row=0, column=0, columnspan=3, sticky="w")
            return
        bt = d.block_type(ref)
        obj = d.get(ref)
        self.form_title.configure(text=d.label(ref) if bt else f"{d.label(ref)} (unknown type)")
        if bt is None:
            tk.Label(self.form_inner, text="This part has a type the settings catalog does not know. Edit it in the JSON editor below.",
                     justify="left", anchor="w", fg=AMBER, wraplength=self.px(380)).grid(row=0, column=0, columnspan=3, sticky="w")
            return
        self._form_note = tk.Label(self.form_inner, text=bt.help, justify="left", anchor="w", fg=MUTED,
                                   wraplength=max(self.px(200), self.form_canvas.winfo_width() - self.px(24)))
        self._form_note.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, self.px(8)))
        row = self._form(self.form_inner, bt.settings, obj, (), 1, bt)
        self._unknown_settings(self.form_inner, obj, bt, row)
        self.update_idletasks()
        self.form_canvas.yview_moveto(first)

    def _read_only(self) -> bool:
        return self.design is None or self.design.provided

    def _form(self, parent: tk.Misc, settings: tuple[vs.Setting, ...], obj: dict, path: tuple, row: int,
              bt: vs.BlockType | None) -> int:
        for s in settings:
            if s.when and not vd.sibling_in(s.when, obj, settings):
                continue
            present = any(k in obj for k in (s.key, *s.aliases))
            if s.ignored and not present:
                continue
            row = self._setting(parent, s, obj, path, row, settings, bt)
        return row

    def _setting(self, parent: tk.Misc, s: vs.Setting, obj: dict, path: tuple, row: int, siblings: tuple[vs.Setting, ...],
                 bt: vs.BlockType | None) -> int:
        key = next((k for k in (s.key, *s.aliases) if k in obj), s.key)
        value = obj.get(key)
        where = path + (key,)
        if s.kind == vs.GROUP:
            frame = ttk.LabelFrame(parent, text=s.title + (" *" if s.required else ""))
            frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(self.px(4), self.px(4)))
            frame.columnconfigure(1, weight=1)
            self._bind_help(frame, s)
            inner = value if isinstance(value, dict) else {}
            self._form(frame, s.children, inner, where, 0, bt)
            if s.ignored:
                tk.Label(frame, text=f"Ignored by the simulator: {s.ignored}", fg=AMBER, anchor="w").grid(columnspan=3, sticky="w")
            return row + 1
        if s.kind == vs.TABLE:
            return self._table(parent, s, value if isinstance(value, list) else [], where, row, bt)
        label = ttk.Label(parent, text=s.title + (" *" if s.required else ""), wraplength=self.px(190), justify="left")
        label.grid(row=row, column=0, sticky="w", padx=(0, self.px(8)), pady=self.px(3))
        var = tk.StringVar()
        if s.kind == vs.MAP:
            self._map_editor(parent, s, value if isinstance(value, dict) else {}, where, row)
            self._bind_help(label, s)
            return row + 1
        present = key in obj
        if s.kind == vs.LIST:
            var.set(", ".join(value) if isinstance(value, list) else "")
        else:
            var.set(vd.format_value(s, value) if present else "")
        hint = self._default_hint(s)
        choices = self._choices(s, hint)
        if choices is not None:
            if not present:
                var.set(choices[0])  # "(default: x)": the setting is left out of the file
            widget = ttk.Combobox(parent, textvariable=var, values=choices, width=max(14, min(28, max(len(c) for c in choices) + 2)),
                                  state="readonly" if not self._read_only() else "disabled")
            widget.bind("<<ComboboxSelected>>", lambda _e, p=where, st=s, v=var: self._commit(p, st, v.get(), key_for_rename=(bt, path)))
        else:
            widget = ttk.Entry(parent, textvariable=var, width=14, state="normal" if not self._read_only() and not s.managed else "disabled")
            self._hint_overlay(widget, var, hint)
            for sequence in ("<Return>", "<FocusOut>"):
                widget.bind(sequence, lambda _e, p=where, st=s, v=var: self._commit(p, st, v.get(), key_for_rename=(bt, path)))
        widget.grid(row=row, column=1, sticky="ew", pady=self.px(3))
        side = "set by the launcher" if s.managed else s.unit
        if side:
            ttk.Label(parent, text=side, foreground=MUTED).grid(row=row, column=2, sticky="w", padx=(self.px(8), 0))
        for w in (label, widget):
            self._bind_help(w, s)
        field = Field(where, s, var, widget, label)
        self.fields[where] = field
        self._mark(field)
        return row + 1

    @staticmethod
    def _default_hint(s: vs.Setting) -> str:
        """What an empty field means: the simulator's default, or just that it is optional."""
        if s.default is not None and s.kind not in (vs.GROUP, vs.TABLE):
            return f"default: {vd.format_value(s, s.default)}"
        return "" if s.required else "optional"

    def _hint_overlay(self, entry: tk.Misc, var: tk.StringVar, text: str) -> None:
        """Grey text inside an empty, unfocused entry (the variable itself stays empty)."""
        if not text:
            return
        overlay = tk.Label(entry, text=text, fg="#8a8a8a", bg="white", font=self.app.font, anchor="w", cursor="xterm")

        def update(*_args) -> None:
            if var.get() or entry.focus_get() is entry:
                overlay.place_forget()
            else:
                overlay.place(x=self.px(5), y=self.px(2), relheight=0.85)

        var.trace_add("write", update)
        entry.bind("<FocusIn>", update, add="+")
        entry.bind("<FocusOut>", update, add="+")
        overlay.bind("<Button-1>", lambda _e: entry.focus_set())
        update()

    def _choices(self, s: vs.Setting, hint: str = "") -> list[str] | None:
        first = f"({hint})" if hint else ""
        if s.kind == vs.ENUM:
            return [first] + [vd.format_value(s, v) for v in s.choice_values()]
        if s.kind == vs.BOOL:
            return [first, "true", "false"]
        d = self.design
        if d is not None and s.kind == vs.LINKREF:
            return [first] + d.names("links")
        if d is not None and s.kind == vs.ACTREF:
            return [first] + d.names("actuators")
        return None

    def _note_for(self, s: vs.Setting) -> str:
        bits = []
        if s.unit:
            bits.append(s.unit)
        if s.default is not None and s.kind not in (vs.GROUP, vs.TABLE):
            bits.append(f"default {vd.format_value(s, s.default)}")
        return "   ".join(bits)

    def _bind_help(self, widget: tk.Misc, s: vs.Setting) -> None:
        text = self._help_text(s)
        widget.bind("<Enter>", lambda _e: self.help_label.configure(text=text), add="+")
        widget.bind("<FocusIn>", lambda _e: self.help_label.configure(text=text), add="+")

    @staticmethod
    def _help_text(s: vs.Setting) -> str:
        parts = [f"{s.title}  ({s.key})"]
        if s.help:
            parts.append(s.help)
        facts = []
        if s.unit:
            facts.append(f"unit: {s.unit}")
        if s.minimum is not None or s.maximum is not None:
            facts.append(f"range: {'' if s.minimum is None else f'{s.minimum:g}'} to {'' if s.maximum is None else f'{s.maximum:g}'}")
        if s.default is not None and s.kind not in (vs.GROUP, vs.TABLE):
            facts.append(f"default: {vd.format_value(s, s.default)}")
        if s.required:
            facts.append("required")
        if s.aliases:
            facts.append("also accepted as: " + ", ".join(s.aliases))
        if s.legacy:
            facts.append("not read by the simulator: " + ", ".join(s.legacy))
        if s.managed:
            facts.append(s.managed)
        if s.note:
            facts.append(s.note)
        if facts:
            parts.append(" | ".join(facts))
        return "\n".join(parts)

    def _mark(self, field: Field) -> None:
        """Colour a setting's label red when the design has an error about it."""
        d, ref = self.design, self.current
        if d is None or ref is None:
            return
        # (cheap: only whether this exact path is named by an issue)
        bad = any(i.ref == ref and i.level == "error" and tuple(i.path) == field.path for i in d.issues())
        field.label.configure(foreground=RED if bad else "")

    # -- tables, maps, unknown keys
    def _table(self, parent: tk.Misc, s: vs.Setting, rows: list, where: tuple, row: int, bt: vs.BlockType | None) -> int:
        frame = ttk.LabelFrame(parent, text=s.title + (" *" if s.required else ""))
        frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(self.px(4), self.px(4)))
        frame.columnconfigure(0, weight=1)
        self._bind_help(frame, s)
        for i, item in enumerate(rows):
            sub = ttk.LabelFrame(frame, text=f"{self._singular(s.title)} {i + 1}")
            sub.grid(row=i, column=0, sticky="ew", pady=self.px(3))
            sub.columnconfigure(1, weight=1)
            self._form(sub, s.children, item if isinstance(item, dict) else {}, where + (i,), 0, bt)
            remove = ttk.Button(sub, text="Remove this entry", command=lambda idx=i, w=where: self._remove_row(w, idx),
                                state="disabled" if self._read_only() else "normal")
            remove.grid(row=999, column=0, columnspan=3, sticky="e", pady=(self.px(4), 0))
        add = ttk.Button(frame, text=f"Add {self._singular(s.title).lower()}",
                         command=lambda st=s, w=where: self._add_row(st, w), state="disabled" if self._read_only() else "normal")
        add.grid(row=len(rows), column=0, sticky="w", pady=(self.px(4), 0))
        return row + 1

    @staticmethod
    def _singular(title: str) -> str:
        return title[:-1] if title.endswith("s") and not title.endswith("ss") else title

    def _add_row(self, s: vs.Setting, where: tuple) -> None:
        d, ref = self.design, self.current
        if d is None or ref is None:
            return
        try:
            d.append_row(ref, where, row_template(s))
        except vd.DesignError as exc:
            messagebox.showwarning("MRRSimulator", str(exc))
            return
        self._after_edit(structural=True)

    def _remove_row(self, where: tuple, index: int) -> None:
        d, ref = self.design, self.current
        if d is None or ref is None:
            return
        d.remove_value(ref, where + (index,))
        self._after_edit(structural=True)

    def _map_editor(self, parent: tk.Misc, s: vs.Setting, values: dict, where: tuple, row: int) -> tk.Text:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=1, columnspan=2, sticky="ew", pady=self.px(3))
        frame.columnconfigure(0, weight=1)
        text = tk.Text(frame, height=min(8, max(3, len(values) + 1)), width=28, font=("Consolas", 9), wrap="none")
        text.grid(row=0, column=0, sticky="ew")
        text.insert("1.0", "\n".join(f"{k} = {v}" for k, v in values.items()))
        if self._read_only():
            text.configure(state="disabled")
        names = [n for n, _ in s.catalog]
        if names and not self._read_only():
            picker = ttk.Combobox(frame, values=names, state="readonly", width=24)
            picker.grid(row=1, column=0, sticky="w", pady=(self.px(4), 0))
            picker.set("Add a known parameter...")

            def add(_e=None) -> None:
                name = picker.get()
                if name in names and f"{name} =" not in text.get("1.0", "end"):
                    text.insert("end", ("" if text.get("1.0", "end").strip() == "" else "\n") + f"{name} = 0")
                    commit()
                picker.set("Add a known parameter...")
            picker.bind("<<ComboboxSelected>>", add)

        def commit(_e=None) -> None:
            parsed: dict = {}
            for line in text.get("1.0", "end").splitlines():
                if not line.strip():
                    continue
                name, _, raw = line.partition("=")
                try:
                    parsed[name.strip()] = vd.parse_value(vs.Setting("v", vs.NUMBER, name.strip()), raw)
                except vd.DesignError as exc:
                    messagebox.showwarning("MRRSimulator", f"{line.strip()}: {exc}")
                    return
                if parsed[name.strip()] is None:
                    parsed.pop(name.strip())
            d, ref = self.design, self.current
            if d is None or ref is None:
                return
            if parsed != values:
                d.set_value(ref, where, parsed or None)
                self._after_edit(structural=False)
        text.bind("<FocusOut>", commit)
        self.map_editors[where] = text
        return text

    def _unknown_settings(self, parent: tk.Misc, obj: dict, bt: vs.BlockType, row: int) -> None:
        """Keys in the file that the catalog does not list (including spellings the simulator does not read)."""
        skip = {"type"} | (set(vd.STRUCTURE) if bt.section == "vehicle" else set())
        known = vs.known_keys(bt) | {s.key for s in bt.settings}
        legacy = {old: s for s in bt.settings for old in s.legacy}
        extra = [k for k in obj if k not in known and k not in skip and not k.startswith("$")]
        if not extra:
            return
        frame = ttk.LabelFrame(parent, text="Other settings in this file")
        frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(self.px(8), 0))
        frame.columnconfigure(1, weight=1)
        for i, key in enumerate(extra):
            hint = f"the simulator reads '{legacy[key].key}' instead" if key in legacy else "not a known setting (kept as it is)"
            ttk.Label(frame, text=key).grid(row=i, column=0, sticky="w", padx=(0, self.px(8)))
            var = tk.StringVar(value=json.dumps(obj[key]))
            entry = ttk.Entry(frame, textvariable=var, state="disabled" if self._read_only() else "normal")
            entry.grid(row=i, column=1, sticky="ew")
            ttk.Label(frame, text=hint, foreground=AMBER if key in legacy else MUTED).grid(row=i, column=2, padx=(self.px(8), 0))
            entry.bind("<Return>", lambda _e, k=key, v=var: self._commit_unknown(k, v.get()))
            entry.bind("<FocusOut>", lambda _e, k=key, v=var: self._commit_unknown(k, v.get()))

    def _commit_unknown(self, key: str, text: str) -> None:
        d, ref = self.design, self.current
        if d is None or ref is None:
            return
        try:
            value = json.loads(text)
        except ValueError:
            messagebox.showwarning("MRRSimulator", f"{key}: not valid JSON.")
            self._render_form()
            return
        if d.get(ref).get(key) != value:
            d.set_value(ref, (key,), value)
            self._after_edit(structural=False)

    # ------------------------------------------------------------------------------------- committing
    def commit_field(self, path: tuple, text: str) -> bool:
        """What typing `text` into the field at `path` and pressing Enter does (also used by the tests)."""
        field = self.fields.get(path)
        if field is None:
            raise KeyError(path)
        field.var.set(text)
        return self._commit(path, field.setting, text, key_for_rename=(self.design.block_type(self.current) if self.design and self.current else None, path[:-1]))

    def _commit(self, path: tuple, s: vs.Setting, text: str, key_for_rename=None) -> bool:
        d, ref = self.design, self.current
        if d is None or ref is None or d.provided or self._building:
            return False
        try:
            if text.startswith("(default") or text == "(optional)":
                text = ""
            if s.kind == vs.LIST:
                items = [t.strip() for t in text.split(",") if t.strip()]
                value = items or None
            else:
                value = vd.parse_value(s, text)
            current = d.get_value(ref, path)
            if value == current and type(value) is type(current):
                return True
            bt = key_for_rename[0] if key_for_rename else None
            if bt is not None and len(path) == 1 and path[0] == bt.name_key and ref.section not in ("vehicle", "controller") and value is not None:
                d.rename_block(ref, str(value))
            else:
                d.set_value(ref, path, value)
        except vd.DesignError as exc:
            messagebox.showwarning("MRRSimulator", str(exc))
            self._render_form()
            return False
        controls = any(x.when and x.when[0] == s.key for x in self._siblings_of(path)) or s.kind == vs.ENUM
        self._after_edit(structural=controls or s.key in ("type",))
        return True

    def _siblings_of(self, path: tuple) -> tuple[vs.Setting, ...]:
        d, ref = self.design, self.current
        if d is None or ref is None:
            return ()
        bt = d.block_type(ref)
        if bt is None:
            return ()
        settings = bt.settings
        for step in path[:-1]:
            if isinstance(step, int):
                continue
            nxt = next((x for x in settings if x.key == step or step in x.aliases), None)
            if nxt is None:
                return ()
            settings = nxt.children
        return settings

    def _after_edit(self, structural: bool) -> None:
        self._refresh_tree()
        if structural:
            self._render_form()
        else:
            for field in self.fields.values():
                self._mark(field)
        self._refresh_side()
        self._update_state()

    def apply_json(self) -> None:
        d = self.design
        if d is None or d.provided:
            return
        try:
            doc = json.loads(scenecheck.strip_jsonc(self.json_editor.get("1.0", "end")))
            d.replace_document(doc)
        except (ValueError, vd.DesignError) as exc:
            messagebox.showerror("MRRSimulator", f"The JSON could not be applied: {exc}")
            return
        if self.current is None or (not self.current.single and self.current.index >= d.count(self.current.section)):
            self.current = vd.BlockRef("vehicle")
        self._refresh_all()

    # -------------------------------------------------------------------------- problems and preview
    def _refresh_side(self) -> None:
        d = self.design
        self.problems.delete(*self.problems.get_children())
        self._issues.clear()
        text = ""
        if d is not None:
            order = {"error": 0, "warning": 1, "info": 2}
            found = sorted(d.issues(), key=lambda i: order[i.level])
            for issue in found:
                item = self.problems.insert("", "end", text=f"{issue.level.upper():8}{issue.message}", tags=(issue.level,))
                self._issues[item] = issue
            if not found:
                self.problems.insert("", "end", text="No problems found.", tags=("info",))
            errors = sum(1 for i in found if i.level == "error")
            warnings = sum(1 for i in found if i.level == "warning")
            self.bottom.tab(0, text=f"Problems ({errors} errors, {warnings} warnings)" if errors or warnings else "Problems")
            text = json.dumps(d.doc, indent=2)
        else:
            self.bottom.tab(0, text="Problems")
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")
        if self.focus_get() is not self.json_editor:
            self.json_editor.delete("1.0", "end")
            self.json_editor.insert("1.0", text)

    def _goto_problem(self) -> None:
        chosen = self.problems.selection()
        issue = self._issues.get(chosen[0]) if chosen else None
        if issue is not None and issue.ref is not None:
            self.select_block(issue.ref)
            field = self.fields.get(tuple(issue.path))
            if field is not None:
                field.widget.focus_set()

    # ---------------------------------------------------------------------------------------- state
    def _update_state(self) -> None:
        d = self.design
        editable = d is not None and not d.provided
        has_block = editable and self.current is not None and not self.current.single

        def enable(button: ttk.Button, on: bool) -> None:
            button.state(["!disabled"] if on else ["disabled"])

        enable(self.duplicate_button, d is not None)
        enable(self.rename_button, editable)
        enable(self.delete_button, editable)
        enable(self.use_button, d is not None)
        enable(self.copy_json_button, d is not None)
        enable(self.open_folder_button, d is not None)
        enable(self.add_button, editable)
        enable(self.remove_button, bool(has_block))
        enable(self.copy_button, bool(has_block))
        enable(self.up_button, bool(has_block))
        enable(self.down_button, bool(has_block))
        enable(self.undo_button, bool(editable and d.can_undo))
        enable(self.redo_button, bool(editable and d.can_redo))
        enable(self.save_button, bool(editable and d.dirty))
        enable(self.revert_button, bool(editable and d.dirty))
        enable(self.apply_button, editable)
        enable(self.fix_button, bool(editable and d.legacy_count()))
        if d is None:
            self.banner.configure(text="")
        elif d.provided:
            self.banner.configure(text="This is a provided vehicle: it cannot be changed. Press Duplicate... to make your own copy to edit.")
        else:
            why = d.runnable_problem()
            self.banner.configure(text=f"This design cannot be flown by the simulator here: {why}." if why else "")
        if self.banner.cget("text"):
            self.banner.grid()
        else:
            self.banner.grid_remove()
        if d is not None:
            marker = " *" if d.dirty else ""
            self.vehicle_frame.configure(text=f"Vehicle: {d.name}{marker}")
        self.use_button.configure(text="Use the provided vehicle when running" if d is not None and d.provided else "Use when running")
