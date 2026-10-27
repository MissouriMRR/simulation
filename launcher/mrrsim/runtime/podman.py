"""Rootless podman inside the WSL distro, driven from Windows via `wsl.exe --exec`.

Every container the launcher starts carries the label `mrrsim=1`, so leftovers (the launcher
crashed, the console was closed) can always be found and removed: Windows Job Objects do not
reach into WSL.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ..core.log import LOG
from . import wsl

LABEL = "mrrsim"
# Subcommands that change state. In dry-run mode these are printed, not executed.
MUTATING = {"run", "stop", "rm", "kill", "build", "pull", "create", "start", "restart", "cp"}


class PodmanError(RuntimeError):
    pass


@dataclass(frozen=True)
class Mount:
    src: str  # a path as seen INSIDE WSL (see wsl.to_wsl_path)
    dst: str
    mode: str = "ro"

    def arg(self) -> str:
        return f"{self.src}:{self.dst}:{self.mode}"


def run_args(
    image: str,
    *,
    name: str,
    command: Sequence[str] = (),
    detach: bool = False,
    rm: bool = True,
    init: bool = True,
    env: Mapping[str, str] | None = None,
    mounts: Sequence[Mount] = (),
    labels: Mapping[str, str] | None = None,
    network: str = "host",
    stop_timeout: int | None = None,
    workdir: str | None = None,
) -> list[str]:
    """Arguments for `podman run` (pure function, unit-tested)."""
    args = ["run"]
    if detach:
        args.append("-d")
    if rm:
        args.append("--rm")
    if init:
        args.append("--init")
    args += ["--network", network, "--name", name]
    for key, value in (labels or {}).items():
        args += ["--label", f"{key}={value}"]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    for mount in mounts:
        args += ["-v", mount.arg()]
    if stop_timeout is not None:
        args += ["--stop-timeout", str(stop_timeout)]
    if workdir:
        args += ["-w", workdir]
    args.append(image)
    args.extend(command)
    return args


class LogFollower:
    """`podman logs -f <name>` redirected into a file for the lifetime of a container."""

    def __init__(self, proc: subprocess.Popen, handle) -> None:
        self._proc = proc
        self._handle = handle

    def stop(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._handle.close()


class Podman:
    def __init__(self, distro: str, *, init: bool = True, dry_run: bool = False) -> None:
        self.distro = distro
        self.init = init
        self.dry_run = dry_run

    # ------------------------------------------------------------------ plumbing
    def display(self, args: Sequence[str]) -> str:
        return shlex.join(wsl.exec_argv(self.distro, ["podman", *args]))

    def run(self, args: Sequence[str], *, timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
        args = list(args)
        if self.dry_run and args and args[0] in MUTATING:
            LOG.info("[dry-run] %s", self.display(args))
            return subprocess.CompletedProcess(args, 0, "", "")
        LOG.debug("podman %s", " ".join(args))
        result = wsl.run(self.distro, ["podman", *args], timeout=timeout)
        if check and result.returncode != 0:
            raise PodmanError(
                f"podman {args[0]} failed (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    # --------------------------------------------------------------------- reads
    def version(self) -> str | None:
        result = self.run(["--version"], timeout=30, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def rootless(self) -> bool | None:
        result = self.run(["info", "--format", "{{.Host.Security.Rootless}}"], timeout=30, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip().lower() == "true"

    def has_init_binary(self) -> bool:
        probe = "command -v catatonit >/dev/null 2>&1 || test -x /usr/libexec/podman/catatonit"
        return wsl.run(self.distro, ["sh", "-c", probe], timeout=30).returncode == 0

    def image_exists(self, ref: str) -> bool:
        return self.run(["image", "exists", ref], timeout=30, check=False).returncode == 0

    def container_exists(self, name: str) -> bool:
        return self.run(["container", "exists", name], timeout=30, check=False).returncode == 0

    def is_running(self, name: str) -> bool:
        result = self.run(["container", "inspect", "--format", "{{.State.Running}}", name], timeout=30, check=False)
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def ps(self, label: str = LABEL) -> list[dict]:
        result = self.run(["ps", "-a", "--filter", f"label={label}", "--format", "json"], timeout=30, check=False)
        text = result.stdout.strip()
        if result.returncode != 0 or not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def logs_tail(self, name: str, lines: int = 40) -> str:
        result = self.run(["logs", "--tail", str(lines), name], timeout=30, check=False)
        return (result.stdout + result.stderr).strip()

    # -------------------------------------------------------------------- writes
    def run_detached(
        self,
        image: str,
        name: str,
        *,
        command: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        mounts: Sequence[Mount] = (),
        labels: Mapping[str, str] | None = None,
        rm: bool = True,
        stop_timeout: int | None = None,
    ) -> str:
        args = run_args(
            image, name=name, command=command, detach=True, rm=rm, init=self.init,
            env=env, mounts=mounts, labels=labels, stop_timeout=stop_timeout,
        )
        # The first start of an image can take a while (layer extraction).
        return self.run(args, timeout=300).stdout.strip()

    def run_attached(
        self,
        image: str,
        name: str,
        *,
        command: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        mounts: Sequence[Mount] = (),
        labels: Mapping[str, str] | None = None,
    ) -> subprocess.Popen | None:
        """Foreground `podman run`; stdout and stderr are merged into proc.stdout."""
        args = run_args(
            image, name=name, command=command, detach=False, rm=True, init=self.init,
            env=env, mounts=mounts, labels=labels,
        )
        if self.dry_run:
            LOG.info("[dry-run] %s", self.display(args))
            return None
        return wsl.popen(
            self.distro, ["podman", *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )

    def stop(self, names: Sequence[str], timeout: int = 10) -> None:
        if names:
            self.run(["stop", "--ignore", "-t", str(timeout), *names], timeout=timeout * len(names) + 30, check=False)

    def rm_force(self, names: Sequence[str]) -> None:
        if names:
            self.run(["rm", "-f", "--ignore", *names], timeout=60, check=False)

    def reap(self, label: str = LABEL) -> list[str]:
        """Remove every container carrying `label`. Returns their names."""
        names = [n for row in self.ps(label) for n in (row.get("Names") or [])]
        self.rm_force(names)
        return names

    def copy_from(self, name: str, src: str, dest: str) -> bool:
        """`podman cp name:src dest` (dest is a path inside WSL). False if there was nothing to copy."""
        result = self.run(["cp", f"{name}:{src}", dest], timeout=120, check=False)
        if result.returncode != 0:
            LOG.debug("podman cp %s:%s failed: %s", name, src, result.stderr.strip())
        return result.returncode == 0

    def follow_logs(self, name: str, path: Path) -> LogFollower | None:
        if self.dry_run:
            return None
        handle = open(path, "wb")
        proc = wsl.popen(self.distro, ["podman", "logs", "-f", name], stdout=handle, stderr=subprocess.STDOUT)
        return LogFollower(proc, handle)

    def build(self, args: Sequence[str]) -> int:
        full = ["build", *args]
        if self.dry_run:
            LOG.info("[dry-run] %s", self.display(full))
            return 0
        LOG.info("podman %s", " ".join(full))
        return wsl.call(self.distro, ["podman", *full])
