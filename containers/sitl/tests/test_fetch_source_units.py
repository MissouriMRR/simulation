"""The parts of fetch_source.py that need no git and no network, so they run on any computer (Windows too):

    cd containers/sitl && python -m unittest tests.test_fetch_source_units

(launcher/tests/test_container_scripts.py runs it with the launcher's tests. test_fetch_source.py, the end-to-end test
against a fake GitHub, needs Linux: launcher/tests/test_fetch_source_wsl.py runs it in WSL.)
"""
import io
import os
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fetch_source  # noqa: E402


def tarball(files: dict[str, bytes], top: str = "repo-abc123") -> bytes:
    """A GitHub-style tarball: every file below one top-level folder."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{top}/{name}" if top else name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def answer(data: bytes) -> mock.MagicMock:
    """What urlopen returns: a context manager whose response has a length header and reads `data` in pieces."""
    response = mock.MagicMock()
    stream = io.BytesIO(data)
    response.__enter__.return_value.headers = {"Content-Length": str(len(data))}
    response.__enter__.return_value.read.side_effect = stream.read
    return response


class ParseRepo(unittest.TestCase):
    def test_github_addresses_in_every_spelling(self):
        for url in ("https://github.com/ArduPilot/ardupilot.git", "https://github.com/ArduPilot/ardupilot",
                    "https://github.com/ArduPilot/ardupilot/", "git@github.com:ArduPilot/ardupilot.git",
                    "ssh://git@github.com/ArduPilot/ardupilot.git", "ArduPilot/ardupilot", " ArduPilot/ardupilot.git "):
            self.assertEqual(fetch_source.parse_repo(url), ("ArduPilot", "ardupilot"), url)

    def test_anything_else_is_refused_with_what_to_do(self):
        for url in ("https://gitlab.com/a/b.git", "https://example.com/x", "just-a-name", ""):
            with self.assertRaises(fetch_source.FetchError) as caught:
                fetch_source.parse_repo(url)
            self.assertIn("--skip", str(caught.exception), url)


class Download(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dest = os.path.join(self._tmp.name, "x.tar.gz")
        for patch in (mock.patch.object(fetch_source.time, "sleep"), mock.patch.object(fetch_source, "log")):
            setattr(self, patch.attribute, patch.start())
            self.addCleanup(patch.stop)

    def urlopen(self, *answers):
        return mock.patch.object(fetch_source.urllib.request, "urlopen", side_effect=list(answers))

    def test_a_whole_tarball_is_kept(self):
        data = tarball({"README": b"hi\n"})
        with self.urlopen(answer(data)):
            fetch_source.download("https://x/r/tar.gz/abc", self.dest, "r")
        self.assertEqual(Path(self.dest).read_bytes(), data)
        self.sleep.assert_not_called()

    def test_a_network_error_is_tried_again(self):
        with self.urlopen(urllib.error.URLError("reset"), answer(tarball({"a": b"1"}))):
            fetch_source.download("https://x/r/tar.gz/abc", self.dest, "r")
        self.assertEqual(self.sleep.call_count, 1)

    def test_a_cut_short_tarball_is_tried_again(self):
        whole = tarball({"a": b"1" * 5000})
        with self.urlopen(answer(whole[:len(whole) // 2]), answer(whole)):
            fetch_source.download("https://x/r/tar.gz/abc", self.dest, "r")
        self.assertEqual(Path(self.dest).read_bytes(), whole)

    def test_a_ref_that_does_not_exist_gives_up_at_once(self):
        missing = urllib.error.HTTPError("https://x", 404, "Not Found", hdrs=None, fp=io.BytesIO(b""))
        self.addCleanup(missing.close)
        with self.urlopen(missing), self.assertRaises(fetch_source.FetchError) as caught:
            fetch_source.download("https://x/r/tar.gz/nope", self.dest, "r")
        self.assertIn("does that ref exist?", str(caught.exception))
        self.sleep.assert_not_called()

    def test_it_gives_up_after_its_tries_without_a_last_wait(self):
        failures = [urllib.error.URLError("down")] * fetch_source.DOWNLOAD_TRIES
        with self.urlopen(*failures), self.assertRaises(fetch_source.FetchError) as caught:
            fetch_source.download("https://x/r/tar.gz/abc", self.dest, "r")
        self.assertIn(f"giving up after {fetch_source.DOWNLOAD_TRIES} attempts", str(caught.exception))
        self.assertEqual(self.sleep.call_count, fetch_source.DOWNLOAD_TRIES - 1)


class Extract(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def archive(self, files: dict[str, bytes], top: str = "repo-abc123") -> str:
        path = self.tmp / "a.tar.gz"
        path.write_bytes(tarball(files, top))
        return str(path)

    def test_the_top_level_folder_is_dropped(self):
        fetch_source.extract(self.archive({"wscript": b"#waf\n", "ArduCopter/main.cpp": b"int main(){}\n"}),
                             str(self.tmp / "dest"))
        self.assertEqual((self.tmp / "dest" / "wscript").read_bytes(), b"#waf\n")
        self.assertTrue((self.tmp / "dest" / "ArduCopter" / "main.cpp").is_file())
        self.assertFalse((self.tmp / "dest" / "repo-abc123").exists())

    def test_a_path_that_leaves_the_folder_is_refused(self):
        with self.assertRaises((fetch_source.FetchError, tarfile.TarError)):
            fetch_source.extract(self.archive({"../outside.txt": b"x"}), str(self.tmp / "dest"))
        self.assertFalse((self.tmp / "outside.txt").exists())


class SourcesFile(unittest.TestCase):
    def test_each_run_records_only_what_it_fetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "src"
            dest.mkdir()  # the real fetch_commit makes it

            def fetch_into(owner, repo, commit, into, rel, workdir):
                fetch_source._manifest.append((commit, f"{owner}/{repo}", rel or "."))

            with mock.patch.object(fetch_source, "fetch_commit", return_value="c" * 40), \
                    mock.patch.object(fetch_source, "fetch_into", side_effect=fetch_into), \
                    mock.patch.object(fetch_source, "git"), mock.patch.object(fetch_source, "fetch_submodules"), \
                    mock.patch.object(fetch_source, "log"):
                for _ in range(2):
                    self.assertEqual(fetch_source.main(["--repo", "ArduPilot/ardupilot", "--ref", "Copter-4.7.0",
                                                        "--dest", str(dest)]), 0)
            self.assertEqual((dest / ".mrrsim-sources.txt").read_text(encoding="utf-8").splitlines(),
                             [f"{'c' * 40} ArduPilot/ardupilot ."])


if __name__ == "__main__":
    unittest.main()
