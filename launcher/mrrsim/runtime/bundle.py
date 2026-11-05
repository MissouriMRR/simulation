"""`mrrsim bundle`: one zip with everything a maintainer needs to diagnose a
problem."""

from __future__ import annotations

import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from ..core import paths
from ..core.config import Config
from . import doctor, wsl
from .podman import Podman

# === CONSTANTS ================================================================


_MAX_FILE = 50 * 1024 * 1024
_README = (
    "MRRSimulator support bundle\n"
    "\n"
    "doctor.txt      preflight report\n"
    "system/         WSL and podman versions, containers, images\n"
    "config/         mrrsim.toml, .wslconfig, current.json\n"
    "sessions/       logs of the most recent session (launcher, unreal, scene, "
    "sitl-N, script)\n"
    "unreal/         Project AirSim server log and Unreal logs, if present\n"
    "omitted.txt     files left out because they were too big or unreadable "
    "(only if any)\n"
    "\n"
    "This bundle can contain your Windows user name and folder paths. "
    "Nothing is uploaded; send it\n"
    "to the maintainers yourself.\n"
)


# === PROBES ===================================================================


def _capture(fn: Callable[[], str]) -> str:
    """What `fn` returns, or a line saying that it failed: one probe that
    goes wrong must not stop the rest of the bundle."""
    try:
        return fn()
    except Exception as exc:  # a failing probe must not abort the whole bundle
        return f"(failed: {exc})"


def _wsl_text(args: list[str]) -> str:
    """What `wsl.exe <args>` printed, standard output and errors together."""
    result = wsl.wsl_command(args)
    return (result.stdout + result.stderr).strip()


def _podman_text(podman: Podman, args: list[str]) -> str:
    """What `podman <args>` printed, standard output and errors together."""
    result = podman.run(args, timeout=60, check=False)
    return (result.stdout + result.stderr).strip()


# === ADDING FILES =============================================================


def _add_file(
    bundle: zipfile.ZipFile, path: Path, arcname: str, omitted: list[str]
) -> None:
    """Put the file in the zip as `arcname`. A file that is not there is simply
    not there; one that is bigger than _MAX_FILE or cannot be read is left out
    and added to `omitted`, with the reason."""
    try:
        if not path.is_file():
            return
        size = path.stat().st_size
        if size > _MAX_FILE:
            omitted.append(
                f"{arcname}: {size / 1024**2:.0f} MB is over the "
                f"{_MAX_FILE // 1024**2} MB limit"
            )
            return
        bundle.write(path, arcname)
    except OSError as exc:
        omitted.append(f"{arcname}: could not be read ({exc})")


def _add_tree(
    bundle: zipfile.ZipFile, root: Path, prefix: str, omitted: list[str]
) -> None:
    """Put every file under `root` in the zip, below `prefix`."""
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            name = f"{prefix}/{path.relative_to(root).as_posix()}"
            _add_file(bundle, path, name, omitted)


# === THE BUNDLE ===============================================================


def create_bundle(cfg: Config, say: Callable[[str], None] | None = None) -> Path:
    """Write the support zip (the preflight report, WSL and podman details,
    the settings, and the logs of the latest run) and return its path. The
    probes are asked at the same time, and `say` is told what is going on,
    because a slow WSL can keep them waiting for a minute."""
    say = say or (lambda text: None)
    out = paths.bundles_dir() / f"mrrsim-support-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    podman = Podman(cfg.wsl.distro, init=cfg.wsl.use_init)
    probes = {
        "doctor.txt": lambda: doctor.format_report(doctor.run_checks(cfg)),
        "system/wsl-status.txt": lambda: _wsl_text(["--status"]),
        "system/wsl-distros.txt": lambda: _wsl_text(["-l", "-v"]),
        "system/podman-version.txt": lambda: _podman_text(podman, ["version"]),
        "system/podman-info.txt": lambda: _podman_text(podman, ["info"]),
        "system/podman-ps.txt": lambda: _podman_text(podman, ["ps", "-a"]),
        "system/podman-images.txt": lambda: _podman_text(podman, ["images"]),
    }
    say("asking WSL, podman and the preflight checks (a slow WSL can take a minute)")
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        asked = {name: pool.submit(_capture, probe) for name, probe in probes.items()}
        answers = {name: future.result() for name, future in asked.items()}

    say("adding the settings and the logs")
    omitted: list[str] = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("README.txt", _README)
        for name, text in answers.items():
            bundle.writestr(name, text)

        _add_file(bundle, cfg.source, "config/mrrsim.toml", omitted)
        _add_file(bundle, wsl.wslconfig_path(), "config/wslconfig.txt", omitted)
        _add_file(bundle, paths.state_file(), "config/current.json", omitted)

        latest = paths.latest_session()
        if latest is not None:
            _add_tree(bundle, latest, f"sessions/{latest.name}", omitted)

        exe = cfg.unreal.exe
        if len(exe.parents) > 2:
            sim_dir = exe.parents[2]
            _add_file(
                bundle,
                sim_dir / "projectairsim_server.log",
                "unreal/projectairsim_server.log",
                omitted,
            )
            _add_tree(bundle, sim_dir / "Saved" / "Logs", "unreal/Saved-Logs", omitted)
        if omitted:
            bundle.writestr(
                "omitted.txt", "Left out of this bundle:\n" + "\n".join(omitted) + "\n"
            )
    return out
