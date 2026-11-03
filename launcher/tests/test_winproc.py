"""runtime/winproc.py: the kill-on-close job, the installer's mutexes, pid checks and the memory size. On Windows the tests
use the real Windows calls (with child processes of their own and mutexes with names of their own, never the launcher's);
the fallbacks that make everything a no-op elsewhere are tested on any system, with `os.kill` faked, because on Windows
`os.kill(pid, 0)` would end that process."""
import os
import subprocess
import sys
import time
import unittest
import uuid
from unittest import mock

from mrrsim.core import paths
from mrrsim.runtime import winproc

WINDOWS = sys.platform == "win32"


def sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def ends_within(proc: subprocess.Popen, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return False


class Child(unittest.TestCase):
    def child(self) -> subprocess.Popen:
        proc = sleeper()
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc


@unittest.skipUnless(WINDOWS, "needs the Windows calls")
class Job(Child):
    def job(self) -> winproc.JobObject:
        job = winproc.JobObject()
        self.addCleanup(job.close)
        return job

    def test_a_process_in_the_job_dies_when_the_job_is_closed(self):
        job, proc = self.job(), self.child()
        self.assertTrue(job.assign(proc))
        self.assertIsNone(proc.poll())
        job.close()
        self.assertTrue(ends_within(proc, 10), "the process is still running")

    def test_a_process_that_was_not_assigned_is_left_alone(self):
        job, proc = self.job(), self.child()
        job.close()
        self.assertFalse(ends_within(proc, 1))

    def test_closing_twice_is_fine_and_a_closed_job_takes_nobody(self):
        job, proc = self.job(), self.child()
        job.close()
        job.close()
        self.assertFalse(job.assign(proc))

    def test_a_process_that_has_ended_cannot_be_assigned_and_it_is_logged(self):
        job = self.job()
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        with self.assertLogs("mrrsim", level="WARNING") as logs:
            self.assertFalse(job.assign(proc))
        self.assertIn("job object:", "\n".join(logs.output))

    def test_a_job_that_cannot_be_made_is_an_os_error(self):
        with mock.patch.object(winproc._k32, "CreateJobObjectW", return_value=0):
            with self.assertRaises(OSError) as caught:
                winproc.JobObject()
        self.assertIn("CreateJobObjectW failed", str(caught.exception))

    def test_a_job_that_cannot_be_set_up_is_closed_again_and_an_os_error(self):
        with mock.patch.object(winproc._k32, "SetInformationJobObject", return_value=0), \
                mock.patch.object(winproc._k32, "CloseHandle", wraps=winproc._k32.CloseHandle) as closed:
            with self.assertRaises(OSError) as caught:
                winproc.JobObject()
        self.assertIn("SetInformationJobObject failed", str(caught.exception))
        closed.assert_called_once()


@unittest.skipUnless(WINDOWS, "needs the Windows calls")
class Mutexes(unittest.TestCase):
    def setUp(self):
        unique = uuid.uuid4().hex
        patch = mock.patch.object(winproc, "APP_MUTEXES", (f"MrrsimTest{unique}", f"Global\\MrrsimTest{unique}"))
        patch.start()
        self.addCleanup(patch.stop)

    def mutexes(self) -> winproc.AppMutexes:
        held = winproc.AppMutexes()
        self.addCleanup(held.release)
        return held

    def test_the_first_launcher_gets_them_and_the_second_does_not(self):
        first, second = self.mutexes(), self.mutexes()
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())

    def test_a_launcher_that_was_turned_away_holds_nothing(self):
        first, second, third = self.mutexes(), self.mutexes(), self.mutexes()
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        self.assertEqual(second._handles, [])
        first.release()
        self.assertTrue(third.acquire())  # nothing of the second is in the way

    def test_after_release_another_launcher_can_start(self):
        first, second = self.mutexes(), self.mutexes()
        self.assertTrue(first.acquire())
        first.release()
        self.assertTrue(second.acquire())

    def test_losing_the_second_name_gives_back_the_first(self):
        unique = uuid.uuid4().hex
        local, shared = f"MrrsimTest{unique}a", f"Global\\MrrsimTest{unique}b"
        with mock.patch.object(winproc, "APP_MUTEXES", (shared,)):
            holder = self.mutexes()
            self.assertTrue(holder.acquire())
        with mock.patch.object(winproc, "APP_MUTEXES", (local, shared)):
            turned_away = self.mutexes()
            self.assertFalse(turned_away.acquire())
        with mock.patch.object(winproc, "APP_MUTEXES", (local,)):
            self.assertTrue(self.mutexes().acquire())  # the local one was let go again

    def test_a_mutex_that_cannot_be_made_is_skipped_with_a_warning(self):
        with mock.patch.object(winproc._k32, "CreateMutexW", return_value=0):
            with self.assertLogs("mrrsim", level="WARNING") as logs:
                self.assertTrue(self.mutexes().acquire())
        self.assertEqual("\n".join(logs.output).count("could not create mutex"), 2)

