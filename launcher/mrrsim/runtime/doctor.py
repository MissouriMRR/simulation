"""Preflight checks ("mrrsim doctor"). Read-only."""

from __future__ import annotations

import ast
import os
import platform
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..core import paths, ports
from ..core.config import Config
from ..scene import scenecheck
from ..userscripts import pipinstall
from . import wsl
from .podman import SOURCE_LABEL, Mount, Podman, PodmanError, run_args, source_stamp
from .winproc import IS_WINDOWS, total_ram_gb

# === CONSTANTS ================================================================


OK, WARN, FAIL = "ok", "warn", "fail"
# The installer's thresholds (MB): block below MIN, warn below WARN.
MIN_RAM_MB = 7000
WARN_RAM_MB = 15000
# Container images are large (see containers/README.md) and WSL keeps them in a
# growing VHDX.
WARN_FREE_GB = 10
MIN_FREE_GB = 3
# The oldest Windows build that runs WSL 2, and the oldest with mirrored
# networking (Windows 11 22H2), which the simulator needs: the installer's own
# limit (MinWindowsBuild in the installer script).
WSL2_MIN_BUILD = 19041
MIRRORED_MIN_BUILD = 22621
# What to do when WSL did not answer a question at all.
NO_ANSWER_HINT = "run `wsl --shutdown`, wait ten seconds and try again"
_DISCRETE_GPU = re.compile(r"NVIDIA|Radeon RX|Radeon Pro|Arc\(TM\) A\d", re.I)
_VIRTUAL_GPU = re.compile(r"Parsec|Remote Display|Virtual|Hyper-V|Citrix|VMware", re.I)


# === THE CHECK ================================================================


@dataclass
class Check:
    """One line of the report: what was checked, how it went (`ok`, `warn` or
    `fail`), what was found and, when it did not go well, what to do."""

    name: str
    status: str
    detail: str
    hint: str = ""


# === HOST CHECKS ==============================================================


def _windows_build() -> int | None:
    """The Windows build number, or None off Windows or if the version cannot
    be read."""
    if not IS_WINDOWS:
        return None
    try:
        return int(platform.version().split(".")[2])  # e.g. 10.0.26300
    except (IndexError, ValueError):
        return None


def _windows(cfg: Config) -> Check:
    """The Windows build against the oldest one that runs WSL 2 (19041) and the
    oldest with mirrored networking (22621, Windows 11 22H2), which the
    simulator needs unless [wsl] require_mirrored is false."""
    if not IS_WINDOWS:
        return Check(
            "Windows",
            WARN,
            f"running on {sys.platform}; the Unreal build is Windows-only",
        )
    build = _windows_build()
    if build is None:
        return Check("Windows", WARN, f"could not parse version {platform.version()!r}")
    if build < WSL2_MIN_BUILD:
        return Check(
            "Windows",
            FAIL,
            f"build {build}; WSL2 needs Windows 10 2004 (19041) or newer",
        )
    if build < MIRRORED_MIN_BUILD:
        return Check(
            "Windows",
            FAIL if cfg.wsl.require_mirrored else WARN,
            f"build {build}; the simulator links Windows and WSL through "
            "mirrored networking, which needs Windows 11 22H2 (build "
            f"{MIRRORED_MIN_BUILD}) or newer",
            "update to Windows 11 22H2 or newer",
        )
    return Check("Windows", OK, f"build {build}")


def _ram() -> Check:
    """Installed memory against the installer's minimum and recommended."""
    gb = total_ram_gb()
    if gb is None:
        return Check("RAM", WARN, "could not read installed memory")
    mb = gb * 1024
    detail = f"{gb:.1f} GB installed"
    if mb < MIN_RAM_MB:
        return Check(
            "RAM",
            FAIL,
            detail,
            "8 GB is the minimum; an integrated GPU also borrows from this",
        )
    if mb < WARN_RAM_MB:
        return Check(
            "RAM",
            WARN,
            detail,
            "16 GB recommended: Unreal + the WSL VM + the GPU all share it",
        )
    return Check("RAM", OK, detail)


