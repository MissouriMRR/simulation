"""Every color the window uses, named by what it is for. The tabs and widgets take their colors from here and nowhere
else (test_colors.py checks that no other file of the window writes a color of its own)."""

# ---------------------------------------------------------------------------------------------------- text
TEXT = "#1a1a1a"            # the big numbers in the value boxes
MUTED = "#5e5c64"           # hints, notes and other secondary text
PLACEHOLDER = "#8a8a8a"     # the gray hint inside an empty field
SCRIPT = "#1a5fb4"          # lines the script printed (blue)
ERROR = "#c01c28"           # errors, the Kill Unreal button, a setting with an error (red)
WARNING = "#b25000"         # warnings (amber)
OK = "#26a269"              # checks that passed (green)

# ------------------------------------------------------------------------------------------------- surfaces
SURFACE = "#ffffff"         # the value boxes and the inside of a field (white)
BORDER = "#c4c4c4"          # the thin frame around the output and the value boxes
PANEL_FALLBACK = "#f0f0f0"  # the panel color when the theme does not say (Windows' own)
DRAG_GHOST = "#fff8c5"      # the label that follows the pointer while a part is dragged (pale yellow)

# --------------------------------------------------------------------------------------------- Simulator tab
SIM_BACKGROUND = "#202020"  # behind the simulator shown inside the window
SIM_NOTE = "#b0b0b0"        # the note shown there before the simulator appears
FLYING_DOT = "#2ec27e"      # the dot that shows the free camera took the keyboard (green)
OVERLAY_KEY = "#ff00ff"     # see-through in that dot's window (never shown)
