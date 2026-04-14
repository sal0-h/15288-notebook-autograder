"""Process-wide API state: active assignment, locks, project root."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import HTTPException

from config_models import AppConfig
from config_models import load_app_config
from utils import setup_assignment_logging

logger = logging.getLogger(__name__)

# Project root (parent of the ``api`` package)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_active_config_path: Path | None = None
_cached_config: AppConfig | None = None

grading_lock = threading.Lock()
results_lock = threading.Lock()
rubric_lock = threading.Lock()


def get_active_config_path() -> Path | None:
    return _active_config_path


def set_active_config_path(path: Path | None) -> None:
    global _active_config_path, _cached_config
    _active_config_path = path
    _cached_config = None


def invalidate_config_cache() -> None:
    """Clear cached config so the next read reloads from disk."""
    global _cached_config
    _cached_config = None


def get_active_app_config() -> AppConfig:
    """Return the active assignment config, cached after first load.

    Call :func:`invalidate_config_cache` after saving config to disk.
    """
    global _cached_config
    if _active_config_path is None:
        raise HTTPException(
            status_code=400,
            detail="No assignment loaded. Use Setup to load or create an assignment.",
        )
    if _cached_config is None:
        _cached_config = load_app_config(_active_config_path)
    return _cached_config


def setup_file_logging() -> None:
    """Point file logging at the active assignment's autograder.log."""
    if _active_config_path is None:
        return
    try:
        cfg = get_active_app_config()
        out_dir = Path(cfg.output_dir)
        assignment_name = cfg.assignment_name or "DEFAULT"
    except (HTTPException, FileNotFoundError, ValueError, OSError) as e:
        logger.warning(
            "Could not load config for file logging (%s): %s. Using defaults.",
            _active_config_path,
            e,
        )
        out_dir = Path("output")
        assignment_name = "DEFAULT"
    log_path = setup_assignment_logging(assignment_name, out_dir)
    logger.info("Logging to %s", log_path)