def _gpu() -> Check:
    """The physical video controllers, virtual ones left out: FAIL for the
    basic Microsoft adapter, WARN when there is no discrete GPU."""
    if not IS_WINDOWS:
        return Check("GPU", WARN, "not checked off Windows")
    try:
        out = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-CimInstance Win32_VideoController).Name",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return Check("GPU", WARN, "could not query video controllers")
    names = [
        n.strip() for n in out.splitlines() if n.strip() and not _VIRTUAL_GPU.search(n)
    ]
    if not names:
        return Check("GPU", WARN, "no physical video controller found")
    detail = ", ".join(names)
    if any("Basic Display" in n for n in names):
        return Check(
            "GPU",
            FAIL,
            detail,
            "Microsoft Basic Display Adapter has no 3D acceleration; install "
            "the GPU driver",
        )
    if not any(_DISCRETE_GPU.search(n) for n in names):
        return Check(
            "GPU",
            WARN,
            detail,
            "integrated GPU only: expect low FPS; plan to use the reduced "
            "graphics profile",
        )
    return Check("GPU", OK, detail)


def _disk() -> Check:
    """Free space on the drive of the launcher's data folder."""
    target = paths.data_dir()
    while not target.exists() and target != target.parent:
        target = target.parent
    free_gb = shutil.disk_usage(target).free / 1024**3
    detail = f"{free_gb:.1f} GB free on {target.drive or target}"
    if free_gb < MIN_FREE_GB:
        return Check("Disk", FAIL, detail, "container images need several GB")
    if free_gb < WARN_FREE_GB:
        return Check(
            "Disk",
            WARN,
            detail,
            f"{WARN_FREE_GB}+ GB recommended for the container images",
        )
    return Check("Disk", OK, detail)


# === WSL AND PODMAN ===========================================================


def _effective_networking(cfg: Config) -> Check:
    """What the running WSL VM actually got. .wslconfig only says what was asked
    for: when mirrored networking cannot start, WSL silently falls back to
    'none' and the containers cannot reach Unreal."""
    mode, problem = wsl.effective_networking_mode(cfg.wsl.distro)
    if mode == "mirrored":
        return Check("WSL networking", OK, "mirrored (Windows and WSL share 127.0.0.1)")
    if mode is None and problem:
        return Check(
            "WSL networking",
            WARN,
            f"could not read the effective mode ({problem})",
            NO_ANSWER_HINT,
        )
    if mode is None:
        return Check(
            "WSL networking",
            OK,
            "mirrored in .wslconfig (this WSL cannot report its effective mode)",
        )
    detail = (
        ".wslconfig asks for mirrored, but the WSL VM is running with "
        f"networkingMode={mode}"
    )
    if problem:
        detail += f" ({problem})"
    build = _windows_build()
    if build is not None and build < MIRRORED_MIN_BUILD:
        hint = (
            f"this Windows (build {build}) is too old for mirrored "
            f"networking: it needs Windows 11 22H2 (build {MIRRORED_MIN_BUILD}) "
            "or newer"
        )
    else:
        hint = (
            "restart Windows; if it persists, in an administrator PowerShell "
            "run: Restart-Service hns ; wsl --shutdown. "
            "A new VPN or virtual network adapter is a common cause"
        )
    return Check("WSL networking", FAIL, detail, hint)


