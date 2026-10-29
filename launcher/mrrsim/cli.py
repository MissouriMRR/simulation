"""Command line of the launcher: mrrsim doctor | up | run | scripts | gui |
designs | pip | down | status | logs | wsl-config | bundle | images."""

from __future__ import annotations

import _thread
import argparse
import dataclasses
import re
import sys
import threading
import time
from pathlib import Path

from . import __version__
from .core import config, paths
from .core.log import setup
from .designs import vehicle_design
from .runtime import bundle, doctor, session, wsl
from .runtime.podman import Podman
from .scene import camerastream, poseargs, scenecheck, scenegen
from .userscripts import pipinstall, projects, scripts

# === CONSTANTS ================================================================


_RUNTIME_CHECK = (
    "import sys, dronekit, pymavlink; from pymavlink import mavutil; "
    "print('dronekit and pymavlink import on Python', sys.version.split()[0])"
)
_AIRSIM_CHECK = (
    "import sys, dronekit; import projectairsim; from projectairsim import "
    "ProjectAirSimClient, World; "
    "print('projectairsim and dronekit import on Python', "
    "sys.version.split()[0])"
)
_RELAY_CHECK = (
    "import sys; sys.path.insert(0, '/opt/mrrsim'); import camera_relay, "
    "scene_loader; "
    "print('camera relay and scene loader import')"
)


# === OPTION HELPERS ===========================================================


def _adopt_full_image(
    cfg: config.Config,
    who: str,
    beyond_light: frozenset[str],
    needs_airsim: bool,
) -> None:
    """Run in the full image (the one with Project AirSim, OpenCV, numpy and the
    packages `mrrsim pip install` adds) when `who` needs more than the light
    image has."""
    if cfg.scripts.image == cfg.scene.image or not (
        needs_airsim or beyond_light
    ):
        return
    reason = (
        "imports projectairsim"
        if needs_airsim
        else "imports " + ", ".join(sorted(beyond_light)[:5])
    )
    print(f"{who} {reason}: using image {cfg.scene.image}")
    cfg.scripts.image = cfg.scene.image


def _project_script(
    cfg: config.Config, value: str, project: str
) -> scripts.ScriptInfo:
    """Run a file of a project folder: the whole folder is what the script
    container sees."""
    root = Path(project).resolve()
    if not root.is_dir():
        raise config.ConfigError(f"--project {project}: not a folder")
    given = Path(value)
    path = (
        given
        if given.is_absolute()
        else (root / value if (root / value).is_file() else Path.cwd() / value)
    )
    if not path.is_file():
        raise config.ConfigError(
            f"--script {value}: not found in the project {root}"
        )
    path = path.resolve()
    if not projects.contains(root, path):
        raise config.ConfigError(
            f"{path} is not inside the project folder {root}"
        )
    cfg.scripts.dir = root
    cfg.scripts.main = projects.relative_name(root, path)
    # a codebase writes its own state files (state.json, results/): into a
    # scratch layer
    cfg.scripts.overlay = True
    info = scripts.describe(path)
    scan = projects.scan(root)
    if (
        scan.needs_airsim
    ):  # some file of the project may be the one that imports it
        info = dataclasses.replace(info, needs_airsim=True)
    _adopt_full_image(
        cfg, "the project", scan.full_image_for, info.needs_airsim
    )
    print(
        f"project {root.name}: running {cfg.scripts.main} with the whole "
        "folder available"
    )
    return info


def _override_script(
    cfg: config.Config, value: str, project: str | None = None
) -> scripts.ScriptInfo:
    """Pick the script to run, and adopt what the file itself says it needs: the
    image that has Project AirSim if it imports it. Returns what was found out
    about the script."""
    if project:
        return _project_script(cfg, value, project)
    given = Path(value)
    inside = cfg.scripts.dir / value
    user_copy = paths.user_scripts_dir() / value
    if not given.is_absolute() and inside.is_file():
        cfg.scripts.main = value
    elif not given.is_absolute() and user_copy.is_file():
        cfg.scripts.dir = user_copy.parent
        cfg.scripts.main = value
    elif given.is_file():
        cfg.scripts.dir = given.resolve().parent
        cfg.scripts.main = given.name
    else:
        raise config.ConfigError(
            f"--script {value}: not found (looked in {cfg.scripts.dir}, "
            f"{paths.user_scripts_dir()} and the current folder)"
        )
    info = scripts.describe(cfg.scripts.dir / cfg.scripts.main)
    _adopt_full_image(
        cfg,
        info.name,
        scripts.needs_full_image(
            info.imports, scripts.local_names(cfg.scripts.dir)
        ),
        info.needs_airsim,
    )
    return info


