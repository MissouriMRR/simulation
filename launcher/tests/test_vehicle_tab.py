"""The Vehicles tab and its link to the Run tab. These create real (hidden) Tk windows, so they only run when
MRRSIM_GUI_TESTS=1 and are meant to be started on a hidden desktop (see ui/test_run_tab.py)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config, paths
from mrrsim.designs import vehicle_design as vd, vehicle_schema as vs
from mrrsim.scene import scenecheck
from mrrsim.ui import app as window
from mrrsim.ui.run.commands import PROVIDED_CHOICE
from mrrsim.ui.vehicles import vehicle_tab

GUI = os.environ.get("MRRSIM_GUI_TESTS") == "1"


def settle(root, times=5):
    for _ in range(times):
        root.update()


@unittest.skipUnless(GUI, "set MRRSIM_GUI_TESTS=1 (and run on a hidden desktop) to test the real window")
class Tab(unittest.TestCase):
    def setUp(self):
        import tkinter as tk

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.vehicles = self.tmp / "vehicles"
        patches = mock.patch.dict("os.environ", {
            "MRRSIM_USER_SCRIPTS": str(self.tmp / "mine"), "MRRSIM_DATA_DIR": str(self.tmp / "data"),
            "MRRSIM_VEHICLES_DIR": str(self.vehicles)})
        patches.start()
        self.addCleanup(patches.stop)
        self.addCleanup(self._tmp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        wd = mock.patch.object(paths, "work_dir", return_value=self.tmp / "work")
        wd.start()
        self.addCleanup(wd.stop)
        self.cfg = config.load(paths.repo_root() / "config" / "mrrsim.toml")
        self.app = window.App(self.root, self.cfg)
        self.tab = self.app.vehicles_tab
        self.root.deiconify()
        settle(self.root)

    # helpers ---------------------------------------------------------------------------------------
    def duplicate(self, name="My quad"):
        with mock.patch.object(vehicle_tab.simpledialog, "askstring", return_value=name):
            self.tab.duplicate_design()
        settle(self.root)
        return self.tab.design

    def item_of(self, ref):
        return self.tab._ref_items[ref]

    def quiet_dialogs(self):
        return mock.patch.multiple(vehicle_tab.messagebox, showwarning=mock.DEFAULT, showerror=mock.DEFAULT, showinfo=mock.DEFAULT)

    # the window ------------------------------------------------------------------------------------
    def test_the_run_tab_is_first_then_vehicles_then_the_simulator(self):
        texts = [self.app.tabs.tab(i, "text") for i in range(self.app.tabs.index("end"))]
        self.assertEqual(texts, ["Run", "Vehicles", "Simulator"])
        self.assertEqual(self.app.tabs.index(self.app.tabs.select()), 0)

    def test_it_starts_on_the_provided_vehicle_read_only(self):
        d = self.tab.design
        self.assertTrue(d.provided)
        self.assertIn("provided vehicle", self.tab.banner.cget("text"))
        self.assertIn("disabled", self.tab.save_button.state())
        self.assertIn("disabled", self.tab.add_button.state())
        self.assertIn("disabled", self.tab.rename_button.state())
        self.assertNotIn("disabled", self.tab.duplicate_button.state())
        self.tab.select_block(d.find("sensors", "DownCamera"))
        self.assertTrue(self.tab.fields)
        with self.quiet_dialogs():
            self.assertFalse(self.tab.commit_field(("capture-interval",), "0.5"))
        self.assertEqual(d.get(d.find("sensors", "DownCamera"))["capture-interval"], 0.02)

    def test_the_vehicle_tree_shows_parts_on_their_links(self):
        d = self.tab.design
        tree = self.tab.vehicle_tree
        link = self.item_of(d.find("links", "Frame"))
        kids = [self.tab._block_items[c] for c in tree.get_children(link)]
        names = [d.name_of(r) for r in kids]
        self.assertIn("IMU1", names)
        self.assertIn("Prop_FL_actuator", names)
        self.assertEqual(tree.item(self.item_of(vd.BlockRef("controller")), "text"), 'Controller "ArduPilot_Controller"')

    # making a design ---------------------------------------------------------------------------------
    def test_duplicating_a_provided_vehicle_makes_an_editable_saved_design(self):
        d = self.duplicate("My quad")
        self.assertFalse(d.provided)
        self.assertEqual(d.name, "My quad")
        self.assertTrue((self.vehicles / "My quad.jsonc").is_file())
        self.assertEqual(self.tab.design_combo.get(), "My quad")
        self.assertNotIn("disabled", self.tab.add_button.state())
        self.assertEqual(self.tab.banner.cget("text"), "")

    def test_a_bad_name_is_refused_and_asked_again(self):
        answers = iter(["bad/name", "Good name"])
        with mock.patch.object(vehicle_tab.simpledialog, "askstring", side_effect=lambda *a, **k: next(answers)), \
                mock.patch.object(vehicle_tab.messagebox, "showerror") as error:
            self.tab.duplicate_design()
        error.assert_called_once()
        self.assertEqual(self.tab.design.name, "Good name")

    def test_cancelling_the_name_creates_nothing(self):
        with mock.patch.object(vehicle_tab.simpledialog, "askstring", return_value=None):
            self.tab.duplicate_design()
        self.assertEqual(self.tab.library.names(), [])

    def test_a_new_blank_design(self):
        with mock.patch.object(vehicle_tab.simpledialog, "askstring", return_value="Blank one"):
            self.tab.new_design()
        d = self.tab.design
        self.assertEqual(d.name, "Blank one")
        self.assertEqual([d.name_of(r) for r in d.blocks("sensors")], ["IMU", "GPS"])
        self.assertTrue((self.vehicles / "Blank one.jsonc").is_file())

    # building ----------------------------------------------------------------------------------------
    def test_adding_a_part_puts_it_on_the_selected_link(self):
        d = self.duplicate()
        self.tab.select_block(d.find("links", "Prop_FL"))
        ref = self.tab.add_part(vs.LIDAR)
        self.assertEqual(d.get(ref)["parent-link"], "Prop_FL")
        self.assertEqual(self.tab.current, ref)
        parent_item = self.tab.vehicle_tree.parent(self.item_of(ref))
        self.assertEqual(self.tab._block_items[parent_item], d.find("links", "Prop_FL"))
        self.assertTrue(d.dirty)
        self.assertIn("*", self.tab.vehicle_frame.cget("text"))

    def test_dropping_a_part_on_a_link_attaches_it_there(self):
        d = self.duplicate()
        target = self.item_of(d.find("links", "Prop_RR"))
        ref = self.tab.drop_part(vs.GPS, target)
        self.assertEqual(d.get(ref)["parent-link"], "Prop_RR")
        loose = self.tab.drop_part(vs.BAROMETER, None)           # dropped on empty space: the default link
        self.assertEqual(d.get(loose)["parent-link"], "Frame")

    def test_dropping_on_a_part_uses_that_parts_link(self):
        d = self.duplicate()
        target = self.item_of(d.find("sensors", "IMU1"))
        ref = self.tab.drop_part(vs.AIRSPEED, target)
        self.assertEqual(d.get(ref)["parent-link"], "Frame")

    def test_double_clicking_the_palette_adds_the_part(self):
        self.duplicate()
        part = next(i for i, bt in self.tab._part_items.items() if bt is vs.RADAR)
        self.tab.parts_tree.selection_set(part)
        before = len(self.tab.design.blocks("sensors"))
        self.tab.add_selected_part()
        self.assertEqual(len(self.tab.design.blocks("sensors")), before + 1)

    def test_removing_a_part_asks_and_says_who_uses_it(self):
        d = self.duplicate()
        self.tab.select_block(d.find("links", "Prop_FL"))
        with mock.patch.object(vehicle_tab.messagebox, "askyesno", return_value=False) as ask:
            self.tab.remove_selected()
        self.assertIn("Prop_FL_actuator", ask.call_args.args[1])            # what is attached to it is named
        self.assertIsNotNone(d.find("links", "Prop_FL"))
        with mock.patch.object(vehicle_tab.messagebox, "askyesno", return_value=True):
            self.tab.remove_selected()
        self.assertIsNone(d.find("links", "Prop_FL"))

    def test_duplicate_and_move_buttons(self):
        d = self.duplicate()
        imu = d.find("sensors", "IMU1")
        self.tab.select_block(imu)
        self.tab.duplicate_selected()
        self.assertEqual(d.name_of(self.tab.current), "IMU1Copy")
        self.tab.move_selected(-1)
        self.assertEqual(self.tab.current.index, imu.index)

    def test_undo_and_redo_buttons_follow_the_history(self):
        d = self.duplicate()
        self.assertIn("disabled", self.tab.undo_button.state())
        self.tab.add_part(vs.LIDAR)
        self.assertNotIn("disabled", self.tab.undo_button.state())
        count = len(d.blocks("sensors"))
        self.tab.undo()
        self.assertEqual(len(d.blocks("sensors")), count - 1)
        self.assertNotIn("disabled", self.tab.redo_button.state())
        self.tab.redo()
        self.assertEqual(len(d.blocks("sensors")), count)

    # the form ----------------------------------------------------------------------------------------
    def test_the_form_has_a_field_for_every_setting_of_the_block(self):
        d = self.duplicate()
        ref = self.tab.add_part(vs.LIDAR)
        for s in vs.LIDAR.settings:
            if s.kind in (vs.GROUP, vs.TABLE, vs.MAP) or s.ignored or (s.when and not vd.sibling_in(s.when, d.get(ref), vs.LIDAR.settings)):
                continue
            self.assertIn((s.key,), self.tab.fields, s.key)

    def test_every_camera_capture_setting_is_in_the_form(self):
        d = self.duplicate()
        self.tab.select_block(d.find("sensors", "DownCamera"))
        for s in vs.CAMERA.setting("capture-settings").children:
            self.assertIn(("capture-settings", 0, s.key), self.tab.fields, s.key)

    def test_the_camera_form_streams_pictures_to_the_script(self):
        from mrrsim.scene import camerastream
        d = self.duplicate()
        cam = d.find("sensors", "DownCamera")
        self.tab.select_block(cam)
        key = camerastream.STREAM_KEY
        for s in vs.CAMERA.setting(key).children:
            if not s.when:
                self.assertIn((key, s.key), self.tab.fields, s.key)
        self.assertTrue(self.tab.commit_field((key, "enabled"), "true"))
        self.assertTrue(self.tab.commit_field((key, "port"), "5700"))
        self.assertEqual(d.get(cam)[key], {"enabled": True, "port": 5700})
        self.assertEqual(camerastream.streams(d.doc, 1)[0]["port"], 5700)
        self.assertEqual([i for i in d.issues() if i.level == "error"], [])
        self.tab.commit_field((key, "image-type"), "Segmentation")      # the DownCamera has no such capture
        self.assertTrue([i for i in d.issues() if i.level == "error" and "Send images to scripts" in i.message])

    def test_copy_settings_puts_the_airsim_json_on_the_clipboard(self):
        d = self.duplicate()
        self.tab.copy_settings()
        text = self.root.clipboard_get()
        self.assertEqual(json.loads(text), json.loads(d.airsim_json()))
        self.assertIn("Copied", self.tab.copy_json_button.cget("text"))

    def test_show_in_folder_reveals_the_designs_file_or_the_provided_one(self):
        d = self.duplicate()
        with mock.patch.object(vehicle_tab, "_reveal") as reveal:
            self.tab.show_in_folder()
        reveal.assert_called_once_with(d.path)
        self.tab.open_design(provided=self.tab.library.provided()[0])
        with mock.patch.object(vehicle_tab, "_reveal") as reveal:
            self.tab.show_in_folder()
        self.assertEqual(reveal.call_args.args[0].name, "robot_ardu_quadrotor.jsonc")

    def test_editing_a_field_changes_the_design(self):
        d = self.duplicate()
        cam = d.find("sensors", "DownCamera")
        self.tab.select_block(cam)
        self.assertTrue(self.tab.commit_field(("capture-interval",), "0.5"))
        self.assertEqual(d.get(cam)["capture-interval"], 0.5)
        self.assertTrue(self.tab.commit_field(("capture-settings", 0, "width"), "320"))
        self.assertEqual(d.get(cam)["capture-settings"][0]["width"], 320)
        self.assertTrue(self.tab.commit_field(("origin", "xyz"), "1, 2, 3"))
        self.assertEqual(d.get(cam)["origin"]["xyz"], "1 2 3")
        self.assertTrue(d.dirty)

    def test_a_bad_value_is_refused_with_a_message_and_nothing_changes(self):
        d = self.duplicate()
        cam = d.find("sensors", "DownCamera")
        self.tab.select_block(cam)
        with mock.patch.object(vehicle_tab.messagebox, "showwarning") as warn:
            self.assertFalse(self.tab.commit_field(("capture-interval",), "fast"))
            self.assertFalse(self.tab.commit_field(("origin", "xyz"), "1 2"))
        self.assertEqual(warn.call_count, 2)
        self.assertEqual(d.get(cam)["capture-interval"], 0.02)

    def test_an_emptied_field_removes_the_setting(self):
        d = self.duplicate()
        cam = d.find("sensors", "DownCamera")
        self.tab.select_block(cam)
        self.tab.commit_field(("capture-interval",), "")
        self.assertNotIn("capture-interval", d.get(cam))

    def test_choosing_the_default_option_removes_a_setting(self):
        d = self.duplicate()
        cam = d.find("sensors", "DownCamera")
        self.tab.select_block(cam)
        self.tab.commit_field(("capture-settings", 0, "target-gamma"), "2.2")
        self.assertEqual(d.get(cam)["capture-settings"][0]["target-gamma"], 2.2)
        field = self.tab.fields[("capture-settings", 0, "compress")]
        self.tab.commit_field(("capture-settings", 0, "compress"), "(default: true)")
        self.assertNotIn("compress", d.get(cam)["capture-settings"][0])
        self.assertTrue(field)

    def test_renaming_a_part_in_its_name_field_updates_references(self):
        d = self.duplicate()
        link = d.find("links", "Frame")
        self.tab.select_block(link)
        self.assertTrue(self.tab.commit_field(("name",), "Body"))
        self.assertEqual(d.get(d.find("sensors", "IMU1"))["parent-link"], "Body")
        self.assertEqual(d.get(d.find("actuators", "Prop_FL_actuator"))["parent-link"], "Body")
        self.assertIsNotNone(self.tab.current)
        self.assertEqual(d.name_of(self.tab.current), "Body")

    def test_a_clashing_name_is_refused(self):
        d = self.duplicate()
        self.tab.select_block(d.find("links", "Frame"))
        with mock.patch.object(vehicle_tab.messagebox, "showwarning") as warn:
            self.assertFalse(self.tab.commit_field(("name",), "Prop_FL"))
        warn.assert_called_once()

    def test_adding_and_removing_table_rows(self):
        d = self.duplicate()
        cam = d.find("sensors", "DownCamera")
        self.tab.select_block(cam)
        before = len(d.get(cam)["capture-settings"])
        self.tab._add_row(vs.CAMERA.setting("capture-settings"), ("capture-settings",))
        self.assertEqual(len(d.get(cam)["capture-settings"]), before + 1)
        self.assertIn(("capture-settings", before, "width"), self.tab.fields)
        self.assertEqual(d.get(cam)["capture-settings"][before]["width"], 256)       # a required setting starts at its default
        self.tab._remove_row(("capture-settings",), before)
        self.assertEqual(len(d.get(cam)["capture-settings"]), before)
        self.assertNotIn(("capture-settings", before, "width"), self.tab.fields)

    def test_conditional_settings_appear_and_disappear(self):
        d = self.duplicate()
        battery = self.tab.add_part(vs.BATTERY)
        self.assertIn(("battery-drain-rate-on-start",), self.tab.fields)
        self.assertNotIn(("rotor-power-coefficient",), self.tab.fields)
        self.tab.commit_field(("battery-mode",), "rotor-power-discharge-mode")
        self.assertNotIn(("battery-drain-rate-on-start",), self.tab.fields)
        self.assertIn(("rotor-power-coefficient",), self.tab.fields)
        self.assertEqual(d.get(battery)["battery-mode"], "rotor-power-discharge-mode")

    def test_the_ardupilot_ports_cannot_be_edited(self):
        d = self.duplicate()
        self.tab.select_block(vd.BlockRef("controller"))
        port = self.tab.fields[("ardupilot-settings", "ardupilot-udp-port")]
        self.assertIn("disabled", str(port.widget.cget("state")))

    def test_changing_the_controller_type_swaps_its_settings(self):
        d = self.duplicate()
        self.tab.select_block(vd.BlockRef("controller"))
        self.assertIn(("ardupilot-settings", "ardupilot-ip"), self.tab.fields)
        self.tab.commit_field(("type",), "simple-flight-api")
        self.assertEqual(d.doc["controller"]["type"], "simple-flight-api")
        self.assertNotIn(("ardupilot-settings", "ardupilot-ip"), self.tab.fields)
        self.assertIn("cannot be flown", self.tab.banner.cget("text"))

    def test_a_parameter_table_can_be_edited_as_text(self):
        d = self.duplicate()
        self.tab.commit_field  # (the controller must be Simple Flight to have the parameters)
        d.set_controller_type("simple-flight-api")
        self.tab.select_block(vd.BlockRef("controller"))
        editor = self.tab.map_editors[("simple-flight-api-settings", "parameters")]
        editor.insert("end", "MC_ROLL_P = 0.3")
        editor.event_generate("<FocusOut>")
        settle(self.root)
        self.assertEqual(d.doc["controller"]["simple-flight-api-settings"]["parameters"], {"MC_ROLL_P": 0.3})

    def test_settings_the_catalog_does_not_know_are_listed_and_editable(self):
        d = self.duplicate()
        imu = d.find("sensors", "IMU1")
        d.get(imu)["made-up"] = 3
        self.tab._refresh_all()
        self.tab.select_block(imu)
        self.tab._commit_unknown("made-up", "[1, 2]")
        self.assertEqual(d.get(imu)["made-up"], [1, 2])

    # problems and raw JSON ---------------------------------------------------------------------------
    def test_problems_are_listed_and_marked_on_the_tree(self):
        d = self.duplicate()
        cam = d.find("sensors", "DownCamera")
        d.get(cam)["capture-interval"] = "fast"
        self.tab._refresh_all()
        texts = [self.tab.problems.item(i, "text") for i in self.tab.problems.get_children()]
        self.assertTrue(any(t.startswith("ERROR") and "capture-interval" in t for t in texts))
        self.assertIn("(!)", self.tab.vehicle_tree.item(self.item_of(cam), "text"))
        self.assertIn("1 errors", self.tab.bottom.tab(0, "text").replace("errors,", "errors") if False else self.tab.bottom.tab(0, "text"))

    def test_double_clicking_a_problem_goes_to_the_part(self):
        d = self.duplicate()
        cam = d.find("sensors", "DownCamera")
        d.get(cam)["capture-interval"] = "fast"
        self.tab._refresh_all()
        item = next(i for i, issue in self.tab._issues.items() if issue.level == "error")
        self.tab.problems.selection_set(item)
        self.tab._goto_problem()
        self.assertEqual(self.tab.current, cam)

    def test_fix_spellings_button(self):
        d = self.duplicate()
        self.assertNotIn("disabled", self.tab.fix_button.state())
        self.assertEqual(self.tab.fix_spellings(), 1)
        self.assertIn("disabled", self.tab.fix_button.state())
        self.assertEqual(d.doc["controller"]["ardupilot-settings"]["use-sensor-distance"], False)

    def test_the_json_preview_follows_the_design_and_the_editor_applies_changes(self):
        d = self.duplicate()
        preview = self.tab.preview.get("1.0", "end")
        self.assertEqual(json.loads(preview), d.doc)
        edited = json.loads(preview)
        edited["sensors"] = edited["sensors"][:2]
        self.tab.json_editor.delete("1.0", "end")
        self.tab.json_editor.insert("1.0", json.dumps(edited))
        self.tab.apply_json()
        self.assertEqual(len(d.blocks("sensors")), 2)
        self.tab.undo()
        self.assertGreater(len(d.blocks("sensors")), 2)

    def test_bad_json_is_refused(self):
        d = self.duplicate()
        self.tab.json_editor.delete("1.0", "end")
        self.tab.json_editor.insert("1.0", "{oops")
        with mock.patch.object(vehicle_tab.messagebox, "showerror") as error:
            self.tab.apply_json()
        error.assert_called_once()
        self.assertGreater(len(d.blocks("links")), 1)

    # saving, switching, renaming, deleting -------------------------------------------------------------
    def test_saving_writes_the_file(self):
        d = self.duplicate()
        self.tab.add_part(vs.LIDAR)
        self.assertIn("normal", [str(self.tab.save_button.cget("state")), "normal"])
        self.assertNotIn("disabled", self.tab.save_button.state())
        with mock.patch.object(vehicle_tab.messagebox, "askyesno", return_value=True):
            self.assertTrue(self.tab.save())
        on_disk = scenecheck.load_jsonc(self.vehicles / "My quad.jsonc")
        self.assertIn("lidar", [s["type"] for s in on_disk["sensors"]])
        self.assertFalse(d.dirty)
        self.assertIn("disabled", self.tab.save_button.state())

    def test_saving_a_design_with_errors_asks_first(self):
        d = self.duplicate()
        d.get(d.find("sensors", "DownCamera"))["capture-interval"] = "fast"
        with mock.patch.object(vehicle_tab.messagebox, "askyesno", return_value=False) as ask:
            self.assertFalse(self.tab.save())
        ask.assert_called_once()
        on_disk = scenecheck.load_jsonc(self.vehicles / "My quad.jsonc")
        self.assertEqual(on_disk["sensors"][1]["capture-interval"], 0.02)

    def test_switching_designs_with_unsaved_changes_offers_to_save(self):
        self.duplicate("First")
        self.tab.add_part(vs.LIDAR)
        self.tab.library.create("Second", vd.blank_document())
        self.tab.refresh_designs()
        index = next(i for i, c in enumerate(self.tab._design_choices) if c[2] == "Second")
        self.tab.design_combo.current(index)
        with mock.patch.object(vehicle_tab.messagebox, "askyesnocancel", return_value=None):   # Cancel: stay
            self.tab._on_design_selected()
        self.assertEqual(self.tab.design.name, "First")
        self.assertEqual(self.tab.design_combo.get(), "First")
        with mock.patch.object(vehicle_tab.messagebox, "askyesnocancel", return_value=False):  # No: discard
            self.tab.design_combo.current(next(i for i, c in enumerate(self.tab._design_choices) if c[2] == "Second"))
            self.tab._on_design_selected()
        self.assertEqual(self.tab.design.name, "Second")
        self.assertNotIn("lidar", [s["type"] for s in scenecheck.load_jsonc(self.vehicles / "First.jsonc")["sensors"]])

    def test_switching_and_saving(self):
        self.duplicate("First")
        self.tab.add_part(vs.LIDAR)
        self.tab.library.create("Second", vd.blank_document())
        self.tab.refresh_designs()
        self.tab.design_combo.current(next(i for i, c in enumerate(self.tab._design_choices) if c[2] == "Second"))
        with mock.patch.object(vehicle_tab.messagebox, "askyesnocancel", return_value=True), \
                mock.patch.object(vehicle_tab.messagebox, "askyesno", return_value=True):          # Yes: save
            self.tab._on_design_selected()
        self.assertEqual(self.tab.design.name, "Second")
        self.assertIn("lidar", [s["type"] for s in scenecheck.load_jsonc(self.vehicles / "First.jsonc")["sensors"]])

    def test_revert_discards_unsaved_changes(self):
        d = self.duplicate()
        self.tab.add_part(vs.LIDAR)
        with mock.patch.object(vehicle_tab.messagebox, "askyesno", return_value=True):
            self.tab.revert()
        self.assertNotIn("lidar", [d2["type"] for d2 in self.tab.design.doc["sensors"]])
        self.assertFalse(self.tab.design.dirty)

    def test_renaming_a_design(self):
        self.duplicate("Old name")
        self.tab.use_for_runs()
        with mock.patch.object(vehicle_tab.simpledialog, "askstring", return_value="New name"):
            self.tab.rename_design()
        self.assertEqual(self.tab.design.name, "New name")
        self.assertEqual(self.tab.library.names(), ["New name"])
        self.assertEqual(self.tab.library.selected(), "New name")          # still the one used when running
        self.assertEqual(self.app.run_tab.design_var.get(), "New name")

    def test_renaming_to_a_name_that_exists_is_refused(self):
        self.duplicate("One")
        self.tab.library.create("Two", vd.blank_document())
        self.tab.open_design("One")
        answers = iter(["Two", "Three"])
        with mock.patch.object(vehicle_tab.simpledialog, "askstring", side_effect=lambda *a, **k: next(answers)), \
                mock.patch.object(vehicle_tab.messagebox, "showerror") as error:
            self.tab.rename_design()
        error.assert_called_once()
        self.assertEqual(self.tab.library.names(), ["Three", "Two"])

    def test_deleting_a_design(self):
        self.duplicate("Doomed")
        self.tab.use_for_runs()
        with mock.patch.object(vehicle_tab.messagebox, "askyesno", return_value=False):
            self.tab.delete_design()
        self.assertTrue((self.vehicles / "Doomed.jsonc").exists())
        with mock.patch.object(vehicle_tab.messagebox, "askyesno", return_value=True):
            self.tab.delete_design()
        self.assertFalse((self.vehicles / "Doomed.jsonc").exists())
        self.assertIsNone(self.tab.design)
        self.assertIsNone(self.tab.library.selected())
        self.assertEqual(self.app.run_tab.design_var.get(), PROVIDED_CHOICE)

    def test_provided_vehicles_cannot_be_deleted_or_renamed(self):
        self.assertTrue(self.tab.design.provided)
        with mock.patch.object(vehicle_tab.messagebox, "askyesno") as ask:
            self.tab.delete_design()
        ask.assert_not_called()
        self.assertEqual(len(self.tab.library.provided()), 1)

    # the link to the Run tab -------------------------------------------------------------------------
    def test_use_when_running_chooses_the_design_for_the_run_tab(self):
        self.duplicate("Racer")
        self.tab.use_for_runs()
        self.assertEqual(self.tab.library.selected(), "Racer")
        self.assertEqual(self.app.run_tab.design_var.get(), "Racer")
        self.assertIn("Racer", self.app.run_tab.design_combo.cget("values"))
        started = []
        item = next(i for i, info in self.app.run_tab.infos.items() if info.name == "takeoff_land.py")
        self.app.run_tab.tree.selection_set(item)
        with mock.patch.object(self.app.run_tab, "_start", side_effect=started.append):
            self.app.run_tab.run()
        command = started[0]
        self.assertEqual(command[command.index("--design") + 1], "Racer")
        self.assertIn('of the design "Racer"', self.app.run_tab.output.contents())

    def test_without_a_chosen_design_the_run_command_has_no_design_flag(self):
        started = []
        item = next(i for i, info in self.app.run_tab.infos.items() if info.name == "takeoff_land.py")
        self.app.run_tab.tree.selection_set(item)
        with mock.patch.object(self.app.run_tab, "_start", side_effect=started.append):
            self.app.run_tab.run()
        self.assertNotIn("--design", started[0])

    def test_the_run_tab_can_choose_the_design_too(self):
        self.duplicate("Racer")
        self.app.run_tab.refresh_design_choices()
        self.app.run_tab.design_var.set("Racer")
        self.app.run_tab._design_chosen()
        self.assertEqual(self.tab.library.selected(), "Racer")
        self.app.run_tab.design_var.set(PROVIDED_CHOICE)
        self.app.run_tab._design_chosen()
        self.assertIsNone(self.tab.library.selected())

    def test_the_run_tab_only_lists_designs_that_can_fly_here(self):
        self.duplicate("Flyer")
        d = self.tab.library.create("Other controller", vd.blank_document())
        d.set_controller_type("simple-flight-api")
        self.tab.library.save(d)
        self.app.run_tab.refresh_design_choices()
        values = list(self.app.run_tab.design_combo.cget("values"))
        self.assertIn("Flyer", values)
        self.assertNotIn("Other controller", values)
        self.assertEqual(values[0], PROVIDED_CHOICE)

    def test_a_design_that_cannot_fly_cannot_be_used_when_running(self):
        d = self.duplicate("Quad")
        d.set_controller_type("simple-flight-api")
        with mock.patch.object(vehicle_tab.messagebox, "showwarning") as warn, \
                mock.patch.object(vehicle_tab.messagebox, "askyesno", return_value=True):
            self.tab.use_for_runs()
        warn.assert_called_once()
        self.assertIsNone(self.tab.library.selected())

    def test_the_provided_vehicle_can_be_chosen_again(self):
        self.duplicate("Racer")
        self.tab.use_for_runs()
        self.tab.open_design(None, provided=self.tab.library.provided()[0])
        self.tab.use_for_runs()
        self.assertIsNone(self.tab.library.selected())

    def test_closing_the_window_asks_about_unsaved_designs(self):
        self.duplicate()
        self.tab.add_part(vs.LIDAR)
        closed = []
        with mock.patch.object(self.root, "destroy", side_effect=lambda: closed.append(1)), \
                mock.patch.object(vehicle_tab.messagebox, "askyesnocancel", return_value=None):
            self.app.on_close()
        self.assertEqual(closed, [])                      # cancelled: the window stays
        with mock.patch.object(self.root, "destroy", side_effect=lambda: closed.append(1)), \
                mock.patch.object(vehicle_tab.messagebox, "askyesnocancel", return_value=False):
            self.app.on_close()
        self.assertEqual(closed, [1])

    def test_the_window_is_still_never_smaller_than_its_contents(self):
        settle(self.root)
        min_w, min_h = self.root.minsize()
        # the window may not be taller or wider than the screen less room for the taskbar (see App._fit_to_screen)
        room_w = self.root.winfo_screenwidth() - self.app.px(40)
        room_h = self.root.winfo_screenheight() - self.app.px(90)
        self.assertGreaterEqual(min_w, min(self.root.winfo_reqwidth(), room_w) - 1)
        self.assertGreaterEqual(min_h, min(self.root.winfo_reqheight(), room_h) - 1)

    def test_switching_tabs_shows_the_vehicles_tab(self):
        self.app.tabs.select(self.tab)
        settle(self.root)
        self.assertTrue(self.tab.winfo_ismapped())
        self.app.tabs.select(0)
        settle(self.root)
        self.assertFalse(self.tab.winfo_ismapped())


class RowTemplate(unittest.TestCase):
    def test_a_new_table_row_starts_with_the_required_settings(self):
        row = vehicle_tab.row_template(vs.CAMERA.setting("capture-settings"))
        self.assertEqual(row["width"], 256)
        self.assertEqual(row["image-type"], 0)
        self.assertEqual(row["capture-enabled"], False)
        self.assertNotIn("target-gamma", row)            # optional: left out

    def test_masks_start_at_zero(self):
        row = vehicle_tab.row_template(vs.RADAR.setting("masks"))
        self.assertEqual(set(row), {c.key for c in vs.RADAR.setting("masks").children if c.required})
        self.assertTrue(all(v == 0 for v in row.values()))


if __name__ == "__main__":
    unittest.main()
