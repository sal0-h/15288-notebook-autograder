"""Token usage aggregation and cost from ``MODEL_PRICING``."""

from __future__ import annotations

from config_models import DEFAULT_MODEL
from grading_models import MODEL_PRICING

from llm.types import TokenUsage


def usage_cost_usd(usage: TokenUsage, model: str) -> float:
    """Approximate USD cost for recorded usage at the given model's rates."""
    pt = int(usage.prompt_tokens or 0)
    ct = int(usage.completion_tokens or 0)
    inp, out = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
    return (pt / 1e6 * inp) + (ct / 1e6 * out)
