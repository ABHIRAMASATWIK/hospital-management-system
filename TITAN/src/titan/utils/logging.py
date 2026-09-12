"""Centralized logging configuration.

Provides a single ``setup_logging()`` entrypoint (called once at startup) and a
``get_logger()`` helper so every module obtains a consistently-named logger.
Also fixes UTF-8 console output on Windows, which the original agent handled
inline.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def _ensure_utf8_streams() -> None:
    """Reconfigure stdout/stderr to UTF-8 on Windows.

    The default Windows console encoding can raise ``UnicodeEncodeError`` when
    logging non-ASCII transcripts (e.g. Hindi text from Sarvam STT).
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once for the whole process.

    Args:
        level: Logging level name (e.g. ``"INFO"``, ``"DEBUG"``).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    _ensure_utf8_streams()
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger.

    Args:
        name: Logger name, typically the module or component name.
    """
    return logging.getLogger(name)
