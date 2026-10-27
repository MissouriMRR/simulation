#!/usr/bin/env python3
"""Fetch a GitHub repository and its submodules at a pinned ref, using tarballs.

Why: `git fetch` / `git submodule update` against github.com crawled at ~80 KiB/s on a network
where a plain HTTPS download ran at ~5.8 MB/s. A tarball from codeload.github.com is one ordinary
HTTPS download, and the submodule tarballs can be downloaded in parallel.

What it does
  1. Fetches only the commit and its trees (no file contents; a few hundred KB) with git, to learn
     the exact commit and the commit every submodule is pinned to. A tarball carries neither.
     If git is unreachable it falls back to the GitHub API for the pinned commits.
  2. Downloads the repo's tarball for that exact commit and unpacks it into DEST.
  3. Downloads every submodule tarball at its pinned commit (in parallel) and unpacks it at its
     path, recursing into nested submodules.
  4. Leaves DEST/.git with HEAD at the commit, so `git rev-parse HEAD` works for tools that embed
     the version, and writes DEST/.mrrsim-sources.txt (commit, repo and path of everything
     fetched: the record you need for a GPL source offer).

Standard library and git only. Base URLs can be overridden for tests with MRRSIM_GIT_BASE,
MRRSIM_TARBALL_BASE and MRRSIM_API_BASE; GITHUB_TOKEN (optional) is used for API calls only.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

GIT_BASE = os.environ.get("MRRSIM_GIT_BASE", "https://github.com").rstrip("/")
TARBALL_BASE = os.environ.get("MRRSIM_TARBALL_BASE", "https://codeload.github.com").rstrip("/")
API_BASE = os.environ.get("MRRSIM_API_BASE", "https://api.github.com").rstrip("/")
USER_AGENT = "mrrsim-fetch/1.0"

_URL_RE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)
_SLUG_RE = re.compile(r"^(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?$")

_lock = threading.Lock()
_manifest: list[tuple[str, str, str]] = []  # (commit, owner/repo, path)


class FetchError(RuntimeError):
    pass


def log(message: str) -> None:
    with _lock:
        print(message, flush=True)


def parse_repo(url: str) -> tuple[str, str]:
    for pattern in (_URL_RE, _SLUG_RE):
        match = pattern.match(url.strip())
        if match:
            return match["owner"], match["repo"]
    raise FetchError(
        f"not a GitHub repository: {url!r}. Add its path to --skip, or extend fetch_source.py."
    )


# ------------------------------------------------------------------------------- git
def git(*args: str, cwd: str | None = None, quiet: bool = True, timeout: float | None = None) -> str:
    """Run git. quiet=False lets git's own progress output reach the build log."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, text=True, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE if quiet else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise FetchError(f"git {' '.join(args)} timed out after {timeout:.0f} s") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise FetchError(f"git {' '.join(args)} failed (exit {result.returncode}) {detail}".strip())
    return result.stdout


def fetch_commit(repo_dir: str, owner: str, repo: str, ref: str) -> str:
    """Fetch just the commit and its trees (no blobs) into repo_dir; return the full commit SHA."""
    os.makedirs(repo_dir, exist_ok=True)
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        git("init", "-q", cwd=repo_dir)
    try:
        git("remote", "remove", "origin", cwd=repo_dir)
    except FetchError:
        pass
    git("remote", "add", "origin", f"{GIT_BASE}/{owner}/{repo}.git", cwd=repo_dir)
    last: FetchError | None = None
    # A stalled git connection must not hang the build: after the timeout the callers fall back
    # to the GitHub API for the pinned commits.
    for attempt in range(1, 3):
        try:
            git("fetch", "--progress", "--depth", "1", "--filter=blob:none", "origin", ref,
                cwd=repo_dir, quiet=False, timeout=180)
            last = None
            break
        except FetchError as exc:
            last = exc
            log(f"  git metadata fetch attempt {attempt} failed: {exc}")
            time.sleep(2 ** attempt)
    if last is not None:
        raise last
    return git("rev-parse", "FETCH_HEAD^{commit}", cwd=repo_dir).strip()


