"""Shared utilities for the AI Autograder pipeline."""

from __future__ import annotations  # Enables forward refs in union type annotations.

import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

import httpx
import yaml
from dotenv import load_dotenv

from config_models import (
    DEFAULT_MODEL,
    AppConfig,
    app_config_to_yaml_data,
    ensure_app_config,
    get_config_field,
    merge_partial_config_dict,
    normalize_qid,
)
from grading_helpers import (
    filter_groups_by_grade_only,
    get_active_grade_only,
    get_effective_question_groups,
    get_skipped_feedback,
    is_grade_only_merge_enabled,
    needs_grade_only_merge,
)
from openai import OpenAI

__all__ = [
    "DEFAULT_MODEL",
    "AppConfig",
    "ensure_app_config",
    "app_config_to_yaml_data",
    "get_config_field",
    "normalize_qid",
    "sanitize_assignment_name",
    "load_config",
    "load_app_config",
    "save_config",
    "AssignmentOutputPaths",
    "get_assignment_output_paths",
    "get_openai_client",
    "temperature_for_model",
    "setup_assignment_logging",
    "get_job_logger",
    "filter_groups_by_grade_only",
    "get_active_grade_only",
    "get_effective_question_groups",
    "is_grade_only_merge_enabled",
    "needs_grade_only_merge",
    "get_skipped_feedback",
]


def sanitize_assignment_name(name: str) -> str:
    """Normalize assignment names to a filesystem-safe token."""
    return re.sub(r'[/\\:*?"<>|.]', "_", str(name or "default")).strip("_") or "default"


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


def get_job_logger(config: dict | BaseModel, module_name: str) -> logging.Logger:
    """Get a logger scoped to the current assignment to prevent interleaved logs."""
    assignment_name = get_config_field(config, "assignment_name", "DEFAULT")
    return logging.getLogger(f"autograder.{assignment_name}.{module_name}")


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------


def _read_yaml_dict(path: Path, *, require_exists: bool = True) -> dict:
    if not path.exists():
        if require_exists:
            raise FileNotFoundError(f"Config file not found: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Invalid config format in {path}: expected a YAML mapping.")
    return loaded


def _resolve_config_paths(cfg: dict, config_root: Path) -> dict:
    resolved = dict(cfg)
    for key in ("solution_notebook", "submissions_dir", "parsed_dir", "output_dir"):
        value = resolved.get(key)
        if value and not Path(value).is_absolute():
            resolved[key] = str((config_root / value).resolve())
    return resolved


def _load_assignment_app_config(
    config_path: Path, *, require_exists: bool = True
) -> AppConfig:
    """Single load path: YAML → merged defaults → resolved paths → ``AppConfig``."""
    path = Path(config_path).resolve()
    cfg = _read_yaml_dict(path, require_exists=require_exists)
    cfg = merge_partial_config_dict(cfg)
    project_root = path.parent.parent.parent
    cfg = _resolve_config_paths(cfg, project_root)
    assignment_name = sanitize_assignment_name(cfg.get("assignment_name", "default"))
    assignment_root = (project_root / "output" / assignment_name).resolve()
    cfg["output_dir"] = str(assignment_root)
    cfg["submissions_dir"] = str(assignment_root / "submissions")
    cfg["parsed_dir"] = str(assignment_root / "parsed")
    cfg["assignment_name"] = assignment_name
    return ensure_app_config(cfg)


def load_config(config_path: Path, *, require_exists: bool = True) -> dict:
    """Load assignment config for YAML/JSON APIs (plain dict).

    For pipeline code prefer ``load_app_config`` and pass ``AppConfig`` through internals.
    """
    return _load_assignment_app_config(
        config_path, require_exists=require_exists
    ).model_dump(mode="python")


def load_app_config(config_path: Path, *, require_exists: bool = True) -> AppConfig:
    """Load and validate configuration as ``AppConfig`` (no dict round-trip)."""
    return _load_assignment_app_config(config_path, require_exists=require_exists)


def save_config(config: dict | AppConfig, config_path: Path) -> None:
    """Save config to config_path (the assignment config at output/{name}/config.yaml).

    Writes all fields. Relativizes solution_notebook against the project root
    (config_path.parent.parent.parent).
    """
    validated = ensure_app_config(config)
    cfg = app_config_to_yaml_data(validated)
    cfg["output_dir"] = "output"
    cfg.pop("submissions_dir", None)
    cfg.pop("parsed_dir", None)

    config_path = Path(config_path).resolve()
    project_root = config_path.parent.parent.parent

    # Relativize solution_notebook for portability
    sol = cfg.get("solution_notebook", "")
    if sol:
        sol_path = Path(sol)
        if sol_path.is_absolute():
            try:
                cfg["solution_notebook"] = str(sol_path.relative_to(project_root))
            except ValueError:
                pass

    out_cfg = cfg
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(out_cfg, f, default_flow_style=False, allow_unicode=True)


@dataclass(frozen=True)
class AssignmentOutputPaths:
    """Canonical assignment-scoped runtime paths."""

    output_dir: Path
    parsed_dir: Path
    solution_parsed: Path
    graded_results: Path
    gradescope_dir: Path


def get_assignment_output_paths(config: dict | AppConfig) -> AssignmentOutputPaths:
    """Return canonical assignment-scoped output paths.

    Centralizing these paths avoids subtle mismatches across pipeline modules.
    """
    cfg = ensure_app_config(config)
    output_dir = Path(cfg.output_dir)
    return AssignmentOutputPaths(
        output_dir=output_dir,
        parsed_dir=Path(cfg.parsed_dir),
        solution_parsed=output_dir / "solution_parsed.json",
        graded_results=output_dir / "graded_results.json",
        gradescope_dir=output_dir / "gradescope",
    )


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------


# GPT-5 reasoning models only support temperature=1. Others can use 0 for deterministic output.
def temperature_for_model(model: str) -> float:
    """Use 0 when model supports it (deterministic); else 1. GPT-5 family only supports 1."""
    if model.startswith("gpt-5"):
        return 1.0
    return 0.0


def get_openai_client(
    max_retries: int = 5,
    *,
    max_connections: int = 20,
    max_keepalive_connections: int = 20,
    connect_timeout_s: float = 10.0,
    read_timeout_s: float = 120.0,
    write_timeout_s: float = 30.0,
    pool_timeout_s: float = 30.0,
) -> OpenAI:
    """Initialize OpenAI client with .env key, with SDK-level retries.
    Checks 'key' first (from .env), then OPENAI_API_KEY as fallback."""
    load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("key")
    if not api_key:
        raise ValueError(
            "API key not found. Set OPENAI_API_KEY or 'key' in .env."
        )
    limits = httpx.Limits(
        max_connections=max(1, int(max_connections)),
        max_keepalive_connections=max(1, int(max_keepalive_connections)),
    )
    timeout = httpx.Timeout(
        connect=connect_timeout_s,
        read=read_timeout_s,
        write=write_timeout_s,
        pool=pool_timeout_s,
    )
    http_client = httpx.Client(limits=limits, timeout=timeout)
    return OpenAI(
        api_key=api_key,
        max_retries=max_retries,
        http_client=http_client,
    )
