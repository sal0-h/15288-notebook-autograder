"""Shared utilities: assignment logging and filename sanitization."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from config_models import AppConfig

# Windows path reserved characters; stripped by ``sanitize_filename_component``.
# Also embedded in ``export.RUN_AUTOGRADER`` (must stay in sync).
FILENAME_FORBIDDEN_LITERAL = '/\\:*?"<>|'


def sanitize_filename_component(name: str, *, if_empty: str | None = None) -> str:
    """
    Remove characters that are invalid in cross-platform filenames.

    When ``if_empty`` is set and the result would be empty (e.g. student name was
    only forbidden characters), returns ``if_empty`` instead.
    """
    out = "".join(c for c in name if c not in FILENAME_FORBIDDEN_LITERAL)
    if if_empty is not None and not out:
        return if_empty
    return out


_configured_loggers: dict[str, Path] = {}
_log_lock = threading.Lock()


def setup_assignment_logging(assignment_name: str, output_dir: str | Path) -> Path:
    """Ensure the assignment-specific logger writes to output_dir/autograder.log."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = (out_dir / "autograder.log").resolve()

    global _configured_loggers
    with _log_lock:
        if _configured_loggers.get(assignment_name) == log_path:
            return log_path

        logger = logging.getLogger(f"autograder.{assignment_name}")
        logger.setLevel(logging.INFO)

        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler):
                logger.removeHandler(handler)
                handler.close()

        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        logger.addHandler(handler)

        # Suppress noisy HTTP logs globally just to be safe
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

        _configured_loggers[assignment_name] = log_path

    return log_path


def get_job_logger(config: AppConfig, module_name: str) -> logging.Logger:
    """Get a logger scoped to the current assignment to prevent interleaved logs."""
    assignment_name = config.assignment_name or "DEFAULT"
    return logging.getLogger(f"autograder.{assignment_name}.{module_name}")
