"""Orchestration: the start-up / shut-down order for the whole simulator.

Start:  preflight -> reap stale containers -> Unreal -> scene loader
        -> SITL (one per vehicle) -> health checks -> student script
Stop:   script -> scene loader -> Unreal -> SITL

Unreal goes before SITL on the way down on purpose: ArduPilot's AirSim backend
is lock-step, and closing the autopilot first can leave Unreal hanging.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from ..core import paths, ports
from ..core.config import Config
from ..core.log import LOG, attach_file, detach
from ..userscripts import pipinstall
from . import doctor, health, wsl
from .podman import LABEL, LogFollower, Mount, Podman, PodmanError
from .unreal import UnrealError, UnrealProcess, pid_is, terminate_pid
from .winproc import AppMutexes, JobObject, pid_alive

# === CONSTANTS ================================================================


# Set once the shut-down has begun. Stop requests from the window arrive as
# interrupts; during the shut-down they must be ignored, or a second one would
# abort it half way.
SHUTTING_DOWN = threading.Event()

SCENE_NAME = "mrrsim-scene"
SCRIPT_NAME = "mrrsim-script"
# in the script images: mrrsim_camera.py (the receiver for camera pictures)
SCRIPT_LIB = "/opt/mrrsim/lib"
# where ArduPilot SITL writes its dataflash logs (its working directory)
SITL_LOG_SRC = "/work/logs/."


# === ERRORS ===================================================================


class SessionError(RuntimeError):
    """A problem the user can act on. `up` logs it and returns exit code 2."""


# === NAMES AND NUMBERS ========================================================


def sitl_name(index: int) -> str:
    """The container name of vehicle `index`'s ArduPilot SITL."""
    return f"mrrsim-sitl-{index}"


def _fmt_number(value: float) -> str:
    """A number as text for a setting passed to a container: 5.0 -> "5",
    2.5 -> "2.5"."""
    return str(int(value)) if float(value).is_integer() else str(value)


# === STATE FILE ===============================================================


def read_state() -> dict | None:
    """The state of the run in progress (written by `_begin`). None if there is
    no state file. A file that exists but cannot be used (unreadable, not valid
    JSON, not an object) is reported with a warning and also gives None, so
    that `down` and `status` still work."""
    path = paths.state_file()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("it does not hold a JSON object")
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:  # a bad JSON file is a ValueError
        LOG.warning("ignoring the run-state file %s: %s", path, exc)
        return None
    return state


def _write_state(state: dict) -> None:
    """Save `state` as the current-run file that `down` and `status` read."""
    path = paths.state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _clear_state() -> None:
    """Delete the current-run file."""
    try:
        paths.state_file().unlink()
    except OSError:
        pass


# === SAVING SITL LOGS =========================================================


def save_sitl_logs(podman: Podman, names: list[str], dest_root: Path) -> list[Path]:
    """Copy each SITL container's flight logs to dest_root/<container name>/.
    Returns the folders that got files."""
    saved: list[Path] = []
    for name in names:
        dest = dest_root / name.removeprefix("mrrsim-")
        if podman.dry_run:
            LOG.info("[dry-run] would copy %s:%s to %s", name, SITL_LOG_SRC, dest)
            continue
        dest.mkdir(parents=True, exist_ok=True)
        if podman.copy_from(name, SITL_LOG_SRC, wsl.to_wsl_path(dest)) and any(
            dest.iterdir()
        ):
            saved.append(dest)
        else:
            try:
                # nothing logged: do not leave an empty folder behind
                dest.rmdir()
            except OSError:
                pass
    try:
        dest_root.rmdir()
    except OSError:
        pass
    return saved


# === THE SESSION ==============================================================


