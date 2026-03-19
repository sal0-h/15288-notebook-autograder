"""Tests for llm.usage_helpers."""

from llm.usage_helpers import (
    detach_usage_from_graded_result,
    graded_usage_summary_event,
    merge_graded_usage,
)
from llm.types import TokenUsage
from results_models import GRADED_RESULT_USAGE_KEY


def test_detach_usage_immutable():
    raw = {
        "student_name": "A",
        "total_score": 1.0,
        "total_max": 1.0,
        "questions": {},
        "summary_feedback": ".",
        GRADED_RESULT_USAGE_KEY: {"prompt_tokens": 3, "completion_tokens": 2},
    }
    out, u = detach_usage_from_graded_result(raw)
    assert GRADED_RESULT_USAGE_KEY not in out
    assert GRADED_RESULT_USAGE_KEY in raw
    assert u is not None
    assert u.prompt_tokens == 3
    assert u.completion_tokens == 2


def test_merge_graded_usage():
    total = TokenUsage(prompt_tokens=1, completion_tokens=1)
    r = {GRADED_RESULT_USAGE_KEY: {"prompt_tokens": 5, "completion_tokens": 4}}
    merged = merge_graded_usage(total, r)
    assert merged.prompt_tokens == 6
    assert merged.completion_tokens == 5


def test_graded_usage_summary_event_shape():
    u = TokenUsage(prompt_tokens=100, completion_tokens=50)
    evt = graded_usage_summary_event(u, "gpt-4.1-mini")
    assert evt["status"] == "usage"
    assert evt["usage"] == {"prompt_tokens": 100, "completion_tokens": 50}
    assert "cost_usd" in evt
    assert evt["model"] == "gpt-4.1-mini"