def _wsl_resources(cfg: Config) -> list[Check]:
    """What the running WSL VM actually has, against the [wsl] caps in
    mrrsim.toml."""
    result = wsl.run(
        cfg.wsl.distro,
        ["sh", "-c", "nproc; awk '/MemTotal/ {print $2}' /proc/meminfo"],
        timeout=30,
    )
    try:
        procs, mem_kb = (int(v) for v in result.stdout.split()[:2])
    except ValueError:
        return [
            Check(
                "WSL resources",
                WARN,
                "could not read the VM's CPU count and memory",
            )
        ]
    detail = f"{procs} CPU(s), {mem_kb / 1024 / 1024:.1f} GB in the VM"
    want_procs = wsl.effective_processors(cfg.wsl.processors)
    want_mb = cfg.wsl.memory_mb
    if not want_procs and not want_mb:
        return [Check("WSL resources", OK, f"{detail} (no caps set in [wsl])")]
    # MemTotal is a little below the configured memory; allow 10% for the
    # kernel's share.
    too_many = bool(want_procs) and procs > want_procs
    too_big = bool(want_mb) and mem_kb / 1024 > want_mb * 1.1
    if not (too_many or too_big):
        return [
            Check(
                "WSL resources",
                OK,
                f"{detail} (caps: {cfg.wsl.memory or 'default'}, "
                f"{want_procs or 'all'} CPUs)",
            )
        ]
    configured = (
        not want_procs or wsl.wsl2_setting("processors") == str(want_procs)
    ) and (
        not want_mb
        or (wsl.wsl2_setting("memory") or "").upper() == cfg.wsl.memory.upper()
    )
    hint = (
        "the caps are in .wslconfig but the VM has not restarted: run  wsl --shutdown"
        if configured
        else "write the caps with  mrrsim wsl-config  then run  wsl --shutdown"
    )
    return [
        Check(
            "WSL resources",
            WARN,
            f"{detail}; [wsl] asks for {cfg.wsl.memory or 'default'} / "
            f"{want_procs or 'all'} CPUs",
            hint,
        )
    ]


def _wsl(cfg: Config) -> tuple[list[Check], bool]:
    """The distribution (installed, WSL 2), its processors and memory, and
    the networking mode. Returns the checks and whether the distribution is
    usable, which decides whether podman can be asked."""
    checks: list[Check] = []
    listed, problem = wsl.query_distros()
    distros = {d.name: d for d in listed}
    distro = distros.get(cfg.wsl.distro)
    ready = False
    if problem:  # WSL did not answer: that is not "not installed"
        checks.append(
            Check(
                f"WSL distro '{cfg.wsl.distro}'",
                FAIL,
                f"WSL did not answer ({problem})",
                NO_ANSWER_HINT,
            )
        )
    elif distro is None:
        known = ", ".join(distros) or "none found"
        checks.append(
            Check(
                f"WSL distro '{cfg.wsl.distro}'",
                FAIL,
                f"not installed (installed: {known})",
                f"run the {cfg.wsl.distro!r} setup again: the MRRSimulator "
                "installer adds this distribution. "
                "(Or set [wsl] distro in mrrsim.toml to another distribution "
                "that has podman and the images.)",
            )
        )
    elif distro.version != 2:
        checks.append(
            Check(
                f"WSL distro '{cfg.wsl.distro}'",
                FAIL,
                f"WSL version {distro.version}",
                f"convert it with: wsl --set-version {cfg.wsl.distro} 2",
            )
        )
    else:
        checks.append(
            Check(f"WSL distro '{cfg.wsl.distro}'", OK, f"WSL2, {distro.state}")
        )
        ready = True

    if ready:
        checks += _wsl_resources(cfg)

    mode = wsl.networking_mode()
    if mode == "mirrored" and ready:
        checks.append(_effective_networking(cfg))
    elif mode == "mirrored":
        checks.append(
            Check(
                "WSL networking",
                OK,
                "mirrored in .wslconfig (not checked: no usable distro)",
            )
        )
    else:
        status = FAIL if cfg.wsl.require_mirrored else WARN
        checks.append(
            Check(
                "WSL networking",
                status,
                f"networkingMode={mode}",
                "add 'networkingMode=mirrored' under [wsl2] in "
                f"{wsl.wslconfig_path()}, then run: wsl --shutdown "
                "(needs Windows 11 22H2 or newer)",
            )
        )
    return checks, ready


