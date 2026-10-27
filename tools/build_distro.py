"""Build the WSL distro that the installer ships to students: payload/mrr-rootfs.tar.gz.

    python tools/build_distro.py                       build it (downloads the Ubuntu base on the first run)
    python tools/build_distro.py --verify-only         import the finished payload as 'mrr-verify' and test it

What it makes: official Ubuntu 24.04 with rootless podman, one user called `mrr`, and the three simulator
images already loaded, so the install works offline. Nothing of the developer's own distro goes in: the
images are the only thing taken from it (podman save), and the distro is built from the public Ubuntu image.

Safety: the only distros this script ever creates or deletes are `mrr-build` and `mrr-verify`. It never
touches `mrr` except to read the images from it.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

for stream in (sys.stdout, sys.stderr):  # apt prints characters a Windows code page cannot show
    stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "launcher"))
from mrrsim.core import config, paths  # noqa: E402
from mrrsim.runtime import wsl

# The official Ubuntu 24.04 WSL image, pinned: the download is refused if the checksum differs.
BASE_URL = "https://releases.ubuntu.com/noble/ubuntu-24.04.5-wsl-amd64.wsl"
BASE_SHA256 = "bb415d824822c4b878125729af451a5d18fb13d1cf5cbed9a7393ad64ac6039e"
BUILD_DISTRO, VERIFY_DISTRO = "mrr-build", "mrr-verify"
TEMPORARY = {BUILD_DISTRO, VERIFY_DISTRO}  # the only distros this script may unregister
STUDENT_USER = "mrr"


def say(text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


# ------------------------------------------------------------------------------------------ wsl.exe
def wsl_exe(*args: str, check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(["wsl.exe", *args], capture_output=True, timeout=timeout, env=dict(os.environ, WSL_UTF8="1"))
    result = subprocess.CompletedProcess(proc.args, proc.returncode, wsl.decode(proc.stdout), wsl.decode(proc.stderr))
    if check and result.returncode != 0:
        raise SystemExit(f"wsl.exe {' '.join(args)} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    return result


def run_in(distro: str, argv: list[str], user: str = "", timeout: float | None = 1800, show: bool = False):
    command = ["-d", distro] + (["-u", user] if user else []) + ["--exec", *argv]
    result = wsl_exe(*command, timeout=timeout)
    if show:
        print("    " + (result.stdout.strip() + "\n" + result.stderr.strip()).strip().replace("\n", "\n    "), flush=True)
    return result


def distro_names() -> list[str]:
    return [d.name for d in wsl.list_distros()]


def unregister_temporary(name: str) -> None:
    if name not in TEMPORARY:  # a guard against ever being pointed at a real distro
        raise SystemExit(f"refusing to unregister '{name}': only {sorted(TEMPORARY)} are temporary")
    if name in distro_names():
        say(f"removing the temporary distro '{name}'")
        wsl_exe("--unregister", name, timeout=600)


def wsl_path(path: Path) -> str:
    return wsl.to_wsl_path(path)


# ------------------------------------------------------------------------------------------- steps
def download_base(work: Path) -> Path:
    target = work / BASE_URL.rsplit("/", 1)[1]
    if target.is_file() and sha256(target) == BASE_SHA256:
        say(f"base image already downloaded and checked: {target.name}")
        return target
    say(f"downloading {BASE_URL}")
    partial = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(BASE_URL, timeout=60) as response, open(partial, "wb") as out:
        total, done, shown = int(response.headers.get("Content-Length", 0)), 0, 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if done - shown >= 50 << 20:
                shown = done
                say(f"  {done >> 20} of {total >> 20} MB")
    if sha256(partial) != BASE_SHA256:
        partial.unlink()
        raise SystemExit("the downloaded base image does not match the pinned checksum: refusing to use it")
    partial.replace(target)
    say("checksum matches the published one")
    return target


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def import_build_distro(work: Path, base: Path) -> None:
    unregister_temporary(BUILD_DISTRO)
    location = work / "wsl-build"
    shutil.rmtree(location, ignore_errors=True)
    say(f"importing the base as '{BUILD_DISTRO}'")
    wsl_exe("--import", BUILD_DISTRO, str(location), str(base), "--version", "2", timeout=1800)


def provision() -> None:
    say("installing rootless podman and creating the student user (apt downloads from Ubuntu's archive)")
    run_in(BUILD_DISTRO, ["sh", wsl_path(ROOT / "tools" / "distro" / "provision.sh")], user="root", show=True)
    wsl_exe("--terminate", BUILD_DISTRO)  # restart so /etc/wsl.conf (systemd, default user) takes effect


def save_images(work: Path, source: str, cfg: config.Config) -> Path:
    images = [cfg.sitl.image_ref, cfg.scripts.image, cfg.scene.image]
    archive = work / "images.tar"
    archive.unlink(missing_ok=True)
    say(f"saving {len(images)} images from '{source}': {', '.join(images)}")
    run_in(source, ["podman", "save", "-m", "-o", wsl_path(archive), *images], timeout=1800)
    say(f"images archive: {archive.stat().st_size >> 20} MB")
    return archive


def load_images(archive: Path) -> None:
    say(f"loading the images into '{BUILD_DISTRO}' as user {STUDENT_USER}")
    run_in(BUILD_DISTRO, ["podman", "load", "-i", wsl_path(archive)], user=STUDENT_USER, timeout=3600, show=False)
    listing = run_in(BUILD_DISTRO, ["podman", "images", "--format", "{{.Repository}}:{{.Tag}} {{.Size}}"], user=STUDENT_USER)
    print("    " + listing.stdout.strip().replace("\n", "\n    "), flush=True)


STAMP_FILE = "/etc/mrrsim-distro-version"  # inside the distro; setup compares it with the one it carries


def stamp_distro(cfg: config.Config, output: Path) -> str:
    """Give the distro a version made from what is in it: the image IDs (content hashes, so rebuilding unchanged
    images gives the same stamp), the pinned Ubuntu base and the provisioning script. Setup reads it from an
    installed distro and replaces the distro when it differs from the one in the installer. The stamp is also
    written next to the payload as an include for the installer script (payload/distro_version.iss)."""
    # the build distro holds only the images that were loaded into it, so listing them all is listing these
    ids = run_in(BUILD_DISTRO, ["podman", "images", "--format", "{{.ID}}"], user=STUDENT_USER).stdout.split()
    provision = (ROOT / "tools" / "distro" / "provision.sh").read_bytes()
    digest = hashlib.sha256(",".join(sorted(set(ids))).encode() + BASE_SHA256.encode() + provision).hexdigest()[:12]
    run_in(BUILD_DISTRO, ["sh", "-c", f"echo {digest} > {STAMP_FILE}"], user="root")
    include = output.parent / "distro_version.iss"
    include.write_text(f'#define DistroVersion "{digest}"\n', encoding="utf-8", newline="\n")
    say(f"distro version {digest} (written to {include.name})")
    return digest


def clean_up() -> None:
    say("cleaning caches and machine-specific files")
    run_in(BUILD_DISTRO, ["sh", wsl_path(ROOT / "tools" / "distro" / "cleanup.sh")], user="root", show=True)
    wsl_exe("--terminate", BUILD_DISTRO)


def export(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    say(f"exporting '{BUILD_DISTRO}' to {output}")
    result = wsl_exe("--export", BUILD_DISTRO, str(output), "--format", "tar.gz", check=False, timeout=3600)
    if result.returncode != 0 or not output.is_file():  # an older wsl.exe without --format: tar, then compress here
        say("this wsl.exe cannot write tar.gz directly; exporting a tar and compressing it")
        plain = output.with_suffix("")
        wsl_exe("--export", BUILD_DISTRO, str(plain), timeout=3600)
        with open(plain, "rb") as src, gzip.open(output, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        plain.unlink()
    say(f"payload: {output} ({output.stat().st_size >> 20} MB)")


def verify(payload: Path, work: Path, cfg: config.Config) -> None:
    unregister_temporary(VERIFY_DISTRO)
    location = work / "wsl-verify"
    shutil.rmtree(location, ignore_errors=True)
    say(f"importing the finished payload as '{VERIFY_DISTRO}' to test it")
    wsl_exe("--import", VERIFY_DISTRO, str(location), str(payload), "--version", "2", timeout=1800)
    try:
        who = run_in(VERIFY_DISTRO, ["id", "-un"]).stdout.strip()
        assert who == STUDENT_USER, f"default user is '{who}', expected '{STUDENT_USER}'"
        say(f"default user: {who}")
        for image in (cfg.sitl.image_ref, cfg.scripts.image, cfg.scene.image):
            run_in(VERIFY_DISTRO, ["podman", "image", "exists", image])
        say("all three images are present")
        stamp = run_in(VERIFY_DISTRO, ["cat", STAMP_FILE]).stdout.strip()
        wanted = (payload.parent / "distro_version.iss").read_text(encoding="utf-8").split('"')[1]
        assert stamp == wanted, f"the distro says version {stamp!r} but the installer include says {wanted!r}"
        say(f"distro version {stamp}")
        out = run_in(VERIFY_DISTRO, ["podman", "run", "--rm", "--network", "none", "--entrypoint", "/opt/ardupilot/arducopter",
                                     cfg.sitl.image_ref, "--help"], timeout=300)
        assert "--sysid" in out.stdout + out.stderr, "the SITL image did not start"
        say("the SITL image starts")
        out = run_in(VERIFY_DISTRO, ["podman", "run", "--rm", "--network", "none", "--entrypoint", "python", cfg.scene.image,
                                     "-W", "ignore", "-c", "import projectairsim, dronekit; print('imports ok')"], timeout=300)
        assert "imports ok" in out.stdout, "the Project AirSim image did not start"
        say("the Project AirSim image starts")
        net = run_in(VERIFY_DISTRO, ["sh", "-c", "wslinfo --networking-mode"]).stdout.strip()
        say(f"networking mode inside the distro: {net}")
    finally:
        wsl_exe("--terminate", VERIFY_DISTRO, check=False)
    say(f"verified. '{VERIFY_DISTRO}' is left registered so it can be tried; remove it with: wsl --unregister {VERIFY_DISTRO}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "distro", help="scratch folder (needs ~8 GB)")
    parser.add_argument("--output", type=Path, default=ROOT / "payload" / "mrr-rootfs.tar.gz")
    parser.add_argument("--source-distro", default="mrr", help="distro that holds the built simulator images")
    parser.add_argument("--verify-only", action="store_true", help="skip building; test an existing payload")
    parser.add_argument("--keep-build-distro", action="store_true", help="do not remove mrr-build when finished")
    args = parser.parse_args()
    cfg = config.load(paths.find_config(None))
    args.work.mkdir(parents=True, exist_ok=True)

    if not args.verify_only:
        base = download_base(args.work)
        import_build_distro(args.work, base)
        provision()
        load_images(save_images(args.work, args.source_distro, cfg))
        stamp_distro(cfg, args.output)
        clean_up()
        export(args.output)
        if not args.keep_build_distro:
            unregister_temporary(BUILD_DISTRO)
    verify(args.output, args.work, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
