"""Pydantic models and constants for the grading engine."""

from typing import Literal

from config_models import DEFAULT_MODEL
from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_FEEDBACKS = ("[skipped - not in grade_only]", "[not included in grading groups]")
NO_SUBMISSION = "[no submission]"
LLM_PARSE_ERROR = "[parse error in LLM response]"
LLM_NOT_RETURNED = "[not returned by LLM]"
GRADING_FAILED = "[grading failed after retries]"

# Pricing per 1M tokens (input, output). From docs/OPENAI_VISION_MODELS.md
MODEL_PRICING = {
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5-nano-2025-08-07": (0.05, 0.40),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-mini-2025-08-07": (0.25, 2.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-mini-2025-04-14": (0.40, 1.60),
    "gpt-5": (1.25, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-2025-04-14": (2.00, 8.00),
    "gpt-5.2": (1.75, 14.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4-turbo": (10.00, 30.00),
}


def usage_cost_usd(usage, model: str) -> float:
    """Approximate USD cost for recorded usage at the given model's rates."""
    prompt = getattr(usage, "prompt_tokens", 0)
    completion = getattr(usage, "completion_tokens", 0)
    pt = int(prompt or 0)
    ct = int(completion or 0)
    inp, out = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
    return (pt / 1e6 * inp) + (ct / 1e6 * out)


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