def _podman(cfg: Config, podman: Podman, *, scene: bool, script: bool) -> list[Check]:
    """podman in the distribution: its version, rootless mode, the init
    program, and the container images this run needs."""
    checks: list[Check] = []
    try:
        version = podman.version()
        if version is None:
            return [
                Check(
                    "podman",
                    FAIL,
                    f"not found in '{cfg.wsl.distro}'",
                    "install it inside the distro: sudo apt install podman",
                )
            ]
        checks.append(Check("podman", OK, version))
        wanted = [("SITL image", cfg.sitl.image_ref, "sitl")]
        if script:
            wanted.append(("script image", cfg.scripts.image, "script"))
        if scene:
            wanted.append(("scene-loader image", cfg.scene.image, "script"))
        images, seen = [], set()
        for label, ref, which in wanted:
            if ref not in seen:
                seen.add(ref)
                images.append((label, ref, which))
        # The questions do not depend on each other, and each is a command run
        # in WSL, so they are asked at the same time.
        stamps = {which: source_stamp(paths.containers_dir() / which) for _, _, which in images}
        with ThreadPoolExecutor(max_workers=2 * len(images) + 2) as pool:
            rootless = pool.submit(podman.rootless)
            has_init = pool.submit(podman.has_init_binary) if cfg.wsl.use_init else None
            exist = [pool.submit(podman.image_exists, ref) for _, ref, _ in images]
            built_from = [pool.submit(podman.image_label, ref, SOURCE_LABEL) for _, ref, _ in images]
            if rootless.result() is False:
                checks.append(
                    Check(
                        "podman mode",
                        WARN,
                        "running as root",
                        "rootless podman is the tested configuration",
                    )
                )
            if has_init is not None and not has_init.result():
                checks.append(
                    Check(
                        "catatonit",
                        WARN,
                        "not found",
                        "sudo apt install catatonit, or set use_init = false",
                    )
                )
            for (label, ref, which), found, stamp in zip(images, exist, built_from):
                if found.result() and stamp.result() == stamps[which]:
                    checks.append(Check(label, OK, ref))
                elif found.result():  # made from other build files: the code may need what it lacks
                    checks.append(
                        Check(
                            label,
                            FAIL,
                            f"{ref} is older than containers/{which}",
                            f"rebuild it: mrrsim images build {which}",
                        )
                    )
                else:
                    checks.append(
                        Check(
                            label,
                            FAIL,
                            f"{ref} is not built",
                            f"build it with: mrrsim images build {which}",
                        )
                    )
    except PodmanError as exc:  # WSL did not answer: not podman's "no"
        checks.append(
            Check("podman", FAIL, f"WSL did not answer ({exc})", NO_ANSWER_HINT)
        )
    return checks


# === PROJECT FILES AND PORTS ==================================================


def _unreal(cfg: Config) -> list[Check]:
    """The Unreal build: its executable, and the .pak files that hold its
    content."""
    exe = cfg.unreal.exe
    if not exe.is_file():
        return [
            Check(
                "Unreal build",
                FAIL,
                f"{exe} not found",
                "set [unreal] exe in mrrsim.toml",
            )
        ]
    checks = [
        Check(
            "Unreal build",
            OK,
            f"{exe.name}, {exe.stat().st_size / 1024**2:.0f} MB",
        )
    ]
    if len(exe.parents) > 2:
        paks = list((exe.parents[2] / "Content" / "Paks").glob("*.pak"))
        if paks:
            checks.append(Check("Unreal content", OK, f"{len(paks)} pak file(s)"))
        else:
            checks.append(
                Check(
                    "Unreal content",
                    WARN,
                    f"no .pak under {exe.parents[2] / 'Content' / 'Paks'}",
                    "the exe would start with no map",
                )
            )
    return checks


