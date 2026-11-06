"""Run containers/script/tests/test_camera_relay.py inside the airsim image (needs a WSL distro with the images).
Off by default: MRRSIM_CONTAINER_TESTS=1 turns it on; MRRSIM_TEST_DISTRO names another distro than "mrr"."""
import os
import unittest

from mrrsim.core import paths
from mrrsim.runtime import wsl


@unittest.skipUnless(os.environ.get("MRRSIM_CONTAINER_TESTS") == "1", "set MRRSIM_CONTAINER_TESTS=1 to run the tests inside the image")
class RelayInTheImage(unittest.TestCase):
    def test_relay_and_loader_hook(self):
        distro = os.environ.get("MRRSIM_TEST_DISTRO", "mrr")
        source = wsl.to_wsl_path(paths.repo_root() / "containers" / "script")
        image = os.environ.get("MRRSIM_TEST_IMAGE", "localhost/mrrsim-script:airsim")
        # the image has the receiver in /opt/mrrsim/lib and the loader in /opt/mrrsim; the tests look next to themselves
        command = "cp -r /src /tmp/src && cd /tmp/src && mkdir -p lib && mv mrrsim_camera.py lib/ && PYTHONPATH=/tmp/src/lib python -W ignore -m unittest -v tests.test_camera_relay 2>&1"
        result = wsl.run(distro, ["podman", "run", "--rm", "-v", f"{source}:/src:ro", "-w", "/src", image, "sh", "-c", command], timeout=300)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("OK", output)


if __name__ == "__main__":
    unittest.main()
