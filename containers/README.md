# containers/

Two images, both run in rootless podman inside WSL2 with `--network host`.

| Image | Context | Contents | Used for |
|---|---|---|---|
| `localhost/mrrsim-sitl:<ref>` | [sitl/](sitl) | `arducopter` built from a pinned ArduPilot ref, default params | one container per vehicle |
| `localhost/mrrsim-script:runtime` | [script/](script) | Python 3.12, DroneKit (patched for 3.10+), pymavlink 2.4.49.post1 from the MRR index, `mrrsim_camera.py` (receiver for camera pictures, in `/opt/mrrsim/lib`) | student scripts |
| `localhost/mrrsim-script:airsim` | [script/](script) | runtime + Project AirSim client + `scene_loader.py` and `camera_relay.py` (sends a camera's pictures as UDP packets) | scene loading, scripts that need the AirSim client |

Build them with the launcher (it converts the Windows paths and tags from `config/mrrsim.toml`):

```powershell
mrrsim images build all
```

Building pulls base images (`debian:bookworm-slim`, `python:3.12-slim-bookworm`) and fetches
ArduPilot and Project AirSim, so it needs network access and takes a while the first time. Each
image builds from its own folder (`sitl/`, `script/`); a rebuild with unchanged inputs is served
entirely from podman's layer cache.

### How the ArduPilot source is fetched (and why)

`git fetch` / `git submodule update` against github.com ran at about 80 KiB/s on the network this
was developed on, while a plain HTTPS download ran at about 5.8 MB/s. So
[sitl/fetch_source.py](sitl/fetch_source.py) downloads **tarballs** from `codeload.github.com`
instead:

1. git fetches only the commit and its trees (no file contents) to learn the exact commit and
   each submodule's pinned commit, which a tarball does not carry. If that fails, the GitHub API
   is used instead (`GITHUB_TOKEN` raises its rate limit).
2. The repository tarball for that exact commit is downloaded and unpacked.
3. Each submodule tarball is downloaded at its pinned commit, four in parallel, with nested
   submodules handled the same way. Downloads retry and are checked for truncation.
4. `/src/.git` is left with HEAD at the commit, and `SOURCES.txt` records every commit fetched.

It prints progress every 5 seconds, so a slow download is visible. Its tests run offline against
a fake GitHub (`python3 -m unittest discover -s containers/sitl/tests -v`, in WSL or Linux).

**The script image fetches only the Project AirSim Python client** (`client/python/projectairsim`, about 1 MB),
because a GitHub tarball is the whole repository (646 MB). See `script/fetch_client.py` below. Pin
`PROJECTAIRSIM_REF` to a commit SHA for reproducible builds; the commit is recorded in
`/opt/projectairsim/COMMIT`.

The SITL build skips the hardware-only submodules `ChibiOS`, `esp_idf` and `CrashDebug`
(`SKIP_SUBMODULES` build arg). If `waf configure` reports a missing module, rebuild with
`mrrsim images build sitl --build-arg SKIP_SUBMODULES=`.

`mrrsim images build` also passes `podman build --network host`; use `--build-network default`
to go back to podman's own rootless networking.

### Python 3.12, DroneKit and pymavlink

- **pymavlink** is `2.4.49.post1` from the MRR package index
  (`https://missourimrr.github.io/pypi-index/simple/`), added with `--extra-index-url` in
  `script/requirements.txt` so PyPI still serves everything else. That exact version exists only on the
  MRR index, so pip cannot pick PyPI's copy instead. It depends on `fastcrc`. The SITL image is unchanged:
  it builds ArduPilot with the MAVLink tools bundled in the ArduPilot source, not this package.
- **DroneKit** is the team's own build of 2.9.2, `dronekit-2.9.2-py310-none-any.whl` from the
  MissouriMRR/docs repository (the same file the SUAS code's `pyproject.toml` uses), pinned by its sha256 in
  `script/requirements.txt`. PyPI's 2.9.2 does not import on Python 3.10+ (`collections.MutableMapping`), and the
  team's build also has additions such as wind data.
- **The airsim image also has what the team's flight code imports**: `script/requirements-airsim.txt`
  (`utm`, `scipy`, `shapely`, `aiohttp`, `onnxruntime`, `pyodm`, `siyi-sdk`, ...) and the old `airsim` client
  (heitmeih/AirSim-fixed-pyinstall, pinned to a commit). The image build checks that they all import.
- **`script/fetch_client.py`** downloads the Project AirSim client and the `airsim` fork: a blobless clone lists the
  folder's files and hashes, the files come from raw.githubusercontent.com, each checked against its git hash.
  `git sparse-checkout` and pip's `git+https` were replaced because GitHub began answering their lazy blob
  fetches with HTTP 408, even for an 817-byte README.
- The index has Linux wheels for Python 3.9 to 3.14, so another version is a change of the `PYTHON_IMAGE`
  base tag in the Containerfile.

## SITL environment contract

`containers/sitl/entrypoint.sh` reads these variables (all optional):

| Variable | Default | arducopter flag |
|---|---|---|
| `INSTANCE` | `0` | `-I` (ports shift by `10*INSTANCE`) |
| `SYSID` | `INSTANCE+1` | `--sysid` |
| `MODEL` | `airsim-copter` | `--model` |
| `HOME_LOC` | Rolla golf course | `--home` (must match the scene's home-geo-point) |
| `SPEEDUP` | `1` | `--speedup` |
| `SIM_ADDRESS` | `127.0.0.1` | `--sim-address` |
| `GCS_OUT` | `127.0.0.1:14550` | `--serial0=udpclient:<value>` |
| `DEFAULTS` | `copter.parm,airsim-quadX.parm` | `--defaults` |
| `WIPE` | `1` | `-w` |
| `SYNTHETIC_CLOCK` | `0` | `-S` (a no-op in 4.7.0; for older refs only) |
| `SIM_PORT_IN` / `SIM_PORT_OUT` | unset | `--sim-port-in/out` |
| `EXTRA_ARGS` | empty | appended verbatim |

## Checked against the Copter-4.7.0 source

Read from the fetched source tree and `arducopter --help` (the SITL build succeeded with no network):

- The flags in the table above exist with these spellings: `-I`, `-w`, `-S`, `--home`, `--model`,
  `--speedup`, `--defaults`, `--sysid`, `--sim-address`, `--sim-port-in/out`, `--serial0`.
- `-I N` adds `10 * N` to the AirSim UDP ports too (SITL_cmdline.cpp), so the scheme in the root
  README holds without passing `--sim-port-*`.
- `--serial0 udpclient:HOST:PORT` is valid, and SERIAL0 defaults to `tcp:0:wait`, which blocks
  startup until a client connects; that is why the entrypoint overrides it.
- The defaults for the `airsim-copter` frame are `copter.parm` + **`airsim-quadX.parm`** (capital
  X; `vehicleinfo.py`). The entrypoint refuses to start if a defaults file is missing.
- `waf` needs `--no-submodule-update` for a tarball-assembled tree (already in the Containerfile).

## Still not verified

- **A flight.** Nothing has run against Unreal yet, so it is unknown whether ArduPilot 4.7.0's
  AirSim backend works with Project AirSim's controller.
- **ArduPilot ref**: defaults to `Copter-4.7.0`. Your current images use the moving
  `Copter-4.5` branch. Pick the version your real vehicles fly, then pin it by SHA.
- **The `airsim` image after the OpenCV fix.** The first build could not `import projectairsim`
  (OpenCV needs system libraries the slim base lacks); the apt packages that fix it are in the
  Containerfile but the image has not been rebuilt yet. `mrrsim images test` checks it offline.
  Both images are verified on Python 3.12 with a real flight: DroneKit 2.9.2 and pymavlink 2.4.49.post1 import and fly.
