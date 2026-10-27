import copy
import json
import tempfile
import unittest
from pathlib import Path

from mrrsim.core import paths
from mrrsim.designs import vehicle_design as vd
from mrrsim.designs import vehicle_schema as vs
from mrrsim.scene import scenecheck

PROVIDED = paths.repo_root() / "sim_config" / "robot_ardu_quadrotor.jsonc"


def quad() -> vd.Design:
    return vd.Design(scenecheck.load_jsonc(PROVIDED), name="quad")


def messages(design: vd.Design, level: str | None = None) -> list[str]:
    return [i.message for i in design.issues() if level is None or i.level == level]


class Names(unittest.TestCase):
    def test_good_names(self):
        for name in ("Quad", "My drone 2", "x_y-z.v1", "Drone (big)", "A" * 60):
            self.assertEqual(vd.check_name(name), name)

    def test_spaces_are_tidied(self):
        self.assertEqual(vd.check_name("  My    drone "), "My drone")

    def test_bad_names_say_why(self):
        for bad, word in (("", "name"), ("   ", "name"), ("A" * 61, "longer"), ("a/b", "letters"), ("a\\b", "letters"),
                          ("a:b", "letters"), ("a*b", "letters"), ("é", "letters"), (".hidden", "dot"), ("name.", "dot"),
                          ("CON", "reserved"), ("nul.txt", "reserved"), ("com1", "reserved")):
            with self.assertRaises(vd.DesignError, msg=bad) as caught:
                vd.check_name(bad)
            self.assertIn(word, str(caught.exception).lower(), bad)

    def test_slug_is_safe_for_file_names(self):
        self.assertEqual(vd.slug("My Drone (big)!"), "my_drone_big")
        self.assertEqual(vd.slug("???"), "design")
        self.assertLessEqual(len(vd.slug("x" * 100)), 40)


class Values(unittest.TestCase):
    def setting(self, kind, **kw):
        return vs.Setting("k", kind, "Thing", **kw)

    def test_numbers(self):
        s = self.setting(vs.NUMBER)
        self.assertEqual(vd.parse_value(s, "3"), 3)
        self.assertEqual(vd.parse_value(s, "3.0"), 3.0)
        self.assertIsInstance(vd.parse_value(s, "3.0"), float)
        self.assertEqual(vd.parse_value(s, "-2.5e-3"), -0.0025)
        self.assertIsNone(vd.parse_value(s, "  "))
        with self.assertRaises(vd.DesignError):
            vd.parse_value(s, "abc")

    def test_integers(self):
        s = self.setting(vs.INTEGER)
        self.assertEqual(vd.parse_value(s, "16"), 16)
        self.assertEqual(vd.parse_value(s, "16.0"), 16)
        with self.assertRaises(vd.DesignError):
            vd.parse_value(s, "1.5")

    def test_vectors(self):
        s = self.setting(vs.VEC3)
        self.assertEqual(vd.parse_value(s, "0,0, -1"), "0 0 -1")
        for bad in ("1 2", "1 2 3 4", "a b c"):
            with self.assertRaises(vd.DesignError, msg=bad):
                vd.parse_value(s, bad)
        self.assertEqual(vd.parse_value(self.setting(vs.VEC4), "1 0 0 1"), "1 0 0 1")

    def test_booleans_and_enums(self):
        b = self.setting(vs.BOOL)
        self.assertIs(vd.parse_value(b, "Yes"), True)
        self.assertIs(vd.parse_value(b, "off"), False)
        with self.assertRaises(vd.DesignError):
            vd.parse_value(b, "maybe")
        e = self.setting(vs.ENUM, choices=((0, "Scene"), (1, "Depth")))
        self.assertEqual(vd.parse_value(e, "Depth"), 1)
        self.assertEqual(vd.parse_value(e, "0"), 0)
        with self.assertRaises(vd.DesignError):
            vd.parse_value(e, "Nope")

    def test_text_and_json(self):
        self.assertEqual(vd.parse_value(self.setting(vs.STRING), " hi "), "hi")
        self.assertEqual(vd.parse_value(self.setting(vs.JSON), '{"a": 1}'), {"a": 1})
        with self.assertRaises(vd.DesignError):
            vd.parse_value(self.setting(vs.JSON), "{bad")

    def test_formatting_round_trips(self):
        for kind, value in ((vs.NUMBER, 0.0023536), (vs.INTEGER, 16), (vs.VEC3, "0 0 -1"), (vs.BOOL, True), (vs.STRING, "x")):
            s = self.setting(kind)
            self.assertEqual(vd.parse_value(s, vd.format_value(s, value)), value, kind)
        e = self.setting(vs.ENUM, choices=((0, "Scene"), (1, "Depth")))
        self.assertEqual(vd.format_value(e, 1), "Depth")
        self.assertEqual(vd.format_value(e, None), "")


