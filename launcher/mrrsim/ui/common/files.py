"""Opening folders in the file manager."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def open_folder(path: Path) -> None:
    """Show a folder in Explorer (creating it first, so there is always something to show)."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 (a folder we just made)
    else:
        subprocess.Popen(["xdg-open", str(path)])
