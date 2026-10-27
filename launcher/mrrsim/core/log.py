"""Console + per-session file logging."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# === CONSTANTS ================================================================


LOG = logging.getLogger("mrrsim")
LOG.setLevel(logging.DEBUG)  # the handlers choose what is shown
LOG.propagate = False  # only our own handlers print, whatever the root logger has

_CONSOLE_FMT = "%(asctime)s %(levelname)-5s %(message)s"
_FILE_FMT = "%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s"

_console: logging.Handler | None = None  # the one console handler, once set up


# === CONSOLE ==================================================================


def setup(verbose: bool = False) -> None:
    """Show the log on the console: INFO and above, or everything with
    `verbose`. Calling it again does not add a second handler; it only changes
    the level."""
    global _console
    if _console is None or _console not in LOG.handlers:
        _console = logging.StreamHandler(sys.stdout)
        _console.setFormatter(logging.Formatter(_CONSOLE_FMT, datefmt="%H:%M:%S"))
        LOG.addHandler(_console)
    _console.setLevel(logging.DEBUG if verbose else logging.INFO)


# === SESSION LOG FILE =========================================================


def attach_file(path: Path) -> logging.Handler:
    """Also write everything (including DEBUG) to `path`. Returns the handler
    so it can be passed to `detach`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(_FILE_FMT, datefmt="%Y-%m-%d %H:%M:%S"))
    LOG.addHandler(handler)
    return handler


def detach(handler: logging.Handler) -> None:
    """Stop writing to `handler` (the one `attach_file` returned) and close its
    file."""
    LOG.removeHandler(handler)
    handler.close()
