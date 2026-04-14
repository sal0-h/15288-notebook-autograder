"""Token counting, cost estimation, and usage tracking across LLM calls."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from config_models import DEFAULT_MODEL

# On-disk / API JSON key for token usage on graded rows.
GRADED_RESULT_USAGE_KEY = "_usage"

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


def usage_cost_usd(usage: TokenUsage, model: str) -> float:
    """Approximate USD cost for recorded usage at the given model's rates."""
    prompt = getattr(usage, "prompt_tokens", 0)
    completion = getattr(usage, "completion_tokens", 0)
    pt = int(prompt or 0)
    ct = int(completion or 0)
    inp, out = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
    return (pt / 1e6 * inp) + (ct / 1e6 * out)


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
