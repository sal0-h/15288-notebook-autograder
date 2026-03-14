"""Pydantic models and constants for the grading engine."""

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

# ---------------------------------------------------------------------------
# Pydantic models for LLM response validation
# ---------------------------------------------------------------------------


class QuestionGrade(BaseModel):
    score: float
    feedback: str = ""
    confidence: str = "medium"
    requires_review: bool = False

    @field_validator("score", mode="before")
    @classmethod
    def coerce_score(cls, v) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    @field_validator("feedback", mode="before")
    @classmethod
    def coerce_feedback(cls, v) -> str:
        return str(v) if v is not None else ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v) -> str:
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


class GradingResponse(BaseModel):
    grades: dict[str, QuestionGrade]

    @classmethod
    def from_raw(cls, raw: dict, expected_qids: list[str]) -> "GradingResponse":
        """Parse and normalize LLM output dict. Handles Q4.1 and 4.1 key formats."""
        grades: dict[str, QuestionGrade] = {}
        for k, v in raw.items():
            normalized = k.strip().lstrip("Qq").strip()
            if isinstance(v, dict):
                try:
                    grades[normalized] = QuestionGrade.model_validate(v)
                except Exception:
                    grades[normalized] = QuestionGrade(
                        score=0.0,
                        feedback=LLM_PARSE_ERROR,
                        confidence="low",
                        requires_review=True,
                    )
            elif isinstance(v, (int, float)):
                grades[normalized] = QuestionGrade(score=float(v))

        for qid in expected_qids:
            if qid not in grades:
                grades[qid] = QuestionGrade(
                    score=0.0,
                    feedback=LLM_NOT_RETURNED,
                    confidence="low",
                    requires_review=True,
                )
        return cls(grades=grades)
