"""The value boxes (Altitude, Throttle): which output lines fill them, and the widget that shows them.

A script fills a box by printing a line of its own, like the values of a ground station:
"Altitude: 12.3", "Throttle %: 53" (the "[script]" prefix, a unit and spaces are tolerated). Those lines are shown in the
boxes only, never in the output.
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

_NUMBER = r"([-+]?\d+(?:\.\d+)?)"
VALUE_LINES = {
    "altitude": re.compile(rf"^\s*altitude(?:\s*\(?m\)?)?\s*[:=]\s*{_NUMBER}\s*(?:m|meters?)?\s*$", re.I),
    "throttle": re.compile(rf"^\s*throttle(?:\s*%)?\s*[:=]\s*{_NUMBER}\s*%?\s*$", re.I),
}
VALUE_BOXES = (("altitude", "Altitude (m)", "{:.2f}"), ("throttle", "Throttle (%)", "{:.0f}"))
NO_VALUE = "--"


def value_line(text: str) -> tuple[str, float] | None:
    """(name, number) when this output line is one of the values shown in the boxes, else None. Only lines a script
    printed count."""
    if not text.startswith("[script]"):
        return None
    body = text[len("[script]"):]
    for name, pattern in VALUE_LINES.items():
        match = pattern.match(body)
        if match:
            return name, float(match.group(1))
    return None


class ValueTiles(ttk.Frame):
    """The value boxes (title above a big number), stacked vertically. `labels` holds the number label of each box."""

    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent, padding=(app.px(10), 0, app.px(2), 0))
        self.labels: dict[str, tk.Label] = {}
        for row, (key, title, _fmt) in enumerate(VALUE_BOXES):
            tile = tk.Frame(self, highlightthickness=1, highlightbackground="#c4c4c4", background="#ffffff")
            tile.grid(row=row, column=0, sticky="ew", pady=(0, app.px(8)))
            tk.Label(tile, text=title, font=app.font, fg="#5e5c64", background="#ffffff").pack(
                anchor="w", padx=app.px(10), pady=(app.px(6), 0))
            label = tk.Label(tile, text=NO_VALUE, font=("Segoe UI", 24, "bold"), fg="#1a1a1a", background="#ffffff",
                             width=7, anchor="e")
            label.pack(anchor="e", padx=app.px(10), pady=(0, app.px(6)))
            self.labels[key] = label

    def set(self, key: str, number: float | None) -> None:
        fmt = next(f for k, _t, f in VALUE_BOXES if k == key)
        self.labels[key].configure(text=NO_VALUE if number is None else fmt.format(number))

    def reset(self) -> None:
        for key, _title, _fmt in VALUE_BOXES:
            self.set(key, None)
