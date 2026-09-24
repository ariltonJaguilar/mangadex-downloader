"""
Logging utility - disabled by default, can be enabled via settings.
"""

import logging
import sys
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

# Global flag to track if logging is enabled
_logging_enabled = False
_handlers_added = False


def setup_logging(enable: bool = False, level: int = logging.DEBUG) -> None:
    """
    Setup logging configuration.
    
    Args:
        enable: Whether to enable logging (disabled by default)
        level: Logging level (DEBUG by default when enabled)
    """
    global _logging_enabled, _handlers_added
    _logging_enabled = enable
    
    root_logger = logging.getLogger("comix")
    
    # Close and replace handlers so settings changes take effect immediately
    # and repeated GUI/CLI startup does not duplicate every record.
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    root_logger.setLevel(logging.DEBUG if enable else logging.WARNING)
    root_logger.propagate = False
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if os.name == "nt":
        default_log_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "comix-downloader"
    elif sys.platform == "darwin":
        default_log_dir = Path.home() / "Library" / "Logs" / "comix-downloader"
    else:
        default_log_dir = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "comix-downloader"
    log_dir = Path(os.environ.get("COMIX_LOG_DIR") or default_log_dir)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "comix-downloader.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG if enable else logging.WARNING)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except OSError:
        # Logging must never prevent downloads from starting (read-only home,
        # sandboxed packaging, or an unwritable test directory).
        pass

    if enable:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)
    _handlers_added = bool(root_logger.handlers)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance.
    
    Args:
        name: Logger name (typically __name__)
    
    Returns:
        Logger instance
    """
    if name.startswith("src."):
        name = name.replace("src.", "comix.", 1)
    elif not name.startswith("comix"):
        name = f"comix.{name}"
    
    return logging.getLogger(name)


def is_logging_enabled() -> bool:
    """Check if logging is currently enabled."""
    return _logging_enabled
