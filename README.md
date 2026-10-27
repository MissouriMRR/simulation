# BakedSim: MRRSimulator

A student-team drone simulator: **Project AirSim** (packaged Unreal Engine build, native
Windows) flown by **ArduPilot SITL** and **DroneKit** scripts that run as containers in WSL2.

```text
Windows (native)                          WSL2 distro, rootless podman, --network host
  mrrsim launcher  ──── starts ───▶ Unreal (Simulation-Win64-Shipping.exe)
        │                                      ▲ UDP 9002/9003 + 10i
        └── wsl.exe -d mrr --exec podman ──▶ mrrsim-scene     loads the scene, stays connected
                                             mrrsim-sitl-0..N arducopter, one per vehicle
                                             mrrsim-script    Python 3.12 + DroneKit -> tcp:127.0.0.1:5762+10i
```

Windows and WSL share `127.0.0.1` because `.wslconfig` sets `networkingMode=mirrored`, so there
are no IP addresses to discover and no firewall rules to add.

## Layout

| Folder | Contents |
|---|---|
| [launcher/](launcher) | the `mrrsim` command (Python, stdlib only) and its tests |
| [containers/](containers) | `sitl/` (ArduPilot Copter at a pinned ref) and `script/` (Python 3.12 + DroneKit, pymavlink from the MRR package index, optional Project AirSim client) |
| [config/](config) | `mrrsim.toml`: every setting, with comments |
| [scripts/](scripts) | student scripts; `takeoff_land.py` is the smoke test |
| [sim_config/](sim_config) | Project AirSim scene + robot `.jsonc` (**you must add these**) |
| `Windows/` | the packaged Unreal build (about 1 GB; ignored by git) |
| `mrrsim.cmd` | runs the launcher straight from this folder |

## First run

0. WSL 2 must already be set up (Windows 11 22H2 or newer, WSL 2.0 or newer, virtualization on).
   The installer (`MRRSimulationLauncher.iss`) only checks this and stops with instructions if it is not;
   it never enables WSL itself.
1. `.\mrrsim.cmd doctor` to see what is missing.
2. Copy the scene and robot `.jsonc` files into [sim_config/](sim_config) and set `[scene] scene`
   in [config/mrrsim.toml](config/mrrsim.toml). See [sim_config/README.md](sim_config/README.md).
3. `.\mrrsim.cmd images build all` (downloads base images and clones ArduPilot and Project AirSim).
4. `.\mrrsim.cmd up` runs `scripts/takeoff_land.py`. Use `up --dry-run` first to read the commands.

## Building the installer

