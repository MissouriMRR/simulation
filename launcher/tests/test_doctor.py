"""runtime/doctor.py: the preflight checks and the report. Every outside source (Windows, memory, PowerShell, WSL, podman,
who uses a port) is replaced by a fake, so these run anywhere; the scene checks use real generated scenes."""
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mrrsim.core import config, paths
from mrrsim.runtime import doctor, wsl
from mrrsim.runtime.doctor import FAIL, OK, WARN, Check
from mrrsim.runtime.podman import PodmanError
from mrrsim.scene import scenegen

BASE = "scene_ardupilot_quadrotor.jsonc"
ROBOT = "robot_ardu_quadrotor.jsonc"


def load() -> config.Config:
    return config.load(paths.repo_root() / "config" / "mrrsim.toml")


class Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cfg = load()


class Windows(unittest.TestCase):
    def setUp(self):
        self.cfg = load()

    def check(self, version, windows=True):
        with mock.patch.object(doctor, "IS_WINDOWS", windows), mock.patch.object(doctor.platform, "version", return_value=version):
            return doctor._windows(self.cfg)

    def test_a_build_with_mirrored_networking_is_fine(self):
        for version in ("10.0.26300", "10.0.22621"):
            self.assertEqual(self.check(version).status, OK, version)
        self.assertEqual(self.check("10.0.26300").detail, "build 26300")

    def test_a_build_that_runs_wsl_2_but_not_mirrored_networking_fails_and_says_why(self):
        for version in ("10.0.19041", "10.0.19045", "10.0.22000"):  # Windows 10, and the first Windows 11
            result = self.check(version)
            self.assertEqual(result.status, FAIL, version)
            self.assertIn("mirrored networking", result.detail)
            self.assertIn("22621", result.detail)
            self.assertIn("Windows 11 22H2", result.hint)

    def test_without_the_mirrored_requirement_an_old_build_is_only_a_warning(self):
        self.cfg.wsl.require_mirrored = False
        self.assertEqual(self.check("10.0.19045").status, WARN)
        self.assertEqual(self.check("10.0.19040").status, FAIL)  # WSL 2 itself is still needed

    def test_a_build_too_old_for_wsl_2_fails(self):
        result = self.check("10.0.19040")
        self.assertEqual(result.status, FAIL)
        self.assertIn("19041", result.detail)

    def test_a_version_that_cannot_be_read_is_a_warning(self):
        for version in ("nonsense", "10.0", "10.0.x"):
            result = self.check(version)
            self.assertEqual(result.status, WARN, version)
            self.assertIn(repr(version), result.detail)

    def test_off_windows_it_is_a_warning_naming_the_system(self):
        result = self.check("10.0.26300", windows=False)
        self.assertEqual(result.status, WARN)
        self.assertIn("Windows-only", result.detail)

    def test_the_build_number_alone(self):
        with mock.patch.object(doctor, "IS_WINDOWS", True), mock.patch.object(doctor.platform, "version", return_value="10.0.19045"):
            self.assertEqual(doctor._windows_build(), 19045)
        with mock.patch.object(doctor, "IS_WINDOWS", False):
            self.assertIsNone(doctor._windows_build())


class Ram(unittest.TestCase):
    def status(self, gb):
        with mock.patch.object(doctor, "total_ram_gb", return_value=gb):
            return doctor._ram().status

    def test_the_installers_thresholds(self):
        self.assertEqual(self.status(4), FAIL)
        self.assertEqual(self.status(6.8), FAIL)  # 6963 MB, under 7000
        self.assertEqual(self.status(7.0), WARN)
        self.assertEqual(self.status(14.0), WARN)
        self.assertEqual(self.status(15.0), OK)
        self.assertEqual(self.status(32), OK)

    def test_memory_that_cannot_be_read_is_a_warning(self):
        self.assertEqual(self.status(None), WARN)

    def test_the_detail_says_how_much_there_is(self):
        with mock.patch.object(doctor, "total_ram_gb", return_value=15.94):
            self.assertEqual(doctor._ram().detail, "15.9 GB installed")


