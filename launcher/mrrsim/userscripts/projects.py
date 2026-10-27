"""Project folders: a student's existing codebase, run from the window without copying anything.

A project is a folder the student added in the window. Every runnable `.py` file inside it is listed,
at any depth. When one is run, the whole project folder is what the script container sees (read-only,
as /work, and on the import path), so `import mylib` finds the project's own modules and packages
wherever the entry file sits. Nothing is run or imported here; files are only parsed.

The list of project folders is remembered in Documents\\MRRSimulator\\projects.json.
"""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..core import paths
from . import scripts

MAX_FILES = 3000            # a folder with more Python files than this is not a flight codebase (probably a virtual environment)
MAX_FILE_BYTES = 1_000_000  # bigger files are not parsed
SKIP_DIRS = {"venv", "env", "site-packages", "node_modules", "build", "dist", "htmlcov"}
NOT_ENTRIES = {"setup.py", "conftest.py"}


def store_file() -> Path:
    override = os.environ.get("MRRSIM_PROJECTS_FILE")
    return Path(override) if override else paths.work_dir() / "projects.json"


def load() -> list[Path]:
    """The remembered project folders, in the order they were added. A damaged file counts as empty."""
    try:
        data = json.loads(store_file().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    items = data.get("projects", []) if isinstance(data, dict) else []
    return [Path(p) for p in items if isinstance(p, str) and p.strip()]


def _save(folders: list[Path]) -> None:
    target = store_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"projects": [str(p) for p in folders]}, indent=2) + "\n", encoding="utf-8")


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def add(folder: Path) -> Path:
    """Remember a project folder (once). Returns the folder as stored."""
    folder = Path(os.path.abspath(folder))
    if not folder.is_dir():
        raise NotADirectoryError(f"{folder} is not a folder")
    known = load()
    if not any(_same(folder, k) for k in known):
        _save(known + [folder])
    return folder


def remove(folder: Path) -> None:
    _save([k for k in load() if not _same(k, folder)])


def contains(root: Path, path: Path) -> bool:
    """Is `path` inside `root`?"""
    try:
        Path(os.path.abspath(path)).relative_to(os.path.abspath(root))
    except ValueError:
        return False
    return True


@dataclass
class Scan:
    root: Path
    entries: list[scripts.ScriptInfo]    # the files worth offering to run
    needs_airsim: bool = False           # any file in the project imports projectairsim
    full_image_for: frozenset[str] = frozenset()  # imports of the project (not its own modules) that only the full image has
    files: int = 0                       # Python files looked at
    truncated: bool = False              # stopped at MAX_FILES


def _skipped(name: str, folder: Path) -> bool:
    return (name.startswith(".") or name.startswith("__") or name.lower() in SKIP_DIRS
            or name.endswith(".egg-info") or (folder / name / "pyvenv.cfg").is_file())


def relative_name(root: Path, path: Path) -> str:
    return Path(os.path.relpath(path, root)).as_posix()


def scan(root: Path) -> Scan:
    """Look through a project folder. Entry files are the ones with an `if __name__ == "__main__":` block,
    plus every top-level file (a script that just runs from top to bottom); when a project has neither,
    all of its files are offered, so something can always be picked."""
    root = Path(os.path.abspath(root))
    result = Scan(root, [])
    everything: list[scripts.ScriptInfo] = []
    imports: set[str] = set()
    own: set[str] = set()  # names the project's own files can import from each other
    for folder, dirs, files in os.walk(root):
        here = Path(folder)
        own.update(d for d in dirs if not _skipped(d, here))
        dirs[:] = sorted((d for d in dirs if not _skipped(d, here)), key=str.lower)
        for name in sorted(files, key=str.lower):
            path = here / name
            if not name.endswith(".py"):
                continue
            if result.files >= MAX_FILES:
                result.truncated = True
                dirs[:] = []
                break
            result.files += 1
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            info = scripts.describe(path, scripts.PROJECT)
            result.needs_airsim = result.needs_airsim or info.needs_airsim
            imports |= info.imports
            own.add(path.stem)
            everything.append(dataclasses.replace(info, name=relative_name(root, path), project=root))
    result.full_image_for = scripts.needs_full_image(frozenset(imports), frozenset(own))
    candidates = [i for i in everything if not Path(i.name).name.startswith("_") and Path(i.name).name not in NOT_ENTRIES]
    entries = [i for i in candidates if i.has_main or "/" not in i.name]
    result.entries = entries or candidates
    # every entry runs in the image its project needs: one file importing projectairsim (or cv2, numpy ...) is enough
    result.entries = [dataclasses.replace(i, needs_airsim=result.needs_airsim) for i in result.entries]
    return result
