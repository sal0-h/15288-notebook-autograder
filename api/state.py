"""Process-wide API state: active assignment, locks, project root."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import HTTPException

from config_models import AppConfig, ensure_app_config
from utils import load_config, setup_assignment_logging

logger = logging.getLogger(__name__)

# Project root (parent of the ``api`` package)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_active_config_path: Path | None = None

grading_lock = threading.Lock()
results_lock = threading.Lock()
rubric_lock = threading.Lock()


def get_active_config_path() -> Path | None:
    return _active_config_path


def set_active_config_path(path: Path | None) -> None:
    global _active_config_path
    _active_config_path = path


def get_active_config() -> dict:
    """Return the current assignment config as a dict (YAML/HTTP / merge-friendly).

    Pipeline code should prefer ``get_active_app_config()`` to avoid dict round-trips.
    """
    if _active_config_path is None:
        raise HTTPException(
            status_code=400,
            detail="No assignment loaded. Use Setup to load or create an assignment.",
        )
    return load_config(_active_config_path)


def get_active_app_config() -> AppConfig:
    """Return the active assignment as ``AppConfig`` (same validation as ``get_active_config``).

    Uses ``ensure_app_config(get_active_config())`` so tests that patch ``get_active_config``
    still drive the pipeline.
    """
    if _active_config_path is None:
        raise HTTPException(
            status_code=400,
            detail="No assignment loaded. Use Setup to load or create an assignment.",
        )
    return ensure_app_config(get_active_config())


def setup_file_logging() -> None:
    """Point file logging at the active assignment's autograder.log."""
    if _active_config_path is None:
        return
    try:
        cfg = load_config(_active_config_path)
        out_dir = Path(cfg.get("output_dir", "output"))
        assignment_name = cfg.get("assignment_name", "DEFAULT")
    except (FileNotFoundError, ValueError, OSError) as e:
        logger.warning(
            "Could not load config for file logging (%s): %s. Using defaults.",
            _active_config_path,
            e,
        )
        out_dir = Path("output")
        assignment_name = "DEFAULT"
    log_path = setup_assignment_logging(assignment_name, out_dir)
    logger.info("Logging to %s", log_path)