class Gpu(unittest.TestCase):
    def check(self, names=None, error=None, windows=True):
        completed = subprocess.CompletedProcess([], 0, "\n".join(names or []) + "\n", "")
        run = mock.Mock(return_value=completed, side_effect=error)
        with mock.patch.object(doctor, "IS_WINDOWS", windows), mock.patch.object(doctor.subprocess, "run", run):
            return doctor._gpu()

    def test_a_discrete_gpu_is_fine(self):
        result = self.check(["NVIDIA GeForce RTX 3060"])
        self.assertEqual((result.status, result.detail), (OK, "NVIDIA GeForce RTX 3060"))

    def test_any_discrete_gpu_among_several_is_enough(self):
        self.assertEqual(self.check(["Intel(R) UHD Graphics 630", "NVIDIA GeForce GTX 1650"]).status, OK)
        self.assertEqual(self.check(["AMD Radeon RX 6600M"]).status, OK)
        self.assertEqual(self.check(["Intel(R) Arc(TM) A380 Graphics"]).status, OK)

    def test_only_an_integrated_gpu_is_a_warning(self):
        for name in ("Intel(R) UHD Graphics 630", "Intel(R) Arc(TM) Graphics", "AMD Radeon(TM) Graphics"):
            self.assertEqual(self.check([name]).status, WARN, name)

    def test_the_basic_display_adapter_fails_even_next_to_a_good_gpu(self):
        self.assertEqual(self.check(["Microsoft Basic Display Adapter"]).status, FAIL)
        self.assertEqual(self.check(["Microsoft Basic Display Adapter", "NVIDIA GeForce RTX 3060"]).status, FAIL)

    def test_virtual_adapters_do_not_count(self):
        result = self.check(["Parsec Virtual Display Adapter", "Microsoft Hyper-V Video", "NVIDIA GeForce RTX 3060"])
        self.assertEqual((result.status, result.detail), (OK, "NVIDIA GeForce RTX 3060"))
        self.assertEqual(self.check(["Parsec Virtual Display Adapter"]).detail, "no physical video controller found")

    def test_no_output_is_a_warning(self):
        self.assertEqual(self.check([]).status, WARN)

    def test_powershell_that_cannot_run_or_is_stuck_is_a_warning(self):
        for error in (OSError("no powershell"), subprocess.TimeoutExpired("powershell", 30)):
            result = self.check(error=error)
            self.assertEqual((result.status, result.detail), (WARN, "could not query video controllers"))

    def test_off_windows_nothing_is_asked(self):
        with mock.patch.object(doctor.subprocess, "run") as run:
            self.assertEqual(self.check(windows=False).status, WARN)
        run.assert_not_called()


class Disk(Folder):
    def check(self, free_gb, target=None):
        usage = SimpleNamespace(free=int(free_gb * 1024**3))
        with mock.patch.object(doctor.paths, "data_dir", return_value=target or self.tmp), \
                mock.patch.object(doctor.shutil, "disk_usage", return_value=usage) as used:
            return doctor._disk(), used

    def test_the_thresholds(self):
        for free, status in ((2, FAIL), (3, WARN), (9.9, WARN), (10, OK), (200, OK)):
            self.assertEqual(self.check(free)[0].status, status, free)

    def test_a_data_folder_that_does_not_exist_yet_is_measured_by_its_nearest_parent(self):
        result, used = self.check(50, target=self.tmp / "not" / "there" / "yet")
        self.assertEqual(result.status, OK)
        used.assert_called_once_with(self.tmp)


