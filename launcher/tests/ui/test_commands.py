"""ui/run/commands.py: what the Run tab says to the launcher and how it reads the answers. Always runs (no window)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim import cli
from mrrsim.core import config, paths
from mrrsim.scene import poseargs, scenecheck
from mrrsim.ui.run import commands
from mrrsim.userscripts import scripts

DRONES = [(n, f"127.0.0.{n}", 5000 + n) for n in (1, 2, 3, 4)]  # as IARC-10's mission_config.json lists them

SOURCE = paths.repo_root() / "launcher" / "mrrsim"


class RunCommand(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.object(commands, "cli_command", return_value=["mrrsim"]),
                      mock.patch.dict("os.environ", {"MRRSIM_DATA_DIR": "D:/data"})):
            patch.start()
            self.addCleanup(patch.stop)
        self.stop = str(Path("D:/data/stop.request"))

    def test_run_command(self):
        command = commands.build_run_command(Path("C:/c/mrrsim.toml"), Path("C:/s/a.py"), 2, True, "960x540")
        self.assertEqual(command[:5], ["mrrsim", "-c", str(Path("C:/c/mrrsim.toml")), "run", str(Path("C:/s/a.py"))])
        self.assertEqual(command[command.index("--vehicles") + 1], "2")
        self.assertEqual(command[command.index("--stop-file") + 1], self.stop)
        self.assertIn("--no-cameras", command)
        self.assertEqual(command[command.index("--resolution") + 1], "960x540")

    def test_options_left_out_when_not_asked_for(self):
        command = commands.build_run_command(Path("c.toml"), Path("a.py"), 1, False, None)
        self.assertNotIn("--no-cameras", command)
        self.assertNotIn("--resolution", command)

    def test_the_doctor_and_package_commands_can_be_stopped_too(self):
        self.assertEqual(commands.doctor_command(Path("c.toml")),
                         ["mrrsim", "-c", "c.toml", "doctor", "--stop-file", self.stop])
        self.assertEqual(commands.pip_command(Path("c.toml"), False, ["tqdm", "pyzbar"]),
                         ["mrrsim", "-c", "c.toml", "pip", "install", "tqdm", "pyzbar", "--stop-file", self.stop])
        self.assertEqual(commands.pip_command(Path("c.toml"), True, ["numpy"])[3:5], ["pip", "remove"])
        self.assertEqual(commands.doctor_command(Path("c.toml"), Path("p/run.py"), Path("p")),
                         ["mrrsim", "-c", "c.toml", "doctor", "--script", str(Path("p/run.py")), "--project", "p",
                          "--stop-file", self.stop])                    # the selected script, its imports checked too

    def test_the_launcher_accepts_every_command_the_window_builds(self):
        parser = cli.build_parser()
        for command in (commands.build_run_command(Path("c.toml"), Path("a.py"), 2, True, "960x540", project=Path("p"),
                                                   script_args="-s", design="Quad"),
                        commands.doctor_command(Path("c.toml")), commands.pip_command(Path("c.toml"), False, ["tqdm"]),
                        commands.pip_command(Path("c.toml"), True, ["tqdm"]),
                        commands.iarc_command(Path("c.toml"), Path("run.py"), DRONES, False, None, project=Path("p")),
                        commands.doctor_command(Path("c.toml"), Path("p/run.py"), Path("p"))):
            args = parser.parse_args(command[1:])
            self.assertEqual(args.stop_file, self.stop, command)

    def test_resolution_check(self):
        for good in ("960x540", "640X360", "1280x720", f"{config.MIN_PIXELS}x{config.MAX_PIXELS}"):
            self.assertTrue(commands.valid_resolution(good), good)
        for bad in ("", "960", "10x10", "axb", "960x540x1", "99999x1", f"{config.MIN_PIXELS - 1}x540",
                    f"960x{config.MAX_PIXELS + 1}"):
            self.assertFalse(commands.valid_resolution(bad), bad)


class IarcMode(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.object(commands, "cli_command", return_value=["mrrsim"]),
                      mock.patch.dict("os.environ", {"MRRSIM_DATA_DIR": "D:/data"})):
            patch.start()
            self.addCleanup(patch.stop)

    def test_four_iarc_drones_one_copy_each_fly_the_mission_from_huntsville_at_their_own_ips(self):
        command = commands.iarc_command(Path("c.toml"), Path("p/run.py"), DRONES, True, "960x540", project=Path("p"))
        args = cli.build_parser().parse_args(command[1:])
        self.assertEqual((args.vehicles, args.per_vehicle, args.home, args.design, args.resolution, args.no_cameras),
                         (4, True, "34.6251269,-86.8091462", "IARC Drone", "960x540", True))
        self.assertEqual(args.project, str(Path("p")))
        self.assertEqual(args.copy_env, ["SITL_MAVLINK_HOST=127.0.0.1,127.0.0.2,127.0.0.3,127.0.0.4"])
        kept, pose = poseargs.extract(scripts.split_args(args.script_args), commands.IARC_VEHICLES)
        self.assertEqual(kept, ["--airsim", "--auto-start", "mission"])            # what run.py gets (and -i N)
        self.assertEqual([pose.vehicles[i] for i in range(4)],                      # a north-south line, facing north
                         [(5.0 * i, 0.0, commands.IARC_SPAWN_Z, 0.0, 0.0, 0.0) for i in range(4)])

    def test_the_drones_spawn_at_the_height_of_the_provided_scene(self):
        scene = scenecheck.load_jsonc(paths.repo_root() / "sim_config" / "scene_ardupilot_quadrotor.jsonc")
        robot = next(a for a in scene["actors"] if a.get("type") == "robot")
        self.assertEqual(float(robot["origin"]["xyz"].split()[2]), commands.IARC_SPAWN_Z)

    def write_config(self, folder: Path, drone_info) -> None:
        (folder / commands.IARC_CONFIG).write_text(json.dumps({"self_id": 1, "drone_info": drone_info}), encoding="utf-8")

    def test_the_drones_are_read_from_the_projects_mission_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            info = [{"id": n, "IP": f"10.0.0.{n}", "port": 6000 + n} for n in (4, 3, 2, 1, 5)]   # any order, extras ignored
            self.write_config(folder, info)
            self.assertEqual(commands.iarc_drones(folder), [(n, f"10.0.0.{n}", 6000 + n) for n in (1, 2, 3, 4)])
            self.write_config(folder, info[1:4])                                                 # no drone 4
            with self.assertRaisesRegex(ValueError, "no drone 4"):
                commands.iarc_drones(folder)
            (folder / commands.IARC_CONFIG).write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Could not read the drones"):
                commands.iarc_drones(folder)
            (folder / commands.IARC_CONFIG).unlink()
            with self.assertRaisesRegex(ValueError, "Could not read the drones"):
                commands.iarc_drones(folder)
        with self.assertRaisesRegex(ValueError, "project folder"):
            commands.iarc_drones(None)

    def test_each_port_is_checked_on_its_drones_ip_and_where_the_server_listens(self):
        asked = []

        def free(port, proto="tcp", host=None):
            asked.append((port, host))
            return not (port == 5002 and host == "0.0.0.0") and not (port == 5004 and host == "127.0.0.4")

        with mock.patch.object(commands.ports, "port_is_free", side_effect=free), \
                mock.patch.object(commands.ports, "who_uses", return_value={(5002, "tcp"): "Other.exe (pid 7)"}):
            said = commands.iarc_ports_problem(DRONES)
        self.assertIn((5003, "127.0.0.3"), asked)
        self.assertIn((5003, "0.0.0.0"), asked)
        self.assertIn("drone 2 (127.0.0.2:5002) by Other.exe (pid 7); drone 4 (127.0.0.4:5004).", said)
        with mock.patch.object(commands.ports, "port_is_free", return_value=True):
            self.assertIsNone(commands.iarc_ports_problem(DRONES))

    def test_on_this_computer_every_drone_ip_of_the_loopback_range_can_be_checked(self):
        port = 0  # let the system pick: only that binding works on each address is checked, not a particular port
        for _n, ip, _port in DRONES:
            self.assertTrue(commands.ports.port_is_free(port, "tcp", host=ip), ip)


class ReadingTheAnswers(unittest.TestCase):
    def test_doctor_status_line(self):
        self.assertEqual(commands.doctor_status(0, (16, 0, 0)), "Doctor: all 16 checks passed")
        self.assertIn("1 warning", commands.doctor_status(0, (16, 0, 1)))
        self.assertIn("2 of 16 checks failed", commands.doctor_status(1, (16, 2, 0)))
        self.assertIn("did not finish", commands.doctor_status(2, None))
        self.assertEqual(commands.doctor_status(commands.STOPPED, None), "Doctor stopped")
        self.assertEqual(commands.DOCTOR_SUMMARY.search("16 checks: 0 failed, 1 warnings").groups(), ("16", "0", "1"))

    def test_pip_status_line(self):
        self.assertIn("failed", commands.pip_status(1, ""))
        self.assertIn("does not import", commands.pip_status(0, "x\n[WARN] import pyzbar.pyzbar fails: ImportError\n"))
        self.assertIn("available to every script", commands.pip_status(0, "[ OK ] import tqdm works"))
        self.assertEqual(commands.pip_status(0, "Removed x 1.0", removing=True), "Packages removed")
        self.assertIn("failed", commands.pip_status(1, "", removing=True))
        for removing in (False, True):
            self.assertTrue(commands.pip_status(commands.STOPPED, "", removing).startswith("Stopped"))

    def test_how_each_kind_of_line_is_colored(self):
        for text, tag in (("[script] hello", "script"), ("12:00:00 ERROR bad", "error"), ("[FAIL] WSL: none", "error"),
                          ("12:00:00 WARNING odd", "warning"), ("[WARN] GPS", "warning"), ("[ OK ] Windows", "ok"),
                          ("12:00:00 INFO  started Unreal", None), ("ERRORS happen", None)):
            self.assertEqual(commands.line_tag(text), tag, text)

    def test_the_progress_messages_cover_the_wsl_wake_up_the_student_waits_for(self):
        fragments = dict(commands.PROGRESS)
        self.assertIn("Checking that WSL is responsive", fragments)
        self.assertIn("Waking up WSL", fragments["Checking that WSL is responsive"])

    def test_every_progress_fragment_is_still_something_the_launcher_logs(self):
        source = "\n".join(p.read_text(encoding="utf-8") for p in SOURCE.rglob("*.py") if "ui" not in p.parts)
        for fragment, _message in commands.PROGRESS:
            self.assertIn(fragment, source, "the launcher no longer logs this, so the status line never shows it")

    def test_the_window_follows_the_launchers_vehicle_limit(self):
        self.assertEqual(commands.MAX_VEHICLES, 100)


if __name__ == "__main__":
    unittest.main()
