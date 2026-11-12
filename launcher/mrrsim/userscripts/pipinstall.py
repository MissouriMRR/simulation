"""`mrrsim pip install NAME...`: add Python packages the script images do not have.

pip runs inside the script image (so the packages fit its Python and its CPU) with network access, and puts
what it installs into one folder, Documents\\MRRSimulator\\packages. That folder is mounted into every script
run as /packages and is on PYTHONPATH, so a script finds the packages with no rebuild of any image, and the
packages keep working offline afterwards.

Only the packages the image (and the folder) does not already have are added: the install is first resolved
against the packages the image and the folder already hold (`pip install --dry-run --report`), and only what that
says is missing is downloaded.

What this cannot do: packages that need a system library (for example pyaudio needs portaudio; pyzbar's libzbar0
is already in the full image). They install, but fail to import; the install ends with an import test of each
requested package that says so. Those need the library added to the image (containers/script/Containerfile) and the
images rebuilt.
"""
from __future__ import annotations

import queue
import re
import shutil
import subprocess
import sys
import threading
from typing import Iterator

from ..core import config, paths
from ..runtime import wsl
from ..runtime.podman import LABEL, Mount, Podman

CONTAINER_NAME = "mrrsim-pip"
DRIVER_NAME = ".mrrsim-pip.py"

# One requirement: a name, optional extras, optional version limits. Deliberately no URLs, paths or options
# (nothing that starts with a dash): a package name typed into a window must not be able to change pip's behavior.
_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._\-]*[A-Za-z0-9])?"
_VERSION = r"[<>=!~]=?\s*[A-Za-z0-9.*+!_\-]+"
SPEC = re.compile(rf"{_NAME}(?:\[[A-Za-z0-9,._\-]+\])?(?:\s*{_VERSION}(?:\s*,\s*{_VERSION})*)?")
NAME = re.compile(_NAME)