def _scene(cfg: Config) -> list[Check]:
    """The scene file exists, has an ArduPilot robot with the right ports for
    every vehicle, and has the home point [sitl] home says."""
    if not cfg.scene.scene:
        return [
            Check(
                "scene",
                WARN,
                "[scene] scene is empty: scene loading will be skipped",
            )
        ]
    path = cfg.scene.config_dir / cfg.scene.scene
    if not path.is_file():
        return [
            Check(
                "scene",
                FAIL,
                f"{path} not found",
                "copy your scene + robot .jsonc files into sim_config/ (see "
                "sim_config/README.md)",
            )
        ]
    try:
        problems = scenecheck.check_scene(
            cfg.scene.config_dir, cfg.scene.scene, cfg.sitl.vehicles
        )
    except scenecheck.SceneParseError as exc:
        return [
            Check(
                "scene",
                WARN,
                f"{path.name}: could not check robots and ports ({exc})",
            )
        ]
    if problems:
        return [
            Check(
                "scene",
                FAIL,
                f"{path.name} does not match {cfg.sitl.vehicles} vehicle(s): "
                + "; ".join(problems),
                "`mrrsim up --vehicles N` generates a matching scene; or run "
                "python sim_config/generate_scene.py N, "
                "or pick another scene with --scene (see sim_config/README.md)",
            )
        ]
    checks = [
        Check(
            "scene",
            OK,
            f"{path.name}: {cfg.sitl.vehicles} ArduPilot robot(s), ports match",
        )
    ]
    home = scenecheck.home_problem(
        cfg.scene.config_dir, cfg.scene.scene, cfg.sitl.home
    )
    if home:
        checks.append(Check("scene home", WARN, home))
    return checks


def _script(cfg: Config) -> list[Check]:
    """The script that will run exists."""
    if not cfg.scripts.main:
        return [
            Check(
                "script",
                WARN,
                "[scripts] main is empty: pass --script or use --no-script",
            )
        ]
    path = cfg.scripts.dir / cfg.scripts.main
    if path.is_file():
        return [Check("script", OK, str(path))]
    return [
        Check(
            "script",
            FAIL,
            f"{path} not found",
            "fix [scripts] dir/main in mrrsim.toml or pass --script PATH",
        )
    ]


def import_program(script: Path, main: str) -> str | None:
    """A program that runs only the script's top-level import statements (not code in functions, `if` or `try`), from
    the script's own folder in /work as Python would; None when it imports nothing. Its imports load the modules they
    name, the project's own included, so a module the checkout lacks fails here as it would in the run."""
    tree = ast.parse(script.read_text(encoding="utf-8-sig"))
    lines = [ast.unparse(node) for node in tree.body
             if isinstance(node, ast.Import)
             or (isinstance(node, ast.ImportFrom) and node.level == 0 and node.module != "__future__")]
    if not lines:
        return None
    folder = str(PurePosixPath("/work", main.replace("\\", "/")).parent)
    return "\n".join([f"import sys; sys.path.insert(0, {folder!r})", *lines, "print('imports work')"])


def _import_failure(output: str) -> str:
    """The error of a failed import check, and the project file it happened in: "ModuleNotFoundError: No module named
    'x' (state_machine/drone.py line 23)"."""
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    error = lines[-1] if lines else "it failed without saying why"
    where = re.findall(r'File "/work/([^"]+)", line (\d+)', output)
    return f"{error} ({where[-1][0]} line {where[-1][1]})" if where else error


