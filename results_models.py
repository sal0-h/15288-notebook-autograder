"""Pydantic models for on-disk JSON artifacts (graded results, parsed notebooks)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# On-disk / API JSON key for token usage on graded rows (helpers: detach/merge in this module).
GRADED_RESULT_USAGE_KEY = "_usage"


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


# ---------------------------------------------------------------------------
# Token Usage Tracking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenUsage:
    """Immutable token count aggregate."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    def has_tokens(self) -> bool:
        return bool(self.prompt_tokens or self.completion_tokens)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def merged(self, other: TokenUsage) -> TokenUsage:
        """Return counts combined with another usage record (immutable)."""
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )

    @classmethod
    def from_json_dict(cls, data: Mapping[str, Any] | None) -> TokenUsage:
        """Parse counts from JSON (same keys as fields — usual OpenAI-style usage shape)."""
        if not data:
            return cls()
        return cls(
            prompt_tokens=int(data.get("prompt_tokens", 0) or 0),
            completion_tokens=int(data.get("completion_tokens", 0) or 0),
        )

    def to_json_dict(self) -> dict[str, int]:
        """Serialize for JSON; keys match attribute names (``_usage``, SSE, estimates)."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


def detach_usage_from_graded_result(
    result: dict[str, Any],
) -> tuple[dict[str, Any], TokenUsage | None]:
    """Return ``(payload_without_usage, usage)`` without mutating ``result``."""
    raw = result.get(GRADED_RESULT_USAGE_KEY)
    out = {k: v for k, v in result.items() if k != GRADED_RESULT_USAGE_KEY}
    if not raw:
        return out, None
    return out, TokenUsage.from_json_dict(raw)


def merge_graded_usage(total: TokenUsage, result: dict[str, Any]) -> TokenUsage:
    """Add token counts from a graded result dict into ``total``."""
    _, u = detach_usage_from_graded_result(result)
    if u is not None and u.has_tokens():
        return total.merged(u)
    return total


def graded_usage_summary_event(usage: TokenUsage, model: str) -> dict[str, Any]:
    """SSE / progress dict for end-of-run usage (matches batch grader shape)."""
    from grading_models import usage_cost_usd

    cost = usage_cost_usd(usage, model)
    return {
        "student": "",
        "status": "usage",
        "result": None,
        "error": None,
        "usage": usage.to_json_dict(),
        "cost_usd": round(cost, 4),
        "model": model,
    }
