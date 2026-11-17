"""mrrsim_fleet: one copy of a script per vehicle. Needs only Python, so it runs anywhere:

    cd containers/script && python -m unittest tests.test_fleet

(the signal test only runs on Linux, where the launcher's containers are)
"""
import contextlib
import io
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import mrrsim_camera  # noqa: E402
import mrrsim_fleet  # noqa: E402


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def picture_packet(vehicle: int, camera: str = "Down") -> bytes:
    return mrrsim_camera.packets_for(
        b"jpeg-bytes", vehicle=vehicle, camera=camera, number=1, sim_time_ns=0, width=2, height=2
    )[0]


class Helpers(unittest.TestCase):
    def test_each_copy_gets_its_own_drone_number_at_the_end(self):
        command = mrrsim_fleet.child_command("/work/run.py", ["--airsim", "--config", "x.json"], 3)
        self.assertEqual(command, [sys.executable, "-u", "/work/run.py", "--airsim", "--config", "x.json", "-i", "3"])

    def test_each_copy_gets_its_own_picture_port(self):
        ports = [mrrsim_fleet.relay_port(5600, i) for i in range(4)]
        self.assertEqual(ports, [5601, 5602, 5603, 5604])
        env = mrrsim_fleet.child_env({"A": "1", "SIM_CAMERA_PORT": "5600"}, 2, 5600)
        self.assertEqual(env["SIM_CAMERA_PORT"], "5603")
        self.assertEqual(env["A"], "1")

    def test_without_a_streaming_camera_each_copy_still_gets_its_own_picture_port(self):
        env = mrrsim_fleet.child_env({"A": "1"}, 1, None)
        self.assertEqual(env, {"A": "1", "SIM_CAMERA_PORT": str(mrrsim_camera.DEFAULT_PORT + 2)})

    def test_copy_env_gives_each_copy_its_own_value(self):
        copy_env = '{"SITL_MAVLINK_HOST": ["127.0.0.1", "127.0.0.2", "127.0.0.3"]}'
        env = mrrsim_fleet.child_env({"MRRSIM_COPY_ENV": copy_env}, 2, None)
        self.assertEqual(env["SITL_MAVLINK_HOST"], "127.0.0.3")

    def test_a_signal_death_is_a_shell_style_code(self):
        self.assertEqual(mrrsim_fleet.exit_code(0), 0)
        self.assertEqual(mrrsim_fleet.exit_code(3), 3)
        self.assertEqual(mrrsim_fleet.exit_code(-2), 130)


