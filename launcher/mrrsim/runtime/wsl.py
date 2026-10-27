"""Thin wrapper around wsl.exe: run commands inside a distro, inspect WSL config."""
from __future__ import annotations

import configparser
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..core import inifile

WSL_MOUNT_ROOT = "/mnt"  # default automount root; change if /etc/wsl.conf overrides it


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["WSL_UTF8"] = "1"  # wsl.exe's own messages in UTF-8 instead of UTF-16
    return env


def decode(raw: bytes) -> str:
    """Decode wsl.exe output, tolerating UTF-16 from older wsl.exe builds."""
    if not raw:
        return ""
    if b"\x00" in raw[:8]:
        return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
    return raw.decode("utf-8", errors="replace").lstrip("\ufeff")


def exec_argv(distro: str, argv: list[str]) -> list[str]:
    """Command line that runs `argv` inside `distro` without an intermediate shell."""
    return ["wsl.exe", "-d", distro, "--exec", *argv]


TIMED_OUT = 124  # the exit code of a command that did not answer in time (the same as coreutils' `timeout`)


def run(
    distro: str,
    argv: list[str],
    *,
    timeout: float | None = 60,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command in the distro. A command that does not answer within `timeout` seconds is stopped and reported
    like any failed command (return code TIMED_OUT, the reason in stderr), never as an exception: a slow or stuck WSL
    must not crash the launcher with a traceback."""
    command = exec_argv(distro, argv)
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            env=_env(),
            input=input_text.encode() if input_text is not None else None,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, TIMED_OUT, "", f"no answer from WSL within {timeout:g} s")
    return subprocess.CompletedProcess(
        proc.args, proc.returncode, decode(proc.stdout), decode(proc.stderr)
    )


def warm_up(distro: str, timeout: float = 90, attempts: int = 2) -> tuple[str, float]:
    """Make sure the distro answers, starting it if it is not running. The first command after a restart can take a
    minute on a slow computer, so this waits much longer than an ordinary command does.

    Returns ("ok" | "failed" | "timeout", seconds taken). "failed" means WSL answered but the command did not
    succeed (for example the distro does not exist): that is for the doctor to explain, so it is not an error here."""
    started = time.monotonic()
    for _ in range(attempts):
        result = run(distro, ["true"], timeout=timeout)
        if result.returncode == 0:
            return "ok", time.monotonic() - started
        if result.returncode != TIMED_OUT:
            return "failed", time.monotonic() - started
    return "timeout", time.monotonic() - started


def popen(distro: str, argv: list[str], **kwargs) -> subprocess.Popen:
    return subprocess.Popen(exec_argv(distro, argv), env=_env(), **kwargs)


def call(distro: str, argv: list[str]) -> int:
    """Run with the console's stdio inherited (long builds that should show progress)."""
    return subprocess.call(exec_argv(distro, argv), env=_env())


def wsl_command(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess[str]:
    """Run wsl.exe itself (e.g. --status, -l -v)."""
    proc = subprocess.run(["wsl.exe", *args], capture_output=True, timeout=timeout, env=_env())
    return subprocess.CompletedProcess(
        proc.args, proc.returncode, decode(proc.stdout), decode(proc.stderr)
    )


@dataclass(frozen=True)
class Distro:
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
    result = wsl_command(["-l", "-v"])
    return parse_distro_list(result.stdout) if result.returncode == 0 else []


def wslconfig_path() -> Path:
    """The WSL settings file. MRRSIM_WSLCONFIG points elsewhere (testing against a copy)."""
    override = os.environ.get("MRRSIM_WSLCONFIG")
    if override:
        return Path(override)
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".wslconfig"


def networking_mode(path: Path | None = None) -> str:
    """networkingMode from .wslconfig ('nat' is WSL's default when unset)."""
    path = path or wslconfig_path()
    if not path.is_file():
        return "nat"
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return "nat"
    return parser.get("wsl2", "networkingmode", fallback="nat").strip().lower()


def set_wsl2_keys(text: str, values: dict[str, str]) -> str:
    """Set keys in the [wsl2] section of .wslconfig text, keeping everything else."""
    return inifile.set_keys(text, "wsl2", values)


def effective_networking_mode(distro: str) -> tuple[str | None, str]:
    """(mode the VM is really running with, WSL's own complaint if it fell back). Mode is None when
    this WSL has no `wslinfo` to ask. Starting the distro is what surfaces the complaint."""
    result = run(distro, ["sh", "-c", "wslinfo --networking-mode"], timeout=60)
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


def wsl2_setting(name: str, path: Path | None = None) -> str | None:
    path = path or wslconfig_path()
    if not path.is_file():
        return None
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return None
    return parser.get("wsl2", name.lower(), fallback=None)


def logical_processors() -> int:
    return os.cpu_count() or 1


def effective_processors(requested: int) -> int:
    """`requested`, clamped to half of this machine's logical processors (but never below 2)."""
    return min(requested, max(2, logical_processors() // 2)) if requested else 0


def can_connect(distro: str, host: str, port: int, timeout: int = 3) -> bool | None:
    """Can a process INSIDE the distro open a TCP connection to host:port? None if it cannot be tested
    (odd host name, or no bash in the distro). Windows' own check that a port is open passes even
    while Windows Firewall still blocks WSL, so this asks from the WSL side."""
    if not re.fullmatch(r"[A-Za-z0-9.:-]+", host) or not 0 < port < 65536:
        return None
    result = run(distro, ["timeout", str(timeout), "bash", "-c", f"exec 3<>/dev/tcp/{host}/{port}"], timeout=timeout + 30)
    if result.returncode == 0:
        return True
    if result.returncode == 127:  # no bash / timeout in the distro
        return None
    return False


def to_wsl_path(path: str | Path) -> str:
    """C:\\Users\\me\\x -> /mnt/c/Users/me/x (drive-letter paths only)."""
    resolved = Path(path).resolve()
    drive = resolved.drive
    if len(drive) != 2 or drive[1] != ":":
        raise ValueError(f"cannot map {resolved} into WSL (only drive-letter paths are supported)")
    rest = resolved.as_posix()[2:]
    return f"{WSL_MOUNT_ROOT}/{drive[0].lower()}{rest}"
