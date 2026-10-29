"""Wrapper around wsl.exe: run commands inside a distro, inspect WSL
config."""

from __future__ import annotations

import configparser
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..core import inifile

# === CONSTANTS ================================================================


# where WSL mounts the Windows drives unless the distro's /etc/wsl.conf says
# otherwise (see `automount_root`)
WSL_MOUNT_ROOT = "/mnt"
# the exit code of a command that did not answer in time (the same as coreutils'
# `timeout`)
TIMED_OUT = 124
# the exit code of a command that could not be run at all, e.g. there is no
# wsl.exe (the same as a shell's "command not found")
NOT_FOUND = 127

# where each distro mounts the Windows drives, asked once per run
_AUTOMOUNT_ROOTS: dict[str, str] = {}


# === RUNNING COMMANDS =========================================================


def _env() -> dict[str, str]:
    """The environment wsl.exe is run with, with its own
    messages in UTF-8."""
    env = dict(os.environ)
    env["WSL_UTF8"] = "1"  # wsl.exe's messages in UTF-8 instead of UTF-16
    return env


def decode(raw: bytes) -> str:
    """Decode wsl.exe output, allowing UTF-16 from older wsl.exe builds."""
    if not raw:
        return ""
    if b"\x00" in raw[:8]:
        return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
    return raw.decode("utf-8", errors="replace").lstrip("\ufeff")


def exec_argv(distro: str, argv: list[str]) -> list[str]:
    """Command line that runs `argv` inside `distro` without an intermediate
    shell."""
    return ["wsl.exe", "-d", distro, "--exec", *argv]


