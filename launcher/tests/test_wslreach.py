import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config
from mrrsim.runtime import session, wsl


def load(text: str = "") -> config.Config:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mrrsim.toml"
        path.write_text(text, encoding="utf-8")
        return config.load(path)


def completed(code: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, "", "")


class CanConnect(unittest.TestCase):
    def test_runs_the_probe_inside_the_distro(self):
        with mock.patch.object(wsl, "run", return_value=completed(0)) as run:
            self.assertTrue(wsl.can_connect("mrr", "127.0.0.1", 8989))
        argv = run.call_args.args[1]
        self.assertEqual(argv[:2], ["timeout", "3"])
        self.assertIn("exec 3<>/dev/tcp/127.0.0.1/8989", argv[-1])

    def test_exit_codes(self):
        for code, expected in ((1, False), (124, False), (127, None)):
            with mock.patch.object(wsl, "run", return_value=completed(code)):
                self.assertIs(wsl.can_connect("mrr", "127.0.0.1", 8989), expected, code)

    def test_a_host_that_could_inject_shell_is_not_probed(self):
        with mock.patch.object(wsl, "run") as run:
            self.assertIsNone(wsl.can_connect("mrr", "1.2.3.4; reboot", 8989))
            self.assertIsNone(wsl.can_connect("mrr", "127.0.0.1", 0))
        run.assert_not_called()


class WaitForWsl(unittest.TestCase):
    def setUp(self):
        self.sess = session.Session(load(), dry_run=False)
        sleeper = mock.patch.object(session.time, "sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_returns_at_once_when_reachable(self):
        with mock.patch.object(session.wsl, "can_connect", return_value=True) as probe:
            self.sess._wait_wsl_reaches_unreal()
        probe.assert_called_once()
        self.sleep.assert_not_called()

    def test_waits_through_the_prompt_then_continues(self):
        with mock.patch.object(session.wsl, "can_connect", side_effect=[False, False, True]) as probe, \
                self.assertLogs("mrrsim", level="INFO") as logs:
            self.sess._wait_wsl_reaches_unreal()
        self.assertEqual(probe.call_count, 3)
        text = "\n".join(logs.output)
        self.assertIn("Allow access", text)
        self.assertIn("WSL can reach Unreal now", text)

    def test_gives_up_with_instructions(self):
        cfg = load("[unreal]\nwsl_reach_timeout_s = 5")
        sess = session.Session(cfg, dry_run=False)
        clock = iter([0, 0, 2, 4, 6, 8])
        with mock.patch.object(session.wsl, "can_connect", return_value=False), \
                mock.patch.object(session.time, "monotonic", side_effect=lambda: next(clock)):
            with self.assertRaises(session.SessionError) as caught:
                sess._wait_wsl_reaches_unreal()
        self.assertIn("Allow access", str(caught.exception))
        self.assertIn("Gave up after 5 s", str(caught.exception))

    def test_skipped_when_it_cannot_be_tested_and_in_dry_run_and_when_disabled(self):
        with mock.patch.object(session.wsl, "can_connect", return_value=None) as probe:
            self.sess._wait_wsl_reaches_unreal()
        probe.assert_called_once()
        with mock.patch.object(session.wsl, "can_connect") as probe:
            session.Session(load(), dry_run=True)._wait_wsl_reaches_unreal()
            session.Session(load("[unreal]\nwsl_reach_timeout_s = 0"), dry_run=False)._wait_wsl_reaches_unreal()
        probe.assert_not_called()

    def test_config_validation(self):
        self.assertEqual(load().unreal.wsl_reach_timeout_s, 90.0)
        for text in ("[unreal]\nwsl_reach_timeout_s = -1", '[unreal]\nwsl_reach_timeout_s = "x"'):
            with self.assertRaises(config.ConfigError, msg=text):
                load(text)


if __name__ == "__main__":
    unittest.main()
