"""Graded-result ``_usage`` ↔ ``TokenUsage`` ↔ SSE summary events (single place)."""

from __future__ import annotations

from typing import Any

from llm.cost import usage_cost_usd
from llm.types import TokenUsage
from results_models import GRADED_RESULT_USAGE_KEY


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
