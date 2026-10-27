"""Fetch one folder of a GitHub repository at an exact commit, without a full clone.

    python fetch_client.py REPO_URL REF PATH DEST

REF is a branch, tag or commit. A blobless clone (commits and trees only, a few MB) names the commit and
lists the files of PATH with their git hashes; the files themselves are then downloaded from
raw.githubusercontent.com, in parallel, and each one is checked against its git hash. DEST gets the contents
of PATH, and a COMMIT file naming the commit that was fetched.

Why not `git sparse-checkout`: that makes git fetch the blobs lazily, and GitHub has been seen to answer
such fetches with "HTTP 408 ... could not fetch <blob> from promisor remote" for any blob, however small.
Plain downloads of the same files work.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def download(url: str, expected_sha: str) -> bytes:
    last: Exception | None = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
            actual = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
            if actual != expected_sha:
                raise ValueError(f"hash {actual} does not match the repository's {expected_sha}")
            return data
        except Exception as exc:  # network errors and bad hashes are both worth another try
            last = exc
            time.sleep(1 + attempt * 2)
    raise SystemExit(f"could not download {url}: {last}")


def main(repo: str, ref: str, folder: str, dest: str) -> None:
    match = re.fullmatch(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?", repo)
    if not match:
        raise SystemExit(f"{repo}: only https://github.com/OWNER/REPO URLs are supported")
    owner, name = match.groups()
    folder = folder.strip("/")
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "repo"
        git("clone", "-q", "--filter=blob:none", "--no-checkout", repo, str(clone))
        for candidate in (ref, f"origin/{ref}"):
            try:
                commit = git("rev-parse", "--verify", f"{candidate}^{{commit}}", cwd=clone)
                break
            except subprocess.CalledProcessError:
                continue
        else:
            raise SystemExit(f"{ref} is not a branch, tag or commit of {repo}")
        listing = git("ls-tree", "-r", "--full-tree", commit, "--", folder, cwd=clone)
    files = []
    for line in listing.splitlines():
        meta, path = line.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind == "blob" and mode != "120000":  # symbolic links are not needed here
            files.append((path, sha))
    if not files:
        raise SystemExit(f"{folder} has no files at {commit}")

    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)

    def fetch(item: tuple[str, str]) -> None:
        path, sha = item
        data = download(f"https://raw.githubusercontent.com/{owner}/{name}/{commit}/{path}", sha)
        target = root / path[len(folder):].lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, files))
    (root / "COMMIT").write_text(commit + "\n", encoding="utf-8")
    print(f"fetched {len(files)} files of {folder} from {owner}/{name} at {commit}")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