In Zed: **task: spawn** > *Rebuild installer (full ...)* or *(quick ...)* (`.zed/tasks.json`). From a terminal:
`tools\build_installer.cmd` (everything) or `tools\build_installer.cmd quick` (only the launcher and the installer,
reusing the Linux payload, which is right unless something under `containers\` changed). The result is
`Output\MRRSimulator-Setup-<version>.exe`. The steps it runs:

1. `mrrsim images build script` rebuilds the script images in your `mrr` distro (cached layers make it quick);
   the payload is made from those images, so this must come first when `containers\` changed.
2. `python tools/build_distro.py` makes `payload/mrr-rootfs.tar.gz` (about 700 MB): a clean Ubuntu 24.04
   WSL distribution with rootless podman, one user called `mrr`, and the three simulator images already
   loaded, so installing works offline. It downloads the official Ubuntu image on the first run (checked
   against a pinned checksum), takes the images from your own `mrr` with `podman save`, and tests the
   result. It never touches your `mrr` beyond reading those images. It also stamps the distribution with a
   version (`payload/distro_version.iss`) that changes whenever the images or the provisioning do.
3. `launcher\build.cmd` builds `MRRSimulator.exe` and `MRRSimulatorGUI.exe`.
4. Inno Setup compiles `MRRSimulationLauncher.iss`. At the end of an install, setup imports the payload as the
   WSL distribution `mrr` (for the account running setup). A distribution with that name that setup did not
   create is never replaced. One that setup created is replaced when its version stamp differs from the
   installer's (after asking; silent installs just do it). Your scripts and projects are not inside it.
   Uninstalling offers to remove it only if setup created it. On an upgrade, a `config\mrrsim.toml` you edited
   is kept as `mrrsim.toml.previous`.

Setup also checks `%USERPROFILE%\.wslconfig` for `networkingMode=mirrored`, which the simulator needs. If it
is missing, setup **asks first**: it explains that WSL must restart, which stops everything running in WSL
including Docker Desktop's engine. On Yes it adds the setting (the launcher's `wsl-config --only-networking`
keeps the rest of the file and saves a `.wslconfig.mrrsim.bak`), restarts WSL, and after the distribution is
in place checks that WSL really started mirrored. On No it changes nothing and says how to do it by hand.
Unattended installs: `/MIRRORED=yes` or `/MIRRORED=no`; with neither, nothing is changed.

To test an install without touching a real `mrr`: run setup with `/DISTRO=mrr-test` and point the
installed `config\mrrsim.toml` at the same name.

## The window

`MRRSimulatorGUI.exe` (the Start menu shortcut) lists your scripts and the examples. Pick one, choose
the number of vehicles, optionally turn the cameras off or shrink the simulator window, and press
**Run**. **Stop** lets the script land the vehicle first (press it again to stop at once). **Run doctor**
checks the computer and reports in the status line. **Kill Unreal** force-closes the simulator at once (for
when it hangs); if a run is going, the rest of it is stopped too. Your own
scripts live in `Documents\MRRSimulator\scripts`; **Copy to my scripts** makes an editable copy of an
example. ArduPilot flight logs are saved in `Documents\MRRSimulator\logs`.

**The simulator inside the window.** While a run is going, Unreal's window is moved into the **Simulator** tab
instead of opening as a separate window (Run tab: *Show the simulator inside this window*, on by default; windows
only). The tab also has a copy of the output underneath and the Altitude and Throttle boxes stacked on the right (the
divider between the simulator and the output can be dragged). The tab is selected when the picture appears; it follows the tab's size, the flight carries on while you look
at another tab, and Stop, Kill Unreal or closing MRRSimulator end it as before. Switch the option off to get the
separate Unreal window back. If the picture misbehaves on some graphics card (black, flickering), switch it off.

Beside the output are two value boxes, like a ground station's, for **Altitude** and **Throttle**. A script fills
them by printing a line of its own, `Altitude: 12.3` (metres) or `Throttle %: 53`; those lines are shown in the box
only, never in the output (the session's `script.log` still has them). Both clear when a run starts.

A script can say how many vehicles it wants with a comment near the top: `# mrrsim: vehicles=2`.
Two images exist: a light one (`dronekit`, `pymavlink`) and a full one (also OpenCV, numpy, Project AirSim, the
team's flight-code packages and everything installed with **Install package...**). A script that imports
anything beyond the light image's packages and its own modules (`cv2`, `numpy`, `projectairsim`, an installed
package ...) automatically runs in the full image; the launcher prints which imports caused it.

### Running an existing codebase

**Add project folder...** adds a folder you already have (for example a git checkout). Its Python files are
listed under the project's name at any depth, and Run starts the one you pick with the *whole folder* available
to it: the folder is mounted at `/work` (read-only to your files, see Writes) and put on `PYTHONPATH`, so `import mylib` finds the
project's own modules and packages wherever the entry file is. Nothing is copied, so edits show up on the
next Run (press **Refresh** after adding files). **Remove project** only forgets the folder; your files are
never touched. Projects are remembered in `Documents\MRRSimulator\projects.json`.

- Listed files are those with an `if __name__ == "__main__":` block plus the files in the project's top
  folder; a project with neither lists every file. Virtual environments, `.git`, `__pycache__` and `_private.py` files are left out.
- If *any* file of the project imports something the light image lacks (`projectairsim`, `cv2`, `numpy`, ...),
  the project runs in the full image. The project's own modules do not count.
- The relative path of the script is its title, and the working folder is the project's top folder, so
  `open("data.txt")` reads the project's own data files.
- **Writes:** a script or project may write next to its code (state files, result folders, logs). The folder is mounted as an
  *overlay*: the program reads your files and its writes go to a scratch layer, so **your folder is never
  changed**. After the run, what it wrote is copied to `project-writes` in the session folder (the log line
  says where). Files written to `$SIM_OUT_DIR` (`/out`) are kept with the session logs as for any script. Single scripts get the
  same overlay (the Project AirSim client always writes `projectairsim_client.log` into its working folder, which a
  read-only folder made fail on start).
- **Packages:** both images have `dronekit` (the team's Python 3.12 build) and `pymavlink` (from the MRR
  index). The Project AirSim image, which a project uses when any of its files import `projectairsim`, also
  has `numpy`, `cv2`, `matplotlib`, `requests`, `projectairsim`, and what the team's flight code imports:
  `utm`, `scipy`, `shapely`, `aiohttp`, `aiofiles`, `colorlog`, `onnxruntime`, `pyodm`, `siyi-sdk` and the
  `airsim` client. Others (`pandas`, `yaml`, `open3d`, `pytesseract`) are not included, and nothing can be
  installed while running (the setup is offline): add them to `containers/script/requirements-airsim.txt`
  and rebuild the images.
- **Script arguments:** the window's "Script arguments" box is passed to the script as command-line
  arguments and remembered for each script. A project that picks its connection from flags needs them: the
  SUAS flight code connects to real hardware (`/dev/ttyUSB0`) unless started with `-s`, which is
  `tcp:127.0.0.1:5762`, this simulator's DroneKit port. On the command line: `--script-args=-s`.
- **Where the vehicle spawns:** with `--airsim` among the script arguments, `--position X Y Z` and
  `--rotation ROLL PITCH YAW` place the vehicle, for example `--airsim --position 5 3 -2 --rotation 0 0 90`.
  Metres and degrees in the simulator's NED frame (X forward, Y right, Z down, so a negative Z is above the ground,
  and the vehicle drops to the ground if it is higher than that). Either may be given alone, the missing one keeps the
  scene's value. With several vehicles they set vehicle 0; the others keep the spacing along Y from it and turn the
  same way, and `--vehicle-pose N=x,y,z,roll,pitch,yaw` (repeatable, vehicle numbers count from 0) places one vehicle
  exactly. The launcher uses these options and **removes them**: the script receives only the rest (including
  `--airsim`). Without `--airsim` they are an error and nothing starts; they cannot be combined with `--scene`. The
  launcher prints where each vehicle was placed, and writes the scene for that pose as
  `scene_ardupilot_<N>quadrotors_pose<hash>.jsonc` (a different pose is a different file).
