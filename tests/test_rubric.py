"""Tests for rubric_generate: LLM-based rubric generation."""

import json
from unittest.mock import MagicMock, patch

import pytest

from config_models import (
    RubricEntry,
    RubricItem,
    ensure_app_config,
)
from grading_models import (
    RubricGroupLlmResponse,
    RubricQuestionLlm,
)
from llm.json_runner import MAX_JSON_LLM_ATTEMPTS
from token_usage import TokenUsage
from rubric_generate import generate_rubrics


def _solution_parsed(qids: list[str]) -> dict:
    """Build solution_parsed.json structure."""
    sections = {}
    for qid in qids:
        sec, _ = qid.split(".")
        sections.setdefault(sec, {"questions": {}})
        sections[sec]["questions"][qid] = {
            "points": 2,
            "question_markdown": f"Q{qid}",
            "answer_code_concat": "x = 1",
            "answer_text_concat": "",
            "answer_markdown_concat": "",
        }
    return {"sections": sections}


def _rubric_response(rubric_dict: dict) -> tuple:
    """Build a (RubricGroupLlmResponse, TokenUsage) mock return value."""
    questions = [
        RubricQuestionLlm(
            question_id=qid,
            points=v["points"],
            items=[RubricItem(**item) for item in v["items"]],
        )
        for qid, v in rubric_dict.items()
    ]
    return (RubricGroupLlmResponse(questions=questions), TokenUsage(1, 1))