class Editing(unittest.TestCase):
    def test_the_provided_vehicle_is_listed_as_blocks(self):
        d = quad()
        self.assertEqual([d.label(r) for r in d.blocks("sensors")][:2], ['Camera "Chase"', 'Camera "DownCamera"'])
        self.assertEqual(len(d.blocks("links")), 5)
        self.assertEqual(len(d.blocks("actuators")), 4)
        self.assertEqual(d.blocks()[0], vd.BlockRef("vehicle"))
        self.assertEqual(d.name_of(vd.BlockRef("vehicle")), "Vehicle")

    def test_add_a_sensor_attaches_it_and_names_it_uniquely(self):
        d = quad()
        a = d.add_block(vs.LIDAR)
        b = d.add_block(vs.LIDAR, parent_link="Prop_FL")
        self.assertEqual(d.name_of(a), "Lidar")
        self.assertEqual(d.name_of(b), "Lidar2")
        self.assertEqual(d.get(a)["parent-link"], "Frame")   # the first link
        self.assertEqual(d.get(b)["parent-link"], "Prop_FL")
        self.assertEqual(d.get(a)["type"], "lidar")

    def test_a_new_block_is_a_copy_of_the_template(self):
        d = quad()
        ref = d.add_block(vs.CAMERA)
        d.set_value(ref, ("capture-settings", 0, "width"), 1920)
        self.assertEqual(vs.CAMERA.template["capture-settings"][0]["width"], 640)  # the catalog is untouched

    def test_set_and_remove_values_at_depth(self):
        d = quad()
        cam = d.find("sensors", "DownCamera")
        d.set_value(cam, ("capture-settings", 0, "width"), 800)
        d.set_value(cam, ("origin", "xyz"), "1 2 3")
        d.set_value(cam, ("annotation-settings", "enabled"), True)   # creates the group
        self.assertEqual(d.get_value(cam, ("capture-settings", 0, "width")), 800)
        self.assertEqual(d.get(cam)["annotation-settings"], {"enabled": True})
        d.set_value(cam, ("annotation-settings", "distance-filter-range"), 5)
        d.remove_value(cam, ("annotation-settings", "enabled"))
        self.assertEqual(d.get(cam)["annotation-settings"], {"distance-filter-range": 5})   # still has something: stays
        d.remove_value(cam, ("annotation-settings", "distance-filter-range"))
        self.assertNotIn("annotation-settings", d.get(cam))                                  # empty: removed with it
        d.set_value(cam, ("capture-interval",), None)   # None removes
        self.assertNotIn("capture-interval", d.get(cam))
        self.assertEqual(d.get_value(cam, ("nothing", "here"), "fallback"), "fallback")

    def test_emptied_groups_are_pruned_but_table_rows_are_not(self):
        d = quad()
        cam = d.find("sensors", "DownCamera")
        d.set_value(cam, ("gimbal", "lock-roll"), True)
        d.remove_value(cam, ("gimbal", "lock-roll"))
        self.assertNotIn("gimbal", d.get(cam))
        row = d.get(cam)["capture-settings"][0]
        for key in list(row):
            d.remove_value(cam, ("capture-settings", 0, key))
        self.assertEqual(d.get(cam)["capture-settings"][0], {})        # the row stays: its position is meaningful

    def test_table_rows(self):
        d = quad()
        cam = d.find("sensors", "DownCamera")
        row = {"image-type": 1, "width": 100, "height": 50, "fov-degrees": 90, "capture-enabled": True,
               "pixels-as-float": True, "compress": False}
        before = len(d.get(cam)["capture-settings"])
        self.assertEqual(d.append_row(cam, ("capture-settings",), row), before)
        self.assertEqual(len(d.get(cam)["capture-settings"]), before + 1)
        d.remove_value(cam, ("capture-settings", before))
        self.assertEqual(len(d.get(cam)["capture-settings"]), before)

    def test_remove_a_link_detaches_what_was_on_it(self):
        d = quad()
        d.add_block(vs.GPS, parent_link="Prop_FL")
        before = len(d.blocks("links"))
        self.assertTrue(d.references_to(d.find("links", "Prop_FL")))
        d.remove_block(d.find("links", "Prop_FL"))
        self.assertEqual(len(d.blocks("links")), before - 1)
        gps = d.find("sensors", "GPS2")
        self.assertEqual(d.get(gps)["parent-link"], "")
        self.assertEqual(d.get(d.find("actuators", "Prop_FL_actuator")).get("parent-link", ""), "Frame")  # not on it

    def test_remove_an_actuator_leaves_the_controller_order(self):
        d = quad()
        order = lambda: [e["id"] for e in d.get(vd.BlockRef("controller"))["ardupilot-settings"]["actuator-order"]]
        self.assertIn("Prop_FL_actuator", order())
        d.remove_block(d.find("actuators", "Prop_FL_actuator"))
        self.assertNotIn("Prop_FL_actuator", order())
        self.assertEqual(len(order()), 3)

    def test_rename_a_link_updates_everything_that_names_it(self):
        d = quad()
        d.rename_block(d.find("links", "Frame"), "Body")
        self.assertIsNone(d.find("links", "Frame"))
        self.assertEqual(d.get(d.find("sensors", "IMU1"))["parent-link"], "Body")
        self.assertEqual(d.get(d.find("actuators", "Prop_FL_actuator"))["parent-link"], "Body")
        self.assertEqual({j["parent-link"] for j in d.doc["joints"]}, {"Body"})
        self.assertEqual(messages(d, "error"), [])

    def test_rename_an_actuator_updates_the_controller_and_tilt_targets(self):
        d = quad()
        tilt = d.add_block(vs.TILT)
        d.set_value(tilt, ("tilt-settings", "target"), "Prop_FL_actuator")
        d.rename_block(d.find("actuators", "Prop_FL_actuator"), "Motor1")
        order = [e["id"] for e in d.get(vd.BlockRef("controller"))["ardupilot-settings"]["actuator-order"]]
        self.assertIn("Motor1", order)
        self.assertNotIn("Prop_FL_actuator", order)
        self.assertEqual(d.get(tilt)["tilt-settings"]["target"], "Motor1")

    def test_rename_refuses_clashes_and_blanks(self):
        d = quad()
        with self.assertRaises(vd.DesignError):
            d.rename_block(d.find("links", "Frame"), "Prop_FL")
        with self.assertRaises(vd.DesignError):
            d.rename_block(d.find("links", "Frame"), "  ")
        with self.assertRaises(vd.DesignError):
            d.rename_block(vd.BlockRef("vehicle"), "X")
        d.rename_block(d.find("links", "Frame"), "Frame")   # the same name: nothing happens

    def test_duplicate_and_move(self):
        d = quad()
        imu = d.find("sensors", "IMU1")
        copy_ref = d.duplicate_block(imu)
        self.assertEqual(d.name_of(copy_ref), "IMU1Copy")
        self.assertEqual(copy_ref.index, imu.index + 1)
        moved = d.move_block(copy_ref, -1)
        self.assertEqual(moved.index, imu.index)
        self.assertEqual(d.move_block(vd.BlockRef("sensors", 0), -1).index, 0)   # at the top already
        with self.assertRaises(vd.DesignError):
            d.duplicate_block(vd.BlockRef("controller"))

    def test_the_vehicle_and_controller_stay(self):
        d = quad()
        with self.assertRaises(vd.DesignError):
            d.remove_block(vd.BlockRef("controller"))
        with self.assertRaises(vd.DesignError):
            d.add_block(vs.VEHICLE)

    def test_switching_the_controller(self):
        d = quad()
        d.set_controller_type("simple-flight-api")
        c = d.doc["controller"]
        self.assertEqual((c["type"], c["airframe-setup"]), ("simple-flight-api", "quadrotor-x"))
        self.assertEqual([e["id"] for e in c["simple-flight-api-settings"]["actuator-order"]],
                         ["Prop_FL_actuator", "Prop_FR_actuator", "Prop_RL_actuator", "Prop_RR_actuator"])
        d.set_controller_type("ardupilot-api")
        s = d.doc["controller"]["ardupilot-settings"]
        self.assertEqual((s["ardupilot-udp-port"], s["local-host-udp-port"]), (9003, 9002))
        with self.assertRaises(vd.DesignError):
            d.set_controller_type("magic-api")

    def test_undo_and_redo(self):
        d = quad()
        self.assertFalse(d.dirty)
        cam = d.find("sensors", "DownCamera")
        original = d.get(cam)["capture-interval"]
        d.set_value(cam, ("capture-interval",), 0.5)
        d.add_block(vs.GPS)
        self.assertTrue(d.dirty)
        count = len(d.blocks("sensors"))
        d.undo()
        self.assertEqual(len(d.blocks("sensors")), count - 1)
        d.undo()
        self.assertEqual(d.get(d.find("sensors", "DownCamera"))["capture-interval"], original)
        self.assertFalse(d.dirty)
        self.assertFalse(d.can_undo)
        d.redo()
        d.redo()
        self.assertEqual(len(d.blocks("sensors")), count)
        d.undo()
        d.set_value(d.find("sensors", "Chase"), ("enabled",), False)   # a new edit drops the redo branch
        self.assertFalse(d.can_redo)

    def test_setting_the_same_value_is_not_an_edit(self):
        d = quad()
        cam = d.find("sensors", "DownCamera")
        d.set_value(cam, ("capture-interval",), d.get(cam)["capture-interval"])
        self.assertFalse(d.can_undo)
        self.assertFalse(d.dirty)

    def test_provided_vehicles_are_read_only(self):
        d = vd.Design(scenecheck.load_jsonc(PROVIDED), name="p", provided=True)
        for action in (lambda: d.add_block(vs.GPS), lambda: d.set_value(vd.BlockRef("vehicle"), ("physics-type",), "x"),
                       lambda: d.remove_block(vd.BlockRef("sensors", 0)), lambda: d.rename_block(vd.BlockRef("links", 0), "Z"),
                       lambda: d.fix_legacy(), lambda: d.replace_document({})):
            with self.assertRaises(vd.DesignError):
                action()

    def test_replace_document_is_one_undo_step(self):
        d = quad()
        d.replace_document({"physics-type": "non-physics"})
        self.assertEqual(d.doc, {"physics-type": "non-physics"})
        d.undo()
        self.assertEqual(len(d.blocks("links")), 5)
        with self.assertRaises(vd.DesignError):
            d.replace_document([])