def _override_scene(cfg: config.Config, value: str) -> None:
    """`--scene`: use this scene file (a name inside the scene folder, or a path
    to one); an error if it cannot be found."""
    given = Path(value)
    if not given.is_absolute() and (cfg.scene.config_dir / value).is_file():
        cfg.scene.scene = value
    elif given.is_file():
        cfg.scene.config_dir = given.resolve().parent
        cfg.scene.scene = given.name
    else:
        raise config.ConfigError(
            f"--scene {value}: not found (looked in {cfg.scene.config_dir} and "
            "the current folder)"
        )


def _override_resolution(cfg: config.Config, value: str) -> None:
    """`--resolution WxH`: set the size of the Unreal window and render, for
    example 960x540."""
    match = re.fullmatch(r"(\d+)[xX](\d+)", value.strip())
    if not match:
        raise config.ConfigError(
            f"--resolution {value!r}: expected WIDTHxHEIGHT, for example "
            "960x540"
        )
    cfg.unreal.width, cfg.unreal.height = (
        int(match.group(1)),
        int(match.group(2)),
    )


def _load_design(cfg: config.Config, name: str) -> tuple[str, dict]:
    """A vehicle design made in the Vehicles tab, ready to be the template of
    the scene: (slug, robot config). Refuses a design that would not work (the
    problems are listed)."""
    library = vehicle_design.Library(paths.vehicles_dir(), cfg.scene.config_dir)
    try:
        design = library.load(name)
    except vehicle_design.DesignError as exc:
        raise config.ConfigError(
            f"--design {name}: {exc} (designs are in {paths.vehicles_dir()})"
        ) from None
    errors = [i.message for i in design.issues() if i.level == "error"]
    if errors:
        raise config.ConfigError(
            f"the vehicle design {name!r} has problems; fix them in the "
            "Vehicles tab:\n  - " + "\n  - ".join(errors)
        )
    why = design.runnable_problem()
    if why:
        raise config.ConfigError(
            f"the vehicle design {name!r} cannot be flown here: {why}"
        )
    return vehicle_design.slug(name), design.doc


def _apply_script_and_vehicles(
    cfg: config.Config, args: argparse.Namespace
) -> None:
    """--script/--project and --vehicles: choose the script, and how many
    vehicles to start (--vehicles wins; otherwise the number the script asks for
    in its header, if any)."""
    script_info = (
        _override_script(cfg, args.script, getattr(args, "project", None))
        if args.script
        else None
    )
    if args.vehicles is not None:
        cfg.sitl.vehicles = args.vehicles
    elif script_info is not None and script_info.vehicles:
        cfg.sitl.vehicles = script_info.vehicles
        print(f"{script_info.name} asks for {script_info.vehicles} vehicle(s)")


def _apply_script_arguments(
    cfg: config.Config, args: argparse.Namespace
) -> poseargs.Pose:
    """--script-args: set the arguments the script gets. The spawn pose options
    (--position/--rotation/--vehicle-pose) are the launcher's, so they are taken
    out first; returns that pose (empty when none was given)."""
    if getattr(args, "script_args", None):
        try:
            cfg.scripts.args = scripts.split_args(args.script_args)
        except ValueError as exc:
            raise config.ConfigError(
                f"--script-args {args.script_args!r}: {exc}"
            ) from exc
    pose = poseargs.Pose()
    if cfg.scripts.args:
        try:
            cfg.scripts.args, pose = poseargs.extract(
                cfg.scripts.args, cfg.sitl.vehicles
            )
        except poseargs.PoseError as exc:
            raise config.ConfigError(str(exc)) from exc
    return pose


