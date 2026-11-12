"""The tests of containers/ that need only Python (the fleet runner, fetch_client.py, and the parts of fetch_source.py
that need no git), run with the launcher's own tests so they are not forgotten. Each runs in a child process from its
own folder, the one it is written for. (test_camera_relay.py needs the airsim image and test_fetch_source.py needs
Linux: test_camera_relay_container.py and test_fetch_source_wsl.py run them, when MRRSIM_CONTAINER_TESTS=1.)"""
import subprocess
import sys
import unittest

from mrrsim.core import paths

CONTAINERS = paths.repo_root() / "containers"


class ContainerScriptsOnThisComputer(unittest.TestCase):
    def run_tests(self, folder: str, *modules: str) -> None:
        done = subprocess.run([sys.executable, "-m", "unittest", *modules], cwd=CONTAINERS / folder,
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, (done.stdout + done.stderr)[-4000:])
        self.assertIn("OK", done.stderr)

    def test_the_fleet_runner_and_the_client_fetcher_pass_their_tests(self):
        self.run_tests("script", "tests.test_fleet", "tests.test_fetch_client")

    def test_the_source_fetcher_passes_the_tests_that_need_no_git(self):
        self.run_tests("sitl", "tests.test_fetch_source_units")


if __name__ == "__main__":
    unittest.main()
