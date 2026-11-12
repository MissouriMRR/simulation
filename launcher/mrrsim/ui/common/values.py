"""The value boxes (Altitude, Throttle): which output lines fill them, and the widget that shows them.

A script fills a box by printing a line of its own, like the values of a ground station:
"Altitude: 12.3", "Throttle %: 53" (the "[script]" prefix, a unit and spaces are tolerated). Those lines are shown in the
boxes only, never in the output.
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

from . import colors

_NUMBER = r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)"  # as Python prints them: 12, -2.5, .5, 2., 1e-05
# the boxes, top to bottom: name -> (the line a script prints, the box's title, how the box shows the number)
VALUES = {
    "altitude": (re.compile(rf"^\s*altitude(?:\s*\(?m\)?)?\s*[:=]\s*{_NUMBER}\s*(?:m|meters?)?\s*$", re.I),
                 "Altitude (m)", "{:.2f}"),
    "throttle": (re.compile(rf"^\s*throttle(?:\s*%)?\s*[:=]\s*{_NUMBER}\s*%?\s*$", re.I), "Throttle (%)", "{:.0f}"),
}
NO_VALUE = "--"


def value_line(text: str) -> tuple[str, float] | None:
    """(name, number) when this output line is one of the values shown in the boxes, else None. Only lines a script
    printed count."""
    if not text.startswith("[script]"):
        return None
    body = text[len("[script]"):]
    for name, (pattern, _title, _fmt) in VALUES.items():
        match = pattern.match(body)
        if match:
            return name, float(match.group(1))
    return None


class ValueTiles(ttk.Frame):
    """The value boxes (title above a big number), stacked vertically. `labels` holds the number label of each box."""

    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent, padding=(app.px(10), 0, app.px(2), 0))
        self.labels: dict[str, tk.Label] = {}
        for row, (key, (_pattern, title, _fmt)) in enumerate(VALUES.items()):
            tile = tk.Frame(self, highlightthickness=1, highlightbackground=colors.BORDER, background=colors.SURFACE)
            tile.grid(row=row, column=0, sticky="ew", pady=(0, app.px(8)))
            tk.Label(tile, text=title, font=app.font, fg=colors.MUTED, background=colors.SURFACE).pack(
                anchor="w", padx=app.px(10), pady=(app.px(6), 0))
            label = tk.Label(tile, text=NO_VALUE, font=("Segoe UI", 24, "bold"), fg=colors.TEXT, background=colors.SURFACE,
                             width=7, anchor="e")
            label.pack(anchor="e", padx=app.px(10), pady=(0, app.px(6)))
            self.labels[key] = label

    def set(self, key: str, number: float | None) -> None:
        """Show `number` in the box `key` in that box's format; None empties the box."""
        fmt = VALUES[key][2]
        self.labels[key].configure(text=NO_VALUE if number is None else fmt.format(number))

    def reset(self) -> None:
        """Empty every box (a new run starts)."""
        for key in VALUES:
            self.set(key, None)
