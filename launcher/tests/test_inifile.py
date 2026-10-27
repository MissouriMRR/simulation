"""Setting keys in an .ini file: only the named keys change."""

import unittest

from mrrsim.core import inifile


class SetKeys(unittest.TestCase):
    def test_missing_keys_are_added_to_the_section_and_the_rest_is_kept(self):
        text = "# note\n[a]\nx=1\n\n[wsl2]\nmemory=4GB\n\n[b]\ny=2\n"
        out = inifile.set_keys(text, "wsl2", {"processors": 2})
        self.assertEqual(
            out, "# note\n[a]\nx=1\n\n[wsl2]\nmemory=4GB\nprocessors=2\n\n[b]\ny=2\n"
        )

    def test_a_key_is_replaced_in_place_whatever_its_capitalisation(self):
        out = inifile.set_keys(
            "[wsl2]\nMemory = 4GB\nswap=0\n", "wsl2", {"memory": "8GB"}
        )
        self.assertEqual(out, "[wsl2]\nmemory=8GB\nswap=0\n")

    def test_a_missing_section_is_created_after_a_blank_line(self):
        out = inifile.set_keys("[a]\nx=1\n", "wsl2", {"memory": "8GB"})
        self.assertEqual(out, "[a]\nx=1\n\n[wsl2]\nmemory=8GB\n")

    def test_empty_text_gives_just_the_section(self):
        self.assertEqual(inifile.set_keys("", "wsl2", {"a": 1}), "[wsl2]\na=1\n")

    def test_a_commented_out_key_is_left_and_the_real_one_is_added(self):
        out = inifile.set_keys("[s]\n#k=1\n;k=2\n", "s", {"k": 3})
        self.assertEqual(out, "[s]\n#k=1\n;k=2\nk=3\n")

    def test_the_same_key_in_another_section_is_not_touched(self):
        out = inifile.set_keys("[a]\nk=1\n[b]\nk=2\n", "b", {"k": 9})
        self.assertEqual(out, "[a]\nk=1\n[b]\nk=9\n")

    def test_the_section_name_matches_whatever_its_capitalisation(self):
        out = inifile.set_keys("[WSL2]\nk=1\n", "wsl2", {"k": 2})
        self.assertEqual(out, "[WSL2]\nk=2\n")

    def test_a_comment_after_the_section_header_does_not_hide_it(self):
        out = inifile.set_keys("[wsl2] # my limits\nk=1\n", "wsl2", {"k": 2, "j": 3})
        self.assertEqual(out, "[wsl2] # my limits\nk=2\nj=3\n")

    def test_windows_line_endings_stay_windows_line_endings(self):
        out = inifile.set_keys("[s]\r\nk=1\r\n", "s", {"k": 2, "j": 3})
        self.assertEqual(out, "[s]\r\nk=2\r\nj=3\r\n")

    def test_unix_line_endings_stay_unix_line_endings(self):
        self.assertNotIn("\r", inifile.set_keys("[s]\nk=1\n", "s", {"j": 3}))

    def test_running_it_twice_changes_nothing(self):
        values = {"memory": "8GB", "swap": 0}
        once = inifile.set_keys("[a]\nx=1\n", "wsl2", values)
        self.assertEqual(inifile.set_keys(once, "wsl2", values), once)


if __name__ == "__main__":
    unittest.main()