class Networking(Folder):
    def effective(self, answer):
        with mock.patch.object(wsl, "effective_networking_mode", return_value=answer):
            return doctor._effective_networking(self.cfg)

    def test_mirrored_for_real(self):
        self.assertEqual(self.effective(("mirrored", "")).status, OK)

    def test_a_wsl_that_did_not_answer_is_a_warning_with_the_reason(self):
        result = self.effective((None, "no answer from WSL within 60 s"))
        self.assertEqual(result.status, WARN)
        self.assertIn("no answer from WSL", result.detail)
        self.assertIn("wsl --shutdown", result.hint)

    def test_a_wsl_that_cannot_say_is_trusted_with_its_wslconfig(self):
        result = self.effective((None, ""))
        self.assertEqual(result.status, OK)
        self.assertIn("cannot report", result.detail)

    def test_a_fallback_is_a_failure_that_says_which_mode_and_why(self):
        result = self.effective(("none", "Failed to configure network [0x80070005]"))
        self.assertEqual(result.status, FAIL)
        self.assertIn("networkingMode=none", result.detail)
        self.assertIn("0x80070005", result.detail)
        self.assertIn("Restart-Service hns", result.hint)

    def test_on_a_windows_too_old_for_mirrored_networking_the_hint_says_so(self):
        with mock.patch.object(doctor, "_windows_build", return_value=19045):
            result = self.effective(("nat", ""))
        self.assertEqual(result.status, FAIL)
        self.assertIn("build 19045", result.hint)
        self.assertIn("too old for mirrored networking", result.hint)
        self.assertNotIn("Restart-Service", result.hint)

    def test_on_a_new_enough_windows_or_one_that_cannot_say_the_hint_is_the_restart_advice(self):
        for build in (26300, None):
            with mock.patch.object(doctor, "_windows_build", return_value=build):
                self.assertIn("Restart-Service hns", self.effective(("nat", "")).hint)

    def test_a_wsl_that_did_not_answer_gets_the_shared_hint(self):
        self.assertEqual(self.effective((None, "no answer")).hint, doctor.NO_ANSWER_HINT)

    def test_a_fallback_without_a_reason_is_still_a_failure(self):
        result = self.effective(("nat", ""))
        self.assertEqual(result.status, FAIL)
        self.assertNotIn("(", result.detail)


class Resources(Folder):
    def check(self, output, *, memory="", processors=0, effective=0, settings=None):
        self.cfg.wsl.memory, self.cfg.wsl.processors = memory, processors
        with mock.patch.object(wsl, "run", return_value=subprocess.CompletedProcess([], 0, output, "")), \
                mock.patch.object(wsl, "effective_processors", return_value=effective), \
                mock.patch.object(wsl, "wsl2_setting", side_effect=lambda name, *a: (settings or {}).get(name)):
            return doctor._wsl_resources(self.cfg)

    def test_without_caps_what_the_vm_has_is_just_reported(self):
        (result,) = self.check("8\n16000000\n")
        self.assertEqual(result.status, OK)
        self.assertIn("8 CPU(s)", result.detail)
        self.assertIn("no caps set", result.detail)

    def test_a_vm_within_its_caps_is_fine(self):
        (result,) = self.check("4\n4000000\n", memory="4GB", processors=4, effective=4)
        self.assertEqual(result.status, OK)

    def test_a_vm_over_its_caps_that_is_not_restarted_says_to_shut_wsl_down(self):
        (result,) = self.check("16\n32000000\n", memory="4GB", processors=4, effective=4,
                               settings={"processors": "4", "memory": "4GB"})
        self.assertEqual(result.status, WARN)
        self.assertIn("run  wsl --shutdown", result.hint)
        self.assertNotIn("wsl-config", result.hint)

    def test_a_vm_over_caps_that_were_never_written_says_to_write_them(self):
        (result,) = self.check("16\n32000000\n", memory="4GB", processors=4, effective=4)
        self.assertEqual(result.status, WARN)
        self.assertIn("mrrsim wsl-config", result.hint)

    def test_too_many_processors_alone_is_enough(self):
        (result,) = self.check("16\n4000000\n", memory="4GB", processors=4, effective=4)
        self.assertEqual(result.status, WARN)

    def test_a_little_more_memory_than_asked_for_is_the_kernels_share(self):
        (result,) = self.check("4\n4300000\n", memory="4GB", processors=4, effective=4)  # 4199 MB, under 4096 * 1.1
        self.assertEqual(result.status, OK)

    def test_output_that_cannot_be_read_is_a_warning(self):
        for output in ("", "8\n", "eight\nmany\n"):
            (result,) = self.check(output)
            self.assertEqual(result.status, WARN, output)