# Runs inside the container (python reads it from /packages). Kept as text so nothing has to be packaged with
# the launcher; it only uses the standard library and the pip of the image. MRRSIM_PACKAGES names the packages
# folder (the tests point it at a temporary one); in the container it is /packages.
DRIVER = r'''
import csv, json, os, pathlib, re, shutil, subprocess, sys, tempfile

TARGET = pathlib.Path(os.environ.get("MRRSIM_PACKAGES", "/packages"))

# Imports a package and its direct submodules. A submodule that needs another Python package (an optional
# feature) is skipped; any other error is reported: for example pyzbar.pyzbar fails here, not in pyzbar itself,
# when the ZBar system library is missing.
CHECK = """
import importlib, pkgutil, sys
name = sys.argv[1]
package = importlib.import_module(name)
for info in list(pkgutil.iter_modules(getattr(package, "__path__", [])))[:40]:
    if info.name.startswith(("_", "test")):
        continue
    try:
        importlib.import_module(name + "." + info.name)
    except ModuleNotFoundError:
        pass
    except BaseException as exc:
        if any(word in str(exc).lower() for word in ("tkinter", "libtk", "libtcl")):
            continue  # graphical toolkits are not in the image and a simulator script has no screen anyway
        print("BAD " + name + "." + info.name + " fails: " + type(exc).__name__ + ": " + str(exc).splitlines()[0])
"""


def pip(*args, capture=False, env=None):
    return subprocess.run([sys.executable, "-m", "pip", "--disable-pip-version-check", *args],
                          capture_output=capture, text=True, env=env)


def norm(name):
    return re.sub(r"[-_.]+", "_", name).lower()


def installed():
    found = {}
    for info in sorted(TARGET.glob("*.dist-info")):
        name, _, version = info.name[: -len(".dist-info")].rpartition("-")
        found[norm(name)] = (name, version, info)
    return found


def modules(info):
    top = info / "top_level.txt"
    if top.is_file():
        names = top.read_text(encoding="utf-8").split()
    else:
        names = []
        record = info / "RECORD"
        if record.is_file():
            for row in csv.reader(record.read_text(encoding="utf-8").splitlines()):
                part = row[0].split("/")[0] if row else ""
                if part and not part.endswith(".dist-info") and not part.startswith(("..", "__pycache__", "bin")):
                    names.append(part[:-3] if part.endswith(".py") else part)
    return sorted({n for n in names if n and not n.startswith("_") and not n.endswith(".libs")})


def install(specs):
    TARGET.mkdir(exist_ok=True)
    report = pathlib.Path(tempfile.gettempdir()) / "mrrsim-pip-report.json"
    print("Working out what the image is missing...", flush=True)
    # the folder is on the path of this one run, so what it already holds counts as installed and is not downloaded again
    seen = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(TARGET), os.environ.get("PYTHONPATH", "")])))
    result = pip("install", "--dry-run", "--quiet", "--report", str(report), *specs, capture=True, env=seen)
    if result.returncode:
        print((result.stderr or result.stdout).strip())
        print("Nothing was installed.")
        return 1
    todo = [f"{i['metadata']['name']}=={i['metadata']['version']}" for i in json.loads(report.read_text())["install"]]
    if todo:
        print("Installing: " + ", ".join(todo), flush=True)
        # --no-deps: the list above already holds every dependency that is missing
        if pip("install", "--target", str(TARGET), "--no-deps", "--upgrade", *todo).returncode:
            print("The installation failed.")
            return 1
    else:
        print("Everything asked for is already in the image or in your packages: nothing to install.")
    have = installed()
    problems = 0
    for spec in specs:
        name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", spec).group(0)
        entry = have.get(norm(name))
        if entry is None:
            print(f"[ OK ] {name} is part of the image already")
            continue
        for module in modules(entry[2]):
            test = subprocess.run([sys.executable, "-W", "ignore", "-c", CHECK, module], capture_output=True, text=True,
                                  env=dict(os.environ, PYTHONPATH=str(TARGET)), timeout=120)
            failures = [line[4:] for line in test.stdout.splitlines() if line.startswith("BAD ")]
            if test.returncode:
                failures.append(module + ": " + (test.stderr.strip().splitlines() or ["no message"])[-1])
            if not failures:
                print(f"[ OK ] import {module} works ({entry[0]} {entry[1]})")
            for failure in failures:
                problems += 1
                print(f"[WARN] import {failure}")
    if problems:
        print("The package is installed but does not import. If the message names a missing library or program, it "
              "needs a system library that the image does not have: ask whoever maintains the images to add it "
              "(containers/script/Containerfile) and rebuild them.")
    return 0


def listing():
    have = installed()
    if not have:
        print("No extra packages are installed.")
    for name, version, _ in have.values():
        print(f"{name} {version}")
    return 0


def remove(names):
    """Delete the files a package lists in its RECORD, then its dist-info, then the folders that became empty.
    pip cannot uninstall from a --target folder, so this does it by hand; it never touches anything outside it."""
    have = installed()
    root = TARGET.resolve()
    for name in names:
        entry = have.get(norm(name))
        if entry is None:
            print(f"{name} is not installed")
            continue
        info = entry[2]
        record = info / "RECORD"
        listed = [row[0] for row in csv.reader(record.read_text(encoding="utf-8").splitlines()) if row] if record.is_file() else []
        gone = 0
        for rel in listed:
            target = (TARGET / rel).resolve()
            if root in target.parents and (target.is_file() or target.is_symlink()):
                target.unlink()
                gone += 1
        shutil.rmtree(info, ignore_errors=True)
        for folder in sorted((p for p in TARGET.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            if not any(folder.iterdir()):
                folder.rmdir()
        print(f"Removed {entry[0]} {entry[1]} ({gone} files)")
    return 0


action, specs = sys.argv[1], sys.argv[2:]
sys.exit({"install": install, "remove": remove}.get(action, lambda _: listing())(specs))
'''


