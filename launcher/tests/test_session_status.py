"""`mrrsim status`: what it prints for the recorded run, containers and vehicles."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config
from mrrsim.runtime import session


def load() -> config.Config:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mrrsim.toml"
        path.write_text("", encoding="utf-8")
        return config.load(path)


class Status(unittest.TestCase):
    def run_status(self, state, *, pids=(), listening=True, heartbeat=(True, "ok")):
        """What `status` prints, with the recorded state, the processes that
        are alive (`pids`), whether the vehicle ports answer, and the
        heartbeat result all faked."""
        rows = [{"Names": ["mrrsim-sitl-0"], "State": "running", "Image": "img"}]
        podman = mock.Mock(ps=mock.Mock(return_value=rows))
        out = io.StringIO()
        with (
            mock.patch.object(session, "read_state", return_value=state),
            mock.patch.object(session, "Podman", return_value=podman),
            mock.patch.object(session, "pid_alive", side_effect=lambda p: p in pids),
            mock.patch.object(session.health, "tcp_open", return_value=listening),
            mock.patch.object(session.health, "wait_heartbeat", return_value=heartbeat),
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(session.status(load()), 0)
        return out.getvalue()

    def test_no_recorded_run(self):
        text = self.run_status(None, listening=False)
        self.assertIn("launcher: no active session recorded", text)
        self.assertIn("containers (1):", text)
        self.assertIn("mrrsim-sitl-0", text)
        self.assertIn("vehicle 0 (sysid 1): not listening on tcp/5763", text)

    def test_a_running_launcher_and_unreal(self):
        state = {"session": "s1", "dir": "d", "launcher_pid": 10, "unreal_pid": 20}
        text = self.run_status(state, pids={10, 20})
        self.assertIn("launcher: pid 10 running", text)
        self.assertIn("unreal:   pid 20 running", text)
        self.assertIn("vehicle 0 (sysid 1): ok; connect tcp:127.0.0.1:5762", text)

    def test_processes_that_are_gone_are_reported(self):
        state = {"session": "s1", "dir": "d", "launcher_pid": 10, "unreal_pid": 20}
        text = self.run_status(state, pids=set(), heartbeat=(False, "silent"))
        self.assertIn("launcher: pid 10 NOT running (stale state)", text)
        self.assertIn("unreal:   pid 20 NOT running", text)
        self.assertIn("vehicle 0 (sysid 1): NO HEARTBEAT: silent; connect", text)

    def test_the_vehicle_count_comes_from_the_recorded_run(self):
        state = {"session": "s1", "dir": "d", "vehicles": 2}
        text = self.run_status(state)
        self.assertIn("vehicle 1 (sysid 2)", text)
        self.assertNotIn("vehicle 2", text)


if __name__ == "__main__":
    unittest.main()