class Distro(Folder):
    def check(self, distros, mode="mirrored", problem="", **patches):
        resources = patches.get("resources", [Check("WSL resources", OK, "fine")])
        with mock.patch.object(wsl, "query_distros", return_value=(distros, problem)), \
                mock.patch.object(wsl, "networking_mode", return_value=mode), \
                mock.patch.object(wsl, "wslconfig_path", return_value=Path("C:/u/.wslconfig")), \
                mock.patch.object(doctor, "_wsl_resources", return_value=resources) as resources_check, \
                mock.patch.object(doctor, "_effective_networking", return_value=Check("WSL networking", OK, "real")):
            checks, ready = doctor._wsl(self.cfg)
        return checks, ready, resources_check

    def test_a_missing_distro_names_what_is_installed_and_blocks_podman(self):
        checks, ready, resources = self.check([wsl.Distro("Ubuntu", "Stopped", 2, False)])
        self.assertFalse(ready)
        self.assertEqual((checks[0].status, checks[0].name), (FAIL, "WSL distro 'mrr'"))
        self.assertIn("installed: Ubuntu", checks[0].detail)
        resources.assert_not_called()

    def test_a_wsl_that_did_not_answer_is_not_reported_as_not_installed(self):
        checks, ready, resources = self.check([], problem="no answer from WSL within 30 s")
        self.assertFalse(ready)
        self.assertEqual((checks[0].status, checks[0].name), (FAIL, "WSL distro 'mrr'"))
        self.assertIn("WSL did not answer (no answer from WSL within 30 s)", checks[0].detail)
        self.assertNotIn("not installed", checks[0].detail)
        self.assertEqual(checks[0].hint, doctor.NO_ANSWER_HINT)
        resources.assert_not_called()

    def test_no_distros_at_all(self):
        checks, ready, _ = self.check([])
        self.assertIn("installed: none found", checks[0].detail)

    def test_a_wsl_1_distro_fails_with_the_conversion_command(self):
        checks, ready, _ = self.check([wsl.Distro("mrr", "Running", 1, False)])
        self.assertFalse(ready)
        self.assertEqual(checks[0].status, FAIL)
        self.assertIn("wsl --set-version mrr 2", checks[0].hint)

    def test_a_usable_distro_gets_its_resources_and_networking_checked(self):
        checks, ready, resources = self.check([wsl.Distro("mrr", "Running", 2, True)])
        self.assertTrue(ready)
        self.assertEqual([c.name for c in checks], ["WSL distro 'mrr'", "WSL resources", "WSL networking"])
        self.assertEqual(checks[0].detail, "WSL2, Running")
        resources.assert_called_once()

    def test_mirrored_without_a_usable_distro_cannot_be_checked_for_real(self):
        checks, ready, _ = self.check([])
        self.assertEqual(checks[-1].status, OK)
        self.assertIn("not checked", checks[-1].detail)

    def test_another_networking_mode_fails_when_mirrored_is_required_and_warns_when_not(self):
        distros = [wsl.Distro("mrr", "Running", 2, True)]
        checks, _, _ = self.check(distros, mode="nat")
        self.assertEqual((checks[-1].status, checks[-1].detail), (FAIL, "networkingMode=nat"))
        self.assertIn(".wslconfig", checks[-1].hint)
        self.cfg.wsl.require_mirrored = False
        checks, _, _ = self.check(distros, mode="nat")
        self.assertEqual(checks[-1].status, WARN)


