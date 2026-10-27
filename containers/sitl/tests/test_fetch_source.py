"""End-to-end test of fetch_source.py against a fake GitHub (no internet needed).

The fake GitHub is a folder of bare git repos (served over file://) plus a tiny HTTP server that
answers the tarball and API URLs the script uses. POSIX only: run it in WSL or Linux:

    python3 -m unittest discover -s containers/sitl/tests -v
"""
import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "fetch_source.py"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1",
}


def sh(*cmd, cwd=None) -> str:
    env = dict(os.environ, **GIT_ENV)
    return subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


@unittest.skipIf(sys.platform == "win32", "needs POSIX git and file:// semantics; run it in WSL/Linux")
class FetchSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.git_root = cls.root / "git"

        leaf = cls.make_repo("sub", "pymavlink", {"generator/mavgen.py": "print('mavgen')\n"})
        mavlink = cls.make_repo(
            "sub", "mavlink", {"README": "mavlink\n", "message_definitions/a.xml": "<mavlink/>\n"},
            submodules={"pymavlink": ("sub", "pymavlink", leaf)},
        )
        chibios = cls.make_repo("sub", "chibios", {"os/big.c": "x\n"})
        cls.main_sha = cls.make_repo(
            "own", "ardupilot", {"wscript": "#waf\n", "ArduCopter/main.cpp": "int main(){}\n"},
            submodules={"modules/mavlink": ("sub", "mavlink", mavlink), "modules/ChibiOS": ("sub", "chibios", chibios)},
            tag="Copter-9.9.9",
        )
        cls.shas = {"pymavlink": leaf, "mavlink": mavlink, "chibios": chibios}
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), cls.handler_class())
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    # ----------------------------------------------------------------- fake GitHub
    @classmethod
    def make_repo(cls, owner, name, files, submodules=None, tag=None) -> str:
        work = cls.root / "work" / f"{owner}-{name}"
        work.mkdir(parents=True)
        sh("git", "init", "-q", "-b", "main", cwd=work)
        for rel, text in files.items():
            path = work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        if submodules:
            lines = []
            for path, (sub_owner, sub_name, _sha) in submodules.items():
                lines.append(f'[submodule "{path}"]\n\tpath = {path}\n\turl = https://github.com/{sub_owner}/{sub_name}.git\n')
            (work / ".gitmodules").write_text("".join(lines))
        sh("git", "add", "-A", cwd=work)
        for path, (_o, _n, sha) in (submodules or {}).items():
            sh("git", "update-index", "--add", "--cacheinfo", f"160000,{sha},{path}", cwd=work)
        sh("git", "commit", "-q", "-m", "init", cwd=work)
        if tag:
            sh("git", "tag", "-a", tag, "-m", tag, cwd=work)
        bare = cls.git_root / owner / f"{name}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        sh("git", "clone", "-q", "--bare", str(work), str(bare))
        sh("git", "config", "uploadpack.allowFilter", "true", cwd=bare)
        sh("git", "config", "uploadpack.allowAnySHA1InWant", "true", cwd=bare)
        return sh("git", "rev-parse", "HEAD", cwd=work)

    @classmethod
    def handler_class(cls):
        git_root = cls.git_root

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body: bytes, kind="application/octet-stream"):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                tarball = re.fullmatch(r"/([^/]+)/([^/]+)/tar\.gz/(.+)", url.path)
                api = re.fullmatch(r"/repos/([^/]+)/([^/]+)/contents/(.+)", url.path)
                try:
                    if tarball:
                        owner, name, ref = tarball.groups()
                        data = subprocess.run(
                            ["git", "-C", str(git_root / owner / f"{name}.git"), "archive", "--format=tar.gz",
                             f"--prefix={name}-{ref}/", ref],
                            check=True, capture_output=True,
                        ).stdout
                        return self.reply(200, data, "application/gzip")
                    if api:
                        owner, name, path = api.groups()
                        ref = urllib.parse.parse_qs(url.query)["ref"][0]
                        out = subprocess.run(
                            ["git", "-C", str(git_root / owner / f"{name}.git"), "ls-tree", ref, "--", urllib.parse.unquote(path)],
                            check=True, capture_output=True, text=True,
                        ).stdout
                        mode, _typ, rest = out.split(" ", 2)
                        sha = rest.split("\t")[0]
                        body = {"type": "submodule" if mode == "160000" else "file", "sha": sha}
                        return self.reply(200, json.dumps(body).encode(), "application/json")
                except subprocess.CalledProcessError:
                    pass
                self.reply(404, b"not found")

        return Handler

    # ---------------------------------------------------------------------- helpers
    def run_fetch(self, dest: Path, *, ref="Copter-9.9.9", git_base=None, extra=()):
        env = dict(
            os.environ,
            MRRSIM_GIT_BASE=git_base or f"file://{self.git_root}",
            MRRSIM_TARBALL_BASE=f"http://127.0.0.1:{self.port}",
            MRRSIM_API_BASE=f"http://127.0.0.1:{self.port}",
            **GIT_ENV,
        )
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", "https://github.com/own/ardupilot.git",
             "--ref", ref, "--dest", str(dest), *extra],
            env=env, capture_output=True, text=True, timeout=120,
        )

    # ------------------------------------------------------------------------ tests
    def test_fetches_repo_submodules_and_nested_submodules(self):
        dest = self.root / "out-full"
        result = self.run_fetch(dest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((dest / "ArduCopter" / "main.cpp").is_file())
        self.assertTrue((dest / "modules" / "mavlink" / "README").is_file())
        self.assertTrue((dest / "modules" / "mavlink" / "pymavlink" / "generator" / "mavgen.py").is_file())
        self.assertTrue((dest / "modules" / "ChibiOS" / "os" / "big.c").is_file())

    def test_head_is_pinned_to_the_resolved_commit(self):
        dest = self.root / "out-head"
        self.assertEqual(self.run_fetch(dest).returncode, 0)
        self.assertEqual(sh("git", "rev-parse", "HEAD", cwd=dest), self.main_sha)

    def test_manifest_records_every_pinned_commit(self):
        dest = self.root / "out-manifest"
        self.assertEqual(self.run_fetch(dest).returncode, 0)
        manifest = (dest / ".mrrsim-sources.txt").read_text()
        self.assertIn(f"{self.main_sha} own/ardupilot .", manifest)
        self.assertIn(f"{self.shas['mavlink']} sub/mavlink modules/mavlink", manifest)
        self.assertIn(f"{self.shas['pymavlink']} sub/pymavlink modules/mavlink/pymavlink", manifest)

    def test_skip_pattern_leaves_out_matching_submodules(self):
        dest = self.root / "out-skip"
        result = self.run_fetch(dest, extra=["--skip", "^modules/ChibiOS$"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((dest / "modules" / "ChibiOS" / "os" / "big.c").exists())
        self.assertTrue((dest / "modules" / "mavlink" / "README").is_file())
        self.assertIn("skipping submodule modules/ChibiOS", result.stdout)

    def test_github_api_fallback_when_git_metadata_is_unreachable(self):
        dest = self.root / "out-api"
        result = self.run_fetch(dest, git_base="file:///nonexistent")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("continuing without .git", result.stdout)
        self.assertTrue((dest / "modules" / "mavlink" / "pymavlink" / "generator" / "mavgen.py").is_file())
        self.assertFalse((dest / ".git").exists())

    def test_unknown_ref_fails_with_a_clear_message(self):
        result = self.run_fetch(self.root / "out-bad", ref="no-such-ref")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERROR", result.stdout)


if __name__ == "__main__":
    unittest.main()