class Session:
    """One run of the simulator: starts the pieces in order (`up`) and shuts
    them down in the safe order (`down`)."""

    def __init__(self, cfg: Config, *, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry = dry_run
        self.id = time.strftime("%Y%m%d-%H%M%S")
        self.dir = paths.session_dir(self.id)
        self.podman = Podman(cfg.wsl.distro, init=cfg.wsl.use_init, dry_run=dry_run)
        self.vehicles = ports.all_vehicle_ports(cfg.sitl.vehicles)
        self.unreal: UnrealProcess | None = None
        self.mutexes = AppMutexes()
        self.job: JobObject | None = None
        self._followers: list[LogFollower] = []
        self._file_handler = None
        self._down_done = False
        self._wsl_stuck = False
        self._problems: list[str] = []  # shut-down steps that failed
        self._state: dict = {}

    # === PUBLIC API ===========================================================

    def up(self, *, script: bool, keep: bool, unreal: bool, scene: bool) -> int:
        """Run the whole flow. Returns the process exit code."""
        rc = 0
        try:
            self._begin()
            self._preflight(unreal=unreal, scene=scene, script=script)
            if unreal:
                self._start_unreal()
            if scene:
                self._wait_wsl_reaches_unreal()
                self._load_scene()
            self._start_sitl()
            if script:
                rc = self._run_script()
                if keep:
                    self._idle()
            else:
                self._idle()
            return rc
        except KeyboardInterrupt:
            LOG.warning("interrupted")
            return 130
        except (SessionError, UnrealError, PodmanError) as exc:
            LOG.error("%s", exc)
            return 2
        finally:
            self.down()

    def down(self) -> None:
        """Idempotent shut-down in the safe order. Says what it is stopping at
        each step and whether that worked. Never raises."""
        if self._down_done:
            return
        self._down_done = True
        SHUTTING_DOWN.set()
        LOG.info("shutting down: script, scene, Unreal, then SITL")
        names = [sitl_name(vp.index) for vp in self.vehicles]
        if self._wsl_stuck:
            LOG.warning("WSL is not answering: the containers are left alone")
            self._problems.append("containers not stopped (WSL not answering)")
        else:
            self._safe(
                f"stopping the script container ({SCRIPT_NAME})",
                lambda: self.podman.stop([SCRIPT_NAME], timeout=5),
            )
            self._safe(
                f"stopping the scene loader container ({SCENE_NAME})",
                lambda: self.podman.stop([SCENE_NAME], timeout=5),
            )
        if self.unreal is not None:
            self._safe("stopping Unreal", self.unreal.stop)
        if not self._wsl_stuck:
            self._safe(
                f"stopping the SITL container(s) ({', '.join(names)})",
                lambda: self.podman.stop(names, timeout=self.cfg.sitl.stop_timeout_s),
            )
            if self.cfg.sitl.save_logs:
                self._safe(
                    "saving the ArduPilot flight logs",
                    lambda: self._report_saved(
                        save_sitl_logs(self.podman, names, self.sitl_log_root())
                    ),
                )
            self._safe("removing the containers", self.podman.reap)
        if self._followers:
            self._safe("closing the container log readers", self._close_followers)
        if self.job is not None:
            self._safe(
                "closing the Windows job (ends anything still running)",
                self.job.close,
            )
        if not self.dry:
            self._safe("releasing the launcher lock", self.mutexes.release)
        if not self.dry and (read_state() or {}).get("launcher_pid") == os.getpid():
            _clear_state()
        if self._problems:
            LOG.warning(
                "shut down with %d problem(s): %s",
                len(self._problems),
                "; ".join(self._problems),
            )
        else:
            LOG.info("shut down: everything stopped")
        if self._file_handler is not None:
            detach(self._file_handler)
            self._file_handler = None
        if not self.dry:
            LOG.info("logs: %s", self.dir)

    def sitl_log_root(self) -> Path:
        """Where this run's ArduPilot logs are saved: the `log_dir` setting
        under the current folder, one sub-folder per run."""
        return Path.cwd() / self.cfg.sitl.log_dir / self.id

    def _report_saved(self, saved: list[Path]) -> None:
        """Tell the user where the ArduPilot logs went, or that there were
        none."""
        if self.dry:
            return
        if saved:
            LOG.info("ArduPilot logs saved to %s", self.sitl_log_root())
        else:
            LOG.info("no ArduPilot flight logs to save (SITL logs only while armed)")

    # === STEPS ================================================================

    def _wake_wsl(self) -> None:
        """The first WSL command after a restart can take a minute; wait for
        wakeup."""
        LOG.info("Checking that WSL is responsive, may take a minute.")
        state, seconds = wsl.warm_up(self.cfg.wsl.distro)
        if state == "timeout":
            self._wsl_stuck = True
            raise SessionError(
                f"WSL did not answer in {seconds:.0f} s. It may be stuck. "
                "Close this, open PowerShell and run "
                "`wsl --shutdown`, wait ten seconds, then run again. If it "
                "keeps happening, press Run doctor and "
                f"send the output to the maintainers."
            )
        if seconds > 10:
            LOG.info("WSL answered after %.0f s", seconds)

    def _begin(self) -> None:
        """Claim the launcher, open the run's log file and
        Windows job, check that WSL answers, remove containers left over
        from an earlier run and write the state file."""
        if not self.dry:
            if not self.mutexes.acquire():
                raise SessionError(
                    "Another MRRSimulator launcher is already running. "
                    "Check `mrrsim status`, or stop everything with `mrrsim "
                    "down`."
                )
            self.dir.mkdir(parents=True, exist_ok=True)
            self._file_handler = attach_file(self.dir / "launcher.log")
            self.job = JobObject()
        LOG.info(
            "session %s%s",
            self.id,
            " (dry run: nothing will be started)" if self.dry else "",
        )
        self._wake_wsl()
        stale = self.podman.reap()
        if stale:
            LOG.warning(
                "Removed leftover containers from an earlier run: %s",
                ", ".join(stale),
            )
        self._state = {
            "session": self.id,
            "dir": str(self.dir),
            "launcher_pid": os.getpid(),
            "unreal_pid": None,
            "vehicles": len(self.vehicles),
            "log_root": str(self.sitl_log_root()),
            "distro": self.cfg.wsl.distro,
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if not self.dry:
            _write_state(self._state)

    def _preflight(self, *, unreal: bool, scene: bool, script: bool) -> None:
        """Run the doctor's checks and log every problem. A failed check
        stops the run."""
        checks = doctor.run_checks(
            self.cfg,
            unreal=unreal,
            scene=scene,
            script=script,
            podman=self.podman,
        )
        for check in checks:
            if check.status != doctor.OK:
                level = LOG.error if check.status == doctor.FAIL else LOG.warning
                level(
                    "%s: %s%s",
                    check.name,
                    check.detail,
                    f"  -> {check.hint}" if check.hint else "",
                )
        failed = [c for c in checks if c.status == doctor.FAIL]
        if failed and not self.dry:
            raise SessionError(
                f"preflight failed ({len(failed)} problem(s)); run `mrrsim "
                "doctor` for details"
            )
        LOG.info("preflight: %d checks, %d failed", len(checks), len(failed))

    def _start_unreal(self) -> None:
        """Launch Unreal, record its pid in the state file and wait until it
        is listening."""
        self.unreal = UnrealProcess(
            self.cfg.unreal, self.dir / "unreal.log", dry_run=self.dry
        )
        self.unreal.start(self.job)
        if self.unreal.pid is not None:
            self._state["unreal_pid"] = self.unreal.pid
            _write_state(self._state)
        self.unreal.wait_ready()

    def _wait_wsl_reaches_unreal(self) -> None:
        """Unreal listening on Windows does not mean WSL can connect to it: the
        first time a copy of the exe runs, Windows Firewall asks whether to
        allow it, and until someone clicks Allow, connections from WSL are
        refused. Without this the scene loader just dies with 'Connection
        refused'."""
        cfg = self.cfg.unreal
        if self.dry or cfg.wsl_reach_timeout_s <= 0:
            return
        message = (
            f"WSL cannot reach Unreal at {cfg.host}:{cfg.topic_port} yet. If "
            "Windows shows a Security Alert "
            "(Windows Defender Firewall) for 'Multirotor Simulation' or "
            "Simulation-Win64-Shipping.exe, click "
            "'Allow access' (it may be behind other windows). If there is "
            "none, WSL networking may be "
            f"broken: run `mrrsim doctor`."
        )
        deadline = time.monotonic() + cfg.wsl_reach_timeout_s
        last_warned: float | None = None
        while True:
            reachable = wsl.can_connect(self.cfg.wsl.distro, cfg.host, cfg.topic_port)
            if reachable is None:
                LOG.debug("cannot test WSL -> Unreal reachability here; continuing")
                return
            if reachable:
                if last_warned is not None:
                    LOG.info("WSL can reach Unreal now")
                return
            now = time.monotonic()
            if now >= deadline:
                raise SessionError(
                    f"{message}\nGave up after {cfg.wsl_reach_timeout_s:.0f} s."
                )
            if last_warned is None or now - last_warned >= 15:
                LOG.warning(message)
                last_warned = now
            time.sleep(2.0)

    def _load_scene(self) -> None:
        """Run the scene loader in a container and wait for it to say
        "Scene loaded". The loader is then removed, unless it has to stay
        connected (`hold`, or camera streaming)."""
        cfg = self.cfg.scene
        if not cfg.scene:
            LOG.warning("[scene] scene is empty: skipping scene loading")
            return
        LOG.info("loading scene %s", cfg.scene)
        command = [
            "python",
            "-u",
            "/opt/mrrsim/scene_loader.py",
            "--host",
            self.cfg.unreal.host,
            "--config-dir",
            "/sim_config",
            "--scene",
            cfg.scene,
            "--delay",
            _fmt_number(cfg.delay_s),
        ]
        streams = cfg.camera_streams
        hold = cfg.hold or bool(streams)
        if streams and not cfg.hold:
            LOG.info(
                "[scene] hold is off, but camera streaming needs the loader "
                "to stay connected: holding"
            )
        if hold:
            command.append("--hold")
        self.podman.run_detached(
            cfg.image,
            SCENE_NAME,
            command=command,
            env={"MRRSIM_CAMERA_STREAMS": json.dumps(streams)} if streams else None,
            mounts=[Mount(wsl.to_wsl_path(cfg.config_dir), "/sim_config", "ro")],
            labels=self._labels(),
            # a one-shot loader is kept (not --rm) so its last log lines can be
            # read
            rm=hold,
        )
        self._follow(SCENE_NAME, "scene.log")
        if self.dry:
            return
        log_path = self.dir / "scene.log"
        loaded = self._wait_for_text(
            log_path,
            "Scene loaded",
            cfg.load_timeout_s,
            alive=lambda: self.podman.is_running(SCENE_NAME),
        )
        if not loaded:
            tail = self._tail(log_path)
            raise SessionError(
                f"scene did not load within {cfg.load_timeout_s:.0f} s or the "
                f"loader failed:\n{tail}"
            )
        if not hold:
            self.podman.rm_force([SCENE_NAME])
        LOG.info("scene loaded")
        if streams:
            self._report_camera_streams(log_path, streams)

    def _report_camera_streams(self, log_path: Path, streams: list) -> None:
        """Say which UDP port each streaming camera sends to, or warn if the
        loader could not set up a stream."""
        text = (
            log_path.read_text(encoding="utf-8", errors="replace")
            if log_path.is_file()
            else ""
        )
        failed = [line for line in text.splitlines() if "camera stream FAILED" in line]
        if failed:
            LOG.warning(
                "%s (the simulator still works; the script will receive no pictures)",
                failed[-1].strip(),
            )
            return
        for port in sorted({st["port"] for st in streams}):
            names = sorted({st["camera"] for st in streams if st["port"] == port})
            LOG.info(
                "camera %s sends its pictures to UDP port %d",
                ", ".join(names),
                port,
            )

    def _start_sitl(self) -> None:
        """Start one SITL container per vehicle, then wait for each one's
        heartbeat."""
        cfg = self.cfg.sitl
        for vp in self.vehicles:
            env = {
                "INSTANCE": str(vp.index),
                "SYSID": str(vp.sysid),
                "MODEL": cfg.model,
                "HOME_LOC": cfg.home,
                "SPEEDUP": _fmt_number(cfg.speedup),
                "SIM_ADDRESS": cfg.sim_address,
                "GCS_OUT": cfg.gcs_out,
                "WIPE": "1" if cfg.wipe else "0",
                "SYNTHETIC_CLOCK": "1" if cfg.synthetic_clock else "0",
            }
            if cfg.defaults:
                env["DEFAULTS"] = cfg.defaults
            if cfg.params:
                env["PARAMS"] = ",".join(
                    f"{name}={_fmt_number(value)}" for name, value in cfg.params.items()
                )
            if cfg.extra_args:
                env["EXTRA_ARGS"] = " ".join(cfg.extra_args)
            name = sitl_name(vp.index)
            LOG.info(
                "starting SITL %d (sysid %d, DroneKit port %d)",
                vp.index,
                vp.sysid,
                vp.dronekit,
            )
            # Not --rm when logs are saved: `podman cp` needs the container
            # after it has stopped.
            self.podman.run_detached(
                cfg.image_ref,
                name,
                env=env,
                labels=self._labels(),
                stop_timeout=cfg.stop_timeout_s,
                rm=not cfg.save_logs,
            )
            self._follow(name, f"sitl-{vp.index}.log")
        if self.dry:
            return
        for vp in self.vehicles:
            name = sitl_name(vp.index)
            LOG.info(
                "waiting for SITL %d heartbeat on tcp/%d (up to %.0f s)",
                vp.index,
                vp.monitor,
                cfg.health_timeout_s,
            )
            ok, detail = health.wait_heartbeat(
                "127.0.0.1",
                vp.monitor,
                vp.sysid,
                cfg.health_timeout_s,
                alive=lambda n=name: self.podman.is_running(n),
            )
            if not ok:
                raise SessionError(
                    f"SITL {vp.index} is not healthy: {detail}\n--- last log "
                    f"lines ---\n{self.podman.logs_tail(name)}"
                )
            LOG.info(
                "SITL %d up: %s; scripts connect with %s",
                vp.index,
                detail,
                vp.connect_string(),
            )

    def _run_script(self) -> int:
        """Run the student's script in a container, showing its output, and
        return its exit code (130 if it was stopped with Ctrl+C)."""
        sc = self.cfg.scripts
        LOG.info("running script %s", sc.main)
        mounts = self._script_mounts()
        command = [
            "python",
            "-u",
            "/work/" + sc.main.replace("\\", "/"),
            *sc.args,
        ]
        proc = self.podman.run_attached(
            sc.image,
            SCRIPT_NAME,
            command=command,
            env=self._script_env(mounts),
            labels=self._labels(),
            mounts=mounts,
        )
        if proc is None:  # dry run
            return 0
        interrupted = self._wait_for_script(proc)
        if sc.overlay:
            self._keep_script_writes()
        code = proc.poll()
        rc = 130 if interrupted else (code if code is not None else 1)
        LOG.info("script exited with code %s", rc)
        return rc

    def _script_env(self, mounts: list[Mount]) -> dict[str, str]:
        """The environment variables the script's container gets: how to reach
        the vehicles and Unreal, where its output goes, where its cameras send
        pictures, and what it can import (`mounts` says whether extra packages
        are mounted)."""
        env = {
            "SIM_CONNECT": self.vehicles[0].connect_string(),
            "SIM_VEHICLE_COUNT": str(len(self.vehicles)),
            "SIM_OUT_DIR": "/out",
            "PYTHONPATH": "/work:" + SCRIPT_LIB,
            "PROJECTAIRSIM_HOST": self.cfg.unreal.host,
        }
        if any(mount.dst == "/packages" for mount in mounts):
            env["PYTHONPATH"] = (
                "/work:/packages:" + SCRIPT_LIB
            )  # the script's own folder first
        streams = self.cfg.scene.camera_streams
        # where the script finds the pictures of the cameras that send them
        # (CameraReceiver() uses the first port)
        if streams:
            env["SIM_CAMERA_PORT"] = str(streams[0]["port"])
            env["SIM_CAMERA_STREAMS"] = json.dumps(
                [
                    {
                        "vehicle": st["vehicle"],
                        "camera": st["camera"],
                        "port": st["port"],
                    }
                    for st in streams
                ]
            )
        for vp in self.vehicles:
            env[f"SIM_CONNECT_{vp.index}"] = vp.connect_string()
        return env

    def _script_mounts(self) -> list[Mount]:
        """The folders the script's container sees: its own folder at /work,
        this run's folder at /out and, if `mrrsim pip install` added any, the
        extra packages at /packages. With the overlay setting this also
        creates the scratch layer inside WSL."""
        sc = self.cfg.scripts
        work_mode = "ro"
        if sc.overlay:
            # The folder is the lower layer of an overlay: the script can read
            # everything and write anything, the writes land in a scratch folder
            # inside WSL (an overlay's upper layer cannot sit on the Windows
            # drive).
            work_mode = (
                f"O,upperdir={self.overlay_dir}/up,workdir={self.overlay_dir}/wk"
            )
            if not self.dry:
                made = wsl.run(
                    self.cfg.wsl.distro,
                    [
                        "mkdir",
                        "-p",
                        f"{self.overlay_dir}/up",
                        f"{self.overlay_dir}/wk",
                    ],
                )
                if made.returncode != 0:
                    raise SessionError(
                        "could not create the scratch layer "
                        f"{self.overlay_dir}: {made.stderr.strip()}"
                    )
        mounts = [
            Mount(wsl.to_wsl_path(sc.dir), "/work", work_mode),
            Mount(wsl.to_wsl_path(self.dir), "/out", "rw"),
        ]
        extra = paths.packages_dir()  # packages added with `mrrsim pip install`
        if extra.is_dir() and any(
            p.name != pipinstall.DRIVER_NAME for p in extra.iterdir()
        ):
            mounts.append(Mount(wsl.to_wsl_path(extra), "/packages", "ro"))
        return mounts

    def _wait_for_script(self, proc) -> bool:
        """Show the script's output (and write it to script.log) until it
        exits. Ctrl+C gives it `stop_grace_s` to finish, e.g. to land; a second
        Ctrl+C, or the grace running out, stops it. Returns whether Ctrl+C was
        pressed."""
        sc = self.cfg.scripts
        interrupted = False
        with open(self.dir / "script.log", "w", encoding="utf-8") as log_file:
            reader = threading.Thread(
                target=self._tee, args=(proc.stdout, log_file), daemon=True
            )
            reader.start()
            try:
                # poll() loop: Popen.wait() cannot be interrupted by Ctrl+C on
                # Windows
                while proc.poll() is None:
                    time.sleep(0.2)
            except KeyboardInterrupt:
                interrupted = True
                LOG.warning(
                    "Ctrl+C: giving the script %.0f s to finish (e.g. land); "
                    "Ctrl+C again to stop it now",
                    sc.stop_grace_s,
                )
                try:
                    deadline = time.monotonic() + sc.stop_grace_s
                    while proc.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.2)
                except KeyboardInterrupt:
                    pass
                if proc.poll() is None:
                    self._safe(
                        f"stopping the script container ({SCRIPT_NAME})",
                        lambda: self.podman.stop([SCRIPT_NAME], timeout=5),
                    )
            reader.join(timeout=5)
        return interrupted

    @property
    def overlay_dir(self) -> str:
        """The scratch layer of this session's script, inside WSL."""
        return f"/tmp/mrrsim-overlay/{self.id}"

    def _keep_script_writes(self) -> None:
        """Copy what the script wrote into its folder to
        <session>/project-writes, then delete the scratch layer."""
        target = wsl.to_wsl_path(self.dir) + "/project-writes"
        layer = self.overlay_dir
        copy = wsl.run(
            self.cfg.wsl.distro,
            [
                "sh",
                "-c",
                f'mkdir -p "{target}" && cd "{layer}/up" && find . -type f '
                f'-exec cp --parents -t "{target}" {{}} +',
            ],
            timeout=120,
        )
        if copy.returncode != 0:
            LOG.warning(
                "could not copy the files the script wrote: %s",
                copy.stderr.strip()[:200],
            )
        else:
            count = sum(
                1 for p in (self.dir / "project-writes").rglob("*") if p.is_file()
            )
            if count:
                LOG.info(
                    "the script wrote %d file(s) into its folder; they are in "
                    "%s (the folder itself is unchanged)",
                    count,
                    self.dir / "project-writes",
                )
        # the layer's own folders are not removable by plain rm (overlay work
        # dir): remove it from inside podman's user namespace
        wsl.run(
            self.cfg.wsl.distro,
            ["podman", "unshare", "rm", "-rf", layer],
            timeout=60,
        )

    def _idle(self) -> None:
        """Keep the simulator running until Ctrl+C, Unreal exits or a SITL
        container stops."""
        if self.dry:
            LOG.info("[dry-run] would now wait for Ctrl+C")
            return
        hosts = ", ".join(vp.connect_string() for vp in self.vehicles)
        LOG.info("simulator is up. Vehicles: %s. Press Ctrl+C to stop.", hosts)
        last_check = 0.0
        while True:
            time.sleep(1.0)
            if self.unreal is not None and not self.unreal.alive():
                LOG.error("Unreal exited")
                return
            if time.monotonic() - last_check >= 5.0:
                last_check = time.monotonic()
                dead = [
                    sitl_name(vp.index)
                    for vp in self.vehicles
                    if not self.podman.is_running(sitl_name(vp.index))
                ]
                if dead:
                    LOG.error("SITL container(s) stopped: %s", ", ".join(dead))
                    return

    # === UTILITIES ============================================================

    def _labels(self) -> dict[str, str]:
        """The labels put on every container of this run, so that `reap` can
        find them."""
        return {LABEL: "1", f"{LABEL}.session": self.id}

    def _follow(self, name: str, filename: str) -> None:
        """Start copying a container's output into a file in the run's
        folder."""
        follower = self.podman.follow_logs(name, self.dir / filename)
        if follower is not None:
            self._followers.append(follower)

    def _close_followers(self) -> None:
        """Stop every container log reader, even if one of them fails to stop;
        then raise if any did."""
        failed = []
        for follower in self._followers:
            try:
                follower.stop()
            except Exception as exc:  # the others still have to be stopped
                failed.append(str(exc))
        if failed:
            raise SessionError("; ".join(failed))

    @staticmethod
    def _tee(stream, handle) -> None:
        """Print each line the script prints (marked [script]) and write it to
        the script's log file."""
        for line in stream:
            print(f"[script] {line.rstrip()}", flush=True)
            handle.write(line)
            handle.flush()

    @staticmethod
    def _tail(path: Path, lines: int = 30) -> str:
        """The last `lines` lines of a file, or "(no log)" if it cannot be
        read."""
        try:
            return "\n".join(
                path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
            )
        except OSError:
            return "(no log)"

    @staticmethod
    def _wait_for_text(path: Path, needle: str, timeout: float, alive) -> bool:
        """Wait until `needle` appears in the log at `path`. Gives up after
        `timeout` seconds, or once `alive()` says the container is gone
        (after one last look)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in Session._tail(path, 400):
                return True
            if not alive():
                time.sleep(
                    0.5
                )  # one last read: the container may have printed then exited
                return needle in Session._tail(path, 400)
            time.sleep(0.5)
        return False

    def _safe(self, what: str, fn) -> None:
        """Run one shut-down step: say what is being done, then whether it
        worked. A step fails by raising or by returning False. The failure is
        logged and remembered for the summary, never raised."""
        LOG.info("%s...", what)
        reason = ""
        try:
            worked = fn() is not False
        except Exception as exc:  # shutdown must keep going whatever fails
            worked, reason = False, f": {exc}"
        if worked:
            LOG.info("%s: done", what)
        else:
            LOG.warning("%s: FAILED%s", what, reason)
            self._problems.append(what)


# === COMMANDS WITHOUT A SESSION ===============================================


def stop_all(cfg: Config, *, dry_run: bool = False) -> None:
    """`mrrsim down`: stop whatever a previous (possibly crashed) launcher left
    behind."""
    podman = Podman(cfg.wsl.distro, init=cfg.wsl.use_init, dry_run=dry_run)
    state = read_state() or {}
    rows = podman.ps()
    names = [n for row in rows for n in (row.get("Names") or [])]
    helpers = [n for n in names if n in (SCRIPT_NAME, SCENE_NAME)]
    podman.stop(helpers, timeout=5)

    pid = state.get("unreal_pid")
    if pid and pid_is(int(pid), cfg.unreal.exe):
        LOG.info("stopping Unreal (pid %s)", pid)
        if not dry_run:
            terminate_pid(int(pid), cfg.unreal.close_grace_s)
    sitl = [n for n in names if n not in helpers]
    podman.stop(sitl, timeout=cfg.sitl.stop_timeout_s)
    if cfg.sitl.save_logs and sitl and state.get("session"):
        saved = save_sitl_logs(
            podman,
            sitl,
            Path(
                state.get("log_root")
                or Path.cwd() / cfg.sitl.log_dir / state["session"]
            ),
        )
        if saved:
            LOG.info("ArduPilot logs saved to %s", saved[0].parent)
    removed = podman.reap()
    LOG.info(
        "removed %d container(s)%s",
        len(removed),
        f": {', '.join(removed)}" if removed else "",
    )
    if not dry_run:
        _clear_state()


def status(cfg: Config) -> int:
    """`mrrsim status`: print the recorded run, the containers and each
    vehicle's heartbeat. Returns 0."""
    state = read_state()
    podman = Podman(cfg.wsl.distro, init=cfg.wsl.use_init)
    if not state:
        print("launcher: no active session recorded")
    else:
        launcher = state.get("launcher_pid")
        unreal = state.get("unreal_pid")
        print(f"session:  {state.get('session')}  (logs: {state.get('dir')})")
        running = launcher and pid_alive(int(launcher))
        state_text = "running" if running else "NOT running (stale state)"
        print(f"launcher: pid {launcher} {state_text}")
        if unreal:
            state_text = "running" if pid_alive(int(unreal)) else "NOT running"
            print(f"unreal:   pid {unreal} {state_text}")
    rows = podman.ps()
    print(f"containers ({len(rows)}):")
    for row in rows:
        names = ",".join(row.get("Names") or ["?"])
        print(f"  {names:18} {row.get('State', '?'):10} {row.get('Image', '')}")
    count = (
        int(state.get("vehicles", cfg.sitl.vehicles)) if state else cfg.sitl.vehicles
    )
    for vp in ports.all_vehicle_ports(count):
        if not health.tcp_open("127.0.0.1", vp.monitor):
            print(
                f"vehicle {vp.index} (sysid {vp.sysid}): not listening on "
                f"tcp/{vp.monitor}"
            )
            continue
        ok, detail = health.wait_heartbeat("127.0.0.1", vp.monitor, vp.sysid, timeout=6)
        heartbeat = detail if ok else "NO HEARTBEAT: " + detail
        print(
            f"vehicle {vp.index} (sysid {vp.sysid}): {heartbeat}; "
            f"connect {vp.connect_string()}"
        )
    return 0