class Podman(Folder):
    def podman(self, version="podman version 4.9.3", rootless=True, init=True, images=()):
        return mock.Mock(version=mock.Mock(return_value=version), rootless=mock.Mock(return_value=rootless),
                         has_init_binary=mock.Mock(return_value=init),
                         image_exists=mock.Mock(side_effect=lambda ref: ref in images))

    def check(self, podman, scene=True, script=True):
        return doctor._podman(self.cfg, podman, scene=scene, script=script)

    def everything(self):
        return {self.cfg.sitl.image_ref, self.cfg.scripts.image, self.cfg.scene.image}

    def test_podman_that_is_not_there_is_the_only_thing_said(self):
        checks = self.check(self.podman(version=None))
        self.assertEqual([(c.name, c.status) for c in checks], [("podman", FAIL)])
        self.assertIn("apt install podman", checks[0].hint)

    def test_a_good_setup_is_all_ok(self):
        checks = self.check(self.podman(images=self.everything()))
        self.assertTrue(all(c.status == OK for c in checks), checks)
        self.assertEqual(checks[0].detail, "podman version 4.9.3")

    def test_running_as_root_and_a_missing_init_program_are_warnings(self):
        checks = self.check(self.podman(rootless=False, init=False, images=self.everything()))
        self.assertEqual({c.name: c.status for c in checks if c.status == WARN}, {"podman mode": WARN, "catatonit": WARN})

    def test_the_init_program_is_not_asked_for_when_init_is_off(self):
        self.cfg.wsl.use_init = False
        podman = self.podman(init=False, images=self.everything())
        self.check(podman)
        podman.has_init_binary.assert_not_called()

    def test_a_missing_image_fails_with_the_command_that_builds_it(self):
        checks = {c.name: c for c in self.check(self.podman(images={self.cfg.sitl.image_ref}))}
        self.assertEqual(checks["SITL image"].status, OK)
        self.assertEqual(checks["script image"].status, FAIL)
        self.assertIn("mrrsim images build script", checks["script image"].hint)
        self.assertEqual(checks["scene-loader image"].status, FAIL)

    def test_the_sitl_image_is_built_with_its_own_command(self):
        checks = {c.name: c for c in self.check(self.podman(images=set()))}
        self.assertIn("mrrsim images build sitl", checks["SITL image"].hint)

    def test_an_image_that_two_things_need_is_checked_once(self):
        self.cfg.scene.image = self.cfg.scripts.image
        names = [c.name for c in self.check(self.podman(images=self.everything()))]
        self.assertEqual(names.count("script image"), 1)
        self.assertNotIn("scene-loader image", names)

    def test_a_wsl_that_stops_answering_is_not_podman_saying_it_is_missing(self):
        podman = self.podman(images=self.everything())
        podman.version.side_effect = PodmanError("podman --version: no answer from WSL within 30 s")
        checks = self.check(podman)
        self.assertEqual([(c.name, c.status) for c in checks], [("podman", FAIL)])
        self.assertIn("WSL did not answer (podman --version: no answer", checks[0].detail)
        self.assertNotIn("not found", checks[0].detail)
        self.assertEqual(checks[0].hint, doctor.NO_ANSWER_HINT)

    def test_an_image_question_that_gets_no_answer_is_not_an_image_that_is_not_built(self):
        podman = self.podman(images=self.everything())
        podman.image_exists.side_effect = PodmanError("podman image: no answer from WSL within 30 s")
        checks = self.check(podman)
        self.assertEqual([c.status for c in checks], [OK, FAIL])  # what was learned first is kept
        self.assertTrue(all("is not built" not in c.detail for c in checks))
        self.assertIn("WSL did not answer", checks[-1].detail)

    def test_the_questions_are_asked_at_the_same_time(self):
        def slow(value):
            def answer(*args):
                time.sleep(0.3)
                return value
            return answer

        podman = self.podman(images=self.everything())
        podman.rootless.side_effect = slow(True)
        podman.has_init_binary.side_effect = slow(True)
        podman.image_exists.side_effect = slow(True)
        started = time.monotonic()
        checks = self.check(podman)
        self.assertTrue(all(c.status == OK for c in checks), checks)
        self.assertLess(time.monotonic() - started, 0.9)  # five questions of 0.3 s: 1.5 s one after another

    def test_the_results_keep_their_order_whichever_question_answers_first(self):
        def after(seconds, value):
            def answer(*args):
                time.sleep(seconds)
                return value
            return answer

        podman = self.podman(rootless=False, init=False, images=set())
        podman.rootless.side_effect = after(0.3, False)
        podman.has_init_binary.side_effect = after(0.2, False)
        names = [c.name for c in self.check(podman)]
        self.assertEqual(names, ["podman", "podman mode", "catatonit", "SITL image", "script image", "scene-loader image"])

    def test_images_nobody_needs_are_not_asked_about(self):
        podman = self.podman(images=self.everything())
        names = [c.name for c in self.check(podman, scene=False, script=False)]
        self.assertEqual([n for n in names if "image" in n], ["SITL image"])


