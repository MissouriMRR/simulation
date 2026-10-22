"""
Entry point of MRRSimulatorGUI.exe (windowed, no CLI), built by PyInstaller.
"""

import sys

try:
    from mrrsim.ui.app import main

    code = main()
except Exception as exc:  # noqa: BLE001
    # a windowed exe has nowhere to print a traceback: show it instead
    import tkinter
    import traceback
    from tkinter import messagebox

    root = tkinter.Tk()
    root.withdraw()
    messagebox.showerror(
        "MRRSimulator",
        "The window could not start:\n\n"
        + "".join(traceback.format_exception_only(type(exc), exc)),
    )
    code = 1
sys.exit(code)
