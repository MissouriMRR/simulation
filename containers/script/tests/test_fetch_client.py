"""fetch_client.py: one folder of a GitHub repository at a commit. Git and the downloads are replaced, so this needs
no network and only Python:

    cd containers/script && python -m unittest tests.test_fetch_client

(launcher/tests/test_container_scripts.py runs it with the launcher's tests.)
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import fetch_client  # noqa: E402

REPO = "https://github.com/owner/project.git"
COMMIT = "a" * 40
FOLDER = "client/python/projectairsim"


def blob_sha(data: bytes) -> str:
    """The git hash of a file's contents."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def answer(data: bytes) -> mock.MagicMock:
    """What urlopen returns: a context manager whose response reads `data`."""
    response = mock.MagicMock()
    response.__enter__.return_value.read.return_value = data
    return response


class Download(unittest.TestCase):
    def setUp(self):
        sleep = mock.patch.object(fetch_client.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def test_a_file_whose_hash_matches_is_returned(self):
        with mock.patch.object(fetch_client.urllib.request, "urlopen", return_value=answer(b"print(1)\n")):
            self.assertEqual(fetch_client.download("https://x/a.py", blob_sha(b"print(1)\n")), b"print(1)\n")
        self.sleep.assert_not_called()

    def test_a_flaky_download_is_tried_again(self):
        good = answer(b"x = 1\n")
        with mock.patch.object(fetch_client.urllib.request, "urlopen", side_effect=[OSError("reset"), good]):
            self.assertEqual(fetch_client.download("https://x/a.py", blob_sha(b"x = 1\n")), b"x = 1\n")
        self.assertEqual(self.sleep.call_count, 1)

    def test_a_file_that_never_matches_its_hash_stops_the_build_without_a_last_wait(self):
        with mock.patch.object(fetch_client.urllib.request, "urlopen", side_effect=lambda *a, **k: answer(b"changed")):
            with self.assertRaises(SystemExit) as caught:
                fetch_client.download("https://x/a.py", blob_sha(b"original"))
        self.assertIn("does not match", str(caught.exception))
        self.assertEqual(self.sleep.call_count, fetch_client.TRIES - 1)


class Main(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dest = Path(self._tmp.name) / "dest"
        self.files = {f"{FOLDER}/__init__.py": b"", f"{FOLDER}/client.py": b"class Client: ...\n",
                      f"{FOLDER}/utils/image.py": b"def unpack(): ...\n"}

    def fake_git(self, listing: str, known_refs=("main", COMMIT)):
        def git(*args, cwd=None):
            if args[0] == "clone":
                return ""
            if args[0] == "rev-parse":
                name = args[-1].split("^")[0]
                if name in known_refs:
                    return COMMIT
                raise subprocess.CalledProcessError(128, "git")
            if args[0] == "ls-tree":
                return listing
            raise AssertionError(args)
        return git

    def listing(self) -> str:
        lines = [f"100644 blob {blob_sha(data)}\t{path}" for path, data in self.files.items()]
        lines.append(f"120000 blob {'b' * 40}\t{FOLDER}/link")  # a symbolic link: not fetched
        return "\n".join(lines)

    def fetch(self, ref="main", listing=None):
        downloads = []

        def download(url, sha):
            path = url.split(f"/{COMMIT}/", 1)[1]
            downloads.append(url)
            self.assertEqual(sha, blob_sha(self.files[path]))
            return self.files[path]

        with mock.patch.object(fetch_client, "git", side_effect=self.fake_git(self.listing() if listing is None else listing)), \
                mock.patch.object(fetch_client, "download", side_effect=download), mock.patch("builtins.print"):
            fetch_client.main(REPO, ref, FOLDER + "/", str(self.dest))
        return downloads

    def test_the_folder_is_laid_out_under_dest_with_the_commit_recorded(self):
        downloads = self.fetch()
        self.assertEqual((self.dest / "client.py").read_bytes(), b"class Client: ...\n")
        self.assertEqual((self.dest / "utils" / "image.py").read_bytes(), b"def unpack(): ...\n")
        self.assertTrue((self.dest / "__init__.py").is_file())
        self.assertFalse((self.dest / "link").exists())
        self.assertEqual((self.dest / "COMMIT").read_text(encoding="utf-8"), COMMIT + "\n")
        self.assertTrue(all(url.startswith(f"https://raw.githubusercontent.com/owner/project/{COMMIT}/") for url in downloads))
        self.assertEqual(len(downloads), 3)

    def test_only_github_addresses_are_accepted(self):
        for repo in ("https://gitlab.com/owner/project.git", "git@github.com:owner/project.git", "http://github.com/o/p"):
            with self.assertRaises(SystemExit) as caught:
                fetch_client.main(repo, "main", FOLDER, str(self.dest))
            self.assertIn("only https://github.com", str(caught.exception), repo)

    def test_a_ref_the_repository_does_not_have_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            self.fetch(ref="no-such-branch")
        self.assertIn("is not a branch, tag or commit", str(caught.exception))

    def test_an_empty_folder_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            self.fetch(listing="")
        self.assertIn("has no files", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