- Command line: `mrrsim run flight\mission.py --project C:\code\myproject --script-args=-s`.

### Installing extra Python packages

**Install package...** (below the script list) asks for one or more package names, for example `tqdm` or
`requests==2.32.3`, and installs them from the internet into `Documents\MRRSimulator\packages`. Every script
and project run after that finds them (the folder is mounted at `/packages` and is on `PYTHONPATH`), and they
keep working offline. Nothing is rebuilt and the images are not changed. The output pane shows what was
installed; the install ends with an import test of each requested package.

- Only packages the full image does not already have are downloaded: the install is resolved against that
  image first, so `numpy`, `cv2` and the like are never fetched again (a second copy in the packages folder
  would take priority over the image's and could break it).
- **Removing:** in the same dialog type `remove` and the names, for example `remove pyzbar`
  (`mrrsim pip remove pyzbar`). It deletes only what that package installed.
- **System libraries are the limit.** A package that needs a program or library from the operating system
  installs but fails to import; the test says so. Those need the library added to
  `containers/script/Containerfile` and the images rebuilt. The full image already has the ZBar library
  (`libzbar0`), so `pyzbar` works once installed here.
- Names only: web addresses, file paths and pip options are refused.
- Command line: `mrrsim pip install tqdm`, `mrrsim pip remove tqdm`, `mrrsim pip list`, `mrrsim pip reset` (deletes the
  packages folder).

## The Vehicles tab

The **Vehicles** tab (next to **Run**) designs the vehicle that flies. A vehicle is a Project AirSim *robot
config*; the tab shows **every setting the simulator reads** (about 470 across all parts) as forms, and lists the
parts (camera, LiDAR, radar, IMU, GPS, barometer, magnetometer, airspeed, distance sensor, battery; rotors,
control surfaces, tilts, wheels, gimbals; links and joints) as blocks you put on the vehicle.

- **Designs.** Your designs are saved in `Documents\MRRSimulator\vehicles`, one `.jsonc` file each. **New...**
  makes a minimal vehicle, **Duplicate...** copies the one on screen, **Rename...** and **Delete...** do what they
  say (a rename also follows the design that is used when running). The provided vehicle from `sim_config` is
  listed too but is read-only: duplicate it to change it. Name rules: letters, numbers, spaces and `_ - . ( )`.
- **Building.** *Parts* (left): double-click one, press **Add to vehicle**, or drag it onto the vehicle. It goes on
  the link you have selected (or drop it on a link or on a part to attach it there). *Vehicle* (middle): the
  controller, the links with the parts attached to each, the joints. **Remove**, **Duplicate**, **Up/Down**,
  **Undo/Redo** (Ctrl+Z / Ctrl+Y) and **Save** (Ctrl+S). Renaming a link or an actuator updates everything that
  names it. Unsaved changes are asked about before another design is opened or the window is closed.
- **Settings** (right): the form of the selected part. An empty field means "not set": its grey hint is the
  simulator's default, and clearing a field removes the setting from the file. Hover a field for its help, unit,
  range and the places where the docs and the simulator disagree. Tables (a camera's captures, radar masks, the
  actuator order) have **Add** and **Remove** buttons; settings the catalog does not know are kept and shown under
  *Other settings in this file*.
- **Problems.** Below the work area: errors (the simulator would refuse or misread the design), warnings and notes,
  each a double-click away from the setting. Parts with an error show `(!)` in the tree. It also catches
  mistakes the simulator would only reveal as a vehicle that never starts: a missing required setting, a link
  or actuator name that does not exist, no enabled IMU (ArduPilot needs one), an actuator order that does not match
  the rotors. **Fix spellings** renames settings that the docs spell differently from the simulator's code (the
  simulator ignores those silently; see [launcher/VEHICLE_SETTINGS.md](launcher/VEHICLE_SETTINGS.md)).
  The other two tabs show the JSON of the design and let you edit it as text.