class Unreal(Folder):
    def exe(self, paks=0):
        folder = self.tmp / "Sim" / "Binaries" / "Win64"
        folder.mkdir(parents=True)
        exe = folder / "Sim-Win64-Shipping.exe"
        exe.write_bytes(b"x" * 2048)
        if paks:
            content = self.tmp / "Sim" / "Content" / "Paks"
            content.mkdir(parents=True)
            for n in range(paks):
                (content / f"p{n}.pak").write_bytes(b"")
        self.cfg.unreal.exe = exe
        return exe

    def test_a_missing_build_fails_with_the_setting_to_change(self):
        self.cfg.unreal.exe = self.tmp / "nothing.exe"
        (result,) = doctor._unreal(self.cfg)
        self.assertEqual(result.status, FAIL)
        self.assertIn("[unreal] exe", result.hint)

    def test_a_build_with_content_is_fine(self):
        self.exe(paks=3)
        checks = doctor._unreal(self.cfg)
        self.assertEqual([(c.name, c.status) for c in checks], [("Unreal build", OK), ("Unreal content", OK)])
        self.assertEqual(checks[1].detail, "3 pak file(s)")

    def test_a_build_without_its_content_is_a_warning(self):
        self.exe()
        checks = doctor._unreal(self.cfg)
        self.assertEqual((checks[1].name, checks[1].status), ("Unreal content", WARN))

    def test_an_exe_too_shallow_to_have_a_content_folder_is_not_asked_for_one(self):
        self.cfg.unreal.exe = SimpleNamespace(  # something like C:\x.exe: only two parents
            is_file=lambda: True, name="x.exe", stat=lambda: SimpleNamespace(st_size=1024**2), parents=[Path("a"), Path("b")]
        )
        checks = doctor._unreal(self.cfg)
        self.assertEqual([(c.name, c.detail) for c in checks], [("Unreal build", "x.exe, 1 MB")])


class SceneAndScript(Folder):
    def scenes(self, vehicles=2):
        for name in (BASE, ROBOT):
            shutil.copy(paths.repo_root() / "sim_config" / name, self.tmp / name)
        self.cfg.scene.config_dir = self.tmp
        self.cfg.sitl.vehicles = vehicles
        self.cfg.scene.scene = scenegen.generate(self.tmp, BASE, vehicles)

    def test_an_empty_scene_setting_is_a_warning_not_an_error(self):
        self.cfg.scene.scene = ""
        (result,) = doctor._scene(self.cfg)
        self.assertEqual(result.status, WARN)

    def test_a_scene_file_that_is_not_there_fails(self):
        self.cfg.scene.config_dir, self.cfg.scene.scene = self.tmp, "gone.jsonc"
        (result,) = doctor._scene(self.cfg)
        self.assertEqual(result.status, FAIL)
        self.assertIn("gone.jsonc", result.detail)

    def test_a_scene_with_the_right_robots_is_fine(self):
        self.scenes(2)
        (result,) = doctor._scene(self.cfg)
        self.assertEqual(result.status, OK)
        self.assertIn("2 ArduPilot robot(s)", result.detail)

    def test_a_home_point_that_is_not_the_scenes_is_a_warning_with_the_fix(self):
        self.scenes(2)
        self.cfg.sitl.home = "37.9490953,-91.7848293,0,90"
        scene, home = doctor._scene(self.cfg)
        self.assertEqual((scene.status, home.name, home.status), (OK, "scene home", WARN))
        self.assertIn('[sitl] home = "37.94894101,-91.78455965,15,90"', home.detail)

    def test_a_scene_for_another_number_of_vehicles_fails_and_says_how_to_fix_it(self):
        self.scenes(2)
        self.cfg.sitl.vehicles = 3
        (result,) = doctor._scene(self.cfg)
        self.assertEqual(result.status, FAIL)
        self.assertIn("does not match 3 vehicle(s)", result.detail)
        self.assertIn("mrrsim up --vehicles N", result.hint)

    def test_a_scene_that_cannot_be_read_is_a_warning(self):
        broken = self.tmp / "broken.jsonc"
        broken.write_text("{ not json", encoding="utf-8")
        self.cfg.scene.config_dir, self.cfg.scene.scene = self.tmp, "broken.jsonc"
        (result,) = doctor._scene(self.cfg)
        self.assertEqual(result.status, WARN)
        self.assertIn("broken.jsonc", result.detail)

    def test_the_script(self):
        self.cfg.scripts.dir = self.tmp
        self.cfg.scripts.main = ""
        self.assertEqual(doctor._script(self.cfg)[0].status, WARN)
        self.cfg.scripts.main = "fly.py"
        (result,) = doctor._script(self.cfg)
        self.assertEqual(result.status, FAIL)
        self.assertIn("--script", result.hint)
        (self.tmp / "fly.py").write_text("print(1)\n", encoding="utf-8")
        (result,) = doctor._script(self.cfg)
        self.assertEqual((result.status, result.detail), (OK, str(self.tmp / "fly.py")))


