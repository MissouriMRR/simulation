"""Preflight checks ("mrrsim doctor"). Read-only: nothing here starts or changes anything."""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

from ..core import paths, ports
from ..core.config import Config
from ..scene import scenecheck
from . import wsl
from .podman import Podman
from .winproc import IS_WINDOWS, total_ram_gb

OK, WARN, FAIL = "ok", "warn", "fail"

# The installer's thresholds (MB): block below MIN, warn below WARN.
MIN_RAM_MB = 7000
WARN_RAM_MB = 15000
# Container images are large (see containers/README.md) and WSL keeps them in a growing VHDX.
WARN_FREE_GB = 10
MIN_FREE_GB = 3

_DISCRETE_GPU = re.compile(r"NVIDIA|Radeon RX|Radeon Pro|Arc\(TM\) A\d", re.I)
_VIRTUAL_GPU = re.compile(r"Parsec|Remote Display|Virtual|Hyper-V|Citrix|VMware", re.I)


@dataclass
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""


def run_checks(
    cfg: Config,
    *,
    unreal: bool = True,
    scene: bool = True,
    script: bool = True,
    podman: Podman | None = None,
) -> list[Check]:
    checks = [_windows(), _ram(), _gpu(), _disk()]
    wsl_checks, wsl_ready = _wsl(cfg)
    checks += wsl_checks
    if wsl_ready:
        podman = podman or Podman(cfg.wsl.distro, init=cfg.wsl.use_init)
        checks += _podman(cfg, podman, scene=scene and bool(cfg.scene.scene), script=script)
    if unreal:
        checks += _unreal(cfg)
    if scene:
        checks += _scene(cfg)
    if script:
        checks += _script(cfg)
    checks.append(_ports(cfg, unreal))
    return checks


def format_report(checks: list[Check]) -> str:
    marks = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
    lines: list[str] = []
    for check in checks:
        lines.append(f"{marks[check.status]} {check.name}: {check.detail}")
        if check.hint and check.status != OK:
            lines.append(f"         -> {check.hint}")
    failed = sum(c.status == FAIL for c in checks)
    warned = sum(c.status == WARN for c in checks)
    lines.append(f"\n{len(checks)} checks: {failed} failed, {warned} warnings")
    return "\n".join(lines)


# ---------------------------------------------------------------------- host checks
def _windows() -> Check:
    if not IS_WINDOWS:
        return Check("Windows", WARN, f"running on {sys.platform}; the Unreal build is Windows-only")
    version = platform.version()  # e.g. 10.0.26300
    try:
        build = int(version.split(".")[2])
    except (IndexError, ValueError):
        return Check("Windows", WARN, f"could not parse version {version!r}")
    if build < 19041:
        return Check("Windows", FAIL, f"build {build}; WSL2 needs Windows 10 2004 (19041) or newer")
    return Check("Windows", OK, f"build {build}")


def _ram() -> Check:
    gb = total_ram_gb()
    if gb is None:
        return Check("RAM", WARN, "could not read installed memory")
    mb = gb * 1024
    detail = f"{gb:.1f} GB installed"
    if mb < MIN_RAM_MB:
        return Check("RAM", FAIL, detail, "8 GB is the minimum; an integrated GPU also borrows from this")
    if mb < WARN_RAM_MB:
        return Check("RAM", WARN, detail, "16 GB recommended: Unreal + the WSL VM + the GPU all share it")
    return Check("RAM", OK, detail)


def _gpu() -> Check:
    if not IS_WINDOWS:
        return Check("GPU", WARN, "not checked off Windows")
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return Check("GPU", WARN, "could not query video controllers")
    names = [n.strip() for n in out.splitlines() if n.strip() and not _VIRTUAL_GPU.search(n)]
    if not names:
        return Check("GPU", WARN, "no physical video controller found")
    detail = ", ".join(names)
    if any("Basic Display" in n for n in names):
        return Check("GPU", FAIL, detail, "Microsoft Basic Display Adapter has no 3D acceleration; install the GPU driver")
    if not any(_DISCRETE_GPU.search(n) for n in names):
        return Check("GPU", WARN, detail, "integrated GPU only: expect low FPS; plan to use the reduced graphics profile")
    return Check("GPU", OK, detail)


def _disk() -> Check:
    target = paths.data_dir()
    while not target.exists() and target != target.parent:
        target = target.parent
    free_gb = shutil.disk_usage(target).free / 1024**3
    detail = f"{free_gb:.1f} GB free on {target.drive or target}"
    if free_gb < MIN_FREE_GB:
        return Check("Disk", FAIL, detail, "container images need several GB")
    if free_gb < WARN_FREE_GB:
        return Check("Disk", WARN, detail, f"{WARN_FREE_GB}+ GB recommended for the container images")
    return Check("Disk", OK, detail)