- **Getting at the files.** **Copy settings (JSON)** puts the design's Project AirSim robot config on the clipboard (without the
  launcher's own camera stream setting), ready to paste into a `.jsonc` file. **Show file in folder** opens
  the folder of the design's file with the file selected (the provided vehicle's file is in `sim_config`).
- **Flying a design.** **Use when running** (or the *Vehicle* box in the Run tab) chooses which design the next
  run uses; every vehicle of the run is a copy of it, with the UDP ports assigned per vehicle as before.
  `mrrsim run script.py --design NAME` does the same from the command line, and `mrrsim designs` lists them.
  Only a design with an ArduPilot controller can fly here (the SITL containers are ArduPilot); others can be edited
  and saved but are marked "cannot be flown here". The ports of the ArduPilot controller are set by the launcher
  and cannot be edited. Cameras off (`--no-cameras`) and the simulator window size apply to a design as to the
  provided vehicle.
- **What it does not change.** The scene (clock, weather, home point) is not part of a vehicle; it stays in
  `sim_config`. The generated files for a run (`robot_design_<name>[_<k>].jsonc`, `scene_..._design_<name>.jsonc`)
  go next to the scene files, marked `GENERATED`, and your design file is never changed by a run. Comments in a
  design's `.jsonc` are not kept when it is saved.

### Receiving a camera's pictures in your script

A camera in the simulator can send its pictures to the Python script that is running, as UDP packets to a port you
choose. Switch it on in the Vehicles tab (the setting is saved with the design):

1. Select the camera, open **Stream pictures to the script (UDP)** and set **Stream the pictures** to true. Pick the
   **Port** (default 5600), how many **Pictures per second** at most, the **Picture format** (JPEG, or lossless PNG)
   and, to keep them small, **Shrink wider than** (a width in pixels). **Which capture** chooses the image type;
   that capture must have **Send images to scripts** on (the provided `DownCamera` has it on for the colour image);
   the Problems list says so if it is not.
2. Use the design when running (`mrrsim run script.py --design NAME`). The launcher prints which port each camera
   sends to.
