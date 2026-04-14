"""Pydantic models for on-disk JSON artifacts (graded results, parsed notebooks)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Re-export from token_usage for backward compatibility — new code should
# import directly from token_usage.
from token_usage import (  # noqa: F401
    GRADED_RESULT_USAGE_KEY,
    TokenUsage,
    detach_usage_from_graded_result,
    graded_usage_summary_event,
    merge_graded_usage,
)


class Question(BaseModel):
    """Schema for one graded question in a result."""

    model_config = ConfigDict(extra="allow")  # Allow genai_detection fields

    score: float
    max: float
    feedback: str = ""
    confidence: str = "low"
    requires_review: bool = False


class GradedResult(BaseModel):
    """Shape of one entry in ``graded_results.json`` (matches ``grade._build_result_dict``)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    student_name: str
    questions: dict[str, Question] = Field(default_factory=dict)
    total_score: float = 0.0
    total_max: float = 0.0
    summary_feedback: str = ""
    usage: dict[str, int] | None = Field(default=None, alias=GRADED_RESULT_USAGE_KEY)


class ParsedNotebook(BaseModel):
    """Minimal typed shell for parsed ``*.json`` from ``parse_notebook`` (unknown keys allowed)."""

    model_config = ConfigDict(extra="allow")

    student_name: str | None = None
    sections: dict[str, Any] = Field(default_factory=dict)
    duplicate_qids: list[str] = Field(default_factory=list)


def graded_result_to_disk_dict(r: GradedResult) -> dict[str, Any]:
    """Serialize for ``graded_results.json`` (``GRADED_RESULT_USAGE_KEY`` when present)."""
    return r.model_dump(mode="python", by_alias=True, exclude_none=True)