# ---------------------------------------------------------------------- WSL / podman
def _wsl(cfg: Config) -> tuple[list[Check], bool]:
    checks: list[Check] = []
    distros = {d.name: d for d in wsl.list_distros()}
    distro = distros.get(cfg.wsl.distro)
    ready = False
    if distro is None:
        known = ", ".join(distros) or "none found"
        checks.append(Check(
            f"WSL distro '{cfg.wsl.distro}'", FAIL, f"not installed (installed: {known})",
            f"run the {cfg.wsl.distro!r} setup again: the MRRSimulator installer adds this distribution. "
            "(Or set [wsl] distro in mrrsim.toml to another distribution that has podman and the images.)",
        ))
    elif distro.version != 2:
        checks.append(Check(f"WSL distro '{cfg.wsl.distro}'", FAIL, f"WSL version {distro.version}",
                            f"convert it with: wsl --set-version {cfg.wsl.distro} 2"))
    else:
        checks.append(Check(f"WSL distro '{cfg.wsl.distro}'", OK, f"WSL2, {distro.state}"))
        ready = True

    if ready:
        checks += _wsl_resources(cfg)

    mode = wsl.networking_mode()
    if mode == "mirrored" and ready:
        checks.append(_effective_networking(cfg))
    elif mode == "mirrored":
        checks.append(Check("WSL networking", OK, "mirrored in .wslconfig (not checked: no usable distro)"))
    else:
        status = FAIL if cfg.wsl.require_mirrored else WARN
        checks.append(Check(
            "WSL networking", status, f"networkingMode={mode}",
            f"add 'networkingMode=mirrored' under [wsl2] in {wsl.wslconfig_path()}, then run: wsl --shutdown "
            "(needs Windows 11 22H2 or newer)",
        ))
    return checks, ready


def _effective_networking(cfg: Config) -> Check:
    """What the running WSL VM actually got. .wslconfig only says what was asked for: when mirrored
    networking cannot start, WSL silently falls back to 'none' and the containers cannot reach Unreal."""
    mode, problem = wsl.effective_networking_mode(cfg.wsl.distro)
    if mode == "mirrored":
        return Check("WSL networking", OK, "mirrored (Windows and WSL share 127.0.0.1)")
    if mode is None:
        return Check("WSL networking", OK, "mirrored in .wslconfig (this WSL cannot report its effective mode)")
    detail = f".wslconfig asks for mirrored, but the WSL VM is running with networkingMode={mode}"
    if problem:
        detail += f" ({problem})"
    return Check(
        "WSL networking", FAIL, detail,
        "restart Windows; if it persists, in an administrator PowerShell run: Restart-Service hns ; wsl --shutdown. "
        "A new VPN or virtual network adapter is a common cause",
    )


def _wsl_resources(cfg: Config) -> list[Check]:
    """What the running WSL VM actually has, against the [wsl] caps in mrrsim.toml."""
    result = wsl.run(cfg.wsl.distro, ["sh", "-c", "nproc; awk '/MemTotal/ {print $2}' /proc/meminfo"], timeout=30)
    try:
        procs, mem_kb = (int(v) for v in result.stdout.split()[:2])
    except ValueError:
        return [Check("WSL resources", WARN, "could not read the VM's CPU count and memory")]
    detail = f"{procs} CPU(s), {mem_kb / 1024 / 1024:.1f} GB in the VM"
    want_procs = wsl.effective_processors(cfg.wsl.processors)
    want_mb = cfg.wsl.memory_mb
    if not want_procs and not want_mb:
        return [Check("WSL resources", OK, f"{detail} (no caps set in [wsl])")]
    # MemTotal is a little below the configured memory; allow 10% for the kernel's share.
    too_many = bool(want_procs) and procs > want_procs
    too_big = bool(want_mb) and mem_kb / 1024 > want_mb * 1.1
    if not (too_many or too_big):
        return [Check("WSL resources", OK, f"{detail} (caps: {cfg.wsl.memory or 'default'}, {want_procs or 'all'} CPUs)")]
    configured = (
        (not want_procs or wsl.wsl2_setting("processors") == str(want_procs))
        and (not want_mb or (wsl.wsl2_setting("memory") or "").upper() == cfg.wsl.memory.upper())
    )
    hint = (
        "the caps are in .wslconfig but the VM has not restarted: run  wsl --shutdown"
        if configured else
        "write the caps with  mrrsim wsl-config  then run  wsl --shutdown"
    )
    return [Check("WSL resources", WARN, f"{detail}; [wsl] asks for {cfg.wsl.memory or 'default'} / {want_procs or 'all'} CPUs", hint)]


