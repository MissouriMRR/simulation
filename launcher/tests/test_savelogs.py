import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.runtime import session, wsl


class FakePodman:
    """Stands in for Podman: 'copies' files for the containers listed in `logs`."""

    def __init__(self, logs: dict[str, list[str]], dry_run: bool = False) -> None:
        self.logs = logs
        self.distro = "mrr"
        self.dry_run = dry_run
        self.calls: list[tuple[str, str, str]] = []

    def copy_from(self, name: str, src: str, dest: str) -> bool:
        self.calls.append((name, src, dest))
        files = self.logs.get(name)
        if files is None:
            return False
        for file in files:
            (Path(self._local(dest)) / file).write_bytes(b"\xa3\x95")
        return True

    @staticmethod
    def _local(wsl_path: str) -> str:
        return wsl_path  # the test patches wsl.to_wsl_path to the identity


class SaveSitlLogs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "logs" / "s1"
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(wsl, "to_wsl_path", side_effect=lambda path, distro=None: str(path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_copies_each_container_into_its_own_folder(self):
        podman = FakePodman({"mrrsim-sitl-0": ["00000001.BIN"], "mrrsim-sitl-1": ["00000001.BIN", "LASTLOG.TXT"]})
        saved = session.save_sitl_logs(podman, ["mrrsim-sitl-0", "mrrsim-sitl-1"], self.root)
        self.assertEqual([p.name for p in saved], ["sitl-0", "sitl-1"])
        self.assertTrue((self.root / "sitl-1" / "LASTLOG.TXT").is_file())
        self.assertEqual(podman.calls[0][:2], ("mrrsim-sitl-0", "/work/logs/."))

    def test_nothing_logged_leaves_no_empty_folders(self):
        podman = FakePodman({})  # podman cp fails: SITL never armed, so no logs/ directory
        self.assertEqual(session.save_sitl_logs(podman, ["mrrsim-sitl-0"], self.root), [])
        self.assertFalse(self.root.exists())

    def test_one_vehicle_without_logs_does_not_hide_the_other(self):
        podman = FakePodman({"mrrsim-sitl-1": ["00000001.BIN"]})
        saved = session.save_sitl_logs(podman, ["mrrsim-sitl-0", "mrrsim-sitl-1"], self.root)
        self.assertEqual([p.name for p in saved], ["sitl-1"])
        self.assertFalse((self.root / "sitl-0").exists())

    def test_dry_run_touches_nothing(self):
        podman = FakePodman({"mrrsim-sitl-0": ["x"]}, dry_run=True)
        self.assertEqual(session.save_sitl_logs(podman, ["mrrsim-sitl-0"], self.root), [])
        self.assertEqual(podman.calls, [])
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