def _apply_window_options(
    cfg: config.Config, args: argparse.Namespace
) -> None:
    """--resolution and --fullscreen: the size and mode of the Unreal window."""
    if args.resolution:
        _override_resolution(cfg, args.resolution)
    if args.fullscreen:
        cfg.unreal.windowed = False


# === SCENE PREPARATION ========================================================


def _say_placement(pose: poseargs.Pose | None, count: int) -> None:
    """Print one line per vehicle saying where it spawns (nothing when no pose
    was given)."""
    for line in pose.describe(count) if pose else []:
        print(line)


def _prepare_scene(
    cfg: config.Config,
    args: argparse.Namespace,
    pose: poseargs.Pose | None = None,
) -> None:
    """Pick or generate the scene that matches --vehicles, --no-cameras,
    --design and the spawn pose."""
    count, cameras = cfg.sitl.vehicles, not args.no_cameras
    pose = pose or None  # an empty pose is no pose
    if pose and args.scene:
        raise config.ConfigError(
            "--position/--rotation/--vehicle-pose place the vehicles of a "
            "scene the launcher generates: "
            "they cannot be used with --scene (a scene you made yourself "
            "places its own vehicles)"
        )
    if pose and args.no_scene:
        print("note: --position/--rotation have no effect with --no-scene")
        pose = None
    design_name = getattr(args, "design", None)
    if design_name and args.scene:
        raise config.ConfigError(
            "--design and --scene cannot be used together: a design makes its "
            "own scene"
        )
    if args.scene:
        _override_scene(cfg, args.scene)
    if args.no_scene or not cfg.scene.scene:
        return
    folder, current = cfg.scene.config_dir, cfg.scene.scene
    window = (cfg.unreal.width, cfg.unreal.height)
    if design_name:
        design = _load_design(cfg, design_name)
        cfg.scene.scene = scenegen.generate(
            folder,
            current,
            count,
            cameras=cameras,
            window=window,
            design=design,
            pose=pose,
        )
        print(
            f"generated {cfg.scene.scene} ({count} vehicle(s)) with the "
            f"vehicle design {design_name!r}"
        )
        _say_placement(pose, count)
        if cameras:
            cfg.scene.camera_streams = camerastream.streams(design[1], count)
            for stream in cfg.scene.camera_streams[:: max(count, 1)]:
                print(
                    f"camera {stream['camera']} sends its pictures to UDP "
                    f"port {stream['port']} "
                    f"(receive them with mrrsim_camera.CameraReceiver)"
                )
        return
    if cameras and not pose:
        if args.scene:
            # the student's own scene is used as it is; doctor checks that it
            # matches
            return
        if count == 1 and not scenegen.display_resize_needed(
            folder, current, window
        ):
            return  # the original scene, nothing to adjust
        if (
            count > 1
            and (folder / current).is_file()
            and not scenecheck.check_scene(folder, current, count)
        ):
            return  # the configured scene already has the right robots
    cfg.scene.scene = scenegen.generate(
        folder, current, count, cameras=cameras, window=window, pose=pose
    )
    notes = (
        ", cameras disabled"
        if not cameras
        else (
            f", streamed camera set to the {window[0]}x{window[1]} window"
            if cfg.scene.scene != current
            and scenegen.display_resize_needed(folder, current, window)
            else ""
        )
    )
    print(
        f"generated {cfg.scene.scene} ({count} vehicle(s){notes}) from "
        f"{current}"
    )
    _say_placement(pose, count)


# === STOP REQUEST =============================================================


