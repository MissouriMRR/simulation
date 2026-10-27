"""The vehicle settings catalog: complete against the simulator's code, and consistent with itself."""
import json
import unittest
from pathlib import Path

from mrrsim.designs import vehicle_schema as vs

FIXTURE = json.loads((Path(__file__).parent / "data" / "projectairsim_code_keys.json").read_text(encoding="utf-8"))

# the config keys that are not settings of a block but how the blocks are arranged
STRUCTURE = {"type", "sensors", "actuators", "links", "joints", "controller"}
# component in the fixture -> the block types whose settings must cover it
COMPONENTS = {
    "sensor-common": vs.SENSORS, "camera": [vs.CAMERA], "lidar": [vs.LIDAR], "radar": [vs.RADAR], "imu": [vs.IMU],
    "gps": [vs.GPS], "barometer": [vs.BAROMETER], "magnetometer": [vs.MAGNETOMETER], "airspeed": [vs.AIRSPEED],
    "distance-sensor": [vs.DISTANCE], "battery": [vs.BATTERY],
    "actuator-common": vs.ACTUATORS, "input-map": [vs.TILT], "rotor": [vs.ROTOR], "tilt": [vs.TILT],
    "lift-drag-control-surface": [vs.LIFT_DRAG], "wheel": [vs.WHEEL], "gimbal": [vs.GIMBAL], "unreal-vehicle": [vs.UNREAL_VEHICLE],
    "link": [vs.LINK], "inertial": [vs.LINK], "joint": [vs.JOINT], "robot": [vs.VEHICLE],
    "controller-simple-flight": [vs.CONTROLLER], "controller-simple-drive": [vs.CONTROLLER], "controller-px4": [vs.CONTROLLER],
    "controller-ardupilot": [vs.CONTROLLER], "controller-manual": [vs.CONTROLLER], "controller-matlab": [vs.CONTROLLER],
}


class CoversEverythingTheSimulatorReads(unittest.TestCase):
    def test_every_component_of_the_fixture_is_checked(self):
        self.assertEqual(set(FIXTURE["components"]), set(COMPONENTS))

    def test_every_setting_the_cpp_code_reads_is_in_the_catalog(self):
        missing = []
        for component, keys in FIXTURE["components"].items():
            known = set(STRUCTURE)
            for bt in COMPONENTS[component]:
                known |= vs.known_keys(bt)
            missing += [f"{component}: {k}" for k in keys if k not in known]
        self.assertEqual(missing, [], "settings the simulator reads but the catalog lacks")

    def test_the_simple_flight_tuning_parameters_are_all_listed(self):
        listed = {name for name, _ in next(s for s in vs.CONTROLLER.setting("simple-flight-api-settings").children
                                           if s.key == "parameters").catalog}
        self.assertEqual(set(FIXTURE["simple-flight-parameters"]), listed)
        self.assertEqual(len(listed), 94)

    def test_the_fixture_is_not_empty(self):
        self.assertGreater(sum(len(v) for v in FIXTURE["components"].values()), 300)


class SpellingsThatDiffer(unittest.TestCase):
    """Where the code and the docs/schema disagree, the catalog uses the code's spelling and remembers the other."""

    def legacy(self, bt, key):
        found = {s.key: s for _, s in vs.all_settings(bt.settings)}
        return found[key].legacy

    def test_known_disagreements(self):
        self.assertIn("pressure-factor-sigma", self.legacy(vs.BAROMETER, "pressure_factor_sigma"))
        self.assertIn("uncorrelated-noise-sigma", self.legacy(vs.AIRSPEED, "uncorrelated_noise_sigma"))
        self.assertIn("eph-time-constant", self.legacy(vs.GPS, "eph-time-const"))
        self.assertIn("use-distance-sensor", self.legacy(vs.CONTROLLER, "use-sensor-distance"))
        self.assertIn("vertical-fov-start-deg", self.legacy(vs.LIDAR, "vertical-fov-upper-deg"))
        self.assertIn("generic-cylindrical", self.legacy(vs.LIDAR, "lidar-type"))
        self.assertIn("evelocity-min", self.legacy(vs.RADAR, "velocity-min"))

    def test_a_documented_setting_the_code_never_reads_is_marked_ignored(self):
        found = {s.key: s for _, s in vs.all_settings(vs.LIDAR.settings)}
        self.assertTrue(found["radial-scaling"].ignored)
        self.assertNotIn("radial-scaling", vs.known_keys(vs.LIDAR))

    def test_no_legacy_spelling_is_also_a_real_key(self):
        for bt in vs.BLOCK_TYPES:
            real = vs.known_keys(bt)
            for path, s in vs.all_settings(bt.settings):
                for old in s.legacy:
                    self.assertNotIn(old, real - {s.key}, f"{bt.title} {path}: {old}")


