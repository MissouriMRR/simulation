"""
Entry point of `python -m mrrsim` (also mrrsim.cmd), which runs the CLI.
"""

import sys

from .cli import main

sys.exit(main())