class InstallerAgreement(unittest.TestCase):
    def test_the_launcher_holds_exactly_the_mutexes_the_installer_waits_for(self):
        script = (paths.repo_root() / "MRRSimulationLauncher.iss").read_text(encoding="utf-8-sig")
        line = next(ln for ln in script.splitlines() if ln.startswith("AppMutex="))
        self.assertEqual(tuple(line.removeprefix("AppMutex=").split(",")), winproc.APP_MUTEXES)


@unittest.skipUnless(WINDOWS, "needs the Windows calls")
class Processes(Child):
    def test_this_process_is_alive(self):
        self.assertTrue(winproc.pid_alive(os.getpid()))

    def test_a_running_child_is_alive_and_a_finished_one_is_not(self):
        proc = self.child()
        self.assertTrue(winproc.pid_alive(proc.pid))
        proc.kill()
        proc.wait()
        self.assertFalse(winproc.pid_alive(proc.pid))

    def test_a_process_that_ended_with_the_code_for_still_running_is_not_alive(self):
        """259 is what Windows calls STILL_ACTIVE; a process can also simply exit with it."""
        proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(259)"])
        proc.wait()
        self.assertEqual(proc.returncode, 259)
        self.assertFalse(winproc.pid_alive(proc.pid))

    def test_a_process_that_is_busy_for_a_while_is_alive_until_it_ends(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
        self.addCleanup(proc.wait)
        self.assertTrue(winproc.pid_alive(proc.pid))
        proc.wait()
        self.assertFalse(winproc.pid_alive(proc.pid))

    def test_no_such_pid_is_not_alive(self):
        for pid in (0, -4, 2**31 - 8):
            self.assertFalse(winproc.pid_alive(pid), pid)

    def test_the_image_of_this_process_is_python(self):
        name = winproc.image_name(os.getpid())
        self.assertIsNotNone(name)
        self.assertTrue(name.lower().endswith(".exe") and "python" in name.lower(), name)

    def test_the_image_of_a_pid_that_is_gone_is_none(self):
        proc = self.child()
        proc.kill()
        proc.wait()
        self.assertIsNone(winproc.image_name(proc.pid))

    def test_the_memory_is_a_believable_number_of_gib(self):
        gib = winproc.total_ram_gb()
        self.assertIsInstance(gib, float)
        self.assertTrue(1 <= gib <= 16384, gib)


class OffWindows(unittest.TestCase):
    """Everything is a harmless no-op where the Windows calls do not exist."""

    def setUp(self):
        patch = mock.patch.object(winproc, "IS_WINDOWS", False)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_job_takes_nobody_and_closing_it_is_fine(self):
        job = winproc.JobObject()
        self.assertFalse(job.assign(mock.Mock(pid=1)))
        job.close()

    def test_the_mutexes_are_always_free(self):
        held = winproc.AppMutexes()
        self.assertTrue(held.acquire())
        self.assertTrue(winproc.AppMutexes().acquire())
        held.release()

    def test_what_only_windows_can_say_is_none(self):
        self.assertIsNone(winproc.image_name(1234))
        self.assertIsNone(winproc.total_ram_gb())

    def test_a_pid_is_alive_when_signal_zero_can_be_sent_to_it(self):
        with mock.patch.object(winproc.os, "kill") as kill:
            self.assertTrue(winproc.pid_alive(42))
        kill.assert_called_once_with(42, 0)

    def test_a_pid_is_not_alive_when_there_is_no_such_process(self):
        with mock.patch.object(winproc.os, "kill", side_effect=ProcessLookupError):
            self.assertFalse(winproc.pid_alive(42))

    def test_a_process_that_is_not_ours_to_signal_still_exists(self):
        with mock.patch.object(winproc.os, "kill", side_effect=PermissionError):
            self.assertTrue(winproc.pid_alive(42))

    def test_a_pid_that_cannot_be_right_is_not_even_tried(self):
        with mock.patch.object(winproc.os, "kill") as kill:
            self.assertFalse(winproc.pid_alive(0))
            self.assertFalse(winproc.pid_alive(-1))
        kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
