"""Pydantic config models and QID helpers for the AI Autograder.

Extracted from utils.py to separate config schema from I/O, logging, and client setup.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Default model for grading when not specified in config
DEFAULT_MODEL = "gpt-4.1-mini"

_QID_RE = re.compile(r"^\d+\.\d+$")


def normalize_qid(value: str) -> str:
    """Canonicalize question IDs to numeric form (e.g. Q1.1 -> 1.1)."""
    qid = str(value or "").strip().lstrip("Qq").strip()
    if not _QID_RE.match(qid):
        raise ValueError(f"Invalid question ID '{value}'. Expected format like '1.1'.")
    return qid


def sort_key_qid(qid: str) -> tuple[int, int] | tuple[float, str]:
    """Sort key for question IDs. Standard X.Y format sorts numerically; others fall back to string."""
    parts = qid.split(".")
    if len(parts) == 2:
        try:
            return (int(parts[0]), int(parts[1]))
        except ValueError:
            pass
    return (999_999, qid)


class ParsingConfig(BaseModel):
    section_regex: str = r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b"
    question_regex: str = r"(?i)^\s*(-\s*)?Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]"
    keep_images: bool = True

    @field_validator("section_regex", "question_regex")
    @classmethod
    def validate_regex(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"Invalid regex: {e}") from e
        return v


class GradingConfig(BaseModel):
    question_groups: list[list[str]] = Field(default_factory=list)
    # If set, only grade these question IDs; others get 0 [skipped]
    grade_only: list[str] | None = None
    # If True + grade_only set, merge regraded questions into existing results
    grade_only_merge: bool = False

    @field_validator("question_groups", mode="before")
    @classmethod
    def normalize_question_groups(cls, v):
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError(
                "grading.question_groups must be a list of question groups"
            )
        normalized_groups: list[list[str]] = []
        for group in v:
            if not isinstance(group, list):
                raise ValueError("Each grading.question_groups entry must be a list")
            normalized_groups.append([normalize_qid(qid) for qid in group])
        return normalized_groups

    @field_validator("grade_only", mode="before")
    @classmethod
    def normalize_grade_only(cls, v):
        if v in (None, [], ()):
            return None
        if not isinstance(v, list):
            raise ValueError("grading.grade_only must be a list of question IDs")
        return list(dict.fromkeys(normalize_qid(qid) for qid in v))

    @model_validator(mode="after")
    def validate_grade_only_merge_requires_grade_only(self):
        """grade_only_merge is only meaningful when grade_only is set and non-empty."""
        if self.grade_only_merge and not self.grade_only:
            raise ValueError(
                "grade_only_merge=True requires grade_only to be set with at least one question ID"
            )
        return self


def sanitize_llm_text(text: str) -> str:
    """Normalize malformed control-char artifacts seen in some LLM outputs."""
    if not text:
        return text
    replacements = {
        "\x00d7": "x",
        "\x00": "",
        "\x19": "'",
        "\u2019": "'",
        "\u2212": "-",
        "\u00d7": "x",
        "\u2248": "~",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    return "".join(c for c in text if ord(c) >= 32 or c in "\n\r\t")


class RubricItem(BaseModel):
    """Single deduction criterion (structured outputs + config)."""

    description: str
    deduction: float

    @field_validator("deduction", mode="before")
    @classmethod
    def coerce_deduction_nonnegative(cls, v) -> float:
        return abs(float(v))


class RubricEntry(BaseModel):
    points: int = Field(ge=0)
    items: list[RubricItem] = []

    @classmethod
    def from_llm_output(cls, raw: dict) -> RubricEntry:
        """Parse LLM JSON object into a rubric entry; scale item deductions to sum to ``points``."""
        pts_raw = raw.get("points")
        if pts_raw is None:
            raise ValueError(
                "Rubric LLM output must include integer 'points' for each question"
            )
        pts = int(pts_raw)
        raw_items = raw.get("items", [])
        parsed_items: list[dict] = []
        total_deductions = 0.0
        for item in raw_items:
            d = abs(float(item.get("deduction", 0)))
            parsed_items.append(
                {
                    "description": sanitize_llm_text(
                        str(item.get("description", "")).strip()
                    )
                    or "[missing]",
                    "deduction": d,
                }
            )
            total_deductions += d
        if parsed_items and abs(total_deductions - pts) > 0.01:
            if total_deductions <= 0:
                even = round(float(pts) / len(parsed_items), 2)
                for item in parsed_items:
                    item["deduction"] = even
            else:
                scale = pts / total_deductions
                for item in parsed_items:
                    item["deduction"] = round(item["deduction"] * scale, 2)
            diff = float(pts) - sum(i["deduction"] for i in parsed_items)
            parsed_items[-1]["deduction"] = round(
                parsed_items[-1]["deduction"] + diff, 2
            )
        return cls.model_validate({"points": pts, "items": parsed_items})

    @model_validator(mode="after")
    def validate_deductions_sum(self) -> RubricEntry:
        total_deductions = sum(i.deduction for i in self.items)
        if self.items and abs(total_deductions - self.points) > 0.01:
            raise ValueError(
                f"Deductions ({total_deductions}) must sum to exactly {self.points} points"
            )
        return self


# ---------------------------------------------------------------------------
# Structured-output response models (list-based; used by rubric_generate / rubric_review)
# ---------------------------------------------------------------------------


class RubricQuestionLlm(BaseModel):
    """Per-question rubric as emitted by the LLM."""

    question_id: str
    points: int
    items: list[RubricItem]


class RubricGroupLlmResponse(BaseModel):
    """Rubric-generation completion payload."""

    questions: list[RubricQuestionLlm]


class RubricReviewItem(BaseModel):
    """Single criterion description as emitted by the review LLM."""

    description: str


class RubricReviewQuestion(BaseModel):
    """Per-question review output (descriptions only — structure is locked)."""

    question_id: str
    items: list[RubricReviewItem]


class RubricReviewResponse(BaseModel):
    """Rubric-review completion payload."""

    questions: list[RubricReviewQuestion]


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    assignment_name: str = "default"
    model: str = DEFAULT_MODEL
    rubric_model: str = ""  # If set, used for rubric generation; else uses model
    rubric_review: bool = True
    include_reference_in_grading: bool = False
    solution_notebook: str = ""
    submissions_dir: str = "output/submissions"
    parsed_dir: str = "output/parsed"
    output_dir: str = "output"
    workers: int = Field(default=1, ge=1)
    rubrics: dict[str, RubricEntry] = Field(default_factory=dict)
    max_prompt_tokens: int = 80_000
    max_completion_tokens: int = 4_096
    # Optional second pass: GenAI-style suspicion flags (never affects scores)
    genai_detection_model: str = "gpt-4.1-mini"
    genai_detection_max_code_chars: int = 12_000
    gradescope_title_mapping: dict[str, str] = Field(default_factory=dict)
    # Optional: set to the Gradescope assignment/course shown in submission_metadata.json
    # so the autograder ZIP rejects mismatched uploads (wrong assignment package).
    gradescope_assignment_id: int | None = None
    gradescope_course_id: int | None = None
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)
    grading: GradingConfig = Field(default_factory=GradingConfig)


def default_config(assignment_name: str = "default", **overrides) -> dict:
    """Return a minimal valid config dict. Overrides replace top-level keys."""
    cfg = AppConfig(assignment_name=assignment_name)
    data = cfg.model_dump(mode="python")
    for key, value in overrides.items():
        if key in data:
            data[key] = value
    return ensure_app_config(data).model_dump(mode="python")


def ensure_app_config(config: dict | AppConfig) -> AppConfig:
    """Normalize a dict/AppConfig input into an AppConfig object."""
    if isinstance(config, AppConfig):
        return config
    return AppConfig.model_validate(config)


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------


def sanitize_assignment_name(name: str) -> str:
    """Normalize assignment names to a filesystem-safe token (dots become ``_``)."""
    return re.sub(r'[/\\:*?"<>|.]', "_", str(name or "default")).strip("_") or "default"


def _read_yaml_dict(path: Path, *, require_exists: bool = True) -> dict:
    """Load YAML file into dict; return empty dict if file missing (unless required)."""
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
    """Resolve relative paths in config relative to config_root."""
    resolved = dict(cfg)
    for key in ("solution_notebook", "submissions_dir", "parsed_dir", "output_dir"):
        value = resolved.get(key)
        if value and not Path(value).is_absolute():
            resolved[key] = str((config_root / value).resolve())
    return resolved


def load_app_config(config_path: Path, *, require_exists: bool = True) -> AppConfig:
    """Load assignment config YAML and normalize to ``AppConfig``.

    Usage:
        config = load_app_config("output/LabTest_2_S26/config.yaml")
    """
    path = Path(config_path).resolve()
    cfg = _read_yaml_dict(path, require_exists=require_exists)
    project_root = path.parent.parent.parent
    cfg = _resolve_config_paths(cfg, project_root)
    assignment_name = sanitize_assignment_name(cfg.get("assignment_name", "default"))
    assignment_root = (project_root / "output" / assignment_name).resolve()
    cfg["output_dir"] = str(assignment_root)
    cfg["submissions_dir"] = str(assignment_root / "submissions")
    cfg["parsed_dir"] = str(assignment_root / "parsed")
    cfg["assignment_name"] = assignment_name
    return ensure_app_config(cfg)