def pinned_shas(meta_dir: str, commit: str, paths: list[str]) -> dict[str, str]:
    """{submodule path: commit it is pinned to}, read from the tree objects of `commit`."""
    out = git("ls-tree", "-z", commit, "--", *paths, cwd=meta_dir)
    shas: dict[str, str] = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        mode, _, sha = meta.split(" ")
        if mode == "160000":
            shas[path] = sha
    return shas


def api_sha(owner: str, repo: str, path: str, ref: str) -> str:
    """Fallback: ask the GitHub API which commit a submodule path is pinned to."""
    url = f"{API_BASE}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(ref)}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
            data = json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FetchError(f"GitHub API lookup for {owner}/{repo}:{path}@{ref} failed: {exc}") from exc
    if isinstance(data, dict) and data.get("type") == "submodule" and data.get("sha"):
        return data["sha"]
    raise FetchError(f"{owner}/{repo}:{path}@{ref} is not a submodule according to the GitHub API")


def read_gitmodules(root: str) -> dict[str, str]:
    """{path: url} from root/.gitmodules (empty if there is none)."""
    gitmodules = os.path.join(root, ".gitmodules")
    if not os.path.isfile(gitmodules):
        return {}
    try:
        out = git("config", "-f", gitmodules, "-z", "--get-regexp", r"^submodule\..*\.(path|url)$")
    except FetchError:
        return {}  # `git config --get-regexp` exits 1 when nothing matches
    paths: dict[str, str] = {}
    urls: dict[str, str] = {}
    for item in out.split("\0"):
        if not item:
            continue
        key, _, value = item.partition("\n")
        name, _, field = key[len("submodule."):].rpartition(".")
        (paths if field == "path" else urls)[name] = value
    return {paths[name]: urls[name] for name in paths if name in urls}


# --------------------------------------------------------------------------- tarballs
def _verify_gzip(path: str) -> None:
    """Raise if the download was cut short (chunked responses do not announce a length)."""
    with gzip.open(path, "rb") as handle:
        while handle.read(1 << 20):
            pass


def download(url: str, dest: str, label: str) -> None:
    last: Exception | None = None
    for attempt in range(1, 6):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response, open(dest, "wb") as out:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                started = last_report = time.monotonic()
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last_report >= 5:
                        last_report = now
                        size = f"{done / 1048576:.1f}" + (f" of {total / 1048576:.1f}" if total else "")
                        log(f"  {label}: {size} MB ({done / 1024 / (now - started):.0f} KiB/s)")
            _verify_gzip(dest)
            return
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403, 404):
                raise FetchError(f"{url}: HTTP {exc.code} (does that ref exist?)") from exc
            last = exc
        except (urllib.error.URLError, OSError, EOFError, http.client.HTTPException) as exc:
            last = exc
        log(f"  {label}: attempt {attempt} failed ({last}); retrying")
        time.sleep(min(30, 2 ** attempt))
    raise FetchError(f"{url}: giving up after 5 attempts ({last})")


def extract(archive: str, dest: str) -> None:
    """Unpack a GitHub tarball into dest, dropping its single top-level folder."""
    os.makedirs(dest, exist_ok=True)
    root = os.path.realpath(dest)
    kwargs = {"filter": "tar"} if hasattr(tarfile, "tar_filter") else {}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            parts = member.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue  # the top-level folder itself
            member.name = parts[1]
            if member.islnk():
                link = member.linkname.split("/", 1)
                member.linkname = link[1] if len(link) > 1 else member.linkname
            target = os.path.realpath(os.path.join(root, member.name))
            if target != root and not target.startswith(root + os.sep):
                raise FetchError(f"unsafe path in archive: {member.name}")
            tar.extract(member, root, **kwargs)


def fetch_into(owner: str, repo: str, commit: str, dest: str, rel: str, workdir: str) -> None:
    label = f"{owner}/{repo}" + (f" -> {rel}" if rel else "")
    log(f"downloading {label} @ {commit[:12]}")
    handle, archive = tempfile.mkstemp(suffix=".tar.gz", dir=workdir)
    os.close(handle)
    try:
        download(f"{TARBALL_BASE}/{owner}/{repo}/tar.gz/{commit}", archive, label)
        extract(archive, dest)
    finally:
        os.remove(archive)
    with _lock:
        _manifest.append((commit, f"{owner}/{repo}", rel or "."))
    log(f"  {label}: done")