class Validation(unittest.TestCase):
    def test_the_provided_vehicle_has_one_finding_and_no_errors(self):
        found = quad().issues()
        self.assertEqual([i.level for i in found if i.level != "info"], ["warning"])
        self.assertIn("use-sensor-distance", found[0].message)

    def test_fix_spellings_clears_the_warning(self):
        d = quad()
        self.assertEqual(d.fix_legacy(), 1)
        self.assertEqual(d.doc["controller"]["ardupilot-settings"]["use-sensor-distance"], False)
        self.assertNotIn("use-distance-sensor", d.doc["controller"]["ardupilot-settings"])
        self.assertEqual([i for i in d.issues() if i.level in ("warning", "error")], [])
        self.assertEqual(d.fix_legacy(), 0)

    def test_other_legacy_spellings_are_fixed_too(self):
        d = quad()
        gps = d.find("sensors", "GPS")
        d.get(gps).update({"eph-time-constant": 0.5, "eph-min_2d": 2.0})
        baro = d.add_block(vs.BAROMETER)
        d.get(baro).update({"pressure-factor-sigma": 0.1, "uncorrelated-noise-sigma": 0.2})
        lidar = d.add_block(vs.LIDAR)
        del d.get(lidar)["vertical-fov-upper-deg"]
        d.get(lidar).update({"lidar-type": "generic-cylindrical", "vertical-fov-start-deg": -10})
        self.assertGreaterEqual(len([i for i in d.issues() if i.level == "warning"]), 7)
        self.assertEqual(d.fix_legacy(), 7)
        self.assertEqual(d.get(gps)["eph-time-const"], 0.5)
        self.assertEqual(d.get(gps)["eph-min-2d"], 2.0)
        self.assertEqual(d.get(baro)["pressure_factor_sigma"], 0.1)
        self.assertEqual(d.get(lidar)["lidar-type"], "generic_cylindrical")
        self.assertEqual(d.get(lidar)["vertical-fov-upper-deg"], -10)
        self.assertEqual([i for i in d.issues() if i.level == "warning"], [])

    def test_missing_required_settings(self):
        d = quad()
        cam = d.find("sensors", "DownCamera")
        d.remove_value(cam, ("capture-settings", 0, "width"))
        d.remove_value(cam, ("origin",))
        text = messages(d, "error")
        self.assertTrue(any("capture-settings.0.width" in m for m in text), text)
        self.assertTrue(any("origin" in m and "required" in m for m in text), text)

    def test_wrong_types_ranges_and_enums(self):
        d = quad()
        cam = d.find("sensors", "DownCamera")
        d.get(cam)["capture-interval"] = "fast"
        d.get(cam)["capture-settings"][0]["fov-degrees"] = 400
        d.get(cam)["capture-settings"][0]["image-type"] = 9
        d.get(cam)["origin"]["xyz"] = "1 2"
        found = {i.level: i.message for i in d.issues() if "DownCamera" in i.message}
        text = "\n".join(i.message for i in d.issues())
        self.assertIn("capture-interval: must be a number", text)
        self.assertIn("above the usual maximum of 170", text)
        self.assertIn("9 is not one of", text)
        self.assertIn("xyz: must be 3 numbers", text)
        self.assertTrue(found)

    def test_unknown_settings_are_information_not_errors(self):
        d = quad()
        d.get(d.find("sensors", "IMU1"))["made-up-setting"] = 3
        found = [i for i in d.issues() if "made-up-setting" in i.message]
        self.assertEqual([i.level for i in found], ["info"])

    def test_a_documented_but_unread_setting_warns(self):
        d = quad()
        lidar = d.add_block(vs.LIDAR)
        d.get(lidar)["radial-scaling"] = 0.5
        self.assertTrue(any("never reads" in i.message or "never read" in i.message for i in d.issues() if i.level == "warning"))

    def test_duplicate_names_and_dangling_references(self):
        d = quad()
        d.doc["sensors"].append(copy.deepcopy(d.doc["sensors"][2]))   # a second IMU1
        d.get(d.find("sensors", "Chase"))["parent-link"] = "Nowhere"
        text = "\n".join(messages(d, "error"))
        self.assertIn("more than one sensor called 'IMU1'", text)
        self.assertIn("there is no link called 'Nowhere'", text)
        d.get(vd.BlockRef("controller"))["ardupilot-settings"]["actuator-order"].append({"id": "Ghost"})
        self.assertIn("there is no actuator called 'Ghost'", "\n".join(messages(d, "error")))

    def test_ardupilot_needs_an_imu_and_wants_a_gps(self):
        d = quad()
        d.remove_block(d.find("sensors", "IMU1"))
        self.assertTrue(any("enabled IMU" in m for m in messages(d, "error")))
        self.assertIn("no enabled IMU", d.runnable_problem())
        d2 = quad()
        d2.remove_block(d2.find("sensors", "GPS"))
        self.assertTrue(any("no enabled GPS" in m for m in messages(d2, "warning")))
        self.assertEqual(d2.runnable_problem(), "")
        d3 = quad()
        d3.set_value(d3.find("sensors", "IMU1"), ("enabled",), False)   # disabled does not count
        self.assertTrue(any("enabled IMU" in m for m in messages(d3, "error")))

    def test_actuator_order_must_match_the_rotors(self):
        d = quad()
        d.add_block(vs.ROTOR)
        self.assertTrue(any("does not list the rotor" in m and "gets no power" in m for m in messages(d, "error")))

    def test_a_rotor_listed_twice_is_an_error_that_names_it(self):
        """Arduquad had Prop_RL_actuator in two places and Prop_RR_actuator in none: it took off and flipped."""
        d = quad()
        order = d.doc["controller"]["ardupilot-settings"]["actuator-order"]
        order[3]["id"] = order[1]["id"]
        errors = messages(d, "error")
        self.assertTrue(any(f"{order[1]['id']!r} more than once" in m for m in errors), errors)
        self.assertTrue(any("does not list the rotor 'Prop_RR_actuator'" in m for m in errors), errors)
        self.assertEqual(messages(quad(), "error"), [])

    def test_conditional_settings(self):
        d = quad()
        battery = d.add_block(vs.BATTERY)
        d.remove_value(battery, ("battery-drain-rate-on-start",))
        self.assertTrue(any("battery-drain-rate-on-start" in m for m in messages(d, "error")))   # simple mode needs it
        d.set_value(battery, ("battery-mode",), "rotor-power-discharge-mode")
        self.assertFalse(any("battery-drain-rate-on-start" in m for m in messages(d, "error")))

    def test_livox_presets_need_none_of_the_scan_settings(self):
        d = quad()
        lidar = d.add_block(vs.LIDAR)
        for key in ("number-of-channels", "range", "points-per-second", "horizontal-rotation-frequency", "horizontal-fov-start-deg",
                    "horizontal-fov-end-deg", "vertical-fov-upper-deg", "vertical-fov-lower-deg"):
            d.remove_value(lidar, (key,))
        self.assertTrue(len(messages(d, "error")) >= 8)
        d.set_value(lidar, ("lidar-type",), "livox_mid70")
        self.assertEqual(messages(d, "error"), [])

    def test_controller_settings_follow_the_type(self):
        d = quad()
        d.set_controller_type("simple-flight-api")
        self.assertEqual(messages(d, "error"), [])
        self.assertIn("its controller is not ArduPilot", d.runnable_problem())
        d.remove_value(vd.BlockRef("controller"), ("simple-flight-api-settings",))
        self.assertTrue(any("simple-flight-api-settings" in m for m in messages(d, "error")))

    def test_unreal_vehicle_classes_exclude_each_other(self):
        d = quad()
        d.doc.update({"physics-type": "unreal-physics", "unreal-vehicle-class": "/A", "wheeled-vehicle-class": "/B"})
        self.assertTrue(any("not both" in m for m in messages(d, "error")))

    def test_vehicles_that_need_no_controller_or_links_are_fine(self):
        for physics in ("non-physics", "unreal-physics"):
            d = vd.Design({"physics-type": physics, "sensors": [{"id": "Cam", "type": "camera", "enabled": True,
                                                                  "capture-interval": 1, "origin": {"xyz": "0 0 0"},
                                                                  "capture-settings": [{"image-type": 0, "width": 1, "height": 1,
                                                                                         "fov-degrees": 90, "capture-enabled": True,
                                                                                         "pixels-as-float": False, "compress": False}]}]})
            self.assertEqual([i for i in d.issues() if i.level in ("warning", "error")], [], physics)

    def test_the_dollar_schema_key_is_allowed(self):
        d = quad()
        d.doc["$schema"] = "x"
        self.assertEqual([i for i in d.issues() if "$schema" in i.message], [])

    def test_a_blank_design_is_valid(self):
        d = vd.Design(vd.blank_document())
        self.assertEqual([i for i in d.issues() if i.level == "error"], [])
        self.assertEqual(d.runnable_problem(), "")

    def test_an_unknown_type_is_kept_and_reported(self):
        d = quad()
        d.doc["sensors"].append({"id": "Sonar", "type": "sonar", "enabled": True})
        self.assertTrue(any("unknown type 'sonar'" in m for m in messages(d, "warning")))


