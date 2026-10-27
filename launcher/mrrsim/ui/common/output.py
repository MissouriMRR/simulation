"""The output text of a run (read-only, coloured by kind of line). The Run tab and the Simulator tab each show one."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class OutputView(ttk.Frame):
    """A read-only text with a scrollbar. `append` follows the end of the text only if it was already at the end."""

    def __init__(self, parent: tk.Misc, app) -> None:
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.text = tk.Text(self, height=5, wrap="word", state="disabled", font=("Consolas", 10),
                            padx=app.px(10), pady=app.px(8), spacing1=app.px(1), spacing3=app.px(1),
                            relief="flat", borderwidth=1, highlightthickness=1, highlightbackground="#c4c4c4")
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self, command=self.text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)
        self.text.tag_configure("script", foreground="#1a5fb4")
        self.text.tag_configure("error", foreground="#c01c28")
        self.text.tag_configure("warning", foreground="#b25000")
        self.text.tag_configure("note", foreground="#5e5c64")
        self.text.tag_configure("ok", foreground="#26a269")

    def append(self, text: str, tag: str | None = None) -> None:
        at_end = self.text.yview()[1] >= 0.999
        self.text.configure(state="normal")
        self.text.insert("end", text + "\n", tag or ())
        self.text.configure(state="disabled")
        if at_end:
            self.text.see("end")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def contents(self) -> str:
        return self.text.get("1.0", "end")
