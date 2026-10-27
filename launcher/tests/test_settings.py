import tempfile
import unittest
from pathlib import Path

from mrrsim.core import config
from mrrsim.runtime import wsl


def load(text: str) -> config.Config:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mrrsim.toml"
        path.write_text(text, encoding="utf-8")
        return config.load(path)


class FileFormat(unittest.TestCase):
    def test_a_byte_order_mark_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mrrsim.toml"
            path.write_bytes(b'\xef\xbb\xbf[wsl]\ndistro = "other"\n')
            self.assertEqual(config.load(path).wsl.distro, "other")

    def test_a_file_that_is_not_text_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mrrsim.toml"
            path.write_bytes(b"\xff\xfe[\x00w\x00")
            with self.assertRaises(config.ConfigError):
                config.load(path)


class UnrealArgs(unittest.TestCase):
    def test_defaults_are_windowed_without_vsync(self):
        self.assertEqual(
            load("").unreal.launch_args, ["-windowed", "-ResX=1280", "-ResY=720", "-NoVSync", "-nosplash"]
        )

    def test_smaller_window_fullscreen_and_vsync(self):
        cfg = load('[unreal]\nwidth = 640\nheight = 360\nwindowed = false\nvsync = true\nargs = ["-x"]')
        self.assertEqual(cfg.unreal.launch_args, ["-fullscreen", "-ResX=640", "-ResY=360", "-x"])

    def test_close_grace(self):
        self.assertEqual(load("").unreal.close_grace_s, 4.0)
        self.assertEqual(load("[unreal]\nclose_grace_s = 7").unreal.close_grace_s, 7)
        for text in ("[unreal]\nclose_grace_s = 0", '[unreal]\nclose_grace_s = "soon"'):
            with self.assertRaises(config.ConfigError, msg=text):
                load(text)

    def test_rejects_silly_sizes_and_priorities(self):
        for text in ("[unreal]\nwidth = 10", '[unreal]\nheight = "big"', '[unreal]\npriority = "turbo"'):
            with self.assertRaises(config.ConfigError, msg=text):
                load(text)


class Priorities(unittest.TestCase):
    def test_every_priority_the_settings_accept_has_a_windows_class(self):
        from mrrsim.runtime import unreal

        self.assertEqual(set(config.UNREAL_PRIORITIES), set(unreal.PRIORITY_CLASSES))

    def test_the_five_priorities_are_accepted_and_ordered_low_to_high(self):
        self.assertEqual(
            config.UNREAL_PRIORITIES,
            ("low", "below_normal", "normal", "above_normal", "high"),
        )
        with self.assertRaises(config.ConfigError):  # too dangerous to offer: it can freeze Windows
            load('[unreal]\npriority = "realtime"')
        for name in config.UNREAL_PRIORITIES:
            self.assertEqual(load(f'[unreal]\npriority = "{name}"').unreal.priority, name)

    def test_the_windows_flags_are_the_real_priority_classes(self):
        from mrrsim.runtime import unreal

        self.assertEqual(
            unreal.PRIORITY_CLASSES,
            {
                "low": 0x40,           # IDLE_PRIORITY_CLASS
                "below_normal": 0x4000,  # BELOW_NORMAL_PRIORITY_CLASS
                "normal": 0,           # none given: Windows uses NORMAL_PRIORITY_CLASS
                "above_normal": 0x8000,  # ABOVE_NORMAL_PRIORITY_CLASS
                "high": 0x80,          # HIGH_PRIORITY_CLASS
            },
        )


class WslCaps(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(config.parse_size_mb("4GB"), 4096)
        self.assertEqual(config.parse_size_mb(" 2048 mb "), 2048)
        with self.assertRaises(config.ConfigError):
            config.parse_size_mb("lots")

    def test_validation(self):
        self.assertEqual(load('[wsl]\nmemory = "6GB"\nprocessors = 3').wsl.memory_mb, 6144)
        for text in ('[wsl]\nmemory = "512MB"', '[wsl]\nmemory = "x"', "[wsl]\nprocessors = -1"):
            with self.assertRaises(config.ConfigError, msg=text):
                load(text)

    def test_processors_are_clamped_to_half_the_machine(self):
        original = wsl.logical_processors
        try:
            wsl.logical_processors = lambda: 8
            self.assertEqual(wsl.effective_processors(4), 4)
            self.assertEqual(wsl.effective_processors(16), 4)
            wsl.logical_processors = lambda: 4
            self.assertEqual(wsl.effective_processors(4), 2)
            self.assertEqual(wsl.effective_processors(0), 0)
        finally:
            wsl.logical_processors = original


class WslConfigEdit(unittest.TestCase):
    def test_adds_missing_keys_and_keeps_the_rest(self):
        self.assertEqual(
            wsl.set_wsl2_keys("[wsl2]\nnetworkingMode=mirrored\n", {"memory": "4GB", "processors": "4"}),
            "[wsl2]\nnetworkingMode=mirrored\nmemory=4GB\nprocessors=4\n",
        )

    def test_replaces_in_place_case_insensitively(self):
        text = "# mine\n[wsl2]\nMemory=8GB\n\n[experimental]\nmemory=1\n"
        self.assertEqual(
            wsl.set_wsl2_keys(text, {"memory": "4GB", "processors": "4"}),
            "# mine\n[wsl2]\nmemory=4GB\nprocessors=4\n\n[experimental]\nmemory=1\n",
        )

    def test_creates_the_section(self):
        self.assertEqual(wsl.set_wsl2_keys("", {"memory": "4GB"}), "[wsl2]\nmemory=4GB\n")
        self.assertEqual(wsl.set_wsl2_keys("[user]\nx=1\n", {"memory": "4GB"}), "[user]\nx=1\n\n[wsl2]\nmemory=4GB\n")

    def test_idempotent(self):
        once = wsl.set_wsl2_keys("[wsl2]\nnetworkingMode=mirrored\n", {"memory": "4GB"})
        self.assertEqual(wsl.set_wsl2_keys(once, {"memory": "4GB"}), once)

    def test_a_commented_out_key_is_not_replaced(self):
        self.assertEqual(
            wsl.set_wsl2_keys("[wsl2]\n#memory=8GB\n", {"memory": "4GB"}), "[wsl2]\n#memory=8GB\nmemory=4GB\n"
        )


if __name__ == "__main__":
    unittest.main()
