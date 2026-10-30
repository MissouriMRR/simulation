"""Rootless podman in the WSL distro, run from Windows with `wsl.exe --exec`.

Every container the launcher starts carries the label `mrrsim=1`, so leftovers
(the launcher crashed, the console was closed) can always be found and removed."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..core.log import LOG
from . import wsl


# === CONSTANTS ================================================================


LABEL = "mrrsim"
# Subcommands that change state. In dry-run mode these are printed, not
# executed.
MUTATING = {
    "run",
    "stop",
    "rm",
    "kill",
    "build",
    "pull",
    "create",
    "start",
    "restart",
    "cp",
}


# === ERRORS ===================================================================


class PodmanError(RuntimeError):
    """A podman command failed; the message has its exit code and what it
    printed."""


# === RUN COMMANDS =============================================================


@dataclass(frozen=True)
class Mount:
    """A folder shared into a container: `src` is a path inside WSL (see
    wsl.to_wsl_path), `dst` where the container sees it, and `mode` what
    follows the second colon of `-v`: `ro`, `rw` or overlay options."""

    src: str  # a path as seen INSIDE WSL (see wsl.to_wsl_path)
    dst: str
    mode: str = "ro"

    def arg(self) -> str:
        """The value of `podman run -v` for this mount."""
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
) -> list[str]:
    """Arguments for `podman run` (pure function, unit-tested). Every container
    gets the label `mrrsim=1`, whatever `labels` adds."""
    args = ["run"]
    if detach:
        args.append("-d")
    if rm:
        args.append("--rm")
    if init:
        args.append("--init")
    args += ["--network", network, "--name", name]
    for key, value in {LABEL: "1", **(labels or {})}.items():
        args += ["--label", f"{key}={value}"]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    for mount in mounts:
        args += ["-v", mount.arg()]
    if stop_timeout is not None:
        args += ["--stop-timeout", str(stop_timeout)]
    args.append(image)
    args.extend(command)
    return args


# === FOLLOWING A CONTAINER'S LOG ==============================================


class LogFollower:
    """`podman logs -f <name>` redirected into a file for the lifetime of a
    container."""

    def __init__(self, proc: subprocess.Popen, handle) -> None:
        self._proc = proc
        self._handle = handle

    def stop(self) -> None:
        """Stop following: end `podman logs -f` and close the file."""
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._handle.close()


# === THE PODMAN WRAPPER =======================================================


class Podman:
    """Rootless podman in the WSL distro `distro`. Commands go through
    `wsl.exe --exec`, never through a shell. `init` starts every
    container with `--init`, an init process that reaps children and
    passes signals on."""

    def __init__(
        self, distro: str, *, init: bool = True, dry_run: bool = False
    ) -> None:
        self.distro = distro
        self.init = init
        self.dry_run = dry_run

    # === PLUMBING =============================================================

    def display(self, args: Sequence[str]) -> str:
        """The command line of `podman <args>` as Windows would run it, for the
        dry-run output."""
        return shlex.join(wsl.exec_argv(self.distro, ["podman", *args]))

    def run(
        self, args: Sequence[str], *, timeout: float = 120, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run `podman <args>` and return the finished process. A command that
        fails raises PodmanError unless `check` is False; a stuck or missing
        WSL is reported like any failed command (see wsl.run). In dry-run mode
        the commands that change something are only printed."""
        args = list(args)
        if self.dry_run and args and args[0] in MUTATING:
            LOG.info("[dry-run] %s", self.display(args))
            return subprocess.CompletedProcess(args, 0, "", "")
        LOG.debug("podman %s", " ".join(args))
        result = wsl.run(self.distro, ["podman", *args], timeout=timeout)
        if check and result.returncode != 0:
            raise PodmanError(
                f"podman {args[0]} failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    def _launch(self, start: Callable[..., Any], args: Sequence[str], **kwargs) -> Any:
        """Start `podman <args>` with `start` (wsl.popen or wsl.call). A wsl.exe
        that cannot be started is a PodmanError, like a command that fails in
        `run`, not a raw OSError."""
        try:
            return start(self.distro, ["podman", *args], **kwargs)
        except OSError as exc:
            raise PodmanError(
                f"podman {args[0]}: could not run wsl.exe: {exc}"
            ) from exc

    # === READS ================================================================

    def version(self) -> str | None:
        """The output of `podman --version`, or None if it did not answer."""
        result = self.run(["--version"], timeout=30, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def rootless(self) -> bool | None:
        """Is podman running rootless? None if it did not say."""
        result = self.run(
            ["info", "--format", "{{.Host.Security.Rootless}}"],
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip().lower() == "true"

    def has_init_binary(self) -> bool:
        """Is the init program that `--init` needs (catatonit) installed?"""
        probe = (
            "command -v catatonit >/dev/null 2>&1 "
            "|| test -x /usr/libexec/podman/catatonit"
        )
        return wsl.run(self.distro, ["sh", "-c", probe], timeout=30).returncode == 0

    def image_exists(self, ref: str) -> bool:
        """Is the image `ref` in podman's local storage?"""
        return (
            self.run(["image", "exists", ref], timeout=30, check=False).returncode == 0
        )

    def is_running(self, name: str) -> bool:
        """Is the container `name` running right now?"""
        result = self.run(
            ["container", "inspect", "--format", "{{.State.Running}}", name],
            timeout=30,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def ps(self, label: str = LABEL) -> list[dict]:
        """One dict per container, running or not, that has the label `label`,
        as `podman ps --format json` gives them. Empty if there are none, and
        also if podman fails or its answer cannot be read (a warning says
        so)."""
        result = self.run(
            ["ps", "-a", "--filter", f"label={label}", "--format", "json"],
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            LOG.warning(
                "podman ps failed (exit %s): %s",
                result.returncode,
                result.stderr.strip() or result.stdout.strip(),
            )
            return []
        text = result.stdout.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, list):
            LOG.warning("podman ps gave an answer that is not a list: %.200s", text)
            return []
        return data

    def logs_tail(self, name: str, lines: int = 40) -> str:
        """The last `lines` lines that container `name` printed (stdout and
        stderr)."""
        result = self.run(["logs", "--tail", str(lines), name], timeout=30, check=False)
        return (result.stdout + result.stderr).strip()

    # === WRITES ===============================================================

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
        """Start a container in the background and return its id (an empty
        string in dry-run mode). The arguments are those of `run_args`."""
        args = run_args(
            image,
            name=name,
            command=command,
            detach=True,
            rm=rm,
            init=self.init,
            env=env,
            mounts=mounts,
            labels=labels,
            stop_timeout=stop_timeout,
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
        """Foreground `podman run`; stdout and stderr are merged into
        proc.stdout."""
        args = run_args(
            image,
            name=name,
            command=command,
            detach=False,
            rm=True,
            init=self.init,
            env=env,
            mounts=mounts,
            labels=labels,
        )
        if self.dry_run:
            LOG.info("[dry-run] %s", self.display(args))
            return None
        return self._launch(
            wsl.popen,
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

    def stop(self, names: Sequence[str], timeout: int = 10) -> bool:
        """Stop the containers (ones that do not exist are fine). False if
        podman reported an error."""
        if not names:
            return True
        result = self.run(
            ["stop", "--ignore", "-t", str(timeout), *names],
            timeout=timeout * len(names) + 30,
            check=False,
        )
        if result.returncode != 0:
            LOG.debug(
                "podman stop %s failed: %s",
                " ".join(names),
                result.stderr.strip(),
            )
        return result.returncode == 0

    def rm_force(self, names: Sequence[str]) -> None:
        """Remove the containers, stopping them first if needed; ones that do
        not exist are fine. Raises PodmanError if podman refuses."""
        if names:
            self.run(["rm", "-f", "--ignore", *names], timeout=60)

    def reap(self, label: str = LABEL) -> list[str]:
        """Remove every container carrying `label`. Returns their names. Raises
        PodmanError if podman refuses, so the names returned are always of
        containers that are gone."""
        names = [n for row in self.ps(label) for n in (row.get("Names") or [])]
        self.rm_force(names)
        return names

    def copy_from(self, name: str, src: str, dest: str) -> bool:
        """`podman cp name:src dest` (dest is a path inside WSL). False if there
        was nothing to copy."""
        result = self.run(["cp", f"{name}:{src}", dest], timeout=120, check=False)
        if result.returncode != 0:
            LOG.debug("podman cp %s:%s failed: %s", name, src, result.stderr.strip())
        return result.returncode == 0

    def follow_logs(self, name: str, path: Path) -> LogFollower | None:
        """Write what container `name` prints into the file `path` for as long
        as it runs. Returns the follower to stop later (None in dry-run mode).
        """
        if self.dry_run:
            return None
        handle = open(path, "wb")
        try:
            proc = self._launch(
                wsl.popen,
                ["logs", "-f", name],
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
        except PodmanError:
            handle.close()
            raise
        return LogFollower(proc, handle)

    def build(self, args: Sequence[str]) -> int:
        """Run `podman build` with `args`, showing its output in this console.
        Returns its exit code (0 in dry-run mode)."""
        full = ["build", *args]
        if self.dry_run:
            LOG.info("[dry-run] %s", self.display(full))
            return 0
        LOG.info("podman %s", " ".join(full))
        return self._launch(wsl.call, full)
