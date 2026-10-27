"""Where things live: the settings file, the student's folders (scripts, vehicle
designs, packages), and the launcher's own data (sessions, state)."""

from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path

from .. import REPO_ROOT

# === FINDING THE PROGRAM ======================================================


def repo_root() -> Path:
    """The repository root when running from the checkout. It is worked out once
    in mrrsim/__init__.py (REPO_ROOT), not here, so this file can move."""
    return REPO_ROOT


def _app_dir() -> Path:
    """Folder holding install.ini: next to the executable, else the repo
    root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return repo_root()


def containers_dir() -> Path:
    """The folder with the container build files: `containers` next to the exe
    when installed, else in the repository."""
    return _app_dir() / "containers"


def find_config(explicit: str | None = None) -> Path:
    """The settings file to use: the one named by --config, else MRRSIM_CONFIG,
    else the first mrrsim.toml found in the current folder, its config folder,
    or the program's folder. Raises FileNotFoundError listing where it looked.
    """
    if explicit:
        candidates = [Path(explicit)]
    elif os.environ.get("MRRSIM_CONFIG"):
        candidates = [Path(os.environ["MRRSIM_CONFIG"])]
    else:
        candidates = [
            Path.cwd() / "mrrsim.toml",
            Path.cwd() / "config" / "mrrsim.toml",
            _app_dir() / "config" / "mrrsim.toml",
            _app_dir() / "mrrsim.toml",
        ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    tried = "\n  ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        f"mrrsim.toml not found. Looked in:\n  {tried}\nUse --config PATH."
    )


# === USER'S FOLDERS ===========================================================


def documents_dir() -> Path:
    """The user's Documents folder, wherever Windows keeps it (it may be
    redirected, e.g. into OneDrive)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("a", wintypes.DWORD),
                    ("b", wintypes.WORD),
                    ("c", wintypes.WORD),
                    ("d", ctypes.c_ubyte * 8),
                ]

            # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
            guid = GUID(
                0xFDD39AD0,
                0x238F,
                0x46AF,
                (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
            )
            out = ctypes.c_wchar_p()
            if (
                ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(guid), 0, None, ctypes.byref(out)
                )
                == 0
            ):
                try:
                    return Path(out.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(out)
        except (OSError, AttributeError):
            pass
    return Path.home() / "Documents"


def work_dir() -> Path:
    """Where the window runs the launcher from, so ArduPilot's logs/ folder
    lands somewhere writable."""
    return documents_dir() / "MRRSimulator"


def user_scripts_dir() -> Path:
    """The student's own scripts. MRRSIM_USER_SCRIPTS overrides it (tests,
    unusual setups)."""
    override = os.environ.get("MRRSIM_USER_SCRIPTS")
    return Path(override) if override else work_dir() / "scripts"


def vehicles_dir() -> Path:
    """The student's vehicle designs (Project AirSim robot configs made in the
    Vehicles tab). MRRSIM_VEHICLES_DIR overrides it (tests, unusual setups)."""
    override = os.environ.get("MRRSIM_VEHICLES_DIR")
    return Path(override) if override else work_dir() / "vehicles"


def packages_dir() -> Path:
    """Extra Python packages the student installed with `mrrsim pip install`;
    mounted into every script run. MRRSIM_PACKAGES_DIR overrides it (tests,
    unusual setups)."""
    override = os.environ.get("MRRSIM_PACKAGES_DIR")
    return Path(override) if override else work_dir() / "packages"


# === DATA FOLDER ==============================================================


def _data_dir_from_install_ini() -> Path | None:
    """The Inno Setup installer writes DataDir (and AllUsers) to
    {app}\\install.ini."""
    ini = _app_dir() / "install.ini"
    if not ini.is_file():
        return None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(ini, encoding="utf-8")
    except configparser.Error:
        return None
    for section in parser.sections():
        value = parser.get(section, "datadir", fallback="").strip()
        if value:
            return Path(value)
    return None


def data_dir() -> Path:
    """Where the launcher keeps its own files (sessions, support bundles, the
    current-run state): MRRSIM_DATA_DIR, else the installer's DataDir from
    install.ini, else %LOCALAPPDATA%\\MRRSimulator."""
    override = os.environ.get("MRRSIM_DATA_DIR")
    if override:
        return Path(override)
    from_ini = _data_dir_from_install_ini()
    if from_ini:
        return from_ini
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / ".local" / "share") / "MRRSimulator"


def sessions_dir() -> Path:
    """The folder holding one sub-folder per run, each with that run's logs."""
    return data_dir() / "sessions"


def session_dir(session_id: str) -> Path:
    """The folder of one run, named by its timestamp id."""
    return sessions_dir() / session_id


def bundles_dir() -> Path:
    """Where `mrrsim bundle` writes its zip files."""
    return data_dir() / "bundles"


def state_file() -> Path:
    """The file describing the run in progress (its processes), read by `down`
    and `status`."""
    return data_dir() / "current.json"


def latest_session() -> Path | None:
    """The folder of the most recent run, or None if there has been none."""
    root = sessions_dir()
    if not root.is_dir():
        return None
    folders = sorted(p for p in root.iterdir() if p.is_dir())
    return folders[-1] if folders else None