# ------------------------------------------------------------------------ submodules
def fetch_submodules(
    root: str, owner: str, repo: str, commit: str, meta_dir: str | None,
    rel: str, skip: str, workdir: str, jobs: int,
) -> None:
    entries = read_gitmodules(root)
    wanted: dict[str, str] = {}
    for path, url in sorted(entries.items()):
        full = f"{rel}/{path}" if rel else path
        if skip and re.search(skip, full):
            log(f"skipping submodule {full}")
        else:
            wanted[path] = url
    if not wanted:
        return

    if meta_dir is not None:
        shas = pinned_shas(meta_dir, commit, list(wanted))
    else:
        log(f"no git metadata for {owner}/{repo}: asking the GitHub API for pinned commits")
        shas = {path: api_sha(owner, repo, path, commit) for path in wanted}
    for path in wanted:
        if path not in shas:
            log(f"warning: {path} is listed in .gitmodules but is not a submodule at {commit[:12]}; skipping")

    def fetch_one(path: str) -> None:
        sub_owner, sub_repo = parse_repo(wanted[path])
        sub_commit = shas[path]
        full = f"{rel}/{path}" if rel else path
        dest = os.path.join(root, path)
        fetch_into(sub_owner, sub_repo, sub_commit, dest, full, workdir)
        if os.path.isfile(os.path.join(dest, ".gitmodules")):
            nested_meta = tempfile.mkdtemp(prefix="meta-", dir=workdir)
            try:
                try:
                    fetch_commit(nested_meta, sub_owner, sub_repo, sub_commit)
                except FetchError as exc:
                    log(f"  {full}: git metadata unavailable ({exc}); using the GitHub API")
                    nested_meta_dir = None
                else:
                    nested_meta_dir = nested_meta
                fetch_submodules(dest, sub_owner, sub_repo, sub_commit, nested_meta_dir, full, skip, workdir, 1)
            finally:
                shutil.rmtree(nested_meta, ignore_errors=True)

    todo = [path for path in wanted if path in shas]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for future in concurrent.futures.as_completed([pool.submit(fetch_one, p) for p in todo]):
            future.result()  # re-raise the first failure


# ------------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="GitHub URL or owner/repo")
    parser.add_argument("--ref", required=True, help="tag, branch or commit SHA")
    parser.add_argument("--dest", required=True, help="folder to unpack into")
    parser.add_argument("--skip", default="", help="regex of submodule paths NOT to fetch (re.search)")
    parser.add_argument("--jobs", type=int, default=4, help="parallel submodule downloads")
    args = parser.parse_args(argv)

    owner, repo = parse_repo(args.repo)
    dest = os.path.abspath(args.dest)
    workdir = tempfile.mkdtemp(prefix="mrrsim-fetch-")
    started = time.monotonic()
    try:
        log(f"fetching {owner}/{repo} @ {args.ref} into {dest}")
        commit: str | None = None
        try:
            commit = fetch_commit(dest, owner, repo, args.ref)
            log(f"{args.ref} is commit {commit}")
        except FetchError as exc:
            log(f"WARNING: git metadata unavailable ({exc}); continuing without .git")
            shutil.rmtree(os.path.join(dest, ".git"), ignore_errors=True)

        pin = commit or args.ref
        fetch_into(owner, repo, pin, dest, "", workdir)
        if commit:
            git("update-ref", "--no-deref", "HEAD", commit, cwd=dest)
        fetch_submodules(dest, owner, repo, pin, dest if commit else None, "", args.skip, workdir, args.jobs)

        with open(os.path.join(dest, ".mrrsim-sources.txt"), "w", encoding="utf-8") as handle:
            for sha, slug, path in sorted(_manifest, key=lambda row: row[2]):
                handle.write(f"{sha} {slug} {path}\n")
        log(f"done: {len(_manifest)} repositories in {time.monotonic() - started:.0f} s")
        return 0
    except (FetchError, tarfile.TarError, OSError) as exc:
        log(f"ERROR: {exc}")
        return 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
