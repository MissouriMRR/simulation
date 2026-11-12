"""Showing folders and files in the file manager."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from tkinter import messagebox


def _could_not_open(path: Path, exc: OSError) -> None:
    messagebox.showerror("MRRSimulator", f"Could not open {path} in the file manager:\n{exc}")


def open_folder(path: Path) -> None:
    """Show a folder in Explorer (creating it first, so there is always something to show). A failure is said in an
    error box."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # noqa: S606 (a folder of ours)
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        _could_not_open(path, exc)


def show_file(path: Path) -> None:
    """Show the file's folder in Explorer with the file selected (elsewhere: just its folder). A failure is said in
    an error box."""
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", f"/select,{path}"])  # explorer returns 1 even when it worked
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError as exc:
        _could_not_open(path, exc)
