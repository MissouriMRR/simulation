"""The catalog of Project AirSim robot-config settings, as the Vehicles tab uses it.

A vehicle design is a Project AirSim robot config (one .jsonc file). This module describes everything that can be
in one: the parts a vehicle is built from ("blocks": sensors, actuators, links, joints, the controller) and every
setting of each part, with its type, range, default and a line of help.

Where the facts come from, in order of trust:
  1. Project AirSim's C++ (core_sim/src, vehicle_apis), which decides what the simulator really reads. Its list of
     settings is committed as tests/data/projectairsim_code_keys.json (tools/projectairsim_keys.py makes it) and
     test_vehicle_schema.py checks that every setting in it is in this catalog.
  2. The documentation (docs/config_robot.md, docs/sensors/*.md).
  3. The JSON schema in the Python client (robot_config_schema.jsonc) and the example robot configs.
The three disagree in places. A setting the code reads under another spelling than the docs give is listed under
the code's spelling with the other one in `legacy`; a setting the docs describe but the code never reads is kept,
marked `ignored`, so the editor can say so. Both are reported when a design is checked, because the simulator
silently skips a key it does not know.

Everything is plain data (frozen dataclasses): no tkinter and no I/O, so it can be tested without a window.
Source commit of the C++ and docs this was written from: iamaisim/ProjectAirSim 62229cd.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..scene import camerastream

# ------------------------------------------------------------------------------------------------ kinds
NUMBER, INTEGER, BOOL, STRING, ENUM = "number", "integer", "bool", "string", "enum"
VEC3 = "vec3"        # "x y z": three numbers in a string
VEC4 = "vec4"        # "r g b a"
LINKREF = "linkref"  # the name of one of the vehicle's links
ACTREF = "actref"    # the name of one of the vehicle's actuators
GROUP = "group"      # an object with its own settings
TABLE = "table"      # an array of objects, each with the same settings
LIST = "list"        # an array of plain values (strings)
MAP = "map"          # an object of name -> number; `catalog` lists the names the code knows
JSON = "json"        # anything else: edited as raw JSON


@dataclass(frozen=True)
class Setting:
    key: str
    kind: str
    label: str = ""
    help: str = ""
    default: Any = None            # what the simulator uses when the key is missing (None: not a fixed value)
    required: bool = False         # the simulator (or its schema) needs it
    choices: tuple = ()            # ENUM: the allowed values, or (value, label) pairs
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    children: tuple["Setting", ...] = ()   # GROUP: its settings; TABLE: the settings of each row
    when: tuple[str, tuple] | None = None  # only meaningful when the sibling `key` has one of these values
    unless: tuple[str, tuple] | None = None  # required, except when the sibling `key` has one of these values
    aliases: tuple[str, ...] = ()  # other spellings the simulator also reads
    legacy: tuple[str, ...] = ()   # spellings from the docs/schema/samples that the simulator does NOT read
    ignored: str = ""              # documented, but the simulator never reads it; the text says why
    managed: str = ""              # the launcher sets this itself; the text says how
    note: str = ""                 # a caveat worth showing
    source: str = "code"           # code | docs | schema | sample | mrrsim: where it is known from (mrrsim: the launcher's own)
    catalog: tuple = ()            # MAP: (name, help) of the known entries

    @property
    def title(self) -> str:
        return self.label or self.key

    def child(self, key: str) -> "Setting | None":
        return next((c for c in self.children if c.key == key or key in c.aliases), None)

    def choice_values(self) -> tuple:
        return tuple(c[0] if isinstance(c, tuple) else c for c in self.choices)


@dataclass(frozen=True, eq=False)  # compared by identity: the templates are dicts, which cannot be hashed
class BlockType:
    section: str        # where it lives in the config: "vehicle", "controller", "links", "joints", "actuators", "sensors"
    type: str           # the value of its "type" key ("" for links, joints and the vehicle itself)
    title: str
    group: str          # palette heading
    help: str
    settings: tuple[Setting, ...]
    template: dict      # what a newly added block starts as
    singleton: bool = False

    @property
    def name_key(self) -> str:
        """The key that names a block: sensors and controller use "id", actuators and links "name", joints "id"."""
        return "name" if self.section in ("links", "actuators") else "id"

    def setting(self, key: str) -> Setting | None:
        return next((s for s in self.settings if s.key == key or key in s.aliases), None)


# ------------------------------------------------------------------------------------- tiny constructors
def _s(kind: str):
    def make(key: str, label: str = "", default: Any = None, **kw) -> Setting:
        kw["minimum"], kw["maximum"] = kw.pop("min", None), kw.pop("max", None)
        return Setting(key, kind, label, default=default, **kw)
    return make


num, integer, boolean, text, vec3, vec4 = _s(NUMBER), _s(INTEGER), _s(BOOL), _s(STRING), _s(VEC3), _s(VEC4)
link_ref, actuator_ref = _s(LINKREF), _s(ACTREF)


def enum(key: str, label: str, choices: tuple, default: Any = None, **kw) -> Setting:
    return Setting(key, ENUM, label, default=default, choices=tuple(choices), **kw)


def group(key: str, label: str, children: tuple, **kw) -> Setting:
    return Setting(key, GROUP, label, children=tuple(children), **kw)


def table(key: str, label: str, children: tuple, **kw) -> Setting:
    return Setting(key, TABLE, label, children=tuple(children), **kw)


def strings(key: str, label: str, **kw) -> Setting:
    return Setting(key, LIST, label, **kw)


def mapping(key: str, label: str, catalog: tuple = (), **kw) -> Setting:
    return Setting(key, MAP, label, catalog=tuple(catalog), **kw)


# --------------------------------------------------------------------------------------------- shared parts
ORIGIN = group("origin", "Position and rotation", (
    vec3("xyz", "Position", "0 0 0", unit="m", help="X Y Z in the parent's frame (NED: X forward, Y right, Z down)."),
    vec3("geo-point", "Position as latitude/longitude", unit="deg, deg, m",
         help="Latitude Longitude Altitude. Used instead of the position when present; converted using the scene's home point."),
    vec3("rpy-deg", "Rotation (degrees)", "0 0 0", unit="deg", help="Roll Pitch Yaw. Used when 'rpy' is absent."),
    vec3("rpy", "Rotation (radians)", unit="rad", help="Roll Pitch Yaw in radians. Wins over 'rpy-deg' when both are present."),
), help="Where this part is, relative to what it is attached to.")

ORIGIN_REQUIRED = group("origin", ORIGIN.label, ORIGIN.children, required=True, help=ORIGIN.help)

INPUT_MAP = group("input-map", "Control input map", (
    boolean("clamp_input", "Clamp the input", help="Limit the control input to the input range first.", legacy=("clamp-input",)),
    boolean("clamp_output", "Clamp the output", help="Limit the result to the output range.", legacy=("clamp-output",)),
    num("input-min", "Input minimum", -1.0), num("input-max", "Input maximum", 1.0),
    num("output-min", "Output minimum", -1.0), num("output-max", "Output maximum", 1.0),
    num("scale", "Scale", 1.0, help="Scales the mapped value; a negative value inverts it."),
), help="Linearly maps the controller's signal to the range the actuator expects. The code reads the keys with "
        "underscores (clamp_input, clamp_output); the docs spell them with hyphens, which the simulator ignores.")

JSBSIM_CMD = text("jsbsim-cmd", "JSBSim command property", help="JSBSim property this actuator's command is written to.")
JSBSIM_STATE = text("jsbsim-state", "JSBSim state property", help="JSBSim property its state is read from (defaults to the command property).")

ACTUATOR_ORDER = table("actuator-order", "Actuator order", (
    actuator_ref("id", "Actuator", required=True, help="Name of an actuator of this vehicle."),
    num("initial-value", "Initial value", help="Control signal before the controller sends any (manual and MATLAB controllers)."),
    num("scale", "Scale", 1.0, help="Scale applied to this output (PX4)."),
), required=True, help="Which actuator receives which controller output, in output order. Quadrotor-X order: front right, "
                       "rear left, front left, rear right.")


# ----------------------------------------------------------------------------------------------- sensors
def _sensor(type_: str, title: str, help_: str, settings: tuple, template: dict, **kw) -> BlockType:
    common = (
        text("id", "Name", required=True, help="Identifier of this sensor; unique among the vehicle's sensors."),
        boolean("enabled", "Enabled", True, required=True, help="Turn the sensor off without deleting it."),
        link_ref("parent-link", "Attached to link", "", help="The link this sensor is mounted on."),
    )
    return BlockType("sensors", type_, title, "Sensors", help_, common + tuple(settings),
                     {"id": title.replace(" ", ""), "type": type_, "enabled": True, "parent-link": "Frame", **template}, **kw)


IMAGE_TYPES = ((0, "Scene (colour image)"), (1, "Depth planar"), (2, "Depth perspective"), (3, "Segmentation"),
               (4, "Depth visualization"), (5, "Disparity normalized"), (6, "Surface normals"))
CAPTURE = (
    enum("image-type", "Image type", IMAGE_TYPES, 0, required=True, help="What this capture shows."),
    integer("width", "Width", 256, required=True, min=1, unit="px"),
    integer("height", "Height", 144, required=True, min=1, unit="px"),
    num("fov-degrees", "Field of view", 90.0, required=True, min=5, max=170, unit="deg"),
    boolean("capture-enabled", "Send images to scripts", False, required=True,
            help="Capture the raw images and publish them to the client (costs time and bandwidth)."),
    boolean("streaming-enabled", "Show in the simulator window", False,
            help="Stream this view to the simulator window. Only one streaming camera is shown at a time. The launcher "
                 "sizes a streamed camera to the simulator window so the window does not resize at spawn."),
    boolean("pixels-as-float", "Pixels as float", False, required=True, help="Float pixel data instead of 8-bit."),
    boolean("compress", "PNG compression", True, required=True, help="Losslessly compress images sent to the client; slower."),
    num("target-gamma", "Target gamma", 2.5, help="Gamma of the render target (scene images use 1.4 unless set)."),
    enum("auto-exposure-method", "Auto exposure method", ((0, "Histogram"), (1, "Basic"), (2, "Manual")), 0,
         note="The docs list manual as 3; the code's enum has manual as 2.", help="How exposure adapts to brightness."),
    num("auto-exposure-speed", "Auto exposure speed", help="How fast exposure adapts."),
    num("auto-exposure-bias", "Auto exposure bias", help="Exposure compensation."),
    num("auto-exposure-max-brightness", "Max brightness", help="Upper limit of the exposure range (equal min and max disables auto exposure)."),
    num("auto-exposure-min-brightness", "Min brightness", help="Lower limit of the exposure range."),
    num("auto-exposure-low-percent", "Histogram low percent", min=0, max=100, help="70-80 works well."),
    num("auto-exposure-high-percent", "Histogram high percent", min=0, max=100, legacy=("auto-exposoure-high-percent",),
        help="70-80 works well. The docs misspell this key 'auto-exposoure-high-percent'; the code reads this spelling."),
    num("auto-exposure-histogram-log-min", "Histogram log min"),
    num("auto-exposure-histogram-log-max", "Histogram log max"),
    num("motion-blur-amount", "Motion blur", min=0, max=1, help="0.25-0.5 looks reasonable; avoid 1."),
    num("max-depth-meters", "Max depth", 1.0, unit="m", help="Depth visualization: the distance shown as white."),
    num("depth-of-field-focal-region", "Depth of field focal region", unit="m"),
    num("depth-of-field-transition-region", "Depth of field transition region"),
    num("chromatic-aberration-intensity", "Chromatic aberration"),
    boolean("show-debug-plots", "Show debug plots", False),
)
NOISE = (
    boolean("enabled", "Enabled", required=True), integer("image-type", "Image type", required=True, help="Which capture it applies to."),
    num("rand-contrib", "Random noise contribution", 0.2, required=True), num("rand-speed", "Random noise speed", 100000.0, required=True),
    num("rand-size", "Random noise size", 500.0, required=True), integer("rand-density", "Random noise density", 2, required=True),
    num("horz-wave-contrib", "Horizontal wave contribution", 0.03, required=True),
    num("horz-wave-strength", "Horizontal wave strength", 0.08, required=True),
    num("horz-wave-vert-size", "Horizontal wave vertical size", 1.0, required=True),
    num("horz-wave-screen-size", "Horizontal wave screen size", 1.0, required=True),
    num("horz-noise-lines-contrib", "Noise lines contribution", 1.0, required=True),
    num("horz-noise-lines-density-y", "Noise lines density (Y)", 0.01, required=True),
    num("horz-noise-lines-density-xy", "Noise lines density (XY)", 0.5, required=True),
    num("horz-distortion-contrib", "Distortion contribution", 1.0, required=True),
    num("horz-distortion-strength", "Distortion strength", 0.002, required=True),
)
CAMERA = _sensor("camera", "Camera", "Takes pictures: colour, depth, segmentation and more, optionally streamed to the simulator window.", (
    num("capture-interval", "Capture interval", 5.0, required=True, min=0, unit="s", help="Time between captures."),
    table("capture-settings", "Captures", CAPTURE, required=True, help="One entry per kind of image this camera produces."),
    table("noise-settings", "Image noise", NOISE, help="Optional noise models for the captures."),
    group("gimbal", "Gimbal", (
        text("gimbal-id", "Gimbal actuator", help="Name of a gimbal actuator that steers this camera."),
        boolean("lock-roll", "Lock roll", False, required=True), boolean("lock-pitch", "Lock pitch", False, required=True),
        boolean("lock-yaw", "Lock yaw", False, required=True),
    ), help="Mount the camera on a gimbal and choose which axes stay fixed."),
    group("annotation-settings", "Bounding-box annotations", (
        boolean("enabled", "Enabled", False),
        strings("object-ids", "Objects", help="Names of the scene objects to draw boxes around."),
        group("bbox2D-settings", "2D boxes", (enum("alignment", "Alignment", ("axis", "oriented"), "oriented",
                                                   help="'oriented' 2D boxes are not supported yet."),)),
        group("bbox3D-settings", "3D boxes", (enum("alignment", "Alignment", ("axis", "oriented"), "oriented"),)),
        boolean("distance-filter-enabled", "Only near objects", False),
        num("distance-filter-range", "Distance range", 10.0, unit="m"),
    ), help="2D/3D bounding boxes of chosen objects in each image."),
    group("post-process-model-settings", "Neural-network post processing", (
        boolean("enabled", "Enabled", False),
        text("model-filepath", "ONNX model file", help="Absolute path of an ONNX model run on each image."),
        enum("execution-provider", "Run on", ("cpu", "cuda", "tensorrt"), "cpu"),
    ), help="Runs an ONNX model on the captured images."),
    group(camerastream.STREAM_KEY, "Stream pictures to the script (UDP)", (
        boolean("enabled", "Stream the pictures", False, source="mrrsim",
                help="While the simulator runs, send this camera's pictures to a UDP port where the script receives them "
                     "with mrrsim_camera.CameraReceiver (see 'Receiving the pictures' in the README)."),
        text("host", "Send to address", camerastream.DEFAULTS["host"], source="mrrsim",
             help="Where the receiver listens. The script runs on this computer, so the default is right for it."),
        integer("port", "Port", camerastream.DEFAULTS["port"], min=1024, max=65535, source="mrrsim",
                help="The UDP port the script listens on, CameraReceiver(port). Cameras may share a port: each picture says "
                     "which vehicle and camera it came from."),
        enum("image-type", "Which capture", IMAGE_TYPES, camerastream.DEFAULTS["image-type"], source="mrrsim",
             help="Which of the camera's captures to send. That capture must have 'Send images to scripts' on."),
        num("fps", "Pictures per second", camerastream.DEFAULTS["fps"], min=0.1, max=120, unit="pictures/s", source="mrrsim",
            help="At most this many pictures per second (the camera's own capture interval is the upper limit)."),
        enum("format", "Picture format", ("jpeg", "png"), camerastream.DEFAULTS["format"], source="mrrsim",
             help="JPEG is small and fast; PNG is lossless but several times bigger."),
        integer("quality", "JPEG quality", camerastream.DEFAULTS["quality"], min=1, max=100, source="mrrsim",
                when=("format", ("jpeg",)), help="Higher is sharper and bigger."),
        integer("max-width", "Shrink wider than", camerastream.DEFAULTS["max-width"], min=0, unit="px", source="mrrsim",
                help="Scale pictures wider than this down before sending (0: send them as they are). Smaller pictures "
                     "are quicker to send and decode."),
        num("max-depth", "Depth: white at", camerastream.DEFAULTS["max-depth"], min=0.1, unit="m", source="mrrsim",
            help="Depth captures are sent as grey pictures: black is 0 m and white is this distance or more."),
    ), source="mrrsim", note="The launcher's own setting: it is not written into the robot config the simulator reads.",
        help="Send what this camera sees to a program of your own over UDP: the Python script that is running."),
    ORIGIN_REQUIRED,
), {"capture-interval": 0.05, "capture-settings": [{"image-type": 0, "width": 640, "height": 480, "fov-degrees": 90,
                                                      "capture-enabled": True, "streaming-enabled": False,
                                                      "pixels-as-float": False, "compress": False, "target-gamma": 2.5}],
    "origin": {"xyz": "0 0 0", "rpy-deg": "0 0 0"}, "id": "Camera"})

IMU = _sensor("imu", "IMU", "Inertial measurement unit: acceleration and rotation rate with noise.", (
    group("accelerometer", "Accelerometer", (
        num("velocity-random-walk", "Velocity random walk", 0.0023536, required=True, unit="m/s/sqrt(s)"),
        num("tau", "Bias correlation time", 800.0, required=True, unit="s"),
        num("bias-stability", "Bias stability", 0.00035304, required=True, unit="m/s^2"),
        vec3("turn-on-bias", "Turn-on bias", "0 0 0", required=True, unit="m/s^2"),
        num("gravity", "Gravity", 9.80665, unit="m/s^2"),
    ), required=True), group("gyroscope", "Gyroscope", (
        num("angle-random-walk", "Angle random walk", 0.00008727, required=True, unit="rad/sqrt(s)"),
        num("tau", "Bias correlation time", 500.0, required=True, unit="s"),
        num("bias-stability", "Bias stability", 0.0000223, required=True, unit="rad/s"),
        vec3("turn-on-bias", "Turn-on bias", "0 0 0", required=True, unit="rad/s"),
    ), required=True),
), {"id": "IMU", "accelerometer": {"velocity-random-walk": 0.0023536, "tau": 800, "bias-stability": 0.00035304,
                                   "turn-on-bias": "0 0 0"},
    "gyroscope": {"angle-random-walk": 0.00008727, "tau": 500, "bias-stability": 0.0000223, "turn-on-bias": "0 0 0"}})

LIDAR_TYPES = ("generic_cylindrical", "gpu_cylindrical", "depth_lidar", "generic_rosette", "livox_mid70", "livox_avia")
LIVOX_PRESETS = ("lidar-type", ("livox_mid70", "livox_avia"))  # these presets supply the scan settings themselves
LIDAR = _sensor("lidar", "LiDAR", "Laser scanner returning a 3D point cloud. The type chooses a preset whose values "
                                  "are the defaults of the other settings.", (
    enum("lidar-type", "Type", LIDAR_TYPES, "generic_cylindrical", legacy=("generic-cylindrical",),
         help="Scan pattern and the preset the other settings default from (generic_cylindrical: 16 channels, 100 m; gpu_cylindrical: "
              "-45..45 deg wide; depth_lidar: from a depth image; generic_rosette; livox_mid70: 200 m; livox_avia: 400 m, 6 channels). "
              "The docs spell the types with hyphens; the code only accepts underscores."),
    integer("number-of-channels", "Channels", 16, min=1, required=True, unless=LIVOX_PRESETS, help="Simultaneous scan lines."),
    num("range", "Range", 100.0, required=True, unless=LIVOX_PRESETS, unit="m"),
    integer("points-per-second", "Points per second", 100000, required=True, unless=LIVOX_PRESETS, help="Total for the sensor, not per channel."),
    num("report-frequency", "Report frequency", 0.0, min=0, unit="Hz", help="0 means as often as possible; values under 0.5 act like 0."),
    num("horizontal-rotation-frequency", "Horizontal rotation", 10.0, required=True, unless=LIVOX_PRESETS, unit="rev/s"),
    num("horizontal-fov-start-deg", "Horizontal FOV start", 0.0, min=-360, max=360, required=True, unless=LIVOX_PRESETS, unit="deg"),
    num("horizontal-fov-end-deg", "Horizontal FOV end", 360.0, min=-360, max=360, required=True, unless=LIVOX_PRESETS, unit="deg"),
    num("vertical-rotation-frequency", "Vertical rotation", 0.0, unit="rev/s", help="Rosette types only."),
    num("vertical-fov-upper-deg", "Vertical FOV upper", -15.0, min=-90, max=90, required=True, unless=LIVOX_PRESETS, unit="deg",
        legacy=("vertical-fov-start-deg",), help="The docs call this 'vertical-fov-start-deg'; the code reads this name."),
    num("vertical-fov-lower-deg", "Vertical FOV lower", -45.0, min=-90, max=90, required=True, unless=LIVOX_PRESETS, unit="deg",
        legacy=("vertical-fov-end-deg",), help="The docs call this 'vertical-fov-end-deg'; the code reads this name."),
    vec3("scan-rpy", "Scan orientation", "0 0 0", unit="rad", help="Roll Pitch Yaw of the scan axis (cylinder axis / rosette centre)."),
    boolean("disable-self-hits", "Ignore the vehicle itself", False),
    boolean("report-no-return-points", "Report points with no return", False),
    vec3("no-return-point-value", "No-return point value", "0 0 0", when=("report-no-return-points", (True,)),
         help="Position reported for beams that hit nothing."),
    boolean("report-point-cloud", "Report the point cloud", True),
    boolean("report-azimuth-elevation-range", "Report azimuth/elevation/range", False),
    boolean("draw-debug-points", "Draw hit points", False, help="Not supported in Unreal's Shipping build."),
    num("distance-between-lasers", "Distance between lasers", 0.0, unit="cm", help="Mount spacing of multiple lasers."),
    num("angle-between-lasers-pitch-min", "Laser pitch spread min", 0.0, unit="deg"),
    num("angle-between-lasers-pitch-max", "Laser pitch spread max", 0.0, unit="deg"),
    num("angle-between-lasers-yaw-min", "Laser yaw spread min", 0.0, unit="deg"),
    num("angle-between-lasers-yaw-max", "Laser yaw spread max", 0.0, unit="deg"),
    Setting("radial-scaling", NUMBER, "Radial scaling", default=1.0, source="docs",
            ignored="the docs describe it, but the simulator never reads it from the config (it only uses the type's built-in value)"),
    ORIGIN_REQUIRED,
), {"id": "Lidar", "lidar-type": "generic_cylindrical", "number-of-channels": 16, "range": 100, "points-per-second": 100000,
    "horizontal-rotation-frequency": 10, "horizontal-fov-start-deg": 0, "horizontal-fov-end-deg": 360,
    "vertical-fov-upper-deg": -15, "vertical-fov-lower-deg": -45, "disable-self-hits": False, "draw-debug-points": False,
    "origin": {"xyz": "0 0 -0.1", "rpy-deg": "0 0 0"}})

DISTANCE = _sensor("distance-sensor", "Distance sensor", "Measures the distance to the nearest object along a ray.", (
    num("min-distance", "Minimum distance", 0.2, unit="m", help="Only used for MAVLink messages."),
    num("max-distance", "Maximum distance", 40.0, unit="m"),
    num("report-frequency", "Report frequency", 0.0, min=0, unit="Hz"),
    boolean("draw-debug-points", "Draw hit points", False),
    ORIGIN,
), {"id": "Distance", "origin": {"xyz": "0 0 0", "rpy-deg": "0 90 0"}})

GPS = _sensor("gps", "GPS", "Satellite position, with a dilution-of-precision model that improves as the fix settles.", (
    num("eph-time-const", "Horizontal precision time constant", 0.9, aliases=(), legacy=("eph-time-constant",),
        help="Docs and schema call this 'eph-time-constant'; the code reads 'eph-time-const'."),
    num("epv-time-constant", "Vertical precision time constant", 0.9),
    num("eph-initial", "Initial horizontal precision", 100.0), num("epv-initial", "Initial vertical precision", 100.0),
    num("eph-final", "Final horizontal precision", 0.1), num("epv-final", "Final vertical precision", 0.1,
        help="PX4/ArduPilot start fusing GPS once this is small enough."),
    num("eph-min-3d", "Minimum horizontal precision (3D fix)", 3.0, legacy=("eph-min_3d",)),
    num("eph-min-2d", "Minimum horizontal precision (2D fix)", 4.0, legacy=("eph-min_2d",)),
), {"id": "GPS"})

BAROMETER = _sensor("barometer", "Barometer", "Air pressure and altitude with a drift-and-noise model.", (
    num("qnh", "Sea-level pressure (QNH)", 1013.25, unit="mbar"),
    num("pressure_factor_sigma", "Pressure drift sigma", 0.001825, legacy=("pressure-factor-sigma",),
        help="The docs and schema spell this with hyphens; the code reads the underscore spelling."),
    num("pressure-factor-tau", "Pressure drift time constant", 3600, unit="s"),
    num("uncorrelated_noise_sigma", "Uncorrelated noise sigma", 2.7, legacy=("uncorrelated-noise-sigma",),
        help="The docs and schema spell this with hyphens; the code reads the underscore spelling."),
    num("update-latency", "Update latency", 0.0, unit="s"), num("update-frequency", "Update frequency", 50.0, unit="Hz"),
    num("startup-delay", "Startup delay", 0.0, unit="s"),
), {"id": "Barometer"})

MAGNETOMETER = _sensor("magnetometer", "Magnetometer", "Magnetic field in the vehicle's frame, with noise.", (
    vec3("noise-sigma", "Noise sigma", "0.005 0.005 0.005", legacy=("noise_sigma",),
         help="Standard deviation per axis. Some example configs write 'noise_sigma', which the simulator ignores."),
    vec3("noise-bias", "Noise bias", "0 0 0", legacy=("noise_bias",)),
    num("scale-factor", "Scale factor", 1.0), ORIGIN,
), {"id": "Magnetometer"})

AIRSPEED = _sensor("airspeed", "Airspeed", "Differential pressure from a pitot tube.", (
    vec3("forward-xyz", "Forward direction", "1 0 0", help="Direction the pitot tube points, in the link's frame."),
    num("pressure_factor_sigma", "Pressure drift sigma", 0.001825, legacy=("pressure-factor-sigma",)),
    num("pressure-factor-tau", "Pressure drift time constant", 3600, unit="s"),
    num("uncorrelated_noise_sigma", "Uncorrelated noise sigma", 1.052, legacy=("uncorrelated-noise-sigma",)),
), {"id": "Airspeed"})

BATTERY = _sensor("battery", "Battery", "Estimates charge and flight time. It does not model the battery's chemistry.", (
    enum("battery-mode", "Mode", ("simple-discharge-mode", "rotor-power-discharge-mode"), required=True,
         help="Simple: charge drains at a set rate. Rotor power: drains from an energy model of the motors."),
    num("total-battery-capacity", "Total capacity", 100.0, required=True, min=0, help="In joules for rotor-power mode (1 Ah = 3600 J)."),
    num("battery-capacity-on-start", "Capacity at start", 100.0, required=True, min=0, help="Same unit as the total capacity."),
    num("battery-drain-rate-on-start", "Drain rate at start", 0.02, min=0, required=True, when=("battery-mode", ("simple-discharge-mode",)),
        help="Units per second; can be changed while flying through the API. Required in simple mode."),
    num("rotor-power-coefficient", "Rotor power coefficient", 1.0, when=("battery-mode", ("rotor-power-discharge-mode",)),
        help="Scales the energy model to match real use."),
), {"id": "Battery", "battery-mode": "simple-discharge-mode", "total-battery-capacity": 36000,
    "battery-capacity-on-start": 36000, "battery-drain-rate-on-start": 1})

RADAR_MASK = (
    num("azimuth-min", "Azimuth min", required=True, unit="rad"), num("azimuth-max", "Azimuth max", required=True, unit="rad"),
    num("elevation-min", "Elevation min", required=True, unit="rad"), num("elevation-max", "Elevation max", required=True, unit="rad"),
    num("range-min", "Range min", required=True, unit="m"),
    num("range-max", "Range max", required=True, unit="m", legacy=("ange-max",),
        help="The JSON schema misspells this 'ange-max'; the code reads 'range-max'."),
    num("velocity-min", "Velocity min", required=True, unit="m/s", legacy=("evelocity-min",),
        help="The JSON schema misspells this 'evelocity-min'; the code reads 'velocity-min'."),
    num("velocity-max", "Velocity max", required=True, unit="m/s"),
    num("rcs-sqm-min", "RCS min", required=True, unit="m^2"), num("rcs-sqm-max", "RCS max", required=True, unit="m^2"),
)
RADAR = _sensor("radar", "Radar", "Ground-truth radar: detections (range, azimuth, elevation, velocity, RCS) and tracks.", (
    group("fov", "Field of view", (
        num("azimuth-min", "Azimuth min", -1.0472, required=True, unit="rad"), num("azimuth-max", "Azimuth max", 1.0472, required=True, unit="rad"),
        num("elevation-min", "Elevation min", -0.6981, required=True, unit="rad"), num("elevation-max", "Elevation max", 0.6981, required=True, unit="rad"),
        num("azimuth-resolution", "Azimuth resolution", 0.01745, required=True, unit="rad"),
        num("elevation-resolution", "Elevation resolution", 0.05236, required=True, unit="rad"),
    ), required=True),
    num("range-max", "Maximum range", 2000.0, required=True, min=0, unit="m"),
    num("range-resolution", "Range resolution", 3.0, required=True, min=0, unit="m"),
    num("velocity-max", "Maximum velocity", 200.0, required=True, unit="m/s"),
    num("velocity-resolution", "Velocity resolution", 1.0, required=True, min=0, unit="m/s"),
    num("detection-interval", "Detection interval", 0.02, required=True, unit="s"),
    num("track-interval", "Track interval", 0.1, required=True, unit="s"),
    num("rcs-adjust-factor", "RCS adjust factor", 1.0, required=True, min=0),
    boolean("draw-debug-points", "Draw hit points", False, required=True),
    table("masks", "Masks", RADAR_MASK, required=True, help="Windows of the field of view whose detections are dropped."),
    ORIGIN_REQUIRED,
), {"id": "Radar", "fov": {"azimuth-min": -1.0472, "azimuth-max": 1.0472, "elevation-min": -0.6981, "elevation-max": 0.6981,
                           "azimuth-resolution": 0.01745, "elevation-resolution": 0.05236},
    "range-max": 500, "range-resolution": 1, "velocity-max": 100, "velocity-resolution": 1, "detection-interval": 0.02,
    "track-interval": 0.2, "rcs-adjust-factor": 0.1, "draw-debug-points": False, "masks": [],
    "origin": {"xyz": "0 0 0", "rpy-deg": "0 0 0"}})

SENSORS = (CAMERA, IMU, LIDAR, GPS, BAROMETER, MAGNETOMETER, AIRSPEED, DISTANCE, RADAR, BATTERY)


# --------------------------------------------------------------------------------------------- actuators
def _actuator(type_: str, title: str, help_: str, settings: tuple, template: dict, needs_links: bool = True) -> BlockType:
    common = (
        text("name", "Name", required=True, help="Identifier of this actuator; the controller's actuator order refers to it."),
        boolean("enabled", "Enabled", True, required=True),
        link_ref("parent-link", "Applies force to link", "", required=needs_links,
                 help="The link the actuator pushes on."),
        link_ref("child-link", "Spins/moves link", "", required=needs_links,
                 help="The link that is turned visually (a propeller mesh); no joint physics are used."),
        ORIGIN,
    )
    if not needs_links:
        common = common[:2] + (common[4],)
    return BlockType("actuators", type_, title, "Actuators", help_, common + tuple(settings),
                     {"name": title.replace(" ", "_"), "type": type_, "enabled": True, **template})


ROTOR = _actuator("rotor", "Rotor", "A motor and propeller: turns the controller's signal into thrust and torque.", (
    group("rotor-settings", "Rotor", (
        enum("turning-direction", "Turning direction", ("clock-wise", "counter-clock-wise"), "clock-wise", required=True),
        vec3("normal-vector", "Axis", "0.0 0.0 -1.0", required=True,
             help="Rotation axis in NED. Only '0.0 0.0 -1.0' (straight up) is supported."),
        num("coeff-of-thrust", "Thrust coefficient (CT)", required=True), num("coeff-of-torque", "Torque coefficient (CP)", required=True),
        num("max-rpm", "Maximum speed", required=True, unit="rpm"),
        num("propeller-diameter", "Propeller diameter", required=True, unit="m"),
        num("smoothing-tc", "Smoothing time constant", required=True, unit="s", help="First-order filter that models motor inertia."),
        JSBSIM_CMD, JSBSIM_STATE,
    ), required=True),
), {"parent-link": "Frame", "child-link": "Prop", "origin": {"xyz": "0 0 0", "rpy-deg": "0 0 0"},
    "rotor-settings": {"turning-direction": "clock-wise", "normal-vector": "0.0 0.0 -1.0", "coeff-of-thrust": 0.109919,
                       "coeff-of-torque": 0.040164, "max-rpm": 6396.667, "propeller-diameter": 0.2286, "smoothing-tc": 0.005}})

LIFT_DRAG = _actuator("lift-drag-control-surface", "Control surface", "An aileron, rudder or elevator on a wing.", (
    group("lift-drag-control-surface-settings", "Control surface", (
        num("rotation-rate", "Maximum deflection", required=True, unit="rad"),
        num("smoothing-tc", "Smoothing time constant", required=True, unit="s"),
        JSBSIM_CMD, JSBSIM_STATE,
        Setting("target", STRING, "Target", source="docs", ignored="the docs show it, but the simulator never reads it for control surfaces"),
    ), required=True),
), {"parent-link": "Frame", "child-link": "Surface", "origin": {"xyz": "0 0 0", "rpy-deg": "0 0 0"},
    "lift-drag-control-surface-settings": {"rotation-rate": 0.524, "smoothing-tc": 0.5}})

TILT = _actuator("tilt", "Tilt", "Tilts another actuator (a rotor pod) through a limited angle.", (
    group("tilt-settings", "Tilt", (
        actuator_ref("target", "Tilted actuator", required=True, help="Name of the actuator whose orientation this one sets."),
        num("angle-min", "Angle at minimum input", required=True, unit="rad"),
        num("angle-max", "Angle at maximum input", required=True, unit="rad"),
        vec3("axis", "Tilt axis", required=True, help="Rotation axis in NED."),
        num("smoothing-tc", "Smoothing time constant", required=True, unit="s"),
        INPUT_MAP, JSBSIM_CMD, JSBSIM_STATE,
    ), required=True),
), {"parent-link": "Frame", "child-link": "Pod", "origin": {"xyz": "0 0 0", "rpy-deg": "0 0 0"},
    "tilt-settings": {"target": "", "angle-min": 0.0, "angle-max": 1.57, "axis": "0.0 -1.0 0.0", "smoothing-tc": 0.5}})

WHEEL = _actuator("wheel", "Wheel", "A driven, steered or braked wheel (rover physics).", (
    group("wheel-settings", "Wheel", (
        num("wheel-type", "Wheel type", 0.0, required=True, help="0 is a rear wheel, 1 a front wheel."),
        num("coeff-of-friction", "Friction coefficient", 0.0, required=True),
        num("coeff-of-wheel-torque", "Torque coefficient", 1.0, required=True),
        boolean("engine", "Driven by the engine", True, required=True), boolean("steering", "Steered", True, required=True,
                                                                              aliases=("steering-connected",)),
        boolean("brake", "Has a brake", True, required=True),
        vec3("normal-vector", "Axis", "0 0 -1"), num("smoothing-tc", "Smoothing time constant", 0.0, unit="s"),
        text("jsbsim-cmd-engine", "JSBSim engine command"), text("jsbsim-cmd-steering", "JSBSim steering command"),
        text("jsbsim-cmd-brake", "JSBSim brake command"),
    ), required=True),
), {"parent-link": "Frame", "child-link": "Wheel", "origin": {"xyz": "0 0 0", "rpy-deg": "0 0 0"},
    "wheel-settings": {"wheel-type": 0, "coeff-of-friction": 1.0, "coeff-of-wheel-torque": 1.0, "engine": True,
                       "steering": False, "brake": True}})

UNREAL_VEHICLE = _actuator("unreal-vehicle", "Unreal vehicle input", "Forwards one controller output to a parameter of an "
                           "Unreal Blueprint vehicle (needs 'unreal-vehicle-class' on the vehicle).", (
    group("unreal-vehicle-settings", "Unreal vehicle", (
        integer("parameter-index", "Parameter index", -1, required=True, min=0, help="Index of the IProjectAirSimVehicle parameter that receives the signal."),
        integer("control-signal-index", "Signal index", 0, min=0, help="Which element to use when the controller returns several signals."),
    ), required=True),
), {"unreal-vehicle-settings": {"parameter-index": 0}}, needs_links=False)

GIMBAL = _actuator("gimbal", "Gimbal", "Steers a camera (a camera names it in its gimbal settings).", (), {
    "parent-link": "Frame", "child-link": "Gimbal", "origin": {"xyz": "0 0 0", "rpy-deg": "0 0 0"}})

ACTUATORS = (ROTOR, LIFT_DRAG, TILT, WHEEL, GIMBAL, UNREAL_VEHICLE)


# ---------------------------------------------------------------------------------------------- structure
LINK = BlockType("links", "", "Link", "Structure", "A rigid part of the vehicle with mass, a shape for physics and an optional "
                 "visible mesh. Sensors and actuators attach to links.", (
    text("name", "Name", required=True, help="Identifier of this link; unique among the vehicle's links."),
    group("inertial", "Mass and aerodynamics", (
        ORIGIN,
        num("mass", "Mass", required=True, unit="kg"),
        group("inertia", "Inertia", (
            enum("type", "Type", ("geometry", "matrix", "point-mass"), required=True,
                 help="Geometry: a solid box; matrix: the diagonal terms; point-mass: none (the mass still counts for connected links)."),
            group("geometry", "Box", (group("box", "Box", (vec3("size", "Size", required=True, unit="m"),)),),
                  when=("type", ("geometry",))),
            num("ixx", "Ixx", when=("type", ("matrix",)), unit="kg m^2"), num("iyy", "Iyy", when=("type", ("matrix",)), unit="kg m^2"),
            num("izz", "Izz", when=("type", ("matrix",)), unit="kg m^2"),
        ), required=True),
        group("aerodynamics", "Aerodynamic drag", (
            num("drag-coefficient", "Drag coefficient (Cd)"),
            enum("type", "Type", ("geometry", "cross-section-areas-xyz", "lift-drag"),
                 help="How the cross-sectional areas are given; 'lift-drag' uses wing lift/drag formulas instead."),
            group("geometry", "Shape", (
                group("box", "Box", (vec3("size", "Size", required=True, unit="m"),)),
                group("cylinder", "Cylinder", (num("radius", "Radius", required=True, unit="m"),
                                               num("length", "Length", required=True, unit="m"))),
            ), when=("type", ("geometry",)), help="Give either a box or a cylinder."),
            vec3("cross-section-areas-xyz", "Cross-section areas", unit="m^2", when=("type", ("cross-section-areas-xyz",))),
            group("lift-drag", "Lift and drag", (
                num("alpha-0", "Zero-lift angle", unit="rad"), num("alpha-stall", "Stall angle", unit="rad"),
                num("area", "Wing area", unit="m^2"),
                num("c-lift-alpha", "Lift slope"), num("c-lift-alpha-stall", "Lift slope after stall"),
                num("c-drag-alpha", "Drag slope"), num("c-drag-alpha-stall", "Drag slope after stall"),
                num("c-moment-alpha", "Moment slope"), num("c-moment-alpha-stall", "Moment slope after stall"),
                num("control-surface-cl-per-rad", "Control-surface lift per radian"),
                num("control-surface-cd-per-rad", "Control-surface drag per radian"),
                num("control-surface-cm-per-rad", "Control-surface moment per radian"),
                vec3("center-pressure-xyz", "Centre of pressure", unit="m"), vec3("forward-xyz", "Forward direction"),
                vec3("upward-xyz", "Upward direction"),
            ), when=("type", ("lift-drag",))),
        )),
        vec3("inertia-tensor-scale", "Inertia scale", "1 1 1", help="Scales the inertia tensor (Unreal physics)."),
        integer("stablization-threshold-multiplier", "Stabilization threshold multiplier", 1, help="Unreal physics. (Spelled this way in the simulator.)"),
        integer("velocity-solver-iteration-count", "Velocity solver iterations", 1, help="Unreal physics."),
        integer("position-solver-iteration-count", "Position solver iterations", 8, help="Unreal physics."),
    ), help="Needed by fast physics; non-physics vehicles have none."),
    group("collision", "Collision", (
        boolean("enabled", "Detect collisions", True, help="Only has an effect when the link has a visual mesh."),
        num("restitution", "Restitution", min=0, max=1, help="0: no bounce, 1: bounces away at the impact speed."),
        num("friction", "Friction", min=0, max=1),
    )),
    group("visual", "Visible mesh", (
        ORIGIN,
        group("geometry", "Mesh", (
            enum("type", "Type", ("unreal_mesh", "file_mesh"), "unreal_mesh", required=True,
                 help="unreal_mesh uses a mesh already in the Unreal project; file_mesh loads a file."),
            text("name", "Unreal mesh path", when=("type", ("unreal_mesh",)), required=True,
                 help="Content path of the mesh, for example /Drone/Quadrotor1."),
            text("file_name", "Mesh file", when=("type", ("file_mesh",)), required=True),
            vec3("scale", "Scale", "1 1 1", help="Inherited by child links."),
        ), required=True),
        group("material", "Material", (
            vec4("color", "Colour", help="Red Green Blue Alpha."), text("texture", "Texture"),
        )),
    )),
    num("revolutions-per-sec", "Spin rate", unit="rev/s", source="schema",
        help="Animated links (used by environment actors): how fast the link spins about its axis."),
    num("initial-angle", "Initial angle", source="schema", help="Animated links: the angle it starts at."),
    vec3("axis", "Spin axis", source="schema", help="Animated links: the axis it spins about."),
    link_ref("parent-link", "Spins relative to link", source="schema", help="Animated links: the link it is relative to."),
), {"name": "NewLink", "inertial": {"mass": 0.1, "inertia": {"type": "point-mass"}},
    "collision": {"enabled": True, "restitution": 0.1, "friction": 0.5}})

JOINT = BlockType("joints", "", "Joint", "Structure", "Connects a parent link to a child link.", (
    text("id", "Name", required=True), enum("type", "Type", ("fixed", "continuous", "revolute"), "fixed", required=True,
                                             help="Fixed: no relative motion. Continuous: spins freely about the axis. Revolute: a hinge up to the limit."),
    link_ref("parent-link", "Parent link", required=True), link_ref("child-link", "Child link", required=True),
    vec3("axis", "Axis", "0 0 1", required=True, help="'1 0 0', '0 1 0' or '0 0 1'."),
    ORIGIN,
    num("limit", "Angle limit", unit="rad", when=("type", ("revolute",)), help="Revolute joints."),
    num("damping-constant", "Damping", 0.0), num("spring-constant", "Spring", 0.0), num("max-force", "Maximum force", 0.0),
    integer("parent-dominates", "Parent dominates", 0, min=0, max=1),
), {"id": "NewJoint", "type": "fixed", "parent-link": "Frame", "child-link": "", "axis": "0 0 1"})


# ---------------------------------------------------------------------------------------------- controller
SIMPLE_FLIGHT_PARAMS = (  # from tests/data/projectairsim_code_keys.json (the names Simple Flight reads)
    "FW_AIRSPD_FWD FW_AIRSPD_STALL FW_MIN_THR FW_PITCH_D FW_PITCH_I FW_PITCH_MAX FW_PITCH_MIN FW_PITCH_P FW_PR_D FW_PR_I "
    "FW_PR_P FW_P_RMAX FW_ROLL_D FW_ROLL_I FW_ROLL_MAX FW_ROLL_P FW_RR_D FW_RR_I FW_RR_P FW_R_RMAX FW_XY_D FW_XY_I FW_XY_P "
    "FW_XY_VEL_D FW_XY_VEL_I FW_XY_VEL_MAX FW_XY_VEL_P FW_YAW_D FW_YAW_I FW_YAW_MAX FW_YAW_P FW_YR_D FW_YR_I FW_YR_P "
    "FW_Y_RMAX FW_Z_D FW_Z_I FW_Z_P FW_Z_VEL_D FW_Z_VEL_I FW_Z_VEL_MAX FW_Z_VEL_P MC_APRX_ZERO_ANG_VEL MC_APRX_ZERO_VEL "
    "MC_BRK_VEL MC_CP_DIST MC_CP_VEL MC_DIST_ACC MC_MAX_BRK_DIST MC_MIN_BRK_DIST MC_MIN_BRK_VEL MC_OBS_WINDOW "
    "MC_PITCHRATE_D MC_PITCHRATE_I MC_PITCHRATE_MAX MC_PITCHRATE_P MC_PITCH_D MC_PITCH_I MC_PITCH_MAX MC_PITCH_P "
    "MC_ROLLRATE_D MC_ROLLRATE_I MC_ROLLRATE_MAX MC_ROLLRATE_P MC_ROLL_D MC_ROLL_I MC_ROLL_MAX MC_ROLL_P MC_TKO_Z "
    "MC_VEL_TO_BRK_DIST MC_YAWRATE_D MC_YAWRATE_I MC_YAWRATE_MAX MC_YAWRATE_P MC_YAW_D MC_YAW_I MC_YAW_MAX MC_YAW_P "
    "MPC_LAND_SPEED MPC_MIN_THR MPC_XY_D MPC_XY_I MPC_XY_P MPC_XY_VEL_D MPC_XY_VEL_I MPC_XY_VEL_MAX MPC_XY_VEL_P "
    "MPC_Z_D MPC_Z_I MPC_Z_P MPC_Z_VEL_D MPC_Z_VEL_I MPC_Z_VEL_MAX MPC_Z_VEL_P").split()
PX4_PARAMS = (("NAV_RCL_ACT", "Action on RC loss"), ("NAV_DLL_ACT", "Action on data-link loss"),
              ("COM_OBL_RC_ACT", "Offboard-loss RC action"), ("LPE_LAT", "Home latitude"), ("LPE_LON", "Home longitude"),
              ("MNT_MODE_IN", "Gimbal input mode (enables the gimbal)"), ("MNT_MODE_OUT", "Gimbal output mode"))

AIRFRAMES = ("quadrotor-x", "hexarotor-x", "vtol-quad-x-tailsitter", "vtol-quad-tiltrotor", "fixed-wing")
CONTROLLER_TYPES = ("ardupilot-api", "simple-flight-api", "px4-api", "simple-drive-api", "manual-controller-api",
                    "matlab-controller-api", "jsbsim-api")
ARDUPILOT_MANAGED = "The launcher sets this for every vehicle (9003 + 10i / 9002 + 10i) so SITL and the simulator find each other."

CONTROLLER = BlockType("controller", "", "Controller", "Controller", "Decides the actuator commands. This repository's "
                       "SITL setup uses ArduPilot; the other types are for Project AirSim's own controllers.", (
    text("id", "Name", required=True),
    enum("type", "Type", CONTROLLER_TYPES, "ardupilot-api", required=True,
         help="ardupilot-api is the one the launcher's SITL containers connect to."),
    enum("airframe-setup", "Airframe", AIRFRAMES, "quadrotor-x", when=("type", ("simple-flight-api",)),
         help="Simple Flight only; ArduPilot and PX4 learn the airframe from their firmware. (The schema also lists 'ackermann' here, "
              "but the code reads that under 'vehicle-setup' for the Simple Drive controller.)"),
    enum("vehicle-setup", "Vehicle", ("ackermann",), "ackermann", when=("type", ("simple-drive-api",)), source="code"),
    group("ardupilot-settings", "ArduPilot", (
        text("local-host-ip", "Simulator address", "127.0.0.1", help="Address of the simulator side of the UDP link."),
        text("ardupilot-ip", "ArduPilot address", "127.0.0.1", help="Address of the host running ArduPilot SITL."),
        Setting("ardupilot-udp-port", INTEGER, "ArduPilot UDP port", default=9003, required=True, managed=ARDUPILOT_MANAGED,
                help="Port SITL receives sensor data on."),
        Setting("local-host-udp-port", INTEGER, "Simulator UDP port", default=9002, required=True, managed=ARDUPILOT_MANAGED,
                help="Port the simulator receives motor outputs on."),
        boolean("use-sensor-distance", "Send distance-sensor data", False, legacy=("use-distance-sensor", "use-lidar"),
                help="Sends the distance sensor's readings to ArduPilot. The docs, the JSON schema and the sample configs all write "
                     "'use-distance-sensor', which the code does not read."),
        ACTUATOR_ORDER,
    ), when=("type", ("ardupilot-api",)), required=True),
    group("simple-flight-api-settings", "Simple Flight", (
        mapping("parameters", "Tuning parameters", tuple((n, "") for n in SIMPLE_FLIGHT_PARAMS),
                help="Overrides of Simple Flight's PID and limit parameters (MC_ multirotor, FW_ fixed wing, MPC_ position)."),
        ACTUATOR_ORDER,
    ), when=("type", ("simple-flight-api",)), required=True),
    group("simple-drive-api-settings", "Simple Drive", (
        mapping("parameters", "Tuning parameters", help="Overrides of the rover controller's parameters."), ACTUATOR_ORDER,
    ), when=("type", ("simple-drive-api",)), required=True),
    group("px4-settings", "PX4", (
        boolean("use-serial", "Use a serial port (HITL)", True, help="Takes precedence over TCP. On by default in the simulator: turn it off for SITL."),
        text("serial-port", "Serial port", "*", help="'*' finds a Pixhawk automatically."),
        integer("serial-baud-rate", "Serial baud rate", 115200),
        boolean("use-tcp", "Use TCP (SITL)", False), integer("tcp-port", "TCP port", 4560),
        boolean("lock-step", "Lock-step", False, help="Run Project AirSim and PX4 in step with each other."),
        text("udp-address", "UDP address", "127.0.0.1"), integer("udp-port", "UDP port", 14560),
        text("control-ip-address", "Control address", "127.0.0.1", help="'local', 'localhost' or 'remote' also work."),
        integer("control-port", "Control port", 14540, aliases=("control-port-local",)), integer("control-port-remote", "Remote control port", 14580),
        integer("gimbal-port", "Gimbal port", 13280), integer("gimbal-port-remote", "Remote gimbal port", 13030),
        text("qgc-host-ip", "Ground station address", ""), integer("qgc-port", "Ground station port", 0),
        text("local-host-ip", "Local address", "127.0.0.1"), text("model", "PX4 model", "Generic"),
        integer("timeout-lock-step-update-ms", "Lock-step update timeout", 100, unit="ms"),
        integer("timeout-lock-step-actuator-ms", "Lock-step actuator timeout", 500, unit="ms"),
        integer("timeout-lock-step-hil-actuator-control-ms", "Lock-step HIL actuator timeout", 100, unit="ms"),
        num("hil-gps-update-rate-hz", "HIL GPS update rate", 0.0, min=0, unit="Hz"),
        integer("sim-sys-id", "Simulator system id", 142), integer("sim-comp-id", "Simulator component id", 42),
        integer("offboard-sys-id", "Offboard system id", 134), integer("offboard-comp-id", "Offboard component id", 1),
        integer("vehicle-sys-id", "Vehicle system id", 135), integer("vehicle-comp-id", "Vehicle component id", 1),
        mapping("parameters", "PX4 parameters", PX4_PARAMS, help="Parameters sent to PX4 at start-up."),
        ACTUATOR_ORDER,
    ), when=("type", ("px4-api",)), required=True),
    group("manual-controller-api-settings", "Manual controller", (ACTUATOR_ORDER,),
          when=("type", ("manual-controller-api",)), required=True),
    group("matlab-controller-api-settings", "MATLAB controller", (
        group("control-connection", "Connection", (text("ip", "Address", "127.0.0.1"), integer("port", "Port", 8998))),
        ACTUATOR_ORDER,
    ), when=("type", ("matlab-controller-api",)), required=True),
), {"id": "ArduPilot_Controller", "type": "ardupilot-api", "ardupilot-settings": {
    "ardupilot-ip": "127.0.0.1", "ardupilot-udp-port": 9003, "local-host-ip": "127.0.0.1", "local-host-udp-port": 9002,
    "actuator-order": []}}, singleton=True)


# --------------------------------------------------------------------------------------------------- vehicle
JSBSIM_ONLY = ("physics-type", ("jsbsim-physics",))
VEHICLE = BlockType("vehicle", "", "Vehicle", "Vehicle", "Settings of the whole vehicle: which physics moves it.", (
    enum("physics-type", "Physics", ("fast-physics", "non-physics", "matlab-physics", "jsbsim-physics", "unreal-physics"),
         "non-physics", required=True,
         help="Fast physics: light multirotor model. Non-physics: only moves when a script sets its pose (computer vision). "
              "MATLAB: Simulink model. JSBSim: aircraft dynamics. Unreal: Unreal's own physics."),
    text("unreal-vehicle-class", "Unreal vehicle class", "", when=("physics-type", ("unreal-physics",)),
         help="Blueprint or C++ class path; must implement IProjectAirSimVehicle. Not together with a wheeled vehicle class."),
    text("wheeled-vehicle-class", "Wheeled vehicle class", "", when=("physics-type", ("unreal-physics",)),
         help="Class path of an AWheeledVehiclePawn. Not together with an Unreal vehicle class."),
    text("jsbsim-model", "JSBSim model", when=JSBSIM_ONLY, help="Aircraft model name."),
    text("jsbsim-script", "JSBSim script", when=JSBSIM_ONLY),
    text("jsbsim-root-path", "JSBSim models root", "/SimLibs/core_sim/jsbsim/models/", when=JSBSIM_ONLY),
    text("jsbsim-aircraft-path", "Aircraft path", "aircraft", when=JSBSIM_ONLY),
    text("jsbsim-engine-path", "Engine path", "engine", when=JSBSIM_ONLY),
    text("jsbsim-systems-path", "Systems path", "systems", when=JSBSIM_ONLY),
    num("jsbsim-dt", "JSBSim time step", 0.008333333, min=0, unit="s", when=JSBSIM_ONLY,
        help="Largest internal integration step; one state is still published per physics tick."),
    enum("jsbsim-ground-mode", "Ground height", ("constant", "terrain"), "constant", when=JSBSIM_ONLY,
         help="Constant: one height for the whole run. Terrain: sampled from the Unreal terrain."),
), {"physics-type": "fast-physics"}, singleton=True)

BLOCK_TYPES = (VEHICLE, CONTROLLER, LINK, JOINT) + ACTUATORS + SENSORS
SECTION_ORDER = ("vehicle", "controller", "links", "joints", "actuators", "sensors")
LIST_SECTIONS = ("links", "joints", "actuators", "sensors")


def block_type(section: str, type_: str = "") -> BlockType | None:
    """The block type of a config object, from the section it is in and its "type" value."""
    for bt in BLOCK_TYPES:
        if bt.section == section and (bt.type == type_ or section in ("vehicle", "controller", "links", "joints")):
            return bt
    return None


def palette() -> list[tuple[str, list[BlockType]]]:
    """The parts a vehicle can be built from, grouped for the palette: Sensors, Actuators, Structure."""
    groups: dict[str, list[BlockType]] = {}
    for bt in BLOCK_TYPES:
        if bt.section in ("links", "joints", "actuators", "sensors"):
            groups.setdefault(bt.group, []).append(bt)
    return [(name, groups[name]) for name in ("Sensors", "Actuators", "Structure") if name in groups]


# ------------------------------------------------------------------------------------- reading the catalog
def all_settings(settings: tuple[Setting, ...], prefix: str = "") -> list[tuple[str, Setting]]:
    """Every setting below `settings` with its dotted path; table rows count as one level (`capture-settings[].width`)."""
    out: list[tuple[str, Setting]] = []
    for s in settings:
        path = prefix + s.key
        out.append((path, s))
        if s.kind in (GROUP, TABLE):
            out.extend(all_settings(s.children, path + ("[]." if s.kind == TABLE else ".")))
    return out


def known_keys(bt: BlockType) -> set[str]:
    """Every key the simulator reads for this block type, with its accepted aliases (not the legacy spellings)."""
    keys: set[str] = set()
    for _, s in all_settings(bt.settings):
        if not s.ignored:
            keys.add(s.key)
            keys.update(s.aliases)
    return keys
