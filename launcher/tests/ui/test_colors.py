"""ui/common/colors.py is the one place the window's colors are written: no other file of the window has a color of
its own, and every color it names is used."""
import re
import unittest

from mrrsim.core import paths
from mrrsim.ui.common import colors

UI = paths.repo_root() / "launcher" / "mrrsim" / "ui"
PALETTE = UI / "common" / "colors.py"
# a color written as text: "#rgb" / "#rrggbb", or a color name given to an option that takes a color
HEX = re.compile(r"""["']#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?["']""")
NAMED = re.compile(r"""\b(?:fg|bg|foreground|background|highlightbackground|highlightcolor|insertbackground|selectbackground|"""
                   r"""selectforeground|disabledforeground|activebackground|activeforeground|troughcolor|fill|outline)"""
                   r"""\s*=\s*["'](?!["'])(?!nsew|both|x|y|none)[A-Za-z]""")


def ui_files():
    return [p for p in sorted(UI.rglob("*.py")) if p != PALETTE]


class Palette(unittest.TestCase):
    def test_no_window_file_writes_a_color_of_its_own(self):
        found = []
        for path in ui_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if HEX.search(line) or NAMED.search(line):
                    found.append(f"{path.relative_to(UI)}:{number}: {line.strip()}")
        self.assertEqual(found, [], "use a color of ui/common/colors.py instead")

    def test_every_color_of_the_palette_is_used(self):
        names = [n for n in vars(colors) if n.isupper()]
        text = "\n".join(p.read_text(encoding="utf-8") for p in ui_files())
        unused = [n for n in names if f"colors.{n}" not in text]
        self.assertEqual(unused, [], "colors nothing uses")

    def test_every_color_is_a_hex_color(self):
        for name in (n for n in vars(colors) if n.isupper()):
            self.assertRegex(getattr(colors, name), r"^#[0-9a-f]{6}$", name)


if __name__ == "__main__":
    unittest.main()