def _run_command(
    command: list[str],
    timeout: float | None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a wsl.exe command line and return what it printed as text. Problems
    are reported like any failed command, never raised: a command that does not
    answer within `timeout` seconds is stopped (return code TIMED_OUT), and one
    that cannot be run, such as when wsl.exe is not installed, gets NOT_FOUND.
    The reason is in stderr."""
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            env=_env(),
            input=input_text.encode() if input_text is not None else None,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command, TIMED_OUT, "", f"no answer from WSL within {timeout:g} s"
        )
    except OSError as exc:
        return subprocess.CompletedProcess(
            command, NOT_FOUND, "", f"could not run {command[0]}: {exc}"
        )
    return subprocess.CompletedProcess(
        proc.args, proc.returncode, decode(proc.stdout), decode(proc.stderr)
    )


def run(
    distro: str,
    argv: list[str],
    *,
    timeout: float | None = 60,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command in the distro. A slow, stuck or missing WSL is reported
    like any failed command (return code TIMED_OUT or NOT_FOUND, the reason in
    stderr), never raised: it must not crash the launcher with a traceback."""
    return _run_command(exec_argv(distro, argv), timeout, input_text)


def warm_up(distro: str, timeout: float = 90, attempts: int = 2) -> tuple[str, float]:
    """Make sure the distro answers, starting it if it is not running. The first
    command after a restart can take a minute on a slow computer, so this waits
    much longer than an ordinary command does.

    Returns ("ok" | "failed" | "timeout", seconds taken). "failed" means WSL
    answered but the command did not succeed (for example the distro does not
    exist)."""
    started = time.monotonic()
    for _ in range(attempts):
        result = run(distro, ["true"], timeout=timeout)
        if result.returncode == 0:
            return "ok", time.monotonic() - started
        if result.returncode != TIMED_OUT:
            return "failed", time.monotonic() - started
    return "timeout", time.monotonic() - started


def popen(distro: str, argv: list[str], **kwargs) -> subprocess.Popen:
    """Start a command in the distro without waiting for it. `kwargs` go
    to `subprocess.Popen` (for example to redirect the output)."""
    return subprocess.Popen(exec_argv(distro, argv), env=_env(), **kwargs)


def call(distro: str, argv: list[str]) -> int:
    """Run with the console's stdio inherited (long builds that should show
    progress)."""
    return subprocess.call(exec_argv(distro, argv), env=_env())


def wsl_command(
    args: list[str], timeout: float = 30
) -> subprocess.CompletedProcess[str]:
    """Run wsl.exe itself (e.g. --status, -l -v). Problems are reported like
    `run` does, never raised."""
    return _run_command(["wsl.exe", *args], timeout)


# === DISTROS ==================================================================


@dataclass(frozen=True)
class Distro:
    """One line of `wsl.exe -l -v`."""

    name: str
    state: str
    version: int
    default: bool


def parse_distro_list(text: str) -> list[Distro]:
    """Parse the output of `wsl.exe -l -v`."""
    distros: list[Distro] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        default = line.lstrip().startswith("*")
        parts = line.replace("*", " ", 1).split()
        if len(parts) < 3 or parts[0].upper() == "NAME" or not parts[-1].isdigit():
            continue
        distros.append(Distro(parts[0], parts[1], int(parts[-1]), default))
    return distros


def list_distros() -> list[Distro]:
    """The installed distributions; empty if `wsl.exe -l -v` fails."""
    result = wsl_command(["-l", "-v"])
    return parse_distro_list(result.stdout) if result.returncode == 0 else []


# === WSL SETTINGS =============================================================


def wslconfig_path() -> Path:
    """The WSL settings file. MRRSIM_WSLCONFIG points elsewhere (testing against
    a copy)."""
    override = os.environ.get("MRRSIM_WSLCONFIG")
    if override:
        return Path(override)
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".wslconfig"


def wsl2_setting(name: str, path: Path | None = None) -> str | None:
    """A key of the [wsl2] section of .wslconfig (any capitalisation), or
    None if the file, the section or the key is missing or unreadable. A
    comment after the value (`memory=8GB # for builds`) is not part of it."""
    path = path or wslconfig_path()
    if not path.is_file():
        return None
    parser = configparser.ConfigParser(
        strict=False, interpolation=None, inline_comment_prefixes=("#", ";")
    )
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return None
    return parser.get("wsl2", name.lower(), fallback=None)


def networking_mode(path: Path | None = None) -> str:
    """networkingMode from .wslconfig ('nat' is WSL's default when unset)."""
    return (wsl2_setting("networkingmode", path) or "nat").strip().lower()


def set_wsl2_keys(text: str, values: dict[str, str]) -> str:
    """Set keys in the [wsl2] section of .wslconfig text, keeping everything
    else."""
    return inifile.set_keys(text, "wsl2", values)


def logical_processors() -> int:
    """How many logical processors this computer has."""
    return os.cpu_count() or 1


def effective_processors(requested: int) -> int:
    """`requested`, clamped to half of this machine's logical processors. It is
    never below 2 on a machine that has 2, and never more than the machine
    has."""
    logical = logical_processors()
    return min(requested, max(2, logical // 2), logical) if requested else 0


# === ASKING THE RUNNING DISTRO ================================================


def effective_networking_mode(distro: str) -> tuple[str | None, str]:
    """(mode the VM is really running with, WSL's own complaint if it fell
    back). Mode is None when it could not be read: this WSL has no `wslinfo`, or
    it did not answer (the complaint then says so)."""
    result = run(distro, ["sh", "-c", "wslinfo --networking-mode"], timeout=60)
    if result.returncode == TIMED_OUT:
        return None, result.stderr.strip()
    mode = result.stdout.strip().lower()
    problem = ""
    for line in (result.stderr or "").splitlines():
        if "Failed to configure network" in line or "falling back" in line:
            problem = (problem + " " + line.strip()).strip()
    if "Error code" in (result.stderr or ""):
        code = [ln.strip() for ln in result.stderr.splitlines() if "0x" in ln]
        if code:
            problem += f" [{code[0]}]"
    if result.returncode != 0 or not mode:
        return None, problem
    return mode, problem


def can_connect(distro: str, host: str, port: int, timeout: int = 3) -> bool | None:
    """Can a process INSIDE the distro open a TCP connection to host:port? None
    if it cannot be tested."""
    if not re.fullmatch(r"[A-Za-z0-9.:-]+", host) or not 0 < port < 65536:
        return None
    result = run(
        distro,
        [
            "timeout",
            str(timeout),
            "bash",
            "-c",
            f"exec 3<>/dev/tcp/{host}/{port}",
        ],
        timeout=timeout + 30,
    )
    if result.returncode == 0:
        return True
    if result.returncode == NOT_FOUND:  # no bash / timeout in the distro
        return None
    return False


# === PATHS ====================================================================


def automount_root(distro: str) -> str:
    """Where `distro` mounts the Windows drives: the `root` of the [automount]
    section of its /etc/wsl.conf, else /mnt. Asked once per distro (again later
    if WSL did not answer); if the file cannot be read the answer is /mnt."""
    if distro in _AUTOMOUNT_ROOTS:
        return _AUTOMOUNT_ROOTS[distro]
    root = WSL_MOUNT_ROOT
    result = run(distro, ["cat", "/etc/wsl.conf"], timeout=15)
    if result.returncode == 0:
        parser = configparser.ConfigParser(
            strict=False, interpolation=None, inline_comment_prefixes=("#", ";")
        )
        try:
            parser.read_string(result.stdout)
            configured = parser.get("automount", "root", fallback="")
            root = configured.strip().rstrip("/") or root
        except configparser.Error:
            pass
    if result.returncode != TIMED_OUT:
        _AUTOMOUNT_ROOTS[distro] = root
    return root


def to_wsl_path(path: str | Path, distro: str | None = None) -> str:
    """C:\\Users\\me\\x -> /mnt/c/Users/me/x (drive-letter paths only). With a
    `distro`, its own mount root is used (see `automount_root`)."""
    resolved = Path(path).resolve()
    drive = resolved.drive
    if len(drive) != 2 or drive[1] != ":":
        raise ValueError(
            f"cannot map {resolved} into WSL (only drive-letter paths are supported)"
        )
    rest = resolved.as_posix()[2:]
    root = automount_root(distro) if distro else WSL_MOUNT_ROOT
    return f"{root}/{drive[0].lower()}{rest}"
