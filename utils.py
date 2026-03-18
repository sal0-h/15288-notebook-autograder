"""Shared utilities for the AI Autograder pipeline."""

from __future__ import annotations  # Enables forward refs in union type annotations.

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv

from config_models import (
    DEFAULT_MODEL,
    AppConfig,
    ensure_app_config,
    app_config_to_yaml_data,
    normalize_qid,
)
from grading_models import GRADING_FAILED, SKIP_FEEDBACKS
from openai import OpenAI

__all__ = [
    "DEFAULT_MODEL",
    "AppConfig",
    "ensure_app_config",
    "app_config_to_yaml_data",
    "normalize_qid",
    "sanitize_assignment_name",
    "load_config",
    "save_config",
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


def filter_groups_by_grade_only(
    groups: list[list[str]], grade_only: list[str] | None
) -> list[list[str]]:
    """Return question groups filtered to only grade_only questions when provided."""
    if not grade_only:
        return groups
    grade_only_set = set(grade_only)
    return [
        [q for q in group if q in grade_only_set]
        for group in groups
        if group and any(q in grade_only_set for q in group)
    ]


def get_active_grade_only(grading_config: dict | BaseModel) -> list[str] | None:
    grade_only = _config_get(grading_config, "grade_only")
    return grade_only if grade_only else None


def get_effective_question_groups(grading_config: dict | BaseModel) -> list[list[str]]:
    groups = _config_get(grading_config, "question_groups", [])
    grade_only = get_active_grade_only(grading_config)
    return filter_groups_by_grade_only(groups, grade_only)


def is_grade_only_merge_enabled(grading_config: dict | BaseModel) -> bool:
    return bool(
        _config_get(grading_config, "grade_only_merge")
        and get_active_grade_only(grading_config)
    )


def needs_grade_only_merge(
    existing_result: dict | None, grade_only: list[str] | None
) -> bool:
    if not grade_only:
        return False
    if not existing_result:
        return True

    questions = existing_result.get("questions", {})
    retryable_feedback = {SKIP_FEEDBACKS[0], GRADING_FAILED}
    for qid in set(grade_only):
        if qid not in questions:
            return True
        feedback = (questions[qid].get("feedback") or "").strip()
        if feedback in retryable_feedback:
            return True
    return False


def get_skipped_feedback(grade_only: list[str] | None) -> str:
    return SKIP_FEEDBACKS[0] if grade_only else SKIP_FEEDBACKS[1]


def _config_get(config: dict | AppConfig, key: str, default: Any = None) -> Any:
    if hasattr(config, "model_dump"):  # Pydantic model (AppConfig, GradingConfig, etc.)
        return getattr(config, key, default)
    return config.get(key, default)


def _as_dict(config: dict | AppConfig | None) -> dict:
    if hasattr(config, "model_dump"):
        return config.model_dump()
    return dict(config or {})


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
    assignment_name = _config_get(config, "assignment_name", "DEFAULT")
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


def _apply_config_defaults(cfg: dict) -> dict:
    """Ensure all required config fields exist before validation."""
    normalized = dict(cfg or {})
    normalized.setdefault("model", DEFAULT_MODEL)
    normalized.setdefault("rubric_model", "")
    normalized.setdefault("rubric_review", True)
    normalized.setdefault("include_reference_in_grading", False)
    normalized.setdefault("solution_notebook", "")
    normalized.setdefault("workers", 1)
    normalized.setdefault("rubrics", {})
    normalized.setdefault("max_prompt_tokens", 80_000)
    normalized.setdefault("max_completion_tokens", 4_096)

    parsing = dict(normalized.get("parsing") or {})
    parsing.setdefault("section_regex", r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b")
    parsing.setdefault(
        "question_regex",
        r"(?i)^\s*(-\s*)?Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]",
    )
    parsing.setdefault("keep_images", True)
    normalized["parsing"] = parsing

    grading = dict(normalized.get("grading") or {})
    grading.setdefault("question_groups", [])
    grading.setdefault("grade_only", None)
    grading.setdefault("grade_only_merge", False)
    normalized["grading"] = grading
    return normalized


def load_config(config_path: Path, *, require_exists: bool = True) -> dict:
    """Load assignment config from an explicit path.

    config_path must point to the assignment config file, typically at
    output/{assignment_name}/config.yaml.  The project root is inferred as
    config_path.parent.parent.parent; relative paths in the config (e.g.
    solution_notebook) are resolved against it.
    """
    path = Path(config_path).resolve()
    cfg = _read_yaml_dict(path, require_exists=require_exists)
    cfg = _apply_config_defaults(cfg)
    # Project root: output/{name}/config.yaml → 3 levels up
    project_root = path.parent.parent.parent
    cfg = _resolve_config_paths(cfg, project_root)
    assignment_name = sanitize_assignment_name(cfg.get("assignment_name", "default"))
    assignment_root = (project_root / "output" / assignment_name).resolve()
    cfg["output_dir"] = str(assignment_root)
    cfg["submissions_dir"] = str(assignment_root / "submissions")
    cfg["parsed_dir"] = str(assignment_root / "parsed")
    cfg["assignment_name"] = assignment_name
    return ensure_app_config(cfg).model_dump()


def load_app_config(config_path: Path) -> "AppConfig":
    """Load and validate configuration, returning an AppConfig object."""
    return ensure_app_config(load_config(config_path))


def save_config(config: dict | "AppConfig", config_path: Path) -> None:
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
    api_key = os.environ.get("key") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "API key not found. Set 'key=your-api-key' in .env or OPENAI_API_KEY environment variable."
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
