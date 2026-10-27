"""The console log, the per-session log file, and keeping the root logger out."""

import contextlib
import io
import logging
import tempfile
import unittest
from pathlib import Path

from mrrsim.core import log


class LogTestCase(unittest.TestCase):
    def setUp(self):
        """Every test starts with no console handler and puts the logger back."""
        saved = (list(log.LOG.handlers), log._console)
        log.LOG.handlers[:] = []
        log._console = None

        def restore():
            log.LOG.handlers[:] = saved[0]
            log._console = saved[1]

        self.addCleanup(restore)

    def console_handlers(self) -> list[logging.Handler]:
        return [h for h in log.LOG.handlers if isinstance(h, logging.StreamHandler)]


class Console(LogTestCase):
    def test_calling_setup_twice_gives_one_handler(self):
        log.setup()
        log.setup()
        self.assertEqual(len(self.console_handlers()), 1)

    def test_the_level_follows_the_latest_call(self):
        log.setup()
        self.assertEqual(self.console_handlers()[0].level, logging.INFO)
        log.setup(verbose=True)
        self.assertEqual(self.console_handlers()[0].level, logging.DEBUG)
        log.setup()
        self.assertEqual(self.console_handlers()[0].level, logging.INFO)

    def test_a_handler_that_was_removed_is_installed_again(self):
        log.setup()
        log.LOG.handlers[:] = []
        log.setup()
        self.assertEqual(len(self.console_handlers()), 1)

    def test_it_prints_time_level_and_message_to_stdout(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            log.setup()
            log.LOG.info("hello")
            log.LOG.debug("hidden")
        self.assertRegex(out.getvalue(), r"^\d\d:\d\d:\d\d INFO  hello\n$")

    def test_verbose_shows_debug_lines_too(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            log.setup(verbose=True)
            log.LOG.debug("detail")
        self.assertIn("DEBUG detail", out.getvalue())


class RootLogger(LogTestCase):
    def test_lines_do_not_also_go_to_a_handler_on_the_root_logger(self):
        seen = []

        class Spy(logging.Handler):
            def emit(self, record):
                seen.append(record)

        root = logging.getLogger()
        spy = Spy()
        root.addHandler(spy)
        self.addCleanup(root.removeHandler, spy)
        with contextlib.redirect_stdout(io.StringIO()):
            log.setup()
            log.LOG.info("only ours")
        self.assertEqual(seen, [])

    def test_the_logger_lets_every_level_through_to_its_handlers(self):
        self.assertEqual(log.LOG.level, logging.DEBUG)


class SessionFile(LogTestCase):
    def test_it_creates_the_folder_and_writes_everything_with_milliseconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "b" / "launcher.log"
            handler = log.attach_file(path)
            log.LOG.debug("detail")
            log.LOG.warning("careful")
            log.detach(handler)
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        stamp = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}"
        self.assertRegex(lines[0], rf"^{stamp} DEBUG detail$")
        self.assertRegex(lines[1], rf"^{stamp} WARNING careful$")

    def test_after_detach_nothing_more_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "launcher.log"
            handler = log.attach_file(path)
            log.LOG.info("before")
            log.detach(handler)
            log.LOG.info("after")
            text = path.read_text(encoding="utf-8")
        self.assertIn("before", text)
        self.assertNotIn("after", text)
        self.assertNotIn(handler, log.LOG.handlers)


if __name__ == "__main__":
    unittest.main()
