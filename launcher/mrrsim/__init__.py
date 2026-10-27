"""
MRRSimulator Launcher

The version number only exists in MRRSimulationLauncher.iss (`#define
AppVersion`). The rest of the program reads it from __version__ below. This
value comes from the .iss file when run from the source tree, and from
_version.py in the built exes. tools/sync_version.py writes this during
launcher/build.cmd.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ISS = REPO_ROOT / "MRRSimulationLauncher.iss"
_DEFINE = re.compile(r'^\s*#define\s+AppVersion\s+"([^"]+)"', re.MULTILINE)


def version_from_iss(path: Path = ISS) -> str | None:
    """The AppVersion defined in the installer script, or None if the file is
    missing or has none."""
    try:
        found = _DEFINE.search(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return None
    return found.group(1) if found else None


def _find_version() -> str:
    """The version from the installer script, else from the generated
    _version.py, else 'unknown'."""
    from_iss = version_from_iss()
    if from_iss:
        return from_iss
    try:
        from ._version import __version__ as built
    except ImportError:
        return "unknown"
    return built


__version__ = _find_version()