class Library(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.sim = self.tmp / "sim_config"
        self.sim.mkdir()
        (self.sim / "robot_a.jsonc").write_text(PROVIDED.read_text(encoding="utf-8"), encoding="utf-8")
        (self.sim / "robot_b_2.jsonc").write_text("// GENERATED by mrrsim scenegen from robot_a\n{}", encoding="utf-8")
        (self.sim / "scene_x.jsonc").write_text("{}", encoding="utf-8")
        self.lib = vd.Library(self.tmp / "vehicles", self.sim)

    def test_provided_vehicles_skip_generated_copies_and_scenes(self):
        self.assertEqual([p.name for p in self.lib.provided()], ["robot_a.jsonc"])
        d = self.lib.load_provided(self.lib.provided()[0])
        self.assertTrue(d.provided)
        with self.assertRaises(vd.DesignError):
            self.lib.save(d)

    def test_create_save_and_reload(self):
        d = self.lib.create("My quad", quad().doc)
        self.assertEqual(self.lib.names(), ["My quad"])
        self.assertTrue((self.tmp / "vehicles" / "My quad.jsonc").is_file())
        d.set_value(d.find("sensors", "Chase"), ("enabled",), False)
        self.assertTrue(d.dirty)
        self.lib.save(d)
        self.assertFalse(d.dirty)
        again = self.lib.load("My quad")
        self.assertFalse(again.get(again.find("sensors", "Chase"))["enabled"])
        self.assertEqual(again.doc, d.doc)

    def test_the_file_is_plain_jsonc_with_a_header(self):
        self.lib.create("Q", quad().doc)
        text = (self.tmp / "vehicles" / "Q.jsonc").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("// MRRSimulator vehicle design"))
        self.assertEqual(scenecheck.load_jsonc(self.tmp / "vehicles" / "Q.jsonc"), quad().doc)

    def test_names_are_unique_ignoring_case(self):
        self.lib.create("Quad", quad().doc)
        with self.assertRaises(vd.DesignError):
            self.lib.create("quad", quad().doc)
        self.assertEqual(self.lib.unique_name("Quad"), "Quad 2")
        self.lib.create("Quad 2", quad().doc)
        self.assertEqual(self.lib.unique_name("Quad"), "Quad 3")

    def test_duplicate_makes_an_independent_copy(self):
        d = self.lib.create("A", quad().doc)
        copy_ = self.lib.duplicate(d, "B")
        copy_.set_value(copy_.find("sensors", "Chase"), ("enabled",), False)
        self.lib.save(copy_)
        self.assertTrue(self.lib.load("A").get(self.lib.load("A").find("sensors", "Chase"))["enabled"])

    def test_rename(self):
        self.lib.create("Old", quad().doc)
        self.lib.select("Old")
        self.assertEqual(self.lib.rename("Old", "New name"), "New name")
        self.assertEqual(self.lib.names(), ["New name"])
        self.assertEqual(self.lib.selected(), "New name")      # the selection follows the rename
        self.lib.rename("New name", "NEW NAME")                # a change of case only
        self.assertEqual(self.lib.names(), ["NEW NAME"])
        self.lib.create("Other", quad().doc)
        with self.assertRaises(vd.DesignError):
            self.lib.rename("Other", "new name")
        with self.assertRaises(vd.DesignError):
            self.lib.rename("Other", "bad/name")
        with self.assertRaises(vd.DesignError):
            self.lib.rename("Ghost", "Z")

    def test_delete_forgets_the_selection(self):
        self.lib.create("A", quad().doc)
        self.lib.select("A")
        self.lib.delete("A")
        self.assertEqual(self.lib.names(), [])
        self.assertIsNone(self.lib.selected())
        with self.assertRaises(vd.DesignError):
            self.lib.delete("A")

    def test_selection(self):
        self.assertIsNone(self.lib.selected())
        self.lib.create("A", quad().doc)
        self.lib.select("A")
        self.assertEqual(self.lib.selected(), "A")
        self.lib.select(None)
        self.assertIsNone(self.lib.selected())
        self.lib.select("A")
        (self.tmp / "vehicles" / "A.jsonc").unlink()           # a deleted file is not selected any more
        self.assertIsNone(self.lib.selected())

    def test_the_selection_file_is_not_a_design(self):
        self.lib.create("A", quad().doc)
        self.lib.select("A")
        self.assertEqual(self.lib.names(), ["A"])

    def test_broken_files_give_a_clear_error(self):
        (self.tmp / "vehicles").mkdir()
        (self.tmp / "vehicles" / "Bad.jsonc").write_text("{not json", encoding="utf-8")
        with self.assertRaises(vd.DesignError) as caught:
            self.lib.load("Bad")
        self.assertIn("Bad.jsonc", str(caught.exception))
        (self.tmp / "vehicles" / "List.jsonc").write_text("[1]", encoding="utf-8")
        with self.assertRaises(vd.DesignError):
            self.lib.load("List")

    def test_files_with_trailing_commas_load(self):
        (self.tmp / "vehicles").mkdir()
        (self.tmp / "vehicles" / "Comma.jsonc").write_text('{"physics-type": "non-physics", "links": [{"name": "A",},],}', encoding="utf-8")
        self.assertEqual(self.lib.load("Comma").doc["links"][0]["name"], "A")

    def test_saving_does_not_leave_temporary_files(self):
        d = self.lib.create("A", quad().doc)
        self.lib.save(d)
        self.assertEqual(sorted(p.name for p in (self.tmp / "vehicles").iterdir()), ["A.jsonc"])

    def test_loading_a_missing_design(self):
        with self.assertRaises(vd.DesignError):
            self.lib.load("Ghost")

    def test_a_missing_provided_folder_is_fine(self):
        self.assertEqual(vd.Library(self.tmp / "v", None).provided(), [])
        self.assertEqual(vd.Library(self.tmp / "v", self.tmp / "none").provided(), [])


class PathsHelper(unittest.TestCase):
    def test_the_vehicles_folder_is_in_documents_and_can_be_moved_for_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            import os
            from unittest import mock
            with mock.patch.dict(os.environ, {"MRRSIM_VEHICLES_DIR": tmp}):
                self.assertEqual(paths.vehicles_dir(), Path(tmp))
        self.assertEqual(paths.vehicles_dir().name, "vehicles")
        self.assertEqual(paths.vehicles_dir().parent, paths.work_dir())


if __name__ == "__main__":
    unittest.main()
