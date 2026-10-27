# -*- mode: python ; coding: utf-8 -*-
# Two programs in one folder, sharing one copy of the Python runtime (PyInstaller MERGE):
#   MRRSimulator.exe     the command-line launcher (console)
#   MRRSimulatorGUI.exe  the window; no console, so no black box opens behind it
# Build with launcher\build.cmd
import os

here = os.path.abspath(SPECPATH)

cli = Analysis([os.path.join(here, "run_mrrsim.py")], pathex=[here])
gui = Analysis([os.path.join(here, "run_gui.py")], pathex=[here])
MERGE((cli, "run_mrrsim", "MRRSimulator"), (gui, "run_gui", "MRRSimulatorGUI"))

cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="MRRSimulator", console=True)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="MRRSimulatorGUI", console=False)

COLLECT(
    cli_exe, cli.binaries, cli.datas,
    gui_exe, gui.binaries, gui.datas,
    strip=False, upx=False, name="MRRSimulator",
)
