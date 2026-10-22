"""Write launcher/mrrsim/_version.py from the version in MRRSimulationLauncher.iss.

The installer script holds the one copy of the version number (`#define AppVersion "x.y.z"`). The built exes cannot read
that file, so the launcher build (launcher/build.cmd) runs this first and PyInstaller packs the file it writes.
The generated file is git-ignored; never edit it.

    python tools/sync_version.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "launcher"))

from mrrsim import ISS, version_from_iss  # noqa: E402

OUTPUT = ROOT / "launcher" / "mrrsim" / "_version.py"


def main(iss: Path = ISS, output: Path = OUTPUT) -> int:
    version = version_from_iss(iss)
    if not version:
        print(f'error: no `#define AppVersion "..."` in {iss}', file=sys.stderr)
        return 1
    output.write_text(
        "# Written by tools/sync_version.py from MRRSimulationLauncher.iss (AppVersion). Do not edit; not committed.\n"
        f'__version__ = "{version}"\n',
        encoding="utf-8",
        newline="\n",
    )
    print(f"version {version} (from {iss.name}) written to {output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