3. In the script:

   ```python
   from mrrsim_camera import CameraReceiver

   with CameraReceiver(5600) as camera:       # the port from the camera; CameraReceiver() uses the first camera's
       frame = camera.get(timeout=5)          # the newest picture you have not seen yet (None if none came)
       image = frame.image()                  # NumPy array (BGR, like OpenCV); frame.data holds the JPEG/PNG bytes
       print(frame.vehicle, frame.camera, frame.width, frame.height)
   ```

   `camera.latest()` returns the newest picture without waiting, `for frame in camera.frames(timeout=5):` loops
   over them as they come, and `python -m mrrsim_camera --port 5600` shows what a camera sends without a script.
   [scripts/camera_receiver.py](scripts/camera_receiver.py) is a complete example. Environment variables
   `SIM_CAMERA_PORT` (first camera's port) and `SIM_CAMERA_STREAMS` (all of them, as JSON) are set for the script.

Good to know:

- Several cameras and all the vehicles of a run can send to one port: each picture says which vehicle (counted from
  0, like `SIM_CONNECT_<n>`) and which camera it came from. Use different ports to give each camera its own receiver.
- Pictures are dropped rather than queued: a picture that lost a packet is discarded, and a script slower than
  the camera skips to the newest picture (`camera.dropped` counts them). Lower the rate or the size if you need
  every picture.
- The packets are small (1400 bytes) on purpose: WSL's mirrored networking silently loses larger UDP packets between
  programs on the computer. A 640x480 JPEG is about 6-40 packets; a large PNG is hundreds, so prefer JPEG.
- Only one program at a time can receive the simulator's pictures and sensor data, so the launcher's scene loader (which
  holds that connection) does the receiving and relays the pictures; a script that subscribes to the camera itself
  with the Project AirSim client would find nothing. The packet format is in `containers/script/mrrsim_camera.py` for
  receivers written in another language.
- Depth captures are sent as grey pictures (black = 0 m, white = **Depth: white at** metres or more).
- With **Cameras off** (`--no-cameras`) nothing is sent.

## Port scheme (vehicle `i`, from 0)

| | |
|---|---|
| MAVLink SYSID | `i + 1` |
| Physics UDP | `9003 + 10i` (SITL receives sensors), `9002 + 10i` (sim receives motor output) |
| SERIAL0 | `udpclient` to `127.0.0.1:14550` for a GCS (QGC / Mission Planner), all vehicles |
| SERIAL1 | `tcp:127.0.0.1:5762 + 10i`: DroneKit and student scripts |
| SERIAL2 | `tcp:127.0.0.1:5763 + 10i`: launcher health monitor |

## What student scripts get

Environment variables: `SIM_CONNECT` (vehicle 0), `SIM_CONNECT_<i>`, `SIM_VEHICLE_COUNT`,
`SIM_OUT_DIR` (`/out`, writable and kept with the session logs), `PROJECTAIRSIM_HOST`, and, when a camera
streams its pictures to the script, `SIM_CAMERA_PORT` and `SIM_CAMERA_STREAMS` (see "Receiving a camera's pictures").
The script's folder is available at `/work` (writes go to a scratch layer, see "Writes" above).

## Status (honest)

Verified on the development machine (Windows 11, RTX 3060, WSL2 distro `mrr`, mirrored networking):
- **A full flight from one command**: `mrrsim up` starts Unreal, loads the scene, starts ArduPilot
  4.7.0 SITL, and a DroneKit script in a Python 3.12 container takes off to 10 m, hovers, lands and
  disarms (exit code 0). All three images build offline from cache and pass `mrrsim images test`.
- **Two vehicles flying together**: `up --vehicles 2 --script takeoff_land_all.py`. Each vehicle needs
  its own robot in the scene, so the launcher generates `scene_ardupilot_2quadrotors.jsonc` from the
  single-vehicle scene (`sim_config/generate_scene.py`; `doctor` checks the match). This flight was
  verified with a hand-made copy of the same two files; the generated ones are checked by tests and a
  dry run, not yet flown.
- Windows <-> WSL UDP round trips over `127.0.0.1`: about 120-130 us median, p99 under 170 us.
- The launcher's unit tests pass; `doctor`, `status`, `down` and `--dry-run` work.

What it took to arm (all in `[sitl.params]` of [config/mrrsim.toml](config/mrrsim.toml)):
- The scene steps physics every 3 ms (about 333 Hz) but ArduPilot's AirSim defaults want a 300 Hz
  loop, so the gyro and main-loop pre-arm checks failed: `SCHED_LOOP_RATE = 100`.
- `GPS 1: not healthy` with a good 3D fix: `GPS1_TYPE = 100` (the built-in simulated GPS). The cause
  was not confirmed. `scripts/diag_arming.py` shows what the autopilot objects to.

**Not verified:**
- More than two vehicles, a camera in the loop, and frame rates on an integrated GPU (the real target).
- That `--no-cameras` or a smaller window raises the frame rate on an integrated GPU. They run and fly
  (checked on the RTX 3060, where the GPU sat at 98% with and without cameras, so it showed nothing).
- Whether the scene survives the scene-loader disconnecting (`hold = false`).
- Whether the real vehicles' ArduPilot version matches 4.7.0, and that `SCHED_LOOP_RATE = 100` is
  representative enough for tuning work.
- Clean-machine behaviour (no Python, no VC++ runtime, no admin rights).

## Licensing

ArduPilot is GPLv3 and runs as a separate process. Publish the pinned commit (recorded inside the
SITL image at `/opt/ardupilot/COMMIT`) with any image you distribute. Fab/Marketplace assets in the
Unreal project cannot go in a public repo.
