"""
Entry point of MRRSimulator.exe (CLI, no window), built by PyInstaller.
Same as mrrsim/__main__.py, but imports by full name because PyInstaller cannot use relative imports.
"""

from mrrsim.cli import main

raise SystemExit(main())