class DocumentedSettings(unittest.TestCase):
    """Settings from docs/sensors and docs/config_robot.md (the user's pointer), by name."""

    DOCS = {
        vs.CAMERA: ["capture-interval", "annotation-settings", "image-type", "width", "height", "fov-degrees", "capture-enabled",
                    "streaming-enabled", "pixels-as-float", "compress", "auto-exposure-method", "auto-exposure-speed",
                    "auto-exposure-max-brightness", "auto-exposure-min-brightness", "auto-exposure-low-percent",
                    "auto-exposure-histogram-log-min", "auto-exposure-histogram-log-max", "motion-blur-amount", "target-gamma",
                    "max-depth-meters", "bbox2D-settings", "bbox3D-settings", "post-process-model-settings", "model-filepath",
                    "execution-provider", "object-ids", "alignment"],
        vs.BATTERY: ["battery-mode", "total-battery-capacity", "battery-capacity-on-start", "battery-drain-rate-on-start",
                     "rotor-power-coefficient"],
        vs.LIDAR: ["lidar-type", "number-of-channels", "range", "points-per-second", "report-frequency",
                   "horizontal-rotation-frequency", "horizontal-fov-start-deg", "horizontal-fov-end-deg",
                   "vertical-rotation-frequency", "disable-self-hits", "draw-debug-points", "origin"],
        vs.RADAR: ["fov", "range-max", "range-resolution", "velocity-max", "velocity-resolution", "detection-interval",
                   "track-interval", "rcs-adjust-factor", "draw-debug-points", "masks", "azimuth-max", "azimuth-min",
                   "elevation-max", "elevation-min", "azimuth-resolution", "elevation-resolution", "range-min", "velocity-min",
                   "rcs-sqm-min", "rcs-sqm-max"],
        vs.LINK: ["name", "inertial", "collision", "visual", "origin", "mass", "inertia", "aerodynamics", "drag-coefficient",
                  "ixx", "iyy", "izz", "box", "cylinder", "size", "radius", "length", "cross-section-areas-xyz", "enabled",
                  "restitution", "friction", "geometry", "scale"],
        vs.JOINT: ["id", "origin", "parent-link", "child-link", "type", "axis", "limit", "damping-constant"],
        vs.ROTOR: ["turning-direction", "normal-vector", "coeff-of-thrust", "coeff-of-torque", "max-rpm", "propeller-diameter",
                   "smoothing-tc", "name", "enabled", "parent-link", "child-link", "origin"],
        vs.TILT: ["axis", "angle-min", "angle-max", "input-map", "target", "smoothing-tc", "clamp-input", "clamp-output",
                  "input-min", "input-max", "scale", "output-min", "output-max"],
        vs.LIFT_DRAG: ["rotation-rate", "smoothing-tc"],
        vs.CONTROLLER: ["id", "airframe-setup", "type", "simple-flight-api-settings", "px4-settings", "ardupilot-settings",
                        "manual-controller-api-settings", "actuator-order", "local-host-ip", "ardupilot-ip", "ardupilot-udp-port",
                        "local-host-udp-port", "lock-step", "use-serial", "serial-port", "serial-baud-rate", "use-tcp", "tcp-port",
                        "control-ip-address", "control-port", "control-port-remote", "qgc-host-ip", "qgc-port", "parameters",
                        "initial-value"],
        vs.VEHICLE: ["physics-type", "unreal-vehicle-class", "wheeled-vehicle-class", "jsbsim-ground-mode", "jsbsim-dt",
                     "jsbsim-root-path", "jsbsim-aircraft-path", "jsbsim-engine-path", "jsbsim-systems-path", "jsbsim-model"],
    }

    def test_every_documented_setting_is_in_the_catalog(self):
        for bt, keys in self.DOCS.items():
            everything = {s.key for _, s in vs.all_settings(bt.settings)} | {a for _, s in vs.all_settings(bt.settings) for a in s.aliases + s.legacy}
            for key in keys:
                self.assertIn(key, everything, f"{bt.title}: {key}")

    def test_every_sensor_type_of_the_json_schema_has_a_block(self):
        schema_types = {"camera", "imu", "lidar", "distance-sensor", "gps", "barometer", "magnetometer", "radar", "airspeed", "battery"}
        self.assertEqual({bt.type for bt in vs.SENSORS}, schema_types)

    def test_every_actuator_and_controller_type_of_the_json_schema_has_a_block(self):
        self.assertEqual({bt.type for bt in vs.ACTUATORS}, {"rotor", "lift-drag-control-surface", "tilt", "gimbal", "wheel", "unreal-vehicle"})
        self.assertEqual(set(vs.CONTROLLER.setting("type").choice_values()),
                         {"simple-flight-api", "simple-drive-api", "px4-api", "ardupilot-api", "manual-controller-api",
                          "matlab-controller-api", "jsbsim-api"})
        self.assertEqual(set(vs.VEHICLE.setting("physics-type").choice_values()),
                         {"fast-physics", "non-physics", "matlab-physics", "jsbsim-physics", "unreal-physics"})