class Relay(unittest.TestCase):
    def run_relay(self, count=2):
        port = free_udp_port()
        listeners = []
        for index in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", mrrsim_fleet.relay_port(port, index)))
            sock.settimeout(1.0)
            listeners.append(sock)
            self.addCleanup(sock.close)
        stop = threading.Event()
        thread = threading.Thread(target=mrrsim_fleet.relay_pictures, args=(port, count, stop), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(stop.set)
        time.sleep(0.3)  # the relay is bound
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sender.close)
        return port, listeners, lambda data: sender.sendto(data, ("127.0.0.1", port))

    def test_a_picture_goes_only_to_the_copy_of_its_vehicle(self):
        _, listeners, send = self.run_relay(count=2)
        packet = picture_packet(vehicle=1)
        send(packet)
        self.assertEqual(listeners[1].recv(65535), packet)
        listeners[0].settimeout(0.3)
        with self.assertRaises(socket.timeout):
            listeners[0].recv(65535)

    def test_a_vehicle_nobody_runs_and_other_traffic_are_dropped(self):
        _, listeners, send = self.run_relay(count=2)
        send(picture_packet(vehicle=5))
        send(b"not a picture at all, just some bytes that are long enough to have a header....")
        send(b"short")
        send(picture_packet(vehicle=0))  # the one that must still arrive after the bad ones
        self.assertEqual(listeners[0].recv(65535), picture_packet(vehicle=0))

    def test_a_port_that_is_taken_is_reported_not_raised(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as taken:
            taken.bind(("0.0.0.0", 0))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                mrrsim_fleet.relay_pictures(taken.getsockname()[1], 2, threading.Event())
        self.assertIn("is not free", out.getvalue())

    def test_the_receiver_of_a_copy_gets_its_vehicles_pictures_whole(self):
        port = free_udp_port()
        stop = threading.Event()
        thread = threading.Thread(target=mrrsim_fleet.relay_pictures, args=(port, 2, stop), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(stop.set)
        time.sleep(0.3)
        with mrrsim_camera.CameraReceiver(mrrsim_fleet.relay_port(port, 1), host="127.0.0.1") as receiver:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                for packet in mrrsim_camera.packets_for(
                    b"x" * 3000, vehicle=1, camera="Down", number=7, sim_time_ns=5, width=4, height=3
                ):
                    sender.sendto(packet, ("127.0.0.1", port))
            frame = receiver.get(timeout=3)
        self.assertIsNotNone(frame)
        self.assertEqual((frame.vehicle, frame.camera, frame.number, len(frame.data)), (1, "Down", 7, 3000))


class Fleet(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def script(self, body: str) -> str:
        path = self.tmp / "copy.py"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return str(path)

    def run_fleet(self, script, args=(), vehicles=3, **env):
        out = io.StringIO()
        patched = {"SIM_VEHICLE_COUNT": str(vehicles), **env}
        with mock.patch.dict(os.environ, patched), contextlib.redirect_stdout(out):
            if "SIM_CAMERA_PORT" not in env:
                os.environ.pop("SIM_CAMERA_PORT", None)
            code = mrrsim_fleet.main([script, *args])
        return code, out.getvalue().splitlines()

    def test_one_copy_per_vehicle_each_with_its_number_and_the_arguments(self):
        script = self.script("import sys; print('args', sys.argv[1:])")
        code, lines = self.run_fleet(script, ["--airsim", "--auto-start", "demo"], vehicles=3)
        self.assertEqual(code, 0)
        for number in (1, 2, 3):
            self.assertIn(f"[drone {number}] args ['--airsim', '--auto-start', 'demo', '-i', '{number}']", lines)
        self.assertEqual(len([ln for ln in lines if ln.startswith("[drone")]), 3)

    def test_the_first_failing_copy_decides_the_exit_code_and_is_named(self):
        script = self.script("import sys; sys.exit(7 if sys.argv[-1] == '2' else 0)")
        code, lines = self.run_fleet(script, vehicles=3)
        self.assertEqual(code, 7)
        self.assertIn("[fleet] drone 2 ended with code 7", lines)

    def test_stderr_is_shown_too(self):
        script = self.script("import sys; print('oops', file=sys.stderr)")
        _, lines = self.run_fleet(script, vehicles=1)
        self.assertIn("[drone 1] oops", lines)

    def test_a_missing_script_is_a_failure_of_every_copy(self):
        code, lines = self.run_fleet(str(self.tmp / "nothing.py"), vehicles=2)
        self.assertNotEqual(code, 0)
        self.assertIn("[fleet] drone 1 ended with code 2", lines)

    def test_every_copy_is_told_its_own_picture_port(self):
        script = self.script("import os; print('port', os.environ.get('SIM_CAMERA_PORT'))")
        port = free_udp_port()
        _, lines = self.run_fleet(script, vehicles=2, SIM_CAMERA_PORT=str(port))
        self.assertIn(f"[drone 1] port {port + 1}", lines)
        self.assertIn(f"[drone 2] port {port + 2}", lines)

    def test_every_copy_gets_its_own_values_and_a_picture_port_without_a_camera(self):
        script = self.script("import os; print('host', os.environ['SITL_MAVLINK_HOST'], os.environ['SIM_CAMERA_PORT'])")
        _, lines = self.run_fleet(script, vehicles=2, MRRSIM_COPY_ENV='{"SITL_MAVLINK_HOST": ["127.0.0.1", "127.0.0.2"]}')
        self.assertIn(f"[drone 1] host 127.0.0.1 {mrrsim_camera.DEFAULT_PORT + 1}", lines)
        self.assertIn(f"[drone 2] host 127.0.0.2 {mrrsim_camera.DEFAULT_PORT + 2}", lines)

    def test_no_script_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(mrrsim_fleet.main([]), 2)


@unittest.skipUnless(sys.platform.startswith("linux"), "signals are forwarded to the copies on Linux")
class Signals(unittest.TestCase):
    def test_ctrl_c_reaches_every_copy_so_each_can_land_by_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "pilot.py"
            script.write_text(textwrap.dedent("""
                import signal, sys, time
                def land(*_):
                    print("landing", flush=True)
                    sys.exit(0)
                signal.signal(signal.SIGINT, land)
                print("flying", flush=True)
                time.sleep(30)
            """), encoding="utf-8")
            env = {**os.environ, "SIM_VEHICLE_COUNT": "2", "PYTHONPATH": HERE}
            env.pop("SIM_CAMERA_PORT", None)
            proc = subprocess.Popen([sys.executable, "-u", "-m", "mrrsim_fleet", str(script)], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            seen = []
            deadline = time.monotonic() + 20
            while sum("flying" in ln for ln in seen) < 2 and time.monotonic() < deadline:
                seen.append(proc.stdout.readline())
            proc.send_signal(2)
            rest, _ = proc.communicate(timeout=20)
        text = "".join(seen) + rest
        self.assertEqual(text.count("landing"), 2, text)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
