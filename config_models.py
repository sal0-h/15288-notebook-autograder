"""Pydantic config models and QID helpers for the AI Autograder.

Extracted from utils.py to separate config schema from I/O, logging, and client setup.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Default model for grading when not specified in config
DEFAULT_MODEL = "gpt-5-mini"

_QID_RE = re.compile(r"^\d+\.\d+$")


def normalize_qid(value: str) -> str:
    """Canonicalize question IDs to numeric form (e.g. Q1.1 -> 1.1)."""
    qid = str(value or "").strip().lstrip("Qq").strip()
    if not _QID_RE.match(qid):
        raise ValueError(f"Invalid question ID '{value}'. Expected format like '1.1'.")
    return qid


class ParsingConfig(BaseModel):
    section_regex: str
    question_regex: str
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
    grade_only: list[str] | None = (
        None  # If set, only grade these question IDs; others get 0 [skipped]
    )
    grade_only_merge: bool = (
        False  # If True + grade_only set, merge regraded questions into existing results
    )

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
    def validate_deductions_sum(self) -> RubricEntry:
        total_deductions = sum(i.deduction for i in self.items)
        if self.items and abs(total_deductions - self.points) > 0.01:
            raise ValueError(
                f"Deductions ({total_deductions}) must sum to exactly {self.points} points"
            )
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    gradescope_title_mapping: dict[str, str] = Field(default_factory=dict)
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


def ensure_app_config(config: dict | AppConfig) -> AppConfig:
    """Normalize a dict/AppConfig input into an AppConfig object."""
    if isinstance(config, AppConfig):
        return config
    return AppConfig.model_validate(config)


def app_config_to_yaml_data(config: AppConfig) -> dict:
    """Convert AppConfig into plain Python data suitable for YAML dumping."""
    return config.model_dump(mode="python", exclude_none=True)
