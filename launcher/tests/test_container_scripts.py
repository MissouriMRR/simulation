"""The tests of containers/script that need only Python (the fleet runner and fetch_client.py), run with the launcher's
own tests so they are not forgotten. They run in a child process from containers/script, the folder they are written
for. (test_camera_relay.py needs the airsim image: test_camera_relay_container.py runs it there.)"""
import subprocess
import sys
import unittest

from mrrsim.core import paths

SCRIPT = paths.repo_root() / "containers" / "script"


class ContainerScriptsOnThisComputer(unittest.TestCase):
    def test_the_fleet_runner_and_the_client_fetcher_pass_their_tests(self):
        done = subprocess.run([sys.executable, "-m", "unittest", "tests.test_fleet", "tests.test_fetch_client"],
                              cwd=SCRIPT, capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, (done.stdout + done.stderr)[-4000:])
        self.assertIn("OK", done.stderr)


if __name__ == "__main__":
    unittest.main()
