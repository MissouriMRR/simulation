"""Run containers/sitl/tests (fetch_source.py against a fake GitHub, which needs Linux) inside the WSL distro.
Off by default: MRRSIM_CONTAINER_TESTS=1 turns it on; MRRSIM_TEST_DISTRO names another distro than "mrr". The distro
needs python3 and git."""
import os
import unittest

from mrrsim.core import paths
from mrrsim.runtime import wsl


@unittest.skipUnless(os.environ.get("MRRSIM_CONTAINER_TESTS") == "1", "set MRRSIM_CONTAINER_TESTS=1 to run the tests in WSL")
class FetchSourceInWsl(unittest.TestCase):
    def test_fetching_from_a_fake_github(self):
        distro = os.environ.get("MRRSIM_TEST_DISTRO", "mrr")
        folder = wsl.to_wsl_path(paths.repo_root() / "containers" / "sitl")
        result = wsl.run(distro, ["sh", "-c", f"cd '{folder}' && python3 -m unittest discover -s tests -v 2>&1"], timeout=600)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        summary = [line for line in output.splitlines() if line.startswith("OK")]
        self.assertTrue(summary, output)
        self.assertNotIn("skipped", summary[-1])  # in WSL the end-to-end test runs, it is not skipped


if __name__ == "__main__":
    unittest.main()