class Ports(Folder):
    def check(self, busy=(), who=None, unreal=True, vehicles=1):
        self.cfg.sitl.vehicles = vehicles
        with mock.patch.object(doctor.ports, "busy_ports", return_value=list(busy)) as busy_ports, \
                mock.patch.object(doctor.ports, "who_uses", return_value=who or {}):
            return doctor._ports(self.cfg, unreal), busy_ports

    def test_free_ports_are_counted(self):
        result, busy_ports = self.check()
        wanted = busy_ports.call_args.args[0]
        self.assertEqual((result.status, result.detail), (OK, f"{len(wanted)} ports free"))
        self.assertIn((self.cfg.unreal.topic_port, "tcp"), wanted)
        self.assertIn((self.cfg.unreal.service_port, "tcp"), wanted)

    def test_unreals_ports_are_not_needed_when_unreal_is_not_started(self):
        _, with_unreal = self.check(unreal=True)
        _, without = self.check(unreal=False)
        self.assertEqual(len(with_unreal.call_args.args[0]) - len(without.call_args.args[0]), 2)

    def test_every_vehicle_needs_its_own_ports(self):
        _, one = self.check(unreal=False, vehicles=1)
        _, three = self.check(unreal=False, vehicles=3)
        self.assertEqual(len(three.call_args.args[0]), 3 * len(one.call_args.args[0]))

    def test_a_busy_port_fails_and_says_what_needed_it_and_who_has_it(self):
        sensors = doctor.ports.vehicle_ports(0).sim_in
        result, _ = self.check(busy=[(sensors, "udp")], who={(sensors, "udp"): "Discord.exe (pid 12)"})
        self.assertEqual(result.status, FAIL)
        self.assertIn(f"udp/{sensors} (needed by vehicle 0's sensors) is used by Discord.exe (pid 12)", result.detail)
        self.assertIn("close that program", result.hint)

    def test_a_port_held_by_our_own_leftovers_points_to_mrrsim_down(self):
        port = self.cfg.unreal.topic_port
        for owner in ("Simulation-Win64-Shipping.exe (pid 5)", "wslrelay.exe (pid 6)", "python.exe (pid 7)"):
            result, _ = self.check(busy=[(port, "tcp")], who={(port, "tcp"): owner})
            self.assertIn("mrrsim down", result.hint, owner)

    def test_a_busy_port_with_no_known_owner_is_still_listed(self):
        port = self.cfg.unreal.service_port
        result, _ = self.check(busy=[(port, "tcp")])
        self.assertEqual(result.status, FAIL)
        self.assertIn(f"tcp/{port}", result.detail)
        self.assertNotIn("is used by", result.detail)