class TestGenerateRubrics:
    def test_uses_configured_rubric_generation_prompt(self, tmp_path, monkeypatch):
        """Rubric generation should use the prompt text returned by load_prompt."""
        sol = _solution_parsed(["1.1"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        custom_prompt = "CUSTOM RUBRIC SYSTEM PROMPT"
        monkeypatch.setattr(
            "prompt_builder.load_prompt",
            lambda name, assignment_name=None: custom_prompt,
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["1.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
            "rubric_review": False,
        }

        with patch("llm.json_runner.complete_structured") as mock_cs:
            mock_cs.return_value = _rubric_response(
                {
                    "1.1": {
                        "points": 2,
                        "items": [{"description": "Correct", "deduction": 2.0}],
                    }
                }
            )
            generate_rubrics(ensure_app_config(config), client=MagicMock())

        call = mock_cs.call_args
        assert call.kwargs["messages"][0]["content"] == custom_prompt

    def test_uses_configured_rubric_review_prompt(self, tmp_path, monkeypatch):
        """Rubric review second pass should use review_system prompt from load_prompt."""
        sol = {
            "sections": {
                "1": {
                    "questions": {
                        "1.1": {
                            "points": 2,
                            "question_markdown": "Q1.1",
                            "answer_code_concat": "x = 1",
                            "answer_text_concat": "",
                            "answer_markdown_concat": "",
                        }
                    }
                }
            }
        }
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        custom_review_prompt = "CUSTOM RUBRIC REVIEW PROMPT"

        def _load_prompt(name, assignment_name=None):
            return custom_review_prompt if name == "review_system" else "OTHER"

        monkeypatch.setattr("prompt_builder.load_prompt", _load_prompt)

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["1.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
            "rubric_review": True,
        }

        from grading_models import (
            RubricReviewItem,
            RubricReviewQuestion,
            RubricReviewResponse,
        )

        gen_resp = _rubric_response(
            {
                "1.1": {
                    "points": 2,
                    "items": [{"description": "Generated", "deduction": 2.0}],
                }
            }
        )
        review_resp = (
            RubricReviewResponse(
                questions=[
                    RubricReviewQuestion(
                        question_id="1.1",
                        items=[RubricReviewItem(description="Reviewed")],
                    )
                ]
            ),
            TokenUsage(1, 1),
        )

        with patch("llm.json_runner.complete_structured") as mock_cs:
            mock_cs.side_effect = [gen_resp, review_resp]
            rubrics = generate_rubrics(ensure_app_config(config), client=MagicMock())

        assert mock_cs.call_count == 2
        review_call = mock_cs.call_args_list[1]
        assert review_call.kwargs["messages"][0]["content"] == custom_review_prompt
        assert rubrics["1.1"].items[0].description == "Reviewed"

    def test_returns_expected_structure_and_normalizes_keys(self, tmp_path):
        """Mock LLM returns valid response; keys like Q4.1 are normalized to 4.1."""
        sol = _solution_parsed(["4.1", "4.2"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["4.1", "4.2"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
        }

        resp = _rubric_response(
            {
                "Q4.1": {
                    "points": 2,
                    "items": [
                        {"description": "Correct output", "deduction": 1.0},
                        {"description": "Correct method", "deduction": 1.0},
                    ],
                },
                "q4.2": {
                    "points": 2,
                    "items": [
                        {"description": "Full marks: correct.", "deduction": 2.0}
                    ],
                },
            }
        )

        with patch("llm.json_runner.complete_structured", return_value=resp):
            rubrics = generate_rubrics(ensure_app_config(config), client=MagicMock())

        assert "4.1" in rubrics
        assert "4.2" in rubrics
        assert rubrics["4.1"].points == 2
        assert rubrics["4.1"].items[0].description == "Correct output"
        assert rubrics["4.1"].items[0].deduction == 1.0
        assert rubrics["4.2"].points == 2

    def test_missing_question_in_response_triggers_retry_then_placeholder(
        self, tmp_path
    ):
        """When validate raises (question missing from response), retries then falls back."""
        sol = _solution_parsed(["5.1"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["5.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
            "rubric_review": False,
        }

        # LLM keeps returning an empty questions list — missing "5.1" every time
        empty_resp = (RubricGroupLlmResponse(questions=[]), TokenUsage(1, 1))

        with patch("llm.json_runner.time.sleep"):
            with patch(
                "llm.json_runner.complete_structured",
                return_value=empty_resp,
            ) as mock_cs:
                rubrics = generate_rubrics(
                    ensure_app_config(config), client=MagicMock()
                )

        assert mock_cs.call_count == MAX_JSON_LLM_ATTEMPTS
        assert rubrics["5.1"].points == 2
        assert rubrics["5.1"].items[0].description == "[generation failed]"
        assert rubrics["5.1"].items[0].deduction == 2.0

    def test_from_llm_output_requires_points(self):
        with pytest.raises(ValueError, match="points"):
            RubricEntry.from_llm_output(
                {"items": [{"description": "x", "deduction": 1.0}]}
            )

    def test_fallback_when_llm_fails(self, tmp_path):
        """When LLM call raises, fallback to solution points and error message."""
        sol = _solution_parsed(["6.1"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["6.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
            "rubric_review": False,
        }

        with patch("llm.json_runner.time.sleep"):
            with patch(
                "llm.json_runner.complete_structured",
                side_effect=Exception("API error"),
            ):
                rubrics = generate_rubrics(
                    ensure_app_config(config), client=MagicMock()
                )

        assert rubrics["6.1"].points == 2
        assert rubrics["6.1"].items[0].description == "[generation failed]"
        assert rubrics["6.1"].items[0].deduction == 2

    def test_deductions_rescaled_when_off(self, tmp_path):
        """When LLM returns items summing to wrong total, rescale to match points."""
        sol = _solution_parsed(["7.1"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["7.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
        }

        # LLM returns 3 total deductions for a 2-point question
        resp = _rubric_response(
            {
                "7.1": {
                    "points": 2,
                    "items": [
                        {"description": "A", "deduction": 1.0},
                        {"description": "B", "deduction": 1.0},
                        {"description": "C", "deduction": 1.0},
                    ],
                }
            }
        )

        with patch("llm.json_runner.complete_structured", return_value=resp):
            rubrics = generate_rubrics(ensure_app_config(config), client=MagicMock())

        assert rubrics["7.1"].points == 2
        total = sum(i.deduction for i in rubrics["7.1"].items)
        assert abs(total - 2.0) < 0.01

    def test_missing_solution_raises(self, tmp_path):
        """FileNotFoundError when solution_parsed.json does not exist."""
        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["1.1"]]},
        }
        with pytest.raises(FileNotFoundError, match="Solution parsed not found"):
            generate_rubrics(ensure_app_config(config))

    def test_group_indices_merges_into_existing(self, tmp_path):
        """When group_indices is set, only generates for those groups and merges into existing."""
        sol = _solution_parsed(["4.1", "4.2", "5.1"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        existing_rubrics = {
            "4.1": {
                "points": 2,
                "items": [{"description": "Existing 4.1", "deduction": 2.0}],
            },
            "4.2": {
                "points": 2,
                "items": [{"description": "Existing 4.2", "deduction": 2.0}],
            },
        }

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["4.1", "4.2"], ["5.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
            "rubrics": existing_rubrics,
        }

        resp = _rubric_response(
            {
                "5.1": {
                    "points": 2,
                    "items": [{"description": "New 5.1", "deduction": 2.0}],
                }
            }
        )

        with patch("llm.json_runner.complete_structured", return_value=resp):
            rubrics = generate_rubrics(
                ensure_app_config(config), client=MagicMock(), group_indices=[1]
            )

        assert rubrics["4.1"].items[0].description == "Existing 4.1"
        assert rubrics["4.2"].items[0].description == "Existing 4.2"
        assert rubrics["5.1"].items[0].description == "New 5.1"

    def test_group_indices_are_applied_before_grade_only_filter(self, tmp_path):
        """group_indices refer to original question_groups even when grade_only is set."""
        sol = _solution_parsed(["1.1", "1.2", "2.1"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {
                "question_groups": [["1.1"], ["1.2"], ["2.1"]],
                "grade_only": ["2.1"],
            },
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
            "rubrics": {},
        }

        resp = _rubric_response(
            {
                "2.1": {
                    "points": 2,
                    "items": [{"description": "Generated 2.1", "deduction": 2.0}],
                }
            }
        )

        with patch("llm.json_runner.complete_structured", return_value=resp):
            rubrics = generate_rubrics(
                ensure_app_config(config), client=MagicMock(), group_indices=[2]
            )

        assert "2.1" in rubrics
        assert rubrics["2.1"].items[0].description == "Generated 2.1"
