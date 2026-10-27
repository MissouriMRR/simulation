# launcher/

`mrrsim`: starts the whole simulator from one command and shuts it down in the right order.
Python 3.11+, standard library only, so it freezes small with PyInstaller later. Student scripts
do **not** run on this interpreter; they run on Python 3.12 inside a container.

## Run it

From the repo root, no install needed:

```powershell
.\mrrsim.cmd doctor
```

Or install it into a venv: `pip install -e launcher` gives you an `mrrsim` command.

| Command | What it does |
|---|---|
| `mrrsim doctor` | read-only checks: Windows/RAM/GPU/disk, WSL distro + mirrored networking, podman, images, Unreal build, scene file, script, free ports |
| `mrrsim images build [sitl\|script\|all]` | build the container images inside WSL (needs network) |
| `mrrsim images list` | which images exist |
| `mrrsim images test` | offline smoke test of each image: SITL starts, DroneKit/pymavlink and the Project AirSim client import |
| `mrrsim gui` | open the window: pick a script, set vehicles / cameras / window size, Run and Stop (installed as `MRRSimulatorGUI.exe`) |
| `mrrsim scripts` | list your scripts (`Documents\MRRSimulator\scripts`) and the examples |
| `mrrsim run takeoff_land.py` | run one script (a name from `scripts`, or a path); picks the right image and vehicle count from the file |
| `mrrsim run flight\mission.py --project C:\code\proj` | run a file of an existing project folder: the whole folder is mounted at `/work` and importable, whatever depth the file is at (the window's **Add project folder...**) |
| `mrrsim pip install tqdm` | install Python packages the script images lack into `Documents\MRRSimulator\packages` (needs the internet once; also `pip remove NAME`, `pip list`, `pip reset`) |
| `mrrsim designs` | list your vehicle designs (made in the Vehicles tab of the window) and the provided vehicle |
| `mrrsim run script.py --design NAME` | fly a vehicle design instead of the provided vehicle: every vehicle of the run is a copy of it (see VEHICLE_SETTINGS.md) |
| `mrrsim up` | start Unreal, load the scene, start SITL, run the script, shut down |
| `mrrsim up --vehicles 2 --script takeoff_land_all.py` | 2 vehicles: a scene with one robot per vehicle is generated from the single-vehicle one (`--scene` to use your own; checked in preflight) |
| `mrrsim up --no-cameras` | load a copy of the scene with every camera sensor disabled (opt-in; never automatic) |
| `mrrsim up --resolution 960x540` | smaller Unreal window and render (also `[unreal] width/height`); `--fullscreen` for fullscreen |
| `mrrsim wsl-config` | write the `[wsl] memory` / `processors` caps into `%USERPROFILE%\.wslconfig` (backup kept); apply with `wsl --shutdown` |
| `mrrsim up --script my.py --keep` | a different script, leave the sim running afterwards |
| `mrrsim wsl-config [--only-networking]` | write `networkingMode=mirrored` and the `[wsl]` memory / processor caps into `.wslconfig` (backup kept); `--only-networking` skips the caps |
| `mrrsim up --no-script` | simulator only; connect your own tools to `tcp:127.0.0.1:5762` |
| `mrrsim up --dry-run` | print every container command instead of running it |
| `mrrsim status` | processes, containers, and a live heartbeat check per vehicle |
| `mrrsim logs [name] [-f]` | `launcher`, `unreal`, `scene`, `sitl-0`, `script` of the latest session |
| `mrrsim down` | stop anything a crashed run left behind |
| `mrrsim bundle` | zip logs + diagnostics for a bug report |

Exit codes: `0` ok, `1` doctor found failures, `2` configuration or startup error, `130` interrupted;
when a script runs, its own exit code is returned.

## What happens on `mrrsim up`

```text
preflight (doctor) -> reap leftover containers (label mrrsim)
  -> Unreal (native Windows, in a kill-on-close Job Object) ... wait for tcp 8989/8990
  -> wait until a process inside WSL can connect to Unreal (the first run of a new exe location makes
     Windows Firewall ask "Allow?"; WSL is refused until someone clicks Allow)
  -> scene-loader container (--hold) ... wait for "Scene loaded" in its log
  -> one SITL container per vehicle ... wait for a MAVLink heartbeat on tcp 5763+10i from the right sysid
  -> script container (attached) ... its output is shown as [script] and saved
stop: script -> scene -> Unreal -> SITL
```

ArduPilot's flight logs (`.BIN`) are copied to `logs/<session>/sitl-<i>/` under the folder you ran
`mrrsim` from (`[sitl] save_logs`, `log_dir`). Everything else is logged to `%LOCALAPPDATA%\MRRSimulator\sessions\<timestamp>\`
(or the `DataDir` from the installer's `install.ini`, or `MRRSIM_DATA_DIR`).

## Layout

Everything is in the `mrrsim` package (`launcher/mrrsim/`), grouped by what it is for. Entry points: `__main__.py`
(`python -m mrrsim`), `cli.py` (the command-line launcher), and `ui/app.py` (the window). The built exes start from
`launcher/run_mrrsim.py` and `launcher/run_gui.py`.

| Folder / file | Role |
|---|---|
| `cli.py` | argument parsing and commands (`run`, `up`, `down`, `doctor`, `pip`, ...) |
| `core/config.py` | `mrrsim.toml` loading; unknown keys are errors with a "did you mean" hint |
| `core/paths.py` | where the config, user data, sessions and the repository live |
| `core/ports.py` | the per-vehicle port scheme |
| `core/inifile.py`, `core/log.py` | reading `.ini`/`.wslconfig` style files; logging |
| `runtime/session.py` | the start/stop state machine, `down`, `status` |
| `runtime/podman.py` | podman-in-WSL wrapper; labels containers so they can always be reaped; `--dry-run` support |
| `runtime/wsl.py` | `wsl.exe --exec` helper, distro list, `.wslconfig` parsing, Windows -> `/mnt/c` paths |
| `runtime/unreal.py` | start/stop the packaged build, readiness wait |
| `runtime/winproc.py` | Job Object (`KILL_ON_JOB_CLOSE`), the installer's two mutexes, pid helpers |
| `runtime/health.py` | TCP wait and a stdlib MAVLink heartbeat detector |
| `runtime/doctor.py`, `runtime/bundle.py` | the checks (also the start of every run) and the support bundle |
| `scene/scenegen.py`, `scene/scenecheck.py` | generate an N-vehicle scene from the single-vehicle one; check a scene has one robot per vehicle |
| `scene/poseargs.py` | `--position`/`--rotation`/`--vehicle-pose` in the script arguments: where the vehicles spawn |
| `scene/camerastream.py` | the camera's "stream pictures to the script (UDP)" setting: which streams a design asks for, and removing the setting from the robot configs the simulator reads |
| `userscripts/scripts.py` | finds student scripts and reads what they need (imports `projectairsim` -> airsim image; `# mrrsim: vehicles=2`) |
| `userscripts/projects.py` | the remembered project folders (`Documents\MRRSimulator\projects.json`) and the scan that lists their runnable files |
| `userscripts/pipinstall.py` | `mrrsim pip install`: runs pip in the script image into one folder and import-tests the result |
| `designs/vehicle_schema.py` | the catalog of every Project AirSim robot-config setting (types, ranges, defaults, help, where docs and code disagree) |
| `designs/vehicle_design.py` | a vehicle design as blocks: add/remove/rename with references, undo, validation, and the folder of saved designs |
| `ui/app.py` | the window shell: Tk window, fonts, the three tabs, the launcher process, and what the tabs share |
| `ui/run/` | the Run tab (`run_tab.py`) and the commands it sends and the answers it reads (`commands.py`) |
| `ui/vehicles/vehicle_tab.py` | the Vehicles tab (a form made from the catalog) |
| `ui/simulator/` | the Simulator tab (`simulator_tab.py`) and Unreal's window embedding (`embed.py`) |
| `ui/common/` | what the tabs share: `launcher_process.py` (the process the window runs), `output.py`, `values.py` (value boxes), `files.py` |

The window runs `mrrsim run ...` as a background process and shows its output; it never starts the simulator itself.
Tests mirror this: `tests/ui/` has one file per tab and for the shared parts.

Tests (no WSL or podman needed):

```powershell
cd launcher; python -m unittest discover -s tests -t .
```

## Known limits

- **Job Object and containers**: the Job Object kills Unreal if the launcher dies, but it cannot
  reach containers inside WSL. They are labelled `mrrsim=1` and removed on the next `up` / `down`.
- **One-shot health check**: SITL accepts one client per serial port, so SERIAL2 is only probed at
  startup and by `status`. A persistent monitor needs its own connection design.
- **wsl.exe argument quoting**: arguments with spaces or quotes pass through `wsl.exe --exec` but
  this is the least-tested part. Avoid spaces in `[sitl] extra_args` values.
- **Unreal start**: the exe is assigned to the Job Object right after it starts, so a child it
  spawns within that window would not be covered. The Shipping exe is started directly (not the
  `Simulation.exe` stub) to avoid the stub's extra process.
- **Windows firewall prompt**: Unreal listens on all interfaces (`tcp://*:8989`), so Windows may
  show a firewall prompt on first run. Declining is fine for localhost use.
