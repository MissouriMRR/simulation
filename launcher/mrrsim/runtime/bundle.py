"""`mrrsim bundle`: one zip with everything a maintainer needs to diagnose a problem."""
from __future__ import annotations

import time
import zipfile
from pathlib import Path
from typing import Callable

from ..core import paths
from ..core.config import Config
from . import doctor, wsl
from .podman import Podman

_MAX_FILE = 50 * 1024 * 1024
_README = """MRRSimulator support bundle

doctor.txt      preflight report
system/         WSL and podman versions, containers, images
config/         mrrsim.toml, .wslconfig, current.json
sessions/       logs of the most recent session (launcher, unreal, scene, sitl-N, script)
unreal/         Project AirSim server log and Unreal logs, if present

This bundle can contain your Windows user name and folder paths. Nothing is uploaded; send it
to the maintainers yourself.
"""


def _capture(fn: Callable[[], str]) -> str:
    try:
        return fn()
    except Exception as exc:  # a failing probe must not abort the whole bundle
        return f"(failed: {exc})"


def _wsl_text(args: list[str]) -> str:
    result = wsl.wsl_command(args)
    return (result.stdout + result.stderr).strip()


def _podman_text(podman: Podman, args: list[str]) -> str:
    result = podman.run(args, timeout=60, check=False)
    return (result.stdout + result.stderr).strip()


def _add_file(bundle: zipfile.ZipFile, path: Path, arcname: str) -> None:
    try:
        if path.is_file() and path.stat().st_size <= _MAX_FILE:
            bundle.write(path, arcname)
    except OSError:
        pass


def _add_tree(bundle: zipfile.ZipFile, root: Path, prefix: str) -> None:
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            _add_file(bundle, path, f"{prefix}/{path.relative_to(root).as_posix()}")


def create_bundle(cfg: Config) -> Path:
    out = paths.bundles_dir() / f"mrrsim-support-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    podman = Podman(cfg.wsl.distro, init=cfg.wsl.use_init)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("README.txt", _README)
        bundle.writestr("doctor.txt", _capture(lambda: doctor.format_report(doctor.run_checks(cfg))))
        bundle.writestr("system/wsl-status.txt", _capture(lambda: _wsl_text(["--status"])))
        bundle.writestr("system/wsl-distros.txt", _capture(lambda: _wsl_text(["-l", "-v"])))
        bundle.writestr("system/podman-version.txt", _capture(lambda: _podman_text(podman, ["version"])))
        bundle.writestr("system/podman-info.txt", _capture(lambda: _podman_text(podman, ["info"])))
        bundle.writestr("system/podman-ps.txt", _capture(lambda: _podman_text(podman, ["ps", "-a"])))
        bundle.writestr("system/podman-images.txt", _capture(lambda: _podman_text(podman, ["images"])))

        _add_file(bundle, cfg.source, "config/mrrsim.toml")
        _add_file(bundle, wsl.wslconfig_path(), "config/wslconfig.txt")
        _add_file(bundle, paths.state_file(), "config/current.json")

        latest = paths.latest_session()
        if latest is not None:
            _add_tree(bundle, latest, f"sessions/{latest.name}")

        exe = cfg.unreal.exe
        if len(exe.parents) > 2:
            sim_dir = exe.parents[2]
            _add_file(bundle, sim_dir / "projectairsim_server.log", "unreal/projectairsim_server.log")
            _add_tree(bundle, sim_dir / "Saved" / "Logs", "unreal/Saved-Logs")
    return out
