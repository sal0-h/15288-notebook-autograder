"""LLM response schemas and grading constants.

All Pydantic models for structured LLM outputs live here: grading responses,
rubric generation, rubric review, and GenAI detection. Also re-exports
pricing/cost helpers from token_usage for backward compatibility.
"""

from typing import Literal

from pydantic import BaseModel, field_validator

from config_models import RubricItem

# Re-export from token_usage for backward compatibility — new code should
# import directly from token_usage.
from token_usage import MODEL_PRICING, usage_cost_usd  # noqa: F401

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_FEEDBACKS = ("[skipped - not in grade_only]", "[not included in grading groups]")
NO_SUBMISSION = "[no submission]"
LLM_PARSE_ERROR = "[parse error in LLM response]"
LLM_NOT_RETURNED = "[not returned by LLM]"
GRADING_FAILED = "[grading failed after retries]"


# ---------------------------------------------------------------------------
# Pydantic models (LLM wire + pipeline; structured outputs via Responses API)
# ---------------------------------------------------------------------------


class QuestionGrade(BaseModel):
    """One graded question: used as structured-output row and in-memory."""

    question_id: str
    score: float
    feedback: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    requires_review: bool = False

    @field_validator("score", mode="before")
    @classmethod
    def coerce_score(cls, v) -> float:
        return float(v)

    @field_validator("feedback", mode="before")
    @classmethod
    def coerce_feedback(cls, v) -> str:
        return str(v) if v is not None else ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v):
        if v is None:
            return "medium"
        s = str(v).strip().lower()
        if s in ("high", "medium", "low"):
            return s
        return "medium"

    @field_validator("requires_review", mode="before")
    @classmethod
    def coerce_requires_review(cls, v) -> bool:
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("true", "1", "yes")


class GenaiQuestionResult(BaseModel):
    """Per-question GenAI suspicion row: structured output + pipeline."""

    question_id: str
    suspicious_genai: bool = False
    note: str = ""

    @field_validator("suspicious_genai", mode="before")
    @classmethod
    def coerce_suspicious(cls, v) -> bool:
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("true", "1", "yes")

    @field_validator("note", mode="before")
    @classmethod
    def coerce_note(cls, v) -> str:
        return str(v) if v is not None else ""


class GradingLlmResponse(BaseModel):
    """Grading completion payload — one object per call to ``grade_group``."""

    grades: list[QuestionGrade]


class GenaiLlmResponse(BaseModel):
    """GenAI-detection completion payload — one object per student."""

    results: list[GenaiQuestionResult]


# ---------------------------------------------------------------------------
# Rubric LLM response models (structured outputs via Responses API)
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
