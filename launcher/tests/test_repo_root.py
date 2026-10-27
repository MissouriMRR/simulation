"""The repository root is worked out in one place (mrrsim/__init__.py), not by modules counting their own depth."""
import re
import unittest
from pathlib import Path

import mrrsim
from mrrsim.core import paths

PACKAGE = Path(mrrsim.__file__).resolve().parent


class RepoRoot(unittest.TestCase):
    def test_it_is_the_folder_with_the_installer_script_and_the_settings(self):
        root = paths.repo_root()
        self.assertTrue((root / "MRRSimulationLauncher.iss").is_file())
        self.assertTrue((root / "config" / "mrrsim.toml").is_file())
        self.assertTrue((root / "launcher" / "mrrsim").is_dir())

    def test_paths_takes_it_from_the_package_constant(self):
        self.assertEqual(paths.repo_root(), mrrsim.REPO_ROOT)
        self.assertEqual(mrrsim.ISS, mrrsim.REPO_ROOT / "MRRSimulationLauncher.iss")

    def test_only_the_package_file_counts_folder_levels_to_find_the_repository(self):
        """core/paths.py once did, and broke when modules were moved into folders."""
        offenders = []
        for source in PACKAGE.rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            if re.search(r"__file__[^\n]*parents\[", text) and source.name != "__init__.py":
                offenders.append(str(source.relative_to(PACKAGE)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
