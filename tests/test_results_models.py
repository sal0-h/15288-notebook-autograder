"""Tests for graded / parsed artifact models."""

from token_usage import GRADED_RESULT_USAGE_KEY
from results_models import (
    GradedResult,
    ParsedNotebook,
    graded_result_to_disk_dict,
)


def test_graded_result_roundtrip_usage_alias():
    gr = GradedResult(
        student_name="Alice",
        questions={"1.1": {"score": 1, "max": 2, "feedback": "ok"}},
        total_score=1.0,
        total_max=2.0,
        summary_feedback=".",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    d = graded_result_to_disk_dict(gr)
    assert d[GRADED_RESULT_USAGE_KEY] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }
    again = GradedResult.model_validate(d)
    assert again.student_name == "Alice"
    assert again.usage == {"prompt_tokens": 10, "completion_tokens": 5}


def test_parsed_notebook_extra_keys_preserved_in_dump():
    p = ParsedNotebook.model_validate(
        {
            "student_name": "Bob",
            "sections": {"1": {"questions": {}}},
            "duplicate_qids": [],
            "custom_meta": 42,
        }
    )
    dumped = p.model_dump(mode="python")
    assert dumped.get("custom_meta") == 42
