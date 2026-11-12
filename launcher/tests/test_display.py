import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mrrsim.core import config, inifile
from mrrsim.runtime import unreal


def cfg_for(tmp: Path, text: str = "") -> config.UnrealCfg:
    path = tmp / "mrrsim.toml"
    path.write_text(text, encoding="utf-8")
    cfg = config.load(path).unreal
    cfg.exe = tmp / "Windows" / "Simulation" / "Binaries" / "Win64" / "Simulation-Win64-Shipping.exe"
    return cfg


class DisplaySettings(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        env = mock.patch.dict("os.environ", {"LOCALAPPDATA": str(self.tmp / "local")})
        env.start()
        self.addCleanup(env.stop)

    def settings_file(self, cfg):
        return unreal.display_settings_file(cfg.exe)

    def test_the_file_is_the_projects_saved_config(self):
        cfg = cfg_for(self.tmp)
        self.assertEqual(self.settings_file(cfg), self.tmp / "local" / "Simulation" / "Saved" / "Config" / "Windows" / "GameUserSettings.ini")

    def test_windowed_mode_and_the_configured_size_are_written(self):
        cfg = cfg_for(self.tmp, "[unreal]\nwidth = 640\nheight = 360")
        unreal.apply_display_settings(cfg)
        text = self.settings_file(cfg).read_text()
        for line in ("FullscreenMode=2", "ResolutionSizeX=640", "ResolutionSizeY=360", "PreferredFullscreenMode=2",
                     "LastConfirmedFullscreenMode=2", "DesiredScreenWidth=640"):
            self.assertIn(line, text)
        self.assertIn("[/Script/Engine.GameUserSettings]", text)

    def test_fullscreen_when_asked(self):
        cfg = cfg_for(self.tmp, "[unreal]\nwindowed = false")
        unreal.apply_display_settings(cfg)
        self.assertIn("FullscreenMode=0", self.settings_file(cfg).read_text())

    def test_an_existing_file_keeps_its_other_settings_and_line_endings(self):
        cfg = cfg_for(self.tmp, "[unreal]\nwidth = 960\nheight = 540")
        path = self.settings_file(cfg)
        path.parent.mkdir(parents=True)
        path.write_bytes(
            b"[ScalabilityGroups]\r\nsg.ViewDistanceQuality=3\r\n\r\n[/Script/Engine.GameUserSettings]\r\n"
            b"FullscreenMode=1\r\nResolutionSizeX=1280\r\nResolutionSizeY=720\r\nFrameRateLimit=0.000000\r\n"
        )
        unreal.apply_display_settings(cfg)
        raw = path.read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))  # still Windows line endings throughout
        text = raw.decode()
        self.assertIn("sg.ViewDistanceQuality=3", text)
        self.assertIn("FrameRateLimit=0.000000", text)
        self.assertIn("FullscreenMode=2", text)
        self.assertNotIn("FullscreenMode=1", text)
        self.assertEqual(text.count("ResolutionSizeX="), 2)  # the plain and the LastUserConfirmed one
        self.assertIn("ResolutionSizeX=960", text)

    def test_running_it_twice_changes_nothing(self):
        cfg = cfg_for(self.tmp)
        unreal.apply_display_settings(cfg)
        first = self.settings_file(cfg).read_bytes()
        unreal.apply_display_settings(cfg)
        self.assertEqual(self.settings_file(cfg).read_bytes(), first)

    def test_a_file_that_cannot_be_written_is_a_warning_not_a_failure(self):
        cfg = cfg_for(self.tmp)
        with mock.patch("builtins.open", side_effect=PermissionError("denied")), \
                self.assertLogs("mrrsim", level="WARNING") as logs:
            unreal.apply_display_settings(cfg)
        self.assertIn("could not update Unreal's display settings", logs.output[0])


class IniEditing(unittest.TestCase):
    def test_new_section_is_appended(self):
        self.assertEqual(inifile.set_keys("[a]\nx=1\n", "b", {"y": 2}), "[a]\nx=1\n\n[b]\ny=2\n")

    def test_section_names_match_without_regard_to_case(self):
        self.assertEqual(inifile.set_keys("[WSL2]\nMemory=8GB\n", "wsl2", {"memory": "4GB"}), "[WSL2]\nmemory=4GB\n")


if __name__ == "__main__":
    unittest.main()