class Running(Folder):
    def patched(self, **returns):
        stand_ins = {
            "_windows": Check("Windows", OK, ""), "_ram": Check("RAM", OK, ""), "_gpu": Check("GPU", OK, ""),
            "_disk": Check("Disk", OK, ""), "_wsl": ([Check("WSL distro", OK, "")], True),
            "_podman": [Check("podman", OK, "")], "_unreal": [Check("Unreal build", OK, "")],
            "_scene": [Check("scene", OK, "")], "_script": [Check("script", OK, "")], "_ports": Check("ports", OK, ""),
        }
        stand_ins.update(returns)
        self.mocks = {name: mock.Mock(return_value=value) for name, value in stand_ins.items()}
        return mock.patch.multiple(doctor, **self.mocks)

    def names(self, **kwargs):
        with self.patched(), mock.patch.object(doctor, "Podman"):
            return [c.name for c in doctor.run_checks(self.cfg, **kwargs)]

    def test_every_check_in_report_order(self):
        self.assertEqual(self.names(), ["Windows", "RAM", "GPU", "Disk", "WSL distro", "podman", "Unreal build", "scene",
                                        "script", "ports"])

    def test_what_a_run_does_not_need_is_not_checked(self):
        names = self.names(unreal=False, scene=False, script=False)
        self.assertEqual(names, ["Windows", "RAM", "GPU", "Disk", "WSL distro", "podman", "ports"])

    def test_podman_is_not_asked_when_the_distro_is_not_usable(self):
        with self.patched(_wsl=([Check("WSL distro", FAIL, "")], False)) as patched, mock.patch.object(doctor, "Podman"):
            names = [c.name for c in doctor.run_checks(self.cfg)]
        self.assertNotIn("podman", names)
        self.mocks["_podman"].assert_not_called()

    def test_the_given_podman_is_used_and_none_is_made(self):
        given = mock.Mock()
        with self.patched(), mock.patch.object(doctor, "Podman") as made:
            doctor.run_checks(self.cfg, podman=given)
        made.assert_not_called()
        self.assertIs(self.mocks["_podman"].call_args.args[1], given)

    def test_the_scene_images_are_only_wanted_when_there_is_a_scene_to_load(self):
        self.cfg.scene.scene = ""
        with self.patched(), mock.patch.object(doctor, "Podman"):
            doctor.run_checks(self.cfg)
        self.assertIs(self.mocks["_podman"].call_args.kwargs["scene"], False)
        self.cfg.scene.scene = "x.jsonc"
        with self.patched(), mock.patch.object(doctor, "Podman"):
            doctor.run_checks(self.cfg)
        self.assertIs(self.mocks["_podman"].call_args.kwargs["scene"], True)


class Report(unittest.TestCase):
    def test_a_line_per_check_with_the_hint_under_what_is_not_ok(self):
        text = doctor.format_report([
            Check("RAM", OK, "32 GB", "ignored: it is fine"),
            Check("Disk", WARN, "5 GB free", "free some space"),
            Check("GPU", FAIL, "none", "install the driver"),
        ])
        self.assertEqual(text.splitlines()[:5], [
            "[ OK ] RAM: 32 GB",
            "[WARN] Disk: 5 GB free",
            "         -> free some space",
            "[FAIL] GPU: none",
            "         -> install the driver",
        ])
        self.assertTrue(text.endswith("3 checks: 1 failed, 1 warnings"))

    def test_a_check_without_a_hint_has_no_hint_line(self):
        self.assertEqual(doctor.format_report([Check("RAM", FAIL, "1 GB")]).splitlines()[:2],
                         ["[FAIL] RAM: 1 GB", ""])

    def test_the_count_line_is_what_the_window_reads(self):
        from mrrsim.ui.run import commands
        text = doctor.format_report([Check("a", OK, ""), Check("b", FAIL, ""), Check("c", WARN, ""), Check("d", WARN, "")])
        self.assertEqual(commands.DOCTOR_SUMMARY.search(text).groups(), ("4", "1", "2"))


if __name__ == "__main__":
    unittest.main()
