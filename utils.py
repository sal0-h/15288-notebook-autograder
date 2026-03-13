"""Shared utilities for the AI Autograder pipeline."""

import logging
import os
import threading
import re
from typing import Any
from pathlib import Path

import yaml
from dotenv import load_dotenv

from grading_models import SKIP_FEEDBACKS
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Default model for grading when not specified in config
DEFAULT_MODEL = "gpt-5-mini"


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
    retryable_feedback = {SKIP_FEEDBACKS[0], "[grading failed after retries]"}
    for qid in set(grade_only):
        if qid not in questions:
            return True
        feedback = (questions[qid].get("feedback") or "").strip()
        if feedback in retryable_feedback:
            return True
    return False


def get_skipped_feedback(grade_only: list[str] | None) -> str:
    return SKIP_FEEDBACKS[0] if grade_only else SKIP_FEEDBACKS[1]


def _config_get(config: dict | BaseModel, key: str, default: Any = None) -> Any:
    if isinstance(config, BaseModel):
        return getattr(config, key, default)
    return config.get(key, default)


def _as_dict(config: dict | BaseModel | None) -> dict:
    if isinstance(config, BaseModel):
        return config.model_dump()
    return dict(config or {})


_configured_loggers = set()
_log_lock = threading.Lock()


def setup_assignment_logging(assignment_name: str, output_dir: str | Path) -> Path:
    """Ensure the assignment-specific logger writes to output_dir/autograder.log."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = (out_dir / "autograder.log").resolve()

    global _configured_loggers
    if assignment_name in _configured_loggers:
        return log_path

    with _log_lock:
        if assignment_name in _configured_loggers:
            return log_path

        logger = logging.getLogger(f"autograder.{assignment_name}")
        logger.setLevel(logging.INFO)

        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        logger.addHandler(handler)

        # Suppress noisy HTTP logs globally just to be safe
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

        _configured_loggers.add(assignment_name)

    return log_path


def get_job_logger(config: dict | BaseModel, module_name: str) -> logging.Logger:
    """Get a logger scoped to the current assignment to prevent interleaved logs."""
    assignment_name = _config_get(config, "assignment_name", "DEFAULT")
    return logging.getLogger(f"autograder.{assignment_name}.{module_name}")


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------


def _project_root(config_path: Path | None) -> Path:
    """Project root = directory containing the root config.yaml."""
    return (config_path or Path("config.yaml")).resolve().parent


def _is_assignment_config(path: Path) -> bool:
    """True if path is output/{assignment_name}/config.yaml."""
    parts = path.resolve().parts
    return len(parts) >= 3 and "output" in parts and path.name == "config.yaml"


def _read_yaml_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _merge_root_controls(
    cfg: dict,
    root_cfg: dict,
) -> dict:
    merged = dict(cfg)
    if "rubric_review" in root_cfg:
        merged["rubric_review"] = root_cfg["rubric_review"]
    if "include_reference_in_grading" in root_cfg:
        merged["include_reference_in_grading"] = root_cfg[
            "include_reference_in_grading"
        ]
    return merged


def _load_explicit_assignment_config(
    assignment_config_path: Path,
) -> tuple[dict, Path, Path, str, str]:
    project_root = assignment_config_path.parent.parent.parent
    cfg = _read_yaml_dict(assignment_config_path)
    assignment_name = sanitize_assignment_name(cfg.get("assignment_name", "default"))
    base_output = cfg.get("output_dir", "output")
    if Path(base_output).is_absolute():
        base_output = "output"
    root_cfg = _read_yaml_dict(project_root / "config.yaml")
    cfg = _merge_root_controls(cfg, root_cfg)
    return cfg, project_root, project_root, assignment_name, base_output


def _load_root_or_assignment_config(
    root_path: Path,
) -> tuple[dict, Path, Path, str, str]:
    project_root = root_path.parent
    root_cfg = _read_yaml_dict(root_path)
    assignment_name = sanitize_assignment_name(
        root_cfg.get("assignment_name", "default")
    )
    base_output = root_cfg.get("output_dir", "output")
    assignment_config_path = (
        project_root / base_output / assignment_name / "config.yaml"
    )

    if assignment_config_path.exists():
        cfg = _read_yaml_dict(assignment_config_path)
        cfg = _merge_root_controls(cfg, root_cfg)
    else:
        cfg = dict(root_cfg)

    return cfg, project_root, project_root, assignment_name, base_output


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


def load_config(config_path: Path | None = None) -> dict:
    """Load config. Uses output-first layout when available:
    - Root config.yaml: minimal pointer (assignment_name, output_dir)
    - Primary config: output/{assignment_name}/config.yaml (full config)
    Paths are resolved relative to project root. Outputs scoped under output/{assignment_name}/.
    """
    root_path = (config_path or Path("config.yaml")).resolve()

    if (
        config_path is not None
        and _is_assignment_config(root_path)
        and root_path.exists()
    ):
        cfg, project_root, config_root, assignment_name, base_output = (
            _load_explicit_assignment_config(root_path)
        )
    else:
        cfg, project_root, config_root, assignment_name, base_output = (
            _load_root_or_assignment_config(root_path)
        )

    cfg = _apply_config_defaults(cfg)
    cfg = _resolve_config_paths(cfg, config_root)

    # Output dir is always output/{assignment_name}/
    assignment_root = (project_root / base_output / assignment_name).resolve()
    cfg["output_dir"] = str(assignment_root)
    cfg["submissions_dir"] = str(assignment_root / "submissions")
    cfg["parsed_dir"] = str(assignment_root / "parsed")
    cfg["assignment_name"] = assignment_name
    cfg = _apply_config_defaults(cfg)
    return AppConfig.model_validate(cfg).model_dump()


def save_config(config: dict, config_path: Path | None = None) -> None:
    """Save config using output-first layout:
    - Full config → output/{assignment_name}/config.yaml
    - Root config.yaml → minimal pointer (assignment_name, output_dir)"""
    validated = AppConfig.model_validate(config)
    cfg = validated.model_dump()
    cfg["output_dir"] = "output"
    cfg.pop("submissions_dir", None)
    cfg.pop("parsed_dir", None)

    input_path = (config_path or Path("config.yaml")).resolve()
    assignment_name = sanitize_assignment_name(cfg.get("assignment_name", "default"))
    base_output = cfg.get("output_dir", "output")

    # If caller explicitly targets output/{assignment}/config.yaml, treat it as
    # the assignment-scoped runtime config destination (full config goes there).
    if config_path is not None and _is_assignment_config(input_path):
        project_root = input_path.parent.parent.parent
        assignment_config_path = input_path
        root_path = project_root / "config.yaml"
    else:
        project_root = _project_root(config_path)
        assignment_config_path = (
            project_root / base_output / assignment_name / "config.yaml"
        )
        root_path = input_path

    # Relativize solution_notebook for portability (relative to project root)
    sol = cfg.get("solution_notebook", "")
    if sol:
        sol_path = Path(sol)
        if sol_path.is_absolute():
            try:
                cfg["solution_notebook"] = str(sol_path.relative_to(project_root))
            except ValueError:
                pass

    # Save full config to output/{assignment_name}/config.yaml
    # Root-only controls are excluded to keep assignment config focused on runtime artifacts.
    assignment_cfg = {
        k: v
        for k, v in cfg.items()
        if k not in ("prompts", "rubric_review", "include_reference_in_grading")
    }
    assignment_config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(assignment_config_path, "w", encoding="utf-8") as f:
        yaml.dump(assignment_cfg, f, default_flow_style=False, allow_unicode=True)

    # Save root config with assignment pointer + root-level controls.
    root_cfg = {
        "assignment_name": assignment_name,
        "output_dir": base_output,
        "rubric_review": cfg.get("rubric_review", True),
        "include_reference_in_grading": cfg.get("include_reference_in_grading", False),
    }
    with open(root_path, "w", encoding="utf-8") as f:
        yaml.dump(root_cfg, f, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------


# GPT-5 reasoning models only support temperature=1. Others can use 0 for deterministic output.
def temperature_for_model(model: str) -> float:
    """Use 0 when model supports it (deterministic); else 1. GPT-5 family only supports 1."""
    if model.startswith("gpt-5"):
        return 1.0
    return 0.0


def get_openai_client(max_retries: int = 5) -> OpenAI:
    """Initialize OpenAI client with .env key, with SDK-level retries.
    Checks 'key' first (from .env), then OPENAI_API_KEY as fallback."""
    load_dotenv()
    api_key = os.environ.get("key") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "API key not found. Set 'key=your-api-key' in .env or OPENAI_API_KEY environment variable."
        )
    return OpenAI(api_key=api_key, max_retries=max_retries)


# ---------------------------------------------------------------------------
# Pydantic config validation models
# ---------------------------------------------------------------------------


class ParsingConfig(BaseModel):
    section_regex: str
    question_regex: str
    keep_images: bool = True

    @field_validator("section_regex", "question_regex")
    @classmethod
    def validate_regex(cls, v: str) -> str:
        import re

        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"Invalid regex: {e}") from e
        return v


class GradingConfig(BaseModel):
    question_groups: list[list[str]] = Field(default_factory=list)
    grade_only: list[str] | None = (
        None  # If set, only grade these question IDs; others get 0 [skipped]
    )
    grade_only_merge: bool = (
        False  # If True + grade_only set, merge regraded questions into existing results
    )


class RubricItem(BaseModel):
    description: str
    deduction: float  # positive number, e.g. 1.0 means "-1 point"

    @field_validator("deduction", mode="before")
    @classmethod
    def coerce_deduction(cls, v) -> float:
        try:
            return abs(float(v))
        except (TypeError, ValueError):
            return 0.0


class RubricEntry(BaseModel):
    points: int
    items: list[RubricItem] = []

    @field_validator("points", mode="before")
    @classmethod
    def coerce_points(cls, v) -> int:
        if v is None:
            return 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    @model_validator(mode="after")
    def validate_deductions_sum(self) -> "RubricEntry":
        total_deductions = sum(i.deduction for i in self.items)
        if self.items and abs(total_deductions - self.points) > 0.01:
            raise ValueError(
                f"Deductions ({total_deductions}) must sum to exactly {self.points} points"
            )
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    assignment_name: str = "default"
    model: str = DEFAULT_MODEL
    rubric_model: str = ""  # If set, used for rubric generation; else uses model
    rubric_review: bool = (
        True  # If True, run optional second LLM pass to soften rubric wording
    )
    include_reference_in_grading: bool = (
        False  # If True, include raw reference solution in grading prompt
    )
    solution_notebook: str = ""
    submissions_dir: str = "output/submissions"
    parsed_dir: str = "output/parsed"
    output_dir: str = "output"
    workers: int = 1
    rubrics: dict[str, RubricEntry] = Field(default_factory=dict)
    max_prompt_tokens: int = 80_000
    max_completion_tokens: int = 4_096
    parsing: ParsingConfig = Field(
        default_factory=lambda: ParsingConfig(
            section_regex=r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b",
            question_regex=r"(?i)^\s*(-\s*)?Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]",
            keep_images=True,
        )
    )
    grading: GradingConfig = Field(default_factory=GradingConfig)

    @field_validator("workers")
    @classmethod
    def workers_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("workers must be >= 1")
        return v