def _podman(cfg: Config, podman: Podman, *, scene: bool, script: bool) -> list[Check]:
    version = podman.version()
    if version is None:
        return [Check("podman", FAIL, f"not found in '{cfg.wsl.distro}'", "install it inside the distro: sudo apt install podman")]
    checks = [Check("podman", OK, version)]
    rootless = podman.rootless()
    if rootless is False:
        checks.append(Check("podman mode", WARN, "running as root", "rootless podman is the tested configuration"))
    if cfg.wsl.use_init and not podman.has_init_binary():
        checks.append(Check("catatonit", WARN, "not found", "sudo apt install catatonit, or set use_init = false"))
    wanted = [("SITL image", cfg.sitl.image_ref, "sitl")]
    if script:
        wanted.append(("script image", cfg.scripts.image, "script"))
    if scene:
        wanted.append(("scene-loader image", cfg.scene.image, "script"))
    seen: set[str] = set()
    for label, ref, which in wanted:
        if ref in seen:
            continue
        seen.add(ref)
        if podman.image_exists(ref):
            checks.append(Check(label, OK, ref))
        else:
            checks.append(Check(label, FAIL, f"{ref} is not built", f"build it with: mrrsim images build {which}"))
    return checks


# --------------------------------------------------------------------- project files
def _unreal(cfg: Config) -> list[Check]:
    exe = cfg.unreal.exe
    if not exe.is_file():
        return [Check("Unreal build", FAIL, f"{exe} not found", "set [unreal] exe in mrrsim.toml")]
    checks = [Check("Unreal build", OK, f"{exe.name}, {exe.stat().st_size / 1024**2:.0f} MB")]
    if len(exe.parents) > 2:
        paks = list((exe.parents[2] / "Content" / "Paks").glob("*.pak"))
        if paks:
            checks.append(Check("Unreal content", OK, f"{len(paks)} pak file(s)"))
        else:
            checks.append(Check("Unreal content", WARN, f"no .pak under {exe.parents[2] / 'Content' / 'Paks'}",
                                "the exe would start with no map"))
    return checks


def _scene(cfg: Config) -> list[Check]:
    if not cfg.scene.scene:
        return [Check("scene", WARN, "[scene] scene is empty: scene loading will be skipped")]
    path = cfg.scene.config_dir / cfg.scene.scene
    if not path.is_file():
        return [Check("scene", FAIL, f"{path} not found", "copy your scene + robot .jsonc files into sim_config/ (see sim_config/README.md)")]
    try:
        problems = scenecheck.check_scene(cfg.scene.config_dir, cfg.scene.scene, cfg.sitl.vehicles)
    except scenecheck.SceneParseError as exc:
        return [Check("scene", WARN, f"{path.name}: could not check robots and ports ({exc})")]
    if problems:
        return [Check(
            "scene", FAIL, f"{path.name} does not match {cfg.sitl.vehicles} vehicle(s): " + "; ".join(problems),
            "`mrrsim up --vehicles N` generates a matching scene; or run python sim_config/generate_scene.py N, "
            "or pick another scene with --scene (see sim_config/README.md)",
        )]
    return [Check("scene", OK, f"{path.name}: {cfg.sitl.vehicles} ArduPilot robot(s), ports match")]


def _script(cfg: Config) -> list[Check]:
    if not cfg.scripts.main:
        return [Check("script", WARN, "[scripts] main is empty: pass --script or use --no-script")]
    path = cfg.scripts.dir / cfg.scripts.main
    if path.is_file():
        return [Check("script", OK, str(path))]
    return [Check("script", FAIL, f"{path} not found", "fix [scripts] dir/main in mrrsim.toml or pass --script PATH")]


def _ports(cfg: Config, unreal: bool) -> Check:
    wanted: list[tuple[int, str]] = []
    if unreal:
        wanted += [(cfg.unreal.topic_port, "tcp"), (cfg.unreal.service_port, "tcp")]
    for vp in ports.all_vehicle_ports(cfg.sitl.vehicles):
        wanted += vp.listening()
    busy = ports.busy_ports(wanted)
    if busy:
        who = ports.who_uses(busy)
        needed_by: dict[tuple[int, str], str] = {}
        for vp in ports.all_vehicle_ports(cfg.sitl.vehicles):
            needed_by[(vp.sim_in, "udp")] = f"vehicle {vp.index}'s sensors"
            needed_by[(vp.dronekit, "tcp")] = f"vehicle {vp.index}'s scripts"
            needed_by[(vp.monitor, "tcp")] = f"vehicle {vp.index}'s health check"
        listing = "; ".join(
            f"{proto}/{port}" + (f" (needed by {needed_by[(port, proto)]})" if (port, proto) in needed_by else "")
            + (f" is used by {who[(port, proto)]}" if (port, proto) in who else "")
            for port, proto in busy
        )
        foreign = [name for name in who.values() if not name.lower().startswith(("simulation-win64", "wsl", "python", "mrrsimulator"))]
        hint = (
            "close that program, or use fewer vehicles (each vehicle needs its own ports, 10 apart)"
            if foreign else "a leftover from an earlier run? try: mrrsim down"
        )
        return Check("ports", FAIL, f"already in use: {listing}", hint)
    return Check("ports", OK, f"{len(wanted)} ports free")
