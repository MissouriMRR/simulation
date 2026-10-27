"""Write scene_ardupilot_<N>quadrotors.jsonc (and the extra robot configs it needs).

    python sim_config/generate_scene.py 3
    python sim_config/generate_scene.py 4 --spacing 5 --base scene_ardupilot_quadrotor.jsonc
    python sim_config/generate_scene.py 2 --no-cameras
    python sim_config/generate_scene.py 1 --window 640x360

N = 1 writes nothing: the original single-vehicle scene is used as it is (unless --no-cameras, which
always writes a copy with every camera sensor disabled; the originals are never changed). `mrrsim up --vehicles N`
does this automatically; this script is for generating the files by hand.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "launcher"))

from mrrsim import scenegen  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("count", type=int, help="number of vehicles")
    parser.add_argument("--base", default="scene_ardupilot_quadrotor.jsonc", help="single-vehicle template scene")
    parser.add_argument("--spacing", type=float, default=scenegen.DEFAULT_SPACING_M, help="metres between vehicles (along y)")
    parser.add_argument("--no-cameras", action="store_true", help="write a variant with every camera sensor disabled")
    parser.add_argument("--window", metavar="WxH", help="simulator window size: the streamed camera is set to it")
    args = parser.parse_args()
    window = None
    if args.window:
        parts = args.window.lower().split("x")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            parser.error("--window must look like 960x540")
        window = (int(parts[0]), int(parts[1]))
    try:
        name = scenegen.generate(HERE, args.base, args.count, args.spacing, cameras=not args.no_cameras, window=window)
    except (scenegen.SceneGenError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"scene: {HERE / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