def _script_imports(cfg: Config, podman: Podman) -> list[Check]:
    """The script's imports, tried in its image with its folder at /work (and the packages `mrrsim pip install` added),
    before anything starts: a checkout that lacks a module the script needs then fails in seconds, not after Unreal and
    SITL have started."""
    path = cfg.scripts.dir / cfg.scripts.main
    if not cfg.scripts.main or not path.is_file():
        return []  # the script check says so
    try:
        program = import_program(path, cfg.scripts.main)
    except (OSError, SyntaxError, ValueError) as exc:
        return [Check("script imports", FAIL, f"{cfg.scripts.main} cannot be read: {exc}")]
    if program is None:
        return [Check("script imports", OK, f"{cfg.scripts.main} imports nothing")]
    if podman.dry_run:  # nothing is started in a dry run, this container neither
        return [Check("script imports", OK, "not tried (dry run)")]
    from .session import SCRIPT_LIB  # (session imports this module)

    mounts = [Mount(wsl.to_wsl_path(cfg.scripts.dir, cfg.wsl.distro), "/work", "ro")]
    pythonpath = "/work:" + SCRIPT_LIB
    if pipinstall.has_packages():
        mounts.append(Mount(wsl.to_wsl_path(paths.packages_dir(), cfg.wsl.distro), "/packages", "ro"))
        pythonpath = "/work:/packages:" + SCRIPT_LIB
    args = run_args(cfg.scripts.image, name=f"mrrsim-import-check-{os.getpid()}", command=["python", "-c", program],
                    env={"PYTHONPATH": pythonpath, "PYTHONDONTWRITEBYTECODE": "1"}, mounts=mounts, network="none")
    try:
        result = podman.run(args, timeout=180, check=False)
    except PodmanError as exc:
        return [Check("script imports", FAIL, f"WSL did not answer ({exc})", NO_ANSWER_HINT)]
    if result.returncode == 0:
        return [Check("script imports", OK, f"every import of {cfg.scripts.main} works in {cfg.scripts.image}")]
    return [Check("script imports", FAIL, f"{cfg.scripts.main}: {_import_failure(result.stderr + result.stdout)}",
                  "the script's folder lacks a module it imports, or an import fails: fix that code, or check out a "
                  "branch where it is complete")]


def _ports(cfg: Config, unreal: bool) -> Check:
    """Whether the ports that Unreal and the vehicles need are free, and who
    uses them if not."""
    wanted: list[tuple[int, str]] = []
    if unreal:
        wanted += [
            (cfg.unreal.topic_port, "tcp"),
            (cfg.unreal.service_port, "tcp"),
        ]
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
            f"{proto}/{port}"
            + (
                f" (needed by {needed_by[(port, proto)]})"
                if (port, proto) in needed_by
                else ""
            )
            + (f" is used by {who[(port, proto)]}" if (port, proto) in who else "")
            for port, proto in busy
        )
        foreign = [
            name
            for name in who.values()
            if not name.lower().startswith(
                ("simulation-win64", "wsl", "python", "mrrsimulator")
            )
        ]
        hint = (
            "close that program, or use fewer vehicles (each vehicle needs "
            "its own ports, 10 apart)"
            if foreign
            else "a leftover from an earlier run? try: mrrsim down"
        )
        return Check("ports", FAIL, f"already in use: {listing}", hint)
    return Check("ports", OK, f"{len(wanted)} ports free")


# === RUNNING THE CHECKS =======================================================


def run_checks(
    cfg: Config,
    *,
    unreal: bool = True,
    scene: bool = True,
    script: bool = True,
    podman: Podman | None = None,
) -> list[Check]:
    """Run every preflight check and return the results in report order.
    `unreal`, `scene` and `script` switch off the checks of the Unreal
    build, the scene and the script, for a run that does not need them.
    `podman` is a stand-in for the wrapper, which is otherwise made from
    the settings."""
    checks = [_windows(cfg), _ram(), _gpu(), _disk()]
    wsl_checks, wsl_ready = _wsl(cfg)
    checks += wsl_checks
    if wsl_ready:
        podman = podman or Podman(cfg.wsl.distro, init=cfg.wsl.use_init)
        checks += _podman(
            cfg, podman, scene=scene and bool(cfg.scene.scene), script=script
        )
    if unreal:
        checks += _unreal(cfg)
    if scene:
        checks += _scene(cfg)
    if script:
        checks += _script(cfg)
        if any(c.name == "script image" and c.status == OK for c in checks):  # WSL answered and the image is built
            checks += _script_imports(cfg, podman)
    checks.append(_ports(cfg, unreal))
    return checks


def format_report(checks: list[Check]) -> str:
    """The report as text: a line per check (with its hint when it is not OK)
    and the number of failures and warnings."""
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