def _validated(items: list[str], pattern: re.Pattern, message: str) -> list[str]:
    """`items` without the empty ones; ValueError, saying `message` with {item} the first one that does not match
    `pattern`, or saying that no name was given."""
    clean = [i.strip() for i in items if i.strip()]
    if not clean:
        raise ValueError("no package name given")
    for item in clean:
        if not pattern.fullmatch(item):
            raise ValueError(message.format(item=repr(item)))
    return clean


def validate(specs: list[str]) -> list[str]:
    """The requirements, cleaned; ValueError names the first one that is not a plain package requirement."""
    return _validated(specs, SPEC, "{item} is not a package name (use a name like pyzbar or requests==2.32.3; "
                                   "web addresses, file paths and pip options are not accepted)")


def validate_names(names: list[str]) -> list[str]:
    """Package names only (no versions), for `remove`."""
    return _validated(names, NAME, "{item} is not a package name")


def split_names(text: str) -> list[str]:
    """What was typed in the window: package names separated by spaces (a version goes right after the name:
    requests==2.32.3)."""
    return [p for p in re.split(r"\s+", text.strip()) if p]


def has_packages() -> bool:
    """Has anything besides the helper script been installed into the packages folder?"""
    folder = paths.packages_dir()
    return folder.is_dir() and any(p.name != DRIVER_NAME for p in folder.iterdir())


def reset() -> int:
    """Delete the packages folder (exactly that folder) and everything installed into it."""
    folder = paths.packages_dir()
    if folder.is_dir():
        shutil.rmtree(folder)
        print(f"removed {folder}")
    else:
        print("no extra packages are installed")
    return 0


def _lines(proc: subprocess.Popen) -> Iterator[str]:
    """The lines the process prints, as they come. A thread of its own reads them and they are waited for in short
    steps, so Ctrl+C (and the window's Stop) gets through while the process prints nothing: on Windows a blocking
    read is not interrupted until the process ends."""
    lines: queue.Queue[str | None] = queue.Queue()

    def read() -> None:
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=read, name="pip-output", daemon=True).start()
    while True:
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            continue
        if line is None:
            return
        yield line


def run(cfg: config.Config, action: str, specs: list[str] | None = None, dry_run: bool = False) -> int:
    """Run the pip driver in the script image. Returns its exit code."""
    folder = paths.packages_dir()
    folder.mkdir(parents=True, exist_ok=True)
    driver = folder / DRIVER_NAME
    driver.write_text(DRIVER, encoding="utf-8", newline="\n")
    podman = Podman(cfg.wsl.distro, init=cfg.wsl.use_init, dry_run=dry_run)
    try:
        proc = podman.run_attached(
            cfg.scene.image, CONTAINER_NAME,  # the full image: what it already has is not downloaded again
            command=["python", "-u", f"/packages/{DRIVER_NAME}", action, *(specs or [])],
            env={"PIP_NO_CACHE_DIR": "1", "PIP_PROGRESS_BAR": "off"}, labels={LABEL: "1"},
            mounts=[Mount(wsl.to_wsl_path(folder, cfg.wsl.distro), "/packages", "rw")],
        )
        if proc is None:  # dry run
            return 0
        try:
            for line in _lines(proc):
                print(line.rstrip(), flush=True)
            return proc.wait()
        except KeyboardInterrupt:
            podman.stop([CONTAINER_NAME], timeout=5)
            return 130
    finally:
        driver.unlink(missing_ok=True)


def cli(cfg: config.Config, action: str, names: list[str], dry_run: bool = False) -> int:
    """`mrrsim pip install|remove|list|reset`. Returns the exit code: 2 when a name is not accepted (nothing is
    started then), else what the pip run returned."""
    if action == "reset":
        return reset()
    checks = {"install": validate, "remove": validate_names}
    if action not in checks:
        return run(cfg, "list", None, dry_run)
    try:
        specs = checks[action](names)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return run(cfg, action, specs, dry_run)
