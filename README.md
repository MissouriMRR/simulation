# MRRSimulator

```text
Windows (native)                          WSL2 distro, rootless podman, --network host
  mrrsim launcher  ──── starts ───▶ Unreal (Simulation-Win64-Shipping.exe)
        │                                      ▲ UDP 9002/9003 + 10i
        └── wsl.exe -d mrr --exec podman ──▶ mrrsim-scene     loads the scene, stays connected
                                             mrrsim-sitl-0..N arducopter, one per vehicle
                                             mrrsim-script    Python 3.12 + DroneKit -> tcp:127.0.0.1:5762+10i
```

Windows and WSL share `127.0.0.1` through `.wslconfig`'s `networkingMode=mirrored` option

## Layout

| Folder | Contents |
|---|---|
| [launcher/](launcher) | launcher UI and runtime |
| [containers/](containers) | `sitl/` (pinned ArduPilot Copter ref) and `script/` (Python 3.12 + DroneKit, pymavlink from MRR package index) |
| [config/](config) | launcher settings |
| [scripts/](scripts) | script examples |
| [sim_config/](sim_config) | Project AirSim scene + robot `.jsonc` |
| `Windows/` | the packaged Unreal build |
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

Run from the repository root:

```bat
tools\build_installer.cmd
```

Builds everything: the script images, the Linux payload, the launcher and the installer. Use it when anything under `containers\` changed.

```bat
tools\build_installer.cmd quick
```

Rebuilds only the launcher and the installer, and reuses the existing Linux payload.

Either way, the result is `Output\MRRSimulator-Setup-<version>.exe`.

### What a full build does

1. **`mrrsim images build script`** rebuilds the script images in your `mrr` WSL distro (cached layers keep it quick). The payload is made from these images, so this step comes first.
2. **`python tools/build_distro.py`** makes `payload/mrr-rootfs.tar.gz`. The archive is a clean Ubuntu 24.04 WSL distro with rootless podman, a `mrr` user, and the three simulator images already loaded.
   - On the first run it downloads the official Ubuntu image and checks it against a pinned checksum.
   - It copies the images out of your `mrr` with `podman save` and tests the result. It only reads your `mrr` and never changes it.
   - It writes a version stamp to `payload/distro_version.iss`. The stamp changes whenever the images or the provisioning change.
3. **`launcher\build.cmd`** builds `MRRSimulator.exe` and `MRRSimulatorGUI.exe`.
4. **Inno Setup** compiles `MRRSimulationLauncher.iss` into the setup program.

### What the installer does

- **WSL distro:** at the end of an install, setup imports the payload as the WSL distro `mrr` for the account running setup.
  - An `mrr` that setup did not create is never replaced.
  - One that setup created is replaced when its version stamp differs from the installer's. Setup asks first; a silent install replaces it without asking.
  - Your scripts and projects are not stored in the distro.

### Testing an install without touching your `mrr`

```bat
MRRSimulator-Setup-<version>.exe /DISTRO=mrr-test
```

Then set the distro to `mrr-test` in the installed `config\mrrsim.toml` too.

## Receiving a camera's pictures in your script

A camera in the simulator can send its pictures to the Python script that is running, as UDP packets to a port you
choose. Switch it on in the Vehicles tab (the setting is saved with the design):

1. Select the camera, open **Stream pictures to the script (UDP)** and set **Stream the pictures** to true. Pick the
   **Port** (default 5600), how many **Pictures per second** at most, the **Picture format** (JPEG, or lossless PNG)
   and, to keep them small, **Shrink wider than** (a width in pixels). **Which capture** chooses the image type;
   that capture must have **Send images to scripts** on (the provided `DownCamera` has it on for the color image);
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
- Depth captures are sent as gray pictures (black = 0 m, white = **Depth: white at** meters or more).

## Port scheme (vehicle `i`, from 0)

| | |
|---|---|
| MAVLink SYSID | `i + 1` |
| Physics UDP | `9003 + 10i` (SITL receives sensors), `9002 + 10i` (sim receives motor output) |
| SERIAL0 | `udpclient` to `127.0.0.1:14550` for a GCS (QGC / Mission Planner), all vehicles |
| SERIAL1 | `tcp:127.0.0.1:5762 + 10i`: DroneKit and scripts |
| SERIAL2 | `tcp:127.0.0.1:5763 + 10i`: launcher health monitor |
