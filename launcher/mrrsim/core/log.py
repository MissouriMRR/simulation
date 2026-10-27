"""Console + per-session file logging."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# === CONSTANTS ================================================================


LOG = logging.getLogger("mrrsim")

_CONSOLE_FMT = "%(asctime)s %(levelname)-5s %(message)s"
_FILE_FMT = "%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s"


# === CONSOLE ==================================================================


def setup(verbose: bool = False) -> None:
    """Install the console handler (idempotent)."""
    LOG.setLevel(logging.DEBUG)
    if any(getattr(h, "_mrrsim_console", False) for h in LOG.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler._mrrsim_console = True  # type: ignore[attr-defined]
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler.setFormatter(logging.Formatter(_CONSOLE_FMT, datefmt="%H:%M:%S"))
    LOG.addHandler(handler)


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
