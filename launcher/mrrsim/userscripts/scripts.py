"""Finding and describing student scripts: what the `scripts` command and the window list.

A script is a top-level `*.py` file. Two folders are searched: the student's own
(Documents\\MRRSimulator\\scripts) and the examples that ship with the install. Nothing is run or
imported here; files are only parsed.

A script can say how many vehicles it wants in a comment near the top:

    # mrrsim: vehicles=2
"""
from __future__ import annotations

import ast
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from ..core import paths

MINE, EXAMPLES, PROJECT = "mine", "examples", "project"
_HEADER = re.compile(r"^\s*#\s*mrrsim:\s*(.+)$", re.I)
_PAIR = re.compile(r"(\w+)\s*=\s*([\w.-]+)")
_HEADER_LINES = 25  # how far down the file the header comment may be


@dataclass(frozen=True)
class ScriptInfo:
    name: str
    path: Path
    group: str                     # MINE, EXAMPLES or PROJECT
    description: str = ""
    needs_airsim: bool = False     # imports projectairsim, so it needs the image that has it
    vehicles: int | None = None    # from the "# mrrsim: vehicles=N" header
    problem: str = ""              # why it could not be parsed (it can still be run)
    project: Path | None = None    # the project folder this file belongs to (PROJECT group only)
    has_main: bool = True          # has an `if __name__ == "__main__":` block, so it is meant to be run
    imports: frozenset[str] = frozenset()  # top-level names of the third-party (non-standard-library) modules it imports


# What the light image (localhost/mrrsim-script:runtime) has besides the standard library. A script that imports
# anything else (cv2, numpy, a package installed with `mrrsim pip install`, ...) runs in the full image, which has
# all of these and more and is the one `mrrsim pip install` adds packages to.
LIGHT_IMAGE_MODULES = frozenset({"dronekit", "pymavlink", "monotonic", "future", "past", "lxml", "pkg_resources",
                                 "setuptools", "fastcrc"})


def _third_party_imports(tree: ast.AST) -> frozenset[str]:
    """Top-level names of the absolute imports that are not the standard library."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return frozenset(n for n in names if n not in sys.stdlib_module_names)


def local_names(folder: Path) -> frozenset[str]:
    """Names a script in `folder` can import from its own folder: its .py files and sub-folders."""
    try:
        return frozenset(p.stem if p.is_file() else p.name for p in folder.iterdir()
                         if p.is_dir() or p.suffix == ".py")
    except OSError:
        return frozenset()


def needs_full_image(imports: frozenset[str], local: frozenset[str] = frozenset()) -> frozenset[str]:
    """The imports (minus the script's own modules) that the light image cannot provide; empty = the light image will do."""
    return imports - LIGHT_IMAGE_MODULES - local


def _imports_projectairsim(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "projectairsim" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "projectairsim":
            return True
    return False


def _header_options(text: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for line in text.splitlines()[:_HEADER_LINES]:
        match = _HEADER.match(line)
        if match:
            options.update({k.lower(): v for k, v in _PAIR.findall(match.group(1))})
    return options


def _has_main_guard(tree: ast.AST) -> bool:
    """True for a top-level `if __name__ == "__main__":`."""
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            names = [node.test.left, *node.test.comparators]
            if any(isinstance(n, ast.Name) and n.id == "__name__" for n in names) and any(
                    isinstance(n, ast.Constant) and n.value == "__main__" for n in names):
                return True
    return False


def describe(path: Path, group: str = MINE) -> ScriptInfo:
    """Read one script. A script that does not parse is still listed; it will fail when it is run."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ScriptInfo(path.name, path, group, problem=str(exc))
    vehicles = None
    raw = _header_options(text).get("vehicles", "")
    if raw.isdigit() and int(raw) > 0:
        vehicles = int(raw)
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        # fall back to a plain text search so a typo does not hide that the script needs Project AirSim
        needs = bool(re.search(r"^\s*(from|import)\s+projectairsim\b", text, re.M))
        guess = frozenset(m for m in re.findall(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", text, re.M)
                          if m not in sys.stdlib_module_names)
        return ScriptInfo(path.name, path, group, needs_airsim=needs, vehicles=vehicles, imports=guess,
                          problem=f"syntax error on line {exc.lineno}",
                          has_main=bool(re.search(r"^if\s+__name__\s*==", text, re.M)))
    paragraph = (ast.get_docstring(tree) or "").strip().split("\n\n")[0]  # the first paragraph, as one line
    return ScriptInfo(path.name, path, group, description=" ".join(paragraph.split()),
                      needs_airsim=_imports_projectairsim(tree), vehicles=vehicles,
                      has_main=_has_main_guard(tree), imports=_third_party_imports(tree))


def discover(user_dir: Path | None, example_dir: Path | None) -> list[ScriptInfo]:
    """The student's scripts first, then the examples, each group sorted by name."""
    found: list[ScriptInfo] = []
    for folder, group in ((user_dir, MINE), (example_dir, EXAMPLES)):
        if folder is None or not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.py"), key=lambda p: p.name.lower()):
            if not path.name.startswith("_") and path.is_file():
                found.append(describe(path, group))
    return found


def args_file() -> Path:
    """Where the window remembers the arguments typed for each script."""
    override = os.environ.get("MRRSIM_SCRIPT_ARGS_FILE")
    return Path(override) if override else paths.work_dir() / "script_args.json"


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def load_args(path: Path) -> str:
    """The arguments last used with this script ("" if none, or the file is missing or damaged)."""
    try:
        data = json.loads(args_file().read_text(encoding="utf-8-sig"))
        value = data.get(_key(path), "") if isinstance(data, dict) else ""
    except (OSError, ValueError):
        return ""
    return value if isinstance(value, str) else ""


def save_args(path: Path, text: str) -> None:
    """Remember the arguments for this script; empty text forgets them."""
    try:
        data = json.loads(args_file().read_text(encoding="utf-8-sig"))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    if text.strip():
        data[_key(path)] = text.strip()
    else:
        data.pop(_key(path), None)
    try:
        args_file().parent.mkdir(parents=True, exist_ok=True)
        args_file().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # not being able to remember is no reason to stop a run


def split_args(text: str) -> list[str]:
    """What the student typed, split like a command line: `-s --mode "two words"`. Raises ValueError for a
    quote that is never closed."""
    return shlex.split(text, posix=True)


def copy_to(example: ScriptInfo, user_dir: Path) -> Path:
    """Copy an example into the student's folder, never overwriting: foo.py, foo_copy.py, foo_copy2.py ..."""
    user_dir.mkdir(parents=True, exist_ok=True)
    stem, suffix = example.path.stem, example.path.suffix
    target = user_dir / example.path.name
    n = 0
    while target.exists():
        n += 1
        target = user_dir / f"{stem}_copy{'' if n == 1 else n}{suffix}"
    target.write_bytes(example.path.read_bytes())
    return target