def _watch_stop_file(path: Path) -> None:
    """Let another program (the window) ask for a graceful stop: every time
    `path` is created or touched, behave as if Ctrl+C was pressed. The first
    press lets the script land the vehicle, the second stops it at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)

    def watch() -> None:
        last = None
        while True:
            time.sleep(0.4)
            try:
                stamp = path.stat().st_mtime_ns
            except OSError:
                continue
            if stamp != last:
                last = stamp
                if not session.SHUTTING_DOWN.is_set():
                    _thread.interrupt_main()

    threading.Thread(target=watch, name="stop-file", daemon=True).start()


# === COMMANDS: RUNNING ========================================================


def cmd_up(cfg: config.Config, args: argparse.Namespace) -> int:
    """`mrrsim up` (and `run`): apply the options to the settings, prepare the
    scene, then start a Session that runs everything. Returns the session's exit
    code, or 2 when there is no script to run."""
    _apply_script_and_vehicles(cfg, args)
    pose = _apply_script_arguments(cfg, args)
    _apply_window_options(cfg, args)
    cfg.validate()
    _prepare_scene(cfg, args, pose)
    if getattr(args, "stop_file", None):
        _watch_stop_file(Path(args.stop_file))
    want_script = not args.no_script
    if want_script and not cfg.scripts.main:
        print(
            "error: no script configured. Pass --script FILE, set [scripts] "
            "main, or use --no-script.",
            file=sys.stderr,
        )
        return 2
    sess = session.Session(cfg, dry_run=args.dry_run)
    return sess.up(
        script=want_script,
        keep=args.keep,
        unreal=not args.no_unreal,
        scene=not args.no_scene,
    )


def cmd_run(cfg: config.Config, args: argparse.Namespace) -> int:
    """`mrrsim run FILE`: the same as `up --script FILE`."""
    args.script = args.file
    return cmd_up(cfg, args)


def cmd_gui(cfg: config.Config, args: argparse.Namespace) -> int:
    """`mrrsim gui`: open the window."""
    from .ui import app  # tkinter is only needed here

    return app.main(cfg)


def cmd_down(cfg: config.Config, args: argparse.Namespace) -> int:
    """`mrrsim down`: stop everything a previous run left behind (Unreal and the
    containers)."""
    session.stop_all(cfg, dry_run=args.dry_run)
    return 0


def cmd_status(cfg: config.Config, args: argparse.Namespace) -> int:
    """`mrrsim status`: show processes, containers and vehicle heartbeats."""
    return session.status(cfg)


# === COMMANDS: INFORMATION ====================================================


def cmd_doctor(cfg: config.Config, args: argparse.Namespace) -> int:
    """`mrrsim doctor`: check this machine and the project files, print the
    report, and return 1 if any check failed."""
    checks = doctor.run_checks(cfg)
    print(doctor.format_report(checks))
    return 1 if any(c.status == doctor.FAIL for c in checks) else 0


def cmd_scripts(cfg: config.Config, args: argparse.Namespace) -> int:
    """`mrrsim scripts`: list your scripts, the examples and the project
    folders, with what each one needs."""
    found = scripts.discover(paths.user_scripts_dir(), cfg.scripts.dir)
    print(f"your scripts:  {paths.user_scripts_dir()}")
    print(f"examples:      {cfg.scripts.dir}\n")
    if not found:
        print("no scripts found")
    for info in found:
        notes = [
            n
            for n in (
                "Project AirSim" if info.needs_airsim else "",
                f"{info.vehicles} vehicles" if info.vehicles else "",
                f"! {info.problem}" if info.problem else "",
            )
            if n
        ]
        label = "mine    " if info.group == scripts.MINE else "example "
        extra = "  [" + ", ".join(notes) + "]" if notes else ""
        print(f"  {label}{info.name:28} {info.description[:60]}{extra}")
    for folder in projects.load():
        print(f"\nproject {folder}")
        if not folder.is_dir():
            print("  (folder not found)")
            continue
        scan = projects.scan(folder)
        for info in scan.entries:
            print(f"  {info.name:40} {info.description[:50]}")
        if not scan.entries:
            print("  no Python files")
    print(
        "\nrun one with:  mrrsim run <name>"
        "\n               mrrsim run <file> --project <folder>    (a file of "
        "a project folder)"
    )
    return 0


def cmd_designs(cfg: config.Config, args: argparse.Namespace) -> int:
    """`mrrsim designs`: list your vehicle designs (with any problems) and the
    provided vehicles."""
    library = vehicle_design.Library(paths.vehicles_dir(), cfg.scene.config_dir)
    print(f"your vehicle designs: {paths.vehicles_dir()}")
    chosen = library.selected()
    names = library.names()
    for name in names:
        try:
            design = library.load(name)
        except vehicle_design.DesignError as exc:
            print(f"  {name:30} ! {exc}")
            continue
        errors = sum(1 for i in design.issues() if i.level == "error")
        why = design.runnable_problem()
        notes = [
            n
            for n in (
                "selected in the window" if name == chosen else "",
                f"{errors} problem(s)" if errors else "",
                f"cannot fly here: {why}" if why else "",
            )
            if n
        ]
        print(f"  {name:30} {'; '.join(notes)}")
    if not names:
        print(
            "  none yet: open the Vehicles tab of the window and duplicate a "
            "vehicle"
        )
    provided = ", ".join(p.stem for p in library.provided()) or "none"
    print(f"provided (read-only): {provided}")
    print("\nfly one with:  mrrsim run <script> --design <name>")
    return 0


def _tail_lines(path: Path, count: int) -> str:
    """The last `count` lines of a text file."""
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]
    )


def cmd_logs(cfg: config.Config, args: argparse.Namespace) -> int:
    """`mrrsim logs [NAME]`: list the latest session's logs, or show the end of
    one (-n lines; -f keeps following it)."""
    latest = paths.latest_session()
    if latest is None:
        print("no sessions yet", file=sys.stderr)
        return 1
    if not args.name:
        print(f"session {latest.name} ({latest})")
        for path in sorted(latest.glob("*.log")):
            print(f"  {path.stem:12} {path.stat().st_size:>10} bytes")
        return 0
    path = latest / (
        args.name if args.name.endswith(".log") else f"{args.name}.log"
    )
    if not path.is_file():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 1
    print(_tail_lines(path, args.lines))
    if args.follow:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                handle.seek(0, 2)
                while True:
                    line = handle.readline()
                    if line:
                        print(line, end="", flush=True)
                    else:
                        time.sleep(0.3)
        except KeyboardInterrupt:
            pass
    return 0


def cmd_bundle(cfg: config.Config, args: argparse.Namespace) -> int:
    """`mrrsim bundle`: zip the logs and diagnostics for a bug report."""
    out = bundle.create_bundle(cfg)
    print(f"support bundle written to {out}")
    return 0


# === COMMANDS: SETUP ==========================================================


def cmd_pip(cfg: config.Config, args: argparse.Namespace) -> int:
    """`mrrsim pip install|remove|list|reset`: manage the extra Python packages
    that scripts can import."""
    return pipinstall.cli(cfg, args.action, args.names, args.dry_run)


def cmd_wsl_config(cfg: config.Config, args: argparse.Namespace) -> int:
    """Write the [wsl] settings from mrrsim.toml into .wslconfig: mirrored
    networking, memory, processors. With --only-networking just the mirrored
    networking, which is what the installer asks about."""
    values: dict[str, str] = {}
    if cfg.wsl.require_mirrored:
        values["networkingMode"] = "mirrored"
    if not getattr(args, "only_networking", False):
        if cfg.wsl.memory:
            values["memory"] = cfg.wsl.memory
        if cfg.wsl.processors:
            values["processors"] = str(
                wsl.effective_processors(cfg.wsl.processors)
            )
    if not values:
        print(
            "nothing to do: [wsl] require_mirrored is false and memory / "
            "processors are unset in mrrsim.toml"
        )
        return 0
    path = wsl.wslconfig_path()
    old = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    new = wsl.set_wsl2_keys(old, values)
    summary = ", ".join(f"{k}={v}" for k, v in values.items())
    if new.strip() == old.strip():
        print(f"{path} already has {summary}")
    else:
        if old and not args.dry_run:
            backup = path.with_name(path.name + ".mrrsim.bak")
            if not backup.exists():
                backup.write_text(old, encoding="utf-8")
                print(f"backup of the old file: {backup}")
        verb = "would write" if args.dry_run else "wrote"
        print(f"{verb} {path}: {summary}")
        if not args.dry_run:
            path.write_text(new, encoding="utf-8", newline="\r\n")
    print(
        "WSL reads this file at start-up. Apply it with:  wsl --shutdown   "
        "(stops every WSL distro; the next use restarts them)"
    )
    return 0


def _test_images(cfg: config.Config, podman: Podman, sitl_tag: str) -> int:
    """Offline smoke test of the built images: does each one start and import
    what it needs?"""
    probes = [
        (
            "sitl",
            sitl_tag,
            ["--entrypoint", "/opt/ardupilot/arducopter"],
            ["--help"],
            "--sysid",
        ),
        (
            "script",
            cfg.scripts.image,
            ["--entrypoint", "python"],
            ["-W", "ignore", "-c", _RUNTIME_CHECK],
            "import on Python",
        ),
        (
            "scene",
            cfg.scene.image,
            ["--entrypoint", "python"],
            ["-W", "ignore", "-c", _AIRSIM_CHECK],
            "import on Python",
        ),
        (
            "scene",
            cfg.scene.image,
            ["--entrypoint", "python"],
            ["/opt/mrrsim/scene_loader.py", "--help"],
            "--scene",
        ),
        (
            "scene",
            cfg.scene.image,
            ["--entrypoint", "python"],
            ["-W", "ignore", "-c", _RELAY_CHECK],
            "camera relay",
        ),
        (
            "script",
            cfg.scripts.image,
            [
                "--entrypoint",
                "python",
                "-e",
                "PYTHONPATH=" + session.SCRIPT_LIB,
            ],
            ["-c", "import mrrsim_camera; print('mrrsim_camera imports')"],
            "mrrsim_camera imports",
        ),
    ]
    failed = 0
    for label, image, entry, command, expect in probes:
        if not podman.image_exists(image):
            print(f"[FAIL] {label:7} {image}: not built (mrrsim images build)")
            failed += 1
            continue
        result = podman.run(
            ["run", "--rm", "--network", "none", *entry, image, *command],
            timeout=120,
            check=False,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0 and expect in output:
            shown = command[-1] if command[-1] == "--help" else expect
            print(f"[ OK ] {label:7} {image}: {shown}")
        else:
            lines = [line for line in output.splitlines() if line.strip()]
            last = lines[-1][:160] if lines else "no output"
            print(f"[FAIL] {label:7} {image}: {last}")
            failed += 1
    print(f"\n{len(probes)} checks, {failed} failed")
    return 1 if failed else 0


def cmd_images(cfg: config.Config, args: argparse.Namespace) -> int:
    """`mrrsim images build|list|test [sitl|script|all]`: build the container
    images, say which exist, or smoke-test them."""
    podman = Podman(cfg.wsl.distro, init=cfg.wsl.use_init, dry_run=args.dry_run)
    sitl_ref = args.ardupilot_ref or cfg.sitl.ardupilot_ref
    sitl_tag = cfg.sitl.image or f"localhost/mrrsim-sitl:{sitl_ref.lower()}"

    if args.action == "test":
        return _test_images(cfg, podman, sitl_tag)

    if args.action == "list":
        for label, ref in (
            ("sitl", sitl_tag),
            ("script", cfg.scripts.image),
            ("scene", cfg.scene.image),
        ):
            state = "present" if podman.image_exists(ref) else "missing"
            print(f"{state:8} {label:7} {ref}")
        return 0

    containers = paths.containers_dir()
    # Rootless build networking (slirp4netns/pasta) can crawl under WSL2 (seen:
    # ~0.08 MB/s for a git fetch while the host was fast). The host network
    # avoids that layer entirely.
    common = ["--no-cache"] if args.no_cache else []
    if args.build_network != "default":
        common += ["--network", args.build_network]
    for build_arg in args.build_arg:
        common += ["--build-arg", build_arg]
    if args.which in ("sitl", "all"):
        context = wsl.to_wsl_path(containers / "sitl", cfg.wsl.distro)
        rc = podman.build(
            [
                *common,
                "-t",
                sitl_tag,
                "--build-arg",
                f"ARDUPILOT_REF={sitl_ref}",
                context,
            ]
        )
        if rc != 0:
            return rc
    if args.which in ("script", "all"):
        context = wsl.to_wsl_path(containers / "script", cfg.wsl.distro)
        extra = (
            ["--build-arg", f"PROJECTAIRSIM_REF={args.projectairsim_ref}"]
            if args.projectairsim_ref
            else []
        )
        targets = [("airsim", cfg.scene.image)]
        if cfg.scripts.image != cfg.scene.image:
            targets.insert(0, ("runtime", cfg.scripts.image))
        for target, tag in targets:
            rc = podman.build(
                [*common, "--target", target, "-t", tag, *extra, context]
            )
            if rc != 0:
                return rc
    return 0


# === COMMAND LINE PARSER ======================================================


def _add_up_options(up: argparse.ArgumentParser, *, is_up: bool) -> None:
    """The options `up` and `run` share. `up` alone also takes --script and
    --no-script (`run` names its script)."""
    up.add_argument(
        "--vehicles",
        type=int,
        help="number of vehicles (overrides [sitl] vehicles)",
    )
    up.add_argument(
        "--scene",
        help="scene .jsonc to load (file name inside the sim_config folder, "
        "or a path); "
        "default: [scene] scene, or a generated "
        "scene_ardupilot_<N>quadrotors.jsonc when --vehicles > 1",
    )
    up.add_argument(
        "--design",
        metavar="NAME",
        help="fly a vehicle design made in the Vehicles tab (a name from your "
        "Documents folder, MRRSimulator, vehicles) instead of the "
        "provided vehicle; every vehicle of the run is a copy of it. Not "
        "together with --scene",
    )
    up.add_argument(
        "--no-cameras",
        action="store_true",
        help="load a copy of the scene with every camera sensor disabled "
        "(less rendering); never the default",
    )
    up.add_argument(
        "--resolution",
        metavar="WxH",
        help="Unreal window and render size, e.g. 960x540 (default: [unreal] "
        "width/height)",
    )
    up.add_argument(
        "--fullscreen",
        action="store_true",
        help="fullscreen instead of the default window",
    )
    if is_up:
        up.add_argument(
            "--script",
            help="script to run (file name inside the scripts folder, or a "
            "path)",
        )
        up.add_argument(
            "--no-script",
            action="store_true",
            help="start the simulator only; Ctrl+C stops it",
        )
        up.set_defaults(func=cmd_up)
    up.add_argument(
        "--project",
        metavar="DIR",
        help="run the script as part of this project folder: the whole folder "
        "is available to it "
        "(imports from anywhere in it work), and the script may be in a "
        "sub-folder",
    )
    up.add_argument(
        "--script-args",
        metavar="ARGS",
        help=(
            "arguments for the script, as one string; write it with an equals "
            "sign so a leading dash is not taken for an option of mrrsim: "
            '--script-args="-s --fast" (replaces [scripts] args). With '
            "--airsim among them, --position X Y Z, --rotation ROLL PITCH YAW "
            "(metres and degrees, NED) and --vehicle-pose "
            "N=x,y,z,roll,pitch,yaw choose where the vehicles spawn; the "
            "launcher uses them and the script does not receive them: "
            '--script-args="--airsim --position 5 3 -2 --rotation 0 0 90"'
        ),
    )
    up.add_argument(
        "--stop-file", help=argparse.SUPPRESS
    )  # touched by the window to request a graceful stop
    up.add_argument(
        "--keep",
        action="store_true",
        help="keep the simulator running after the script ends",
    )
    up.add_argument(
        "--no-unreal",
        action="store_true",
        help="use an Unreal that is already running",
    )
    up.add_argument(
        "--no-scene", action="store_true", help="skip scene loading"
    )
    up.add_argument(
        "--dry-run",
        action="store_true",
        help="print the container commands instead of starting anything",
    )


def build_parser() -> argparse.ArgumentParser:
    """Define the command line: the global options and every command with its
    arguments."""
    parser = argparse.ArgumentParser(
        prog="mrrsim", description="MRRSimulator launcher"
    )
    parser.add_argument(
        "--version", action="version", version=f"mrrsim {__version__}"
    )
    parser.add_argument(
        "-c", "--config", help="path to mrrsim.toml (default: auto-detect)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show debug output"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "doctor", help="check this machine and the project files"
    ).set_defaults(func=cmd_doctor)

    up = sub.add_parser(
        "up", help="start Unreal, load the scene, start SITL, run a script"
    )
    run = sub.add_parser(
        "run", help="run one script: the same as `up --script FILE`"
    )
    run.add_argument(
        "file", help="script to run: a name from `mrrsim scripts`, or a path"
    )
    run.set_defaults(func=cmd_run, script=None, no_script=False)
    sub.add_parser(
        "scripts", help="list your scripts and the examples"
    ).set_defaults(func=cmd_scripts)
    sub.add_parser(
        "gui", help="open the window for picking and running scripts"
    ).set_defaults(func=cmd_gui)
    sub.add_parser(
        "designs", help="list your vehicle designs (made in the Vehicles tab)"
    ).set_defaults(func=cmd_designs)
    pipc = sub.add_parser(
        "pip",
        help="add Python packages the script images do not have (needs "
        "network once)",
    )
    pipc.add_argument(
        "action",
        choices=["install", "remove", "list", "reset"],
        help="install NAME...  |  remove NAME...  |  list what was installed  "
        "|  reset: delete all installed packages",
    )
    pipc.add_argument(
        "names", nargs="*", help="package names, e.g. tqdm  requests==2.32.3"
    )
    pipc.add_argument(
        "--dry-run",
        action="store_true",
        help="print the container command instead of running it",
    )
    pipc.set_defaults(func=cmd_pip)
    _add_up_options(up, is_up=True)
    _add_up_options(run, is_up=False)

    down = sub.add_parser(
        "down", help="stop everything a previous run left behind"
    )
    down.add_argument("--dry-run", action="store_true")
    down.set_defaults(func=cmd_down)

    sub.add_parser(
        "status", help="show processes, containers and vehicle heartbeats"
    ).set_defaults(func=cmd_status)

    logs = sub.add_parser("logs", help="show logs of the latest session")
    logs.add_argument(
        "name",
        nargs="?",
        help="launcher | unreal | scene | sitl-0 | script (omit to list)",
    )
    logs.add_argument("-n", "--lines", type=int, default=200)
    logs.add_argument("-f", "--follow", action="store_true")
    logs.set_defaults(func=cmd_logs)

    wslc = sub.add_parser(
        "wsl-config",
        help="write mirrored networking and the [wsl] memory/processors caps "
        "into .wslconfig",
    )
    wslc.add_argument("--dry-run", action="store_true")
    wslc.add_argument(
        "--only-networking",
        action="store_true",
        help="only networkingMode=mirrored, not the memory and processor caps",
    )
    wslc.set_defaults(func=cmd_wsl_config)

    sub.add_parser(
        "bundle", help="zip logs and diagnostics for a bug report"
    ).set_defaults(func=cmd_bundle)

    images = sub.add_parser(
        "images", help="build, list or smoke-test the container images"
    )
    images.add_argument("action", choices=["build", "list", "test"])
    images.add_argument(
        "which", nargs="?", choices=["sitl", "script", "all"], default="all"
    )
    images.add_argument(
        "--ardupilot-ref",
        help="tag or commit SHA (default: [sitl] ardupilot_ref)",
    )
    images.add_argument(
        "--projectairsim-ref",
        help="Project AirSim git ref for the airsim image",
    )
    images.add_argument("--no-cache", action="store_true")
    images.add_argument(
        "--build-arg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="extra podman build argument, repeatable (e.g. SKIP_SUBMODULES=)",
    )
    images.add_argument(
        "--build-network",
        default="host",
        metavar="MODE",
        help="podman build --network value (default: host; use 'default' for "
        "podman's own rootless networking)",
    )
    images.add_argument("--dry-run", action="store_true")
    images.set_defaults(func=cmd_images)
    return parser


# === ENTRY POINT ==============================================================


def main(argv: list[str] | None = None) -> int:
    """Entry point of the command-line launcher: parse the arguments, load the
    settings and run the command. Returns its exit code: 2 for a settings or
    argument error, 130 for Ctrl+C."""
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(
            errors="replace"
        )  # script output may not fit the console code page
    setup(args.verbose)
    try:
        cfg = config.load(paths.find_config(args.config))
        return args.func(cfg, args)
    except (FileNotFoundError, config.ConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