class Consistency(unittest.TestCase):
    def test_keys_are_unique_within_each_level(self):
        def check(settings, where):
            keys = [k for s in settings for k in (s.key, *s.aliases)]
            self.assertEqual(len(keys), len(set(keys)), f"duplicate key in {where}: {sorted(k for k in keys if keys.count(k) > 1)}")
            for s in settings:
                check(s.children, f"{where}.{s.key}")
        for bt in vs.BLOCK_TYPES:
            check(bt.settings, bt.title)

    def test_every_when_refers_to_a_sibling(self):
        def check(settings, where):
            siblings = {s.key for s in settings}
            for s in settings:
                if s.when:
                    self.assertIn(s.when[0], siblings, f"{where}.{s.key} is conditional on a setting that is not beside it")
                check(s.children, f"{where}.{s.key}")
        for bt in vs.BLOCK_TYPES:
            check(bt.settings, bt.title)

    def test_enum_defaults_and_ranges_make_sense(self):
        for bt in vs.BLOCK_TYPES:
            for path, s in vs.all_settings(bt.settings):
                if s.kind == vs.ENUM:
                    self.assertTrue(s.choices, f"{bt.title} {path}: an enum without choices")
                    if s.default is not None:
                        self.assertIn(s.default, s.choice_values(), f"{bt.title} {path}")
                if s.minimum is not None and s.maximum is not None:
                    self.assertLessEqual(s.minimum, s.maximum, f"{bt.title} {path}")
                if s.kind in (vs.GROUP, vs.TABLE):
                    self.assertTrue(s.children, f"{bt.title} {path}: a group without settings")

    def test_every_setting_has_a_label_and_the_catalog_is_big(self):
        total = 0
        for bt in vs.BLOCK_TYPES:
            for path, s in vs.all_settings(bt.settings):
                total += 1
                self.assertTrue(s.title, path)
        self.assertGreater(total, 400)

    def test_every_template_uses_only_known_keys_and_names_itself(self):
        for bt in vs.BLOCK_TYPES:
            keys = vs.known_keys(bt) | {"type"}
            for key in bt.template:
                self.assertIn(key, keys, f"{bt.title} template: {key}")
            if bt.section in ("sensors", "actuators", "links", "joints"):
                self.assertTrue(bt.template.get(bt.name_key), f"{bt.title} template has no {bt.name_key}")
            if bt.type:
                self.assertEqual(bt.template.get("type"), bt.type)

    def test_the_palette_lists_every_part_once(self):
        names = [bt.title for _, bts in vs.palette() for bt in bts]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), len(vs.SENSORS) + len(vs.ACTUATORS) + 2)

    def test_block_type_lookup(self):
        self.assertIs(vs.block_type("sensors", "lidar"), vs.LIDAR)
        self.assertIs(vs.block_type("actuators", "rotor"), vs.ROTOR)
        self.assertIs(vs.block_type("links"), vs.LINK)
        self.assertIsNone(vs.block_type("sensors", "sonar"))

    def test_the_ardupilot_ports_are_managed(self):
        group = vs.CONTROLLER.setting("ardupilot-settings")
        self.assertTrue(group.child("ardupilot-udp-port").managed)
        self.assertTrue(group.child("local-host-udp-port").managed)


if __name__ == "__main__":
    unittest.main()
